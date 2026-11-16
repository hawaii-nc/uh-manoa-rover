"""A map you can walk through yourself, to check the model against the real campus.

`build_explorer` writes a folder that opens in any browser (and as a published page):

- the 3D world split into a few GLB files, each small enough to share,
- `data.json`: the rover's walkway network, the planned routes with the stretches a rover
  cannot use, every building's facts and sources, and a list of **things to check**: the
  places where the data is thin, the survey disagrees with itself, or a limit is assumed,
- the page itself (`index.html`, with `vendor/`).

Nothing here decides what is true. The checks list says where to look, and the page keeps
a looks-right / looks-wrong / unsure mark and a note per item for you to hand back.
"""

from __future__ import annotations

import itertools
import json
import math
import shutil
from datetime import date
from pathlib import Path

import networkx as nx

from .planner import NoRouteError, RoutePlanner, landmark_goals
from .traversal import CODE, LEVELS
from .world import PLANAR_GROUPS, World, export_glb

KINDS = ["walkway", "ramp", "stairs", "crosswalk"]
FLAG_GAP, FLAG_CLOSED, FLAG_CUT_UNKNOWN, FLAG_CUT_YES, FLAG_CUT_NO = 1, 2, 4, 8, 16
FLAG_TIER = {"candidate": 32, "supported": 64, "marked": 128, "confirmed": 256}
FLAG_CURB_RAMP, FLAG_ACCESS_RAMP = 512, 1024

# Ground classes whose geometry stands up (curbs, steps, walls); they go in their own file.
STRUCTURE_CLASSES = {"curb", "stairs", "wall", "planter"}


def _ground(key) -> bool:
    return key[0].split("/")[0] in PLANAR_GROUPS


def split_files() -> dict:
    """File name -> filter over scene part keys (group, class, material)."""
    return {
        "world_ground.glb": lambda k: _ground(k) and k[1] not in STRUCTURE_CLASSES,
        "world_structures.glb": lambda k: _ground(k) and k[1] in STRUCTURE_CLASSES,
        "world_objects.glb": lambda k: not _ground(k),
    }


class _Frame:
    """Site coordinates to viewer coordinates (x east, y up, z south), as the GLB uses."""

    def __init__(self, origin, ground_z) -> None:
        self.ox, self.oy, self.oz = origin
        self.ground_z = ground_z

    def at(self, x: float, y: float, z: float | None = None, lift: float = 0.0) -> list:
        if z is None:
            z = float(self.ground_z(x, y))
        return [round(x - self.ox, 1), round(z - self.oz + lift, 2), round(-(y - self.oy), 1)]


def pack_text(path: Path, chunk_bytes: int = 12_000_000) -> list[Path]:
    """Replace a binary file by base64 text chunks (`name.000.txt`, ...). Page hosts that only
    serve text, images and fonts can then carry it; the page decodes the chunks back."""
    import base64

    raw = path.read_bytes()
    chunk_bytes -= chunk_bytes % 3                    # whole base64 groups, so chunks just join
    out = []
    for i in range(0, len(raw), chunk_bytes):
        part = path.with_name(f"{path.stem}.{i // chunk_bytes:03d}.txt")
        part.write_text(base64.b64encode(raw[i:i + chunk_bytes]).decode("ascii"))
        out.append(part)
    path.unlink()
    return out


def build_explorer(site, world: World, out_dir: Path, progress=lambda msg: None,
                   max_file_mb: float = 20, hosted: bool = False) -> dict:
    """Write the explorer bundle for a built (ideally `lite`) world. Returns the files.
    A file over `max_file_mb` (the page host's limit is 20 MiB) is an error. `hosted` stores
    the 3D files as base64 text chunks, for hosts that cannot serve binary files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for name, keep in split_files().items():
        progress(f"writing {name}")
        files[name] = out_dir / name
        export_glb(world.scene, files[name], lite=True, only=keep,
                   instances=name == "world_objects.glb")
    sizes = {n: p.stat().st_size for n, p in files.items()}
    over = {n: s for n, s in sizes.items() if s > max_file_mb * 2**20}
    if over:
        raise RuntimeError(f"files over {max_file_mb} MiB: "
                           f"{ {n: round(s / 2**20, 1) for n, s in over.items()} }")
    agents_file = None
    if world.agents is not None:
        agents_file = f"agents_{world.agents.meta.get('scenario', 'scenario')}.glb"
        export_glb(world.agents, out_dir / agents_file, lite=True)
        files[agents_file] = out_dir / agents_file

    progress("collecting the network, routes and checks")
    data = collect(site, world)
    data["files"] = {"ground": "world_ground.glb", "structures": "world_structures.glb",
                     "objects": "world_objects.glb", "agents": agents_file}
    data["sizes_mb"] = {n: round(s / 1e6, 1) for n, s in sizes.items()}
    if hosted:
        for key, name in list(data["files"].items()):
            if name:
                parts = pack_text(files.pop(name))
                data["files"][key] = [p.name for p in parts]
                files.update({p.name: p for p in parts})
    files["data.json"] = out_dir / "data.json"
    files["data.json"].write_text(json.dumps(data, separators=(",", ":")))

    viewer = Path(__file__).parent / "viewer"
    shutil.copy(viewer / "explorer.html", out_dir / "index.html")
    shutil.copytree(viewer / "vendor", out_dir / "vendor", dirs_exist_ok=True)
    files["index.html"] = out_dir / "index.html"
    return files


# ---- the data the page reads -------------------------------------------------------------


def collect(site, world: World) -> dict:
    report = world.report
    frame = _Frame(report["origin"], world.rasters.ground_z)
    planner = RoutePlanner.from_site(site)
    g = planner.graph.g

    # Pieces are what a rover can drive between: connected over passable edges only.
    usable = nx.Graph()
    usable.add_nodes_from(g)
    usable.add_edges_from((u, v) for u, v, d in g.edges(data=True) if planner.passable(d))
    pieces = sorted(nx.connected_components(usable), key=len, reverse=True)
    piece_of = {n: i for i, comp in enumerate(pieces) for n in comp}
    why: list[str] = []
    edges = []
    for u, v, d in g.edges(data=True):
        a, b = planner.graph.xy(u), planner.graph.xy(v)
        flags = (FLAG_GAP if d.get("gap") else 0) | (FLAG_CLOSED if d["closed"] else 0)
        if d["kind"] == "crosswalk":
            cut = d.get("curb_cut")
            flags |= FLAG_CUT_UNKNOWN if cut is None else (FLAG_CUT_YES if cut else FLAG_CUT_NO)
            flags |= FLAG_TIER.get(d.get("tier") or "marked", 0)
        if d["kind"] == "ramp":
            flags |= FLAG_CURB_RAMP if d.get("ramp_kind") == "curb" else FLAG_ACCESS_RAMP
        reason = planner.why_impassable(d)
        if reason is not None:
            reason = reason.split(" (")[0]          # "too steep"; the grade is its own column
            if reason not in why:
                why.append(reason)
        lift = 0.25
        edges.append([*frame.at(*a, lift=lift), *frame.at(*b, lift=lift),
                      KINDS.index(d["kind"]), piece_of[u], round(d.get("width") or 0, 1),
                      round(d["slope"], 3), flags, -1 if reason is None else why.index(reason),
                      round(d["length"], 1)])

    goals = landmark_goals(site, planner)
    by_building = {b["id"]: b for b in report["buildings"]}
    landmarks = {}
    for k, nodes_ in goals.items():
        spec = site.landmarks[k]
        b = next((x for x in by_building.values()
                  if spec.get("building", "").lower() in x["name"].lower()), None)
        xy = b["centroid"] if b else planner.graph.xy(nodes_[0])
        counts = {}
        for n in nodes_:
            counts[piece_of[n]] = counts.get(piece_of[n], 0) + 1
        landmarks[k] = {"label": site.landmark_label(k), "pos": frame.at(*xy),
                        "piece": min(counts), "pieces": sorted(counts),
                        "access_nodes": len(nodes_)}

    routes, checks = {}, []
    for key, route in site.routes.items():
        legs = []
        for wa, wb in itertools.pairwise(route.waypoints):
            legs.append(_leg(site, planner, frame, goals[wa], goals[wb], wa, wb))
        routes[key] = {"label": route.label, "legs": legs}
    seen = set()
    for r in routes.values():
        for leg in r["legs"]:
            if leg["id"] in seen:
                continue
            seen.add(leg["id"])
            checks.extend(_leg_checks(leg))
    checks.extend(_network_checks(planner, frame, piece_of))
    route_pts = [(p[0] - 0, p[2]) for r in routes.values() for leg in r["legs"] for p in leg["pts"]]
    checks.extend(_ramp_checks(site, world, frame, route_pts))
    buildings, building_checks = _buildings(report, frame)
    checks.extend(building_checks)
    for c in checks:
        # Stable ids (a place, or a building), so your marks survive a rebuild of the data.
        c.setdefault("id", f"{c['cat']}-{c['building']}" if c.get("building")
                     else f"{c['cat']}-{round(c['pos'][0])}-{round(c['pos'][2])}")

    return {
        "site": report["site"], "built": date.today().isoformat(), "origin": report["origin"],
        "size_m": report["size_m"], "bbox": [round(v, 1) for v in report["bbox"]],
        "limits": {"max_slope": planner.params.max_slope,
                   "allow_stairs": planner.params.allow_stairs,
                   "min_width_m": planner.params.min_width_m,
                   "curb_height_m_assumed": 0.15},
        "kinds": KINDS, "why": why, "edges": edges,
        "pieces": [{"n": len(c), "edges": g.subgraph(c).number_of_edges()} for c in pieces[:60]],
        "landmarks": landmarks, "routes": routes, "buildings": buildings, "checks": checks,
        "levels": {"levels": LEVELS, "codes": CODE},
        "sources": report.get("sources", {}), "report_checks": report.get("checks", {}),
        "trees": report.get("trees"), "props": report.get("props"),
        "surface_share": report.get("surface_share"),
    }


def _leg(site, planner, frame, srcs, dsts, wa, wb) -> dict:
    """One leg of a route: the least-blocked path between any entry of the two buildings, what
    stops a rover on it, the single crossings that would fix it, and a profile of the path."""
    label = f"{site.landmark_label(wa)} → {site.landmark_label(wb)}"
    leg = {"id": f"{wa}>{wb}", "label": label, "passable": False, "length_m": None, "pts": [],
           "stretches": [], "note": None, "options": [], "profile": None}
    try:
        info = planner.blockers(srcs, dsts)
    except NoRouteError as e:
        gap = planner.closest_approach(srcs[0], dsts[0])
        leg["note"] = str(e)
        leg["break"] = {"distance_m": round(gap["distance_m"], 1), "a": frame.at(*gap["a"]),
                        "b": frame.at(*gap["b"])}
        return leg
    leg["pts"] = [frame.at(*planner.graph.xy(n), lift=0.3) for n in info["path"]]
    leg["length_m"] = round(info["length_m"], 1)
    leg["passable"] = not info["stretches"]
    for st in info["stretches"]:
        leg["stretches"].append({"reason": st["reason"], "kind": st["kind"],
                                 "from": frame.at(*st["from"], lift=0.3),
                                 "to": frame.at(*st["to"], lift=0.3),
                                 "length_m": round(st["length_m"], 1),
                                 "max_grade": round(st["max_grade"], 3)})
    if any(st["reason"] == "unverified crossing" for st in info["stretches"]):
        for o in planner.connecting_options(srcs, dsts):
            leg["options"].append({"from": frame.at(*o["from"], lift=0.3),
                                   "to": frame.at(*o["to"], lift=0.3),
                                   "length_m": round(o["length_m"], 1), "road": o["road"],
                                   "evidence": o["evidence"]})
    leg["profile"] = _profile(planner, info["path"])
    return leg


def _profile(planner, path) -> dict:
    """What riding this path is like: surfaces, widths, grades and crossings."""
    g = planner.graph.g
    by_kind, widths, grades = {}, [], []
    crossings = []
    for u, v in itertools.pairwise(path):
        d = g.edges[u, v]
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0.0) + d["length"]
        if d.get("width_min") or d.get("width"):
            widths.append(d.get("width_min") or d.get("width"))
        if d["kind"] != "crosswalk":
            grades.append((abs(d["slope"]), d["length"]))
        else:
            crossings.append({"tier": d.get("tier") or "marked", "road": d.get("road"),
                              "length_m": round(d["length"], 1), "curb_cut": d.get("curb_cut")})
    total = sum(by_kind.values()) or 1.0
    limit = planner.params.max_slope
    return {"by_kind_m": {k: round(v) for k, v in by_kind.items()},
            "narrowest_m": round(min(widths), 1) if widths else None,
            "steepest": round(max((g_[0] for g_ in grades), default=0), 3),
            "over_limit_m": round(sum(length for gr, length in grades if gr > limit)),
            "crossings": crossings, "length_m": round(total)}


def _mid(a, b) -> list:
    return [round((a[0] + b[0]) / 2, 1), round((a[1] + b[1]) / 2, 2), round((a[2] + b[2]) / 2, 1)]


def _leg_checks(leg: dict) -> list:
    out = []
    if leg.get("break"):
        br = leg["break"]
        out.append({
            "cat": "break", "sev": 3, "pos": _mid(br["a"], br["b"]),
            "title": f"{leg['label']}: the map has a {br['distance_m']:.1f} m break",
            "why": "The surveyed walkways on the two sides never join. Is there a real path "
                   "here (a gate, a lawn path, a ramp) that the survey missed, or is it a real "
                   "break? The two ends are marked on the map.",
            "marks": [br["a"], br["b"]]})
    for i, st in enumerate(leg["stretches"]):
        if st["reason"] == "unverified crossing":
            continue
        out.append({
            "cat": "blocked", "sev": 3, "pos": _mid(st["from"], st["to"]),
            "title": f"{leg['label']}: {st['reason']}, {st['length_m']:.0f} m",
            "why": f"A rover cannot use this {st['kind']} stretch (steepest grade "
                   f"{st['max_grade']:.0%}). Does it look like this on the ground? Is there a "
                   f"way round that the map does not have?",
            "marks": [st["from"], st["to"]], "id": f"blocked-{leg['id']}-{i}"})
    if leg["options"]:
        # One check per stretch of road, not one per candidate line across it.
        zones: list[list[dict]] = []
        for o in sorted(leg["options"], key=lambda o: (o["road"] or "", o["from"][0])):
            for z in zones:
                if z[0]["road"] == o["road"] and math.dist(
                        (z[-1]["from"][0], z[-1]["from"][2]), (o["from"][0], o["from"][2])) < 40:
                    z.append(o)
                    break
            else:
                zones.append([o])
        for z in sorted(zones, key=lambda z: min(o["length_m"] for o in z)):
            best = min(z, key=lambda o: o["length_m"])
            n = len(z)
            road = best["road"] or "a road"
            out.append({
                "cat": "crossing-needed", "sev": 3, "pos": _mid(best["from"], best["to"]),
                "title": f"{leg['label']}: needs a crossing of {road}, none is mapped",
                "why": f"The two sides join only over {road} ({best['length_m']:.0f} m wide). "
                       f"The map has no crosswalk and no ramp at the curb here, so the rover "
                       f"is not allowed to use it until you check. Walk it: is there a "
                       f"crosswalk, where, and are there curb ramps at both ends? "
                       f"({n} possible line{'s' if n > 1 else ''} across, all drawn.)",
                "marks": [m for o in z[:12] for m in (o["from"], o["to"])],
                "id": f"needed-{leg['id']}-{round(best['from'][0])}-{round(best['from'][2])}"})
    return out


def _ramp_checks(site, world, frame, route_pts) -> list:
    """Mapped ramps near the route: wheels need them where there are stairs or curbs."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    from .walknet import _long_axis, _mean_z
    from .world import WorldConfig, load_landcover

    cfg = WorldConfig.from_site(site)
    if not cfg.landcover or not (site.root / cfg.landcover["file"]).exists():
        return []
    feats = load_landcover(site.root / cfg.landcover["file"], cfg.landcover)
    road = unary_union([g for g, c, _ in feats if c == "road"])
    region = box(*world.rasters.bounds)
    pts = [(p[0] + frame.ox, -p[1] + frame.oy) for p in route_pts]   # back to site x, y
    out = []
    for geom, cls, _ in feats:
        if cls != "ramp" or not geom.intersects(region):
            continue
        for part in getattr(geom, "geoms", [geom]):
            if part.geom_type != "Polygon" or part.area < 2.0:
                continue
            c = part.centroid
            if pts and min(math.dist((c.x, c.y), p) for p in pts) > 60:
                continue
            axis = _long_axis(part)
            if axis is None or axis[2] < 2.0:
                continue
            e1, e2, length, width = axis
            grade = abs(_mean_z(world.rasters.ground_z, e1) - _mean_z(world.rasters.ground_z, e2)
                        ) / max(length, 0.1)
            kind = "curb ramp" if part.distance(road) <= 1.5 else "ramp beside stairs or entrance"
            out.append({"cat": "ramp", "sev": 1, "pos": frame.at(c.x, c.y, lift=0.3),
                        "title": f"{kind.capitalize()}, {length:.0f} m long, {width:.1f} m wide, "
                                 f"grade {grade:.0%}",
                        "why": "UH maps a ramp here. Does it exist and can a wheel use it: is "
                               "the grade gentle, is it wide enough, is there a lip at the "
                               "street end? The grade is measured from the lidar ground."})
    return out


def _network_checks(planner, frame, piece_of) -> list:
    g = planner.graph.g
    out = []
    for u, v, d in g.edges(data=True):
        a, b = frame.at(*planner.graph.xy(u), lift=0.3), frame.at(*planner.graph.xy(v), lift=0.3)
        if d.get("gap"):
            out.append({"cat": "gap", "sev": 1, "pos": _mid(a, b), "marks": [a, b],
                        "title": f"Gap link, {d['length']:.1f} m",
                        "why": "Two surveyed walkway pieces are joined here by a link over "
                               "ground that is not mapped as walkway. Is there really a "
                               "connection (a worn path, a gap in a wall)?"})
        elif d["kind"] == "crosswalk" and d.get("tier") in ("marked", "supported", "confirmed"):
            cut = d.get("curb_cut")
            tier = d["tier"]
            how = {"marked": "OpenStreetMap draws a crosswalk here",
                   "supported": "UH maps a ramp or a gap in the curb at both ends",
                   "confirmed": "you confirmed it"}[tier]
            out.append({"cat": "crosswalk", "sev": 2, "pos": _mid(a, b), "marks": [a, b],
                        "title": f"{tier.capitalize()} crossing of {d.get('road') or 'a road'}, "
                                 f"{d['length']:.0f} m (curb cut "
                                 f"{'unknown' if cut is None else cut})",
                        "why": f"Why the rover may use it: {how}"
                               f"{' (' + d['evidence'] + ')' if d.get('evidence') else ''}. "
                               f"Is there a crosswalk, and are the curb ramps real at both "
                               f"ends?"})
    # Clusters of steep, non-stairs edges: where the lidar says the grade is above 15 %.
    cells: dict = {}
    for u, v, d in g.edges(data=True):
        if d["kind"] == "stairs" or abs(d["slope"]) <= 0.15:
            continue
        a, b = planner.graph.xy(u), planner.graph.xy(v)
        cell = (round((a[0] + b[0]) / 2 / 25), round((a[1] + b[1]) / 2 / 25))
        cells.setdefault(cell, []).append((a, b, d))
    ranked = sorted(cells.values(), key=lambda es: -sum(e[2]["length"] for e in es))[:12]
    for es in ranked:
        mx = sum((e[0][0] + e[1][0]) / 2 for e in es) / len(es)
        my = sum((e[0][1] + e[1][1]) / 2 for e in es) / len(es)
        steepest = max(abs(e[2]["slope"]) for e in es)
        total = sum(e[2]["length"] for e in es)
        out.append({"cat": "steep", "sev": 2, "pos": frame.at(mx, my, lift=0.3),
                    "title": f"Steep walkway: {total:.0f} m over 15 %, up to {steepest:.0%}",
                    "why": "The lidar says this walkway climbs more than a rover can. Real "
                           "slope or a measurement artefact (a wall, a bridge, a rooftop edge)? "
                           "Check how it feels walking it."})
    return out


def _buildings(report: dict, frame: _Frame):
    buildings, checks = [], []
    for b in report["buildings"]:
        cx, cy = b["centroid"]
        height = b["height_m"]
        ring = [[round(x - frame.ox, 1), round(-(y - frame.oy), 1)] for x, y in b["ring"]]
        pos = [round(cx - frame.ox, 1), round(b["base_z"] - frame.oz, 2),
               round(-(cy - frame.oy), 1)]
        facts = {k: b.get(k) for k in (
            "floors", "floors_source", "floor_height", "windows", "doors", "blocks", "pitched",
            "columns", "room_walls", "slab_edges", "plan_layout") if b.get(k) is not None}
        buildings.append({
            "id": b["id"], "name": b["name"], "abbrev": b.get("abbrev"),
            "number": b.get("number"), "tier": b["tier"], "how": b["style_source"],
            "campus": b["campus"], "year": b.get("year"),
            "height_m": height, "footprint_m2": b["footprint_m2"], "sqft": b.get("sqft"),
            "route_m": b.get("route_distance_m"), "pos": pos, "ring": ring,
            "style": b.get("style"), "facts": facts})
        title = f"{b['name'].title()} ({b.get('abbrev') or b['id']})"
        if b["style_source"] == "unverified" and b["tier"] != "C":
            checks.append({
                "cat": "unverified", "sev": 2, "pos": pos, "building": b["id"],
                "title": f"{title}: plain grey, not researched",
                "why": "No photo-backed style sheet exists, so it is grey massing without "
                       "windows or doors (by the project rule). Roughly what is this "
                       "building, and what does its ground floor look like?"})
        elif b.get("plan_layout") and b["campus"]:
            checks.append({
                "cat": "plan", "sev": 2, "pos": pos, "building": b["id"],
                "title": f"{title}: floors built from UH floor plans",
                "why": "The rooms and walls were fitted from UH's floor plans onto the lidar "
                       "roof. Compare the shape, the number of floors and any open corridors "
                       "with the real building."})
        elif b["style_source"] == "sheet" and (b.get("style") or {}).get("confidence") in (
                "low", "guess", "unsure"):
            checks.append({
                "cat": "style", "sev": 1, "pos": pos, "building": b["id"],
                "title": f"{title}: low-confidence look",
                "why": "The style sheet says it is low confidence. Is the colour, window "
                       "layout and entrance right?"})
    return buildings, checks
