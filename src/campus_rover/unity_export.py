"""Export a built world for Unity (or any engine that dresses the world with its own models).

The repo stays the source of truth: ground, curbs, ramps, stairs, crossings and buildings come
from the open data as meshes, and everything that stands on the ground (trees, plants, props,
sculptures, street lights, people) comes as *placements*: what it is, where, which way and how
big, plus a plain fallback model per kind. The engine swaps each kind for a nicer model of its
own choosing and keeps the position and size, so a better tree never moves a tree.

Files written (all positions in the glTF frame of the world GLBs: metres, x east, y up,
z south, relative to the site origin in `site.json`):

    geometry/ground.glb        walkable and drivable surfaces, one node per (group|class|material)
    geometry/structures.glb    curbs, walls, stairs, planters
    geometry/buildings.glb     buildings (walls, roofs, windows, doors)
    geometry/other.glb         anything else that is not dressed (fences, rails, ...)
    prototypes.glb             one fallback model per placement kind, at the origin
    placements.glb             one empty node per placement, named p|<index>|<kind>|<key>,
                               plus axis markers site|origin, site|east10, site|up10, site|south10
    placements.json            the placement records (index matches the node name)
    classes.json               semantic class -> id, traversability level, collision, friction
    network.json               rover network, routes, landmarks (the explorer's data)
    site.json                  CRS, origin, latitude/longitude, time zone, sources
    dress_map.json             kind/key -> count, fallback prototype, chosen model (to fill in)
"""
from __future__ import annotations

import json
import math
import struct
import time
from pathlib import Path

from .meshkit import Scene
from .physics import KINETIC_RATIO, MODEL_COLLISION, SURFACES, collision_for, friction_for
from .traversal import LEVELS, level_of
from .world import World, export_glb

DRESSED_GROUPS = ("trees", "plants", "props")
STRUCTURE_CLASSES = {"curb", "stairs", "wall", "planter"}
GEOMETRY_FILES = {
    "ground.glb": lambda k: k[0] in ("ground", "terrain") and k[1] not in STRUCTURE_CLASSES,
    "structures.glb": lambda k: k[0] in ("ground", "terrain") and k[1] in STRUCTURE_CLASSES,
    "buildings.glb": lambda k: k[0].split("/")[0] == "buildings",
    "other.glb": lambda k: (k[0] not in ("ground", "terrain") and k[0].split("/")[0] != "buildings"
                            and k[0] not in DRESSED_GROUPS),
}
MARKERS = {"origin": (0.0, 0.0, 0.0), "east10": (10.0, 0.0, 0.0), "up10": (0.0, 10.0, 0.0),
           "south10": (0.0, 0.0, 10.0)}


def frame_point(origin, x: float, y: float, z: float) -> list[float]:
    """Site coordinates (x east, y north, z up) to the GLB frame (x east, y up, z south)."""
    ox, oy, oz = origin
    return [round(x - ox, 3), round(z - oz, 3), round(-(y - oy), 3)]


def yaw_quaternion(yaw: float) -> list[float]:
    """Rotation about the up axis, counter-clockwise seen from above (east towards north)."""
    half = yaw / 2.0
    return [0.0, round(math.sin(half), 6), 0.0, round(math.cos(half), 6)]


def write_empty_nodes_glb(path: Path, nodes: list[dict]) -> None:
    """A glTF binary holding only named, placed empty nodes (no meshes, no buffers)."""
    doc = {"asset": {"version": "2.0", "generator": "campus-rover"}, "scene": 0,
           "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes}
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text))
                     + struct.pack("<II", len(text), 0x4E4F534A) + text)


# ---- prototypes ---------------------------------------------------------------------------


def _regroup(src: Scene, dst: Scene, group: str) -> None:
    for (_, cls, mat), mesh in src.parts.items():
        dst.parts[(group, cls, mat)] = mesh
    dst.materials.update(src.materials)
    dst.assets.update(src.assets)
    for inst in src.instances:
        inst.group = group
        dst.instances.append(inst)


def _median(values, default: float) -> float:
    vals = sorted(v for v in values if v)
    return float(vals[len(vals) // 2]) if vals else default


def build_prototypes(scene: Scene, placements: list[dict]) -> tuple[Scene, dict]:
    """One fallback model per (kind, key), standing at the origin, and its reference size."""
    from .furniture import drum_lamp
    from .vegetation import place_ground_cover, place_tree
    from .world import add_prop

    protos = Scene(origin=(0.0, 0.0, 0.0))
    info: dict[str, dict] = {}
    groups: dict[tuple, list] = {}
    for p in placements:
        groups.setdefault((p["kind"], p["key"]), []).append(p)
    cache: dict = {}
    for (kind, key), items in sorted(groups.items()):
        pid = f"{kind}:{key}"
        group = f"proto:{pid}"
        tmp = Scene(origin=(0.0, 0.0, 0.0))
        ref: dict = {}
        if kind == "tree":
            h = _median([p.get("height_m") for p in items], 8.0)
            r = _median([p.get("crown_radius_m") for p in items], 3.0)
            d = _median([p.get("trunk_diameter_m") for p in items], 0.3)
            rep = items[0]
            place_tree(tmp, cache, 0.0, 0.0, 0.0, h, r, d, rep.get("species"),
                       str(rep.get("family") or ""), str(rep.get("habit") or ""), seed=7, lod=0)
            ref = {"height_m": round(h, 2), "crown_radius_m": round(r, 2)}
        elif kind == "plant":
            place_ground_cover(tmp, cache, key, 0.0, 0.0, 0.0, seed=1, lod=0)
        elif kind == "agent" and not items[0].get("model"):
            if not _agent_proto(tmp, key):
                continue
        else:
            model = items[0].get("model") or (key if kind == "art" else None)
            asset = scene.assets.get(model) if model else None
            if asset is None and key == "street_light":
                asset = drum_lamp()
            if asset is not None:
                tmp.place(asset, "proto", 0.0, 0.0, 0.0)
                ref = {"size_m": [round(float(v), 3) for v in asset.size]}
                if key == "street_light":
                    ref["height_m"] = round(float(asset.size[2]), 3)
            elif not add_prop(tmp, items[0].get("cls") or key, 0.0, 0.0, 0.0, seed=0):
                continue
        _regroup(tmp, protos, group)
        info[pid] = {"group": group, "kind": kind, "key": key, "count": len(items), **ref}
    return protos, info


def _agent_proto(tmp: Scene, key: str) -> bool:
    """Fallback look for a person, rider, animal or cart, drawn by the agents module."""
    import numpy as np

    from . import agents as ag

    rng = np.random.default_rng(3)
    draw = {"person": lambda: ag.add_person(tmp, "proto", 0, 0, 0, 0.0, rng),
            "cyclist": lambda: ag.add_bicycle(tmp, "proto", 0, 0, 0, 0.0, rng),
            "parked_bicycle": lambda: ag.add_bicycle(tmp, "proto", 0, 0, 0, 0.0, rng, rider=False),
            "skateboarder": lambda: ag.add_skateboarder(tmp, "proto", 0, 0, 0, 0.0, rng),
            "cart": lambda: ag.add_cart(tmp, "proto", 0, 0, 0, 0.0, rng),
            "chicken": lambda: ag.add_chicken(tmp, "proto", 0, 0, 0, 0.0, rng),
            "cat": lambda: ag.add_cat(tmp, "proto", 0, 0, 0, 0.0, rng),
            "car": lambda: ag.add_car(tmp, "proto", 0, 0, 0, 0.0, rng)}.get(key)
    if draw is None:
        return False
    draw()
    return True


# ---- classes ------------------------------------------------------------------------------


def class_table(scene: Scene, agents: Scene | None, placements: list[dict]) -> dict:
    names = set(LEVELS)
    names.update(k[1] for k in scene.parts)
    names.update(a.cls for a in scene.assets.values())
    if agents is not None:
        names.update(a.cls for a in agents.assets.values())
    names.update(p.get("cls") or {"tree": "tree", "plant": "tree", "art": "art_piece"}.get(
        p["kind"], p["key"]) for p in placements)
    out = {}
    for i, name in enumerate(sorted(names), start=1):           # 0 is the sky / nothing
        static, dynamic = friction_for(name)
        wet_static, _ = friction_for(name, wet=True)
        coll = "trunk" if name == "tree" else MODEL_COLLISION.get(name) or collision_for(name)
        out[name] = {"id": i, "level": level_of(name),
                     "collision": {"none": "mesh", None: "none"}.get(coll, coll),
                     "friction_static": round(static, 3), "friction_dynamic": round(dynamic, 3),
                     "friction_static_wet": round(wet_static, 3),
                     "friction_measured": False}
    return {"classes": out, "levels": ["blocked", "conditional", "traversable", "dynamic"],
            "notes": {"collision": "mesh = exact triangles (static), convexHull = one convex hull, "
                                   "trunk = a capsule round the trunk only (not the leaves), "
                                   "none = no collider.",
                      "friction": "placeholders until measured with the real rover (physics.py); "
                                  f"dynamic = {KINETIC_RATIO} x static",
                      "surfaces": sorted(SURFACES)}}


# ---- site ---------------------------------------------------------------------------------


def site_info(site, world: World) -> dict:
    ox, oy, oz = world.scene.origin
    lat = lon = None
    try:
        from pyproj import Transformer

        t = Transformer.from_crs(site.crs, "EPSG:4326", always_xy=True)
        lon, lat = t.transform(ox, oy)
    except Exception:  # noqa: BLE001 - pyproj missing or CRS unknown: leave it out
        pass
    return {
        "site": site.name, "crs": site.crs, "origin_site_xyz": [ox, oy, oz],
        "origin_lat_lon": [round(lat, 7), round(lon, 7)] if lat is not None else None,
        "time_zone": getattr(site, "timezone", None) or "Pacific/Honolulu",
        "frame": {"units": "metres", "x": "east", "y": "up", "z": "south",
                  "from_site": "(x - ox, z - oz, -(y - oy))",
                  "markers": {k: list(v) for k, v in MARKERS.items()},
                  "note": "Read the site|* markers after import to map this frame to the engine's"},
        "bbox_site": [float(v) for v in world.bbox],
        "route": world.report.get("route"),
        "sources": world.report.get("sources"),
        "built": time.strftime("%Y-%m-%d"),
    }


# ---- main ---------------------------------------------------------------------------------


def export_unity(site, world: World, out_dir: Path, progress=lambda m: None,
                 network: bool = True) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    geo = out_dir / "geometry"
    geo.mkdir(exist_ok=True)
    scene = world.scene
    files: dict[str, Path] = {}
    progress("geometry")
    for name, keep in GEOMETRY_FILES.items():
        if not any(keep(k) for k in scene.parts):
            continue
        export_glb(scene, geo / name, only=keep, instances=False)
        files[f"geometry/{name}"] = geo / name

    placements = [dict(p) for p in scene.placements]
    if world.agents is not None:
        placements += [dict(p) for p in world.agents.placements]
        for inst in world.agents.instances:          # agents drawn from a shared model (cars)
            asset = world.agents.assets[inst.asset]
            placements.append({"kind": "agent", "key": asset.cls, "model": asset.name,
                               "cls": asset.cls,
                               "xyz": [round(inst.x, 3), round(inst.y, 3), round(inst.z, 3)],
                               "yaw": round(inst.yaw, 4)})
    progress("fallback models")
    protos, proto_info = build_prototypes(_merged_assets(scene, world.agents), placements)
    if protos.parts or protos.instances:
        export_glb(protos, out_dir / "prototypes.glb", instances=True)
        files["prototypes.glb"] = out_dir / "prototypes.glb"

    progress("placements")
    nodes = [{"name": f"site|{k}", "translation": list(v)} for k, v in MARKERS.items()]
    for i, p in enumerate(placements):
        p["index"] = i
        p["proto"] = f"{p['kind']}:{p['key']}" if f"{p['kind']}:{p['key']}" in proto_info else None
        p["gltf_xyz"] = frame_point(scene.origin, *p["xyz"])
        nodes.append({"name": f"p|{i}|{p['kind']}|{p['key']}", "translation": p["gltf_xyz"],
                      "rotation": yaw_quaternion(p["yaw"])})
    write_empty_nodes_glb(out_dir / "placements.glb", nodes)
    files["placements.glb"] = out_dir / "placements.glb"
    _write(out_dir / "placements.json", {"prototypes": proto_info, "placements": placements})
    files["placements.json"] = out_dir / "placements.json"

    _write(out_dir / "classes.json", class_table(scene, world.agents, placements))
    files["classes.json"] = out_dir / "classes.json"
    _write(out_dir / "site.json", site_info(site, world))
    files["site.json"] = out_dir / "site.json"
    _write(out_dir / "dress_map.json", dress_map(proto_info))
    files["dress_map.json"] = out_dir / "dress_map.json"
    if network:
        progress("rover network and routes")
        from .explorer import collect

        _write(out_dir / "network.json", collect(site, world))
        files["network.json"] = out_dir / "network.json"
    return files


def _merged_assets(scene: Scene, agents: Scene | None) -> Scene:
    if agents is None:
        return scene
    merged = Scene(origin=scene.origin)
    merged.assets.update(scene.assets)
    merged.assets.update(agents.assets)
    return merged


def dress_map(proto_info: dict) -> dict:
    """Which model dresses each kind. `model` is filled in by hand or in the engine; until then
    the fallback prototype is used. Every chosen model must name its source and licence."""
    return {
        "rules": ["Any free model may be used (owner decision 2026-11-26), with its source URL "
                  "and licence written here.",
                  "Mark restricted licences (for example engine-only or no redistribution) with "
                  "restricted: true, so they can be swapped out if the training moves engines.",
                  "Never Google Maps / Street View content or Mixamo (docs/SCOPE.md 3a).",
                  "A model replaces the look only: position, heading and size stay as placed."],
        "kinds": {pid: {"count": v["count"], "fallback": v["group"], "model": None,
                        "source": None, "licence": None, "restricted": None,
                        **{k: v[k] for k in ("height_m", "crown_radius_m", "size_m") if k in v}}
                  for pid, v in sorted(proto_info.items())},
    }


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, default=_json_default))


def _json_default(o):
    try:
        import numpy as np

        if isinstance(o, np.generic):
            return o.item()
    except ImportError:  # pragma: no cover
        pass
    return str(o)
