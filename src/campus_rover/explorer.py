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
import shutil
from datetime import date
from pathlib import Path

import networkx as nx

from .planner import NoRouteError, RoutePlanner
from .traversal import CODE, LEVELS
from .world import PLANAR_GROUPS, World, export_glb

KINDS = ["walkway", "ramp", "stairs", "crosswalk"]
FLAG_GAP, FLAG_CLOSED, FLAG_CUT_UNKNOWN, FLAG_CUT_YES, FLAG_CUT_NO = 1, 2, 4, 8, 16

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


def build_explorer(site, world: World, out_dir: Path, progress=lambda msg: None,
                   max_file_mb: float = 20) -> dict:
    """Write the explorer bundle for a built (ideally `lite`) world. Returns the files.
    A file over `max_file_mb` (the page host's limit is 20 MiB) is an error."""
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

    pieces = sorted(nx.connected_components(g), key=len, reverse=True)
    piece_of = {n: i for i, comp in enumerate(pieces) for n in comp}
    why: list[str] = []
    edges = []
    for u, v, d in g.edges(data=True):
        a, b = planner.graph.xy(u), planner.graph.xy(v)
        flags = (FLAG_GAP if d.get("gap") else 0) | (FLAG_CLOSED if d["closed"] else 0)
        if d["kind"] == "crosswalk":
            cut = d.get("curb_cut")
            flags |= FLAG_CUT_UNKNOWN if cut is None else (FLAG_CUT_YES if cut else FLAG_CUT_NO)
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

    nodes = site.landmark_nodes(planner.graph)
    landmarks = {k: {"label": site.landmark_label(k), "pos": frame.at(*planner.graph.xy(n)),
                     "piece": piece_of[n]} for k, n in nodes.items()}

    routes, checks = {}, []
    for key, route in site.routes.items():
        legs = []
        for wa, wb in itertools.pairwise(route.waypoints):
            leg = _leg(site, planner, frame, nodes[wa], nodes[wb], wa, wb)
            legs.append(leg)
        routes[key] = {"label": route.label, "legs": legs}
    seen = set()
    for r in routes.values():
        for leg in r["legs"]:
            if leg["id"] in seen:
                continue
            seen.add(leg["id"])
            checks.extend(_leg_checks(leg))
    checks.extend(_network_checks(planner, frame, piece_of))
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


def _leg(site, planner, frame, src, dst, wa, wb) -> dict:
    label = f"{site.landmark_label(wa)} → {site.landmark_label(wb)}"
    leg = {"id": f"{wa}>{wb}", "label": label, "passable": False, "length_m": None, "pts": [],
           "stretches": [], "note": None}
    try:
        info = planner.blockers(src, dst)
    except NoRouteError as e:
        gap = planner.closest_approach(src, dst)
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
    return leg


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
        out.append({
            "cat": "blocked", "sev": 3, "pos": _mid(st["from"], st["to"]),
            "title": f"{leg['label']}: {st['reason']}, {st['length_m']:.0f} m",
            "why": f"A rover cannot use this {st['kind']} stretch (steepest grade "
                   f"{st['max_grade']:.0%}). Does it look like this on the ground? Is there a "
                   f"way round that the map does not have?",
            "marks": [st["from"], st["to"]], "id": f"blocked-{leg['id']}-{i}"})
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
        elif d["kind"] == "crosswalk":
            cut = d.get("curb_cut")
            out.append({"cat": "crosswalk", "sev": 2, "pos": _mid(a, b), "marks": [a, b],
                        "title": f"Crosswalk, {d['length']:.0f} m "
                                 f"(curb cut {'unknown' if cut is None else cut})",
                        "why": "The survey has no record of ramps at the curb here. Is there "
                               "a ramp or a flush crossing at both ends?"})
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
    # Construction closures, one check per cluster.
    cl: dict = {}
    for u, v, d in g.edges(data=True):
        if d["closed"]:
            a, b = planner.graph.xy(u), planner.graph.xy(v)
            cl.setdefault((round((a[0] + b[0]) / 2 / 30), round((a[1] + b[1]) / 2 / 30)),
                          []).append((a, b, d))
    for es in sorted(cl.values(), key=lambda es: -len(es))[:10]:
        mx = sum((e[0][0] + e[1][0]) / 2 for e in es) / len(es)
        my = sum((e[0][1] + e[1][1]) / 2 for e in es) / len(es)
        total = sum(e[2]["length"] for e in es)
        out.append({"cat": "closure", "sev": 1, "pos": frame.at(mx, my, lift=0.3),
                    "title": f"Marked closed: {total:.0f} m of walkway "
                             f"({es[0][2]['closed_reason']})",
                    "why": "UH's construction layer closes this. Is there still a closure or "
                           "fence here, or is it open now?"})
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
