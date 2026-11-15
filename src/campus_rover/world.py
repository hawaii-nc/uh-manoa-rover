"""Assemble the 3D training world for a site and write it out.

    static world  = terrain (road / walkway / grass) + buildings + trees + props
    agents        = people, bikes, vehicles, animals for one scenario (separate file)

Outputs: `world.glb` (browser viewer, Blender) and `world.usdc` (Isaac Sim), plus the
same pair for the agents. Every mesh carries a semantic class.

With lidar the terrain and building shapes are measured. Without it (the demo
campus) the world is flat, buildings are boxes, and walkways are painted from
the walkway graph, so the same code runs offline in tests.
"""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .buildgen import generate_building
from .lidar import Rasters, fill_holes
from .massing import Massing, box_massing, building_massing
from .meshkit import Scene
from .overture import ROAD_WIDTH_M
from .style import Style, auto_style, load_styles, slug

GRASS, WALKWAY, ROAD = 0, 1, 2
SURFACE_NAMES = {GRASS: "grass", WALKWAY: "walkway", ROAD: "road"}
SURFACE_COLORS = {"grass": (0.33, 0.47, 0.22), "walkway": (0.70, 0.68, 0.63),
                  "road": (0.20, 0.20, 0.21)}
INDOOR_PROPS = {"vending_machine", "water_refill"}


@dataclass
class WorldConfig:
    """The `world:` block of site.yaml."""

    landmarks: list[str] = field(default_factory=list)   # corridor = box around these
    pad_m: float = 150.0
    rasters: str | None = None          # lidar grids (.npz), relative to the site folder
    segments: str | None = None         # road/path segments (.json), relative to the site
    background_buildings: str | None = None   # extra footprints (.json) for off-campus buildings
    lidar_tiles: list[str] = field(default_factory=list)   # LAZ tile URLs covering the corridor
    lidar_res: float = 0.5
    terrain_res: float = 1.0
    asphalt_max: float = 5500.0         # ground brightness below this = asphalt
    paved_max: float = 13500.0          # below this = paved, above = grass
    styles: str = "buildings"
    tier_a_m: float = 45.0              # buildings this close to the route are tier A
    tier_b_m: float = 120.0

    @classmethod
    def from_site(cls, site) -> WorldConfig:
        data = dict(site.raw.get("world") or {})
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"site.yaml world: unknown fields {sorted(unknown)}")
        return cls(**data)


# ---- geometry inputs ---------------------------------------------------------------


def building_polygon(building):
    from shapely.geometry import Polygon

    poly = Polygon(building.rings[0], building.rings[1:])
    return poly if poly.is_valid else poly.buffer(0)


def corridor_bbox(site, model, cfg: WorldConfig) -> tuple[float, float, float, float]:
    """Box around the named landmark buildings (or the whole model), padded."""
    polys = []
    for key in cfg.landmarks:
        b = model.find_building(site.landmarks[key].get("building", key))
        if b is not None:
            polys.append(building_polygon(b))
    if not polys:
        x0, y0, x1, y1 = model.bounds()
    else:
        bounds = np.array([p.bounds for p in polys])
        x0, y0 = bounds[:, 0].min(), bounds[:, 1].min()
        x1, y1 = bounds[:, 2].max(), bounds[:, 3].max()
    return (x0 - cfg.pad_m, y0 - cfg.pad_m, x1 + cfg.pad_m, y1 + cfg.pad_m)


def flat_rasters(bbox, res: float = 0.5) -> Rasters:
    """Stand-in grids for a site without lidar: flat ground, no roofs."""
    cols = int(math.ceil((bbox[2] - bbox[0]) / res))
    rows = int(math.ceil((bbox[3] - bbox[1]) / res))
    nan = np.full((rows, cols), np.nan)
    return Rasters(bbox[0], bbox[1], res, np.zeros((rows, cols)), nan.copy(), nan.copy(),
                   np.full((rows, cols), np.inf), np.zeros((rows, cols), dtype=np.uint16))


def paint_lines(rasters: Rasters, lines: list[tuple[list, float]]) -> np.ndarray:
    """Boolean grid: True within width/2 of any line. lines = [(coords, width_m)]."""
    from PIL import Image, ImageDraw

    rows, cols = rasters.shape
    img = Image.new("L", (cols, rows), 0)
    draw = ImageDraw.Draw(img)
    for coords, width in lines:
        px = [((x - rasters.x0) / rasters.res, (y - rasters.y0) / rasters.res) for x, y in coords]
        if len(px) >= 2:
            draw.line(px, fill=255, width=max(1, round(width / rasters.res)), joint="curve")
    return np.asarray(img) > 0


def classify_surface(rasters: Rasters, cfg: WorldConfig, road_lines, walk_lines) -> np.ndarray:
    """Per-cell ground class: grass, walkway or road.

    With lidar, brightness decides: asphalt is dark, concrete is medium and
    grass is bright in the laser's near-infrared. Mapped roads tell road from
    walkway. Without lidar, mapped lines are simply painted on grass.
    """
    from scipy import ndimage

    roads = paint_lines(rasters, road_lines)
    if not np.isfinite(rasters.intensity).any():       # no lidar
        out = np.full(rasters.shape, GRASS, dtype=np.uint8)
        out[paint_lines(rasters, walk_lines)] = WALKWAY
        out[roads] = ROAD
        return out
    bright = ndimage.median_filter(rasters.intensity, size=3)
    paved = bright < cfg.paved_max
    out = np.full(rasters.shape, GRASS, dtype=np.uint8)
    out[paved] = WALKWAY
    out[(paved & roads) | (bright < cfg.asphalt_max)] = ROAD
    return ndimage.median_filter(out, size=5)


# ---- scene pieces ------------------------------------------------------------------


def add_terrain(scene: Scene, rasters: Rasters, surface: np.ndarray, res: float) -> None:
    step = max(1, round(res / rasters.res))
    dtm = rasters.dtm
    rows, cols = dtm.shape
    rs = np.arange(0, rows, step)
    cs = np.arange(0, cols, step)
    # Vertex heights: the cell under each grid node.
    z = dtm[np.ix_(rs, cs)]
    gx, gy = np.meshgrid(rasters.x0 + cs * rasters.res, rasters.y0 + rs * rasters.res)
    verts = np.c_[gx.ravel(), gy.ravel(), z.ravel()]
    n_r, n_c = len(rs), len(cs)
    cell_class = surface[np.ix_(rs[:-1] + step // 2, cs[:-1] + step // 2)]
    r, c = np.meshgrid(np.arange(n_r - 1), np.arange(n_c - 1), indexing="ij")
    a = (r * n_c + c).ravel()
    b, d, e = a + 1, a + n_c, a + n_c + 1
    flat_class = cell_class.ravel()
    for code, name in SURFACE_NAMES.items():
        pick = flat_class == code
        if not pick.any():
            continue
        faces = np.r_[np.c_[a[pick], b[pick], e[pick]], np.c_[a[pick], e[pick], d[pick]]]
        used, remap = np.unique(faces, return_inverse=True)
        mat = scene.material(name, SURFACE_COLORS[name], roughness=1.0)
        scene.mesh("terrain", name, mat).add(verts[used], remap.reshape(-1, 3))


def add_tree(scene: Scene, x, y, z, height, radius, trunk_d, family: str, habit: str,
             seed: int) -> None:
    bark = scene.material("bark", (0.33, 0.26, 0.19))
    greens = [(0.16, 0.36, 0.14), (0.22, 0.42, 0.16), (0.13, 0.30, 0.15), (0.28, 0.44, 0.18)]
    leaves = scene.material(f"leaves_{seed % 4}", greens[seed % 4])
    trunk = scene.mesh("trees", "tree", bark)
    crown = scene.mesh("trees", "tree", leaves)
    if "arecaceae" in family.lower() or "palm" in habit.lower():
        trunk.cylinder((x, y, z), 0.16, height * 0.9, segments=7, top_radius=0.11)
        top = z + height * 0.9
        reach = min(max(radius, 1.8), 3.2)
        for i in range(8):                       # drooping fronds, visible from both sides
            a = 2 * math.pi * i / 8 + seed
            ca, sa = math.cos(a), math.sin(a)
            side = (-sa * 0.35, ca * 0.35)
            p0 = (x + side[0], y + side[1], top)
            p1 = (x - side[0], y - side[1], top)
            mid = (x + ca * reach * 0.6, y + sa * reach * 0.6, top + 0.5)
            tip = (x + ca * reach, y + sa * reach, top - 0.9)
            crown.add([p0, p1, mid, tip], [[0, 1, 2], [1, 0, 2], [0, 2, 3], [2, 0, 3],
                                           [1, 3, 2], [3, 1, 2]])
        crown.blob((x, y, top), (0.5, 0.5, 0.45), seed=seed, wobble=0.05)
        return
    if height < 2.6 or "shrub" in habit.lower():
        r = max(min(radius, 1.6), 0.5)
        crown.blob((x, y, z + max(height, 1.0) * 0.5), (r, r, max(height, 1.0) * 0.55), seed=seed)
        return
    trunk_h = height * 0.45
    trunk.cylinder((x, y, z), min(max(trunk_d / 2, 0.09), 0.9), trunk_h + 0.4, segments=8,
                   top_radius=min(max(trunk_d / 2, 0.09), 0.9) * 0.7)
    crown.blob((x, y, z + trunk_h + (height - trunk_h) * 0.5),
               (radius, radius, (height - trunk_h) * 0.55), seed=seed,
               detail=2 if radius > 5 else 1, wobble=0.14)


PROP_SHAPES = {
    # class: (colour, [(dx, dy, z, sx, sy, sz), ...]) boxes in the prop's own frame
    "bench": ((0.45, 0.32, 0.2), [(0, 0, 0.4, 1.6, 0.45, 0.07), (0, -0.2, 0.47, 1.6, 0.06, 0.45),
                                  (0.7, 0, 0, 0.08, 0.4, 0.4), (-0.7, 0, 0, 0.08, 0.4, 0.4)]),
    "emergency_call_box": ((0.1, 0.25, 0.7), [(0, 0, 0, 0.3, 0.3, 2.7),
                                              (0, 0, 2.7, 0.2, 0.2, 0.25)]),
    "bike_rack": ((0.55, 0.57, 0.6), [
        (0, 0, 0.75, 2.2, 0.05, 0.05), (-1.0, 0, 0, 0.05, 0.05, 0.75),
        (-0.33, 0, 0, 0.05, 0.05, 0.75), (0.33, 0, 0, 0.05, 0.05, 0.75),
        (1.0, 0, 0, 0.05, 0.05, 0.75)]),
    "skateboard_rack": ((0.4, 0.42, 0.45), [(0, 0, 0, 1.2, 0.35, 1.0)]),
    "bike_share_dock": ((0.1, 0.55, 0.6), [
        (0, 0, 0, 5.0, 0.5, 0.25), (-2.2, 0, 0, 0.45, 0.35, 1.9)]),
    "ev_charger": ((0.9, 0.9, 0.9), [(0, 0, 0, 0.4, 0.3, 1.5)]),
    "mailbox": ((0.1, 0.2, 0.55), [(0, 0, 0, 0.55, 0.55, 1.25)]),
    "bulletin_board": ((0.35, 0.25, 0.15), [
        (0, 0, 0.7, 1.6, 0.12, 1.3), (-0.7, 0, 0, 0.1, 0.1, 0.7), (0.7, 0, 0, 0.1, 0.1, 0.7)]),
    "art_piece": ((0.5, 0.42, 0.3), [(0, 0, 0, 1.0, 1.0, 0.4), (0, 0, 0.4, 0.5, 0.5, 1.8)]),
}
PROP_CYLINDERS = {"trash_can": ((0.3, 0.3, 0.32), 0.28, 0.95),
                  "recycling_bin": ((0.1, 0.35, 0.65), 0.28, 0.95)}


def add_prop(scene: Scene, cls: str, x, y, z, seed: int) -> bool:
    yaw = (seed % 8) * math.pi / 4
    if cls in PROP_CYLINDERS:
        color, radius, height = PROP_CYLINDERS[cls]
        scene.mesh("props", cls, scene.material(f"prop_{cls}", color)).cylinder(
            (x, y, z), radius, height, segments=10)
        return True
    if cls not in PROP_SHAPES:
        return False
    color, boxes = PROP_SHAPES[cls]
    mesh = scene.mesh("props", cls, scene.material(f"prop_{cls}", color))
    c, s = math.cos(yaw), math.sin(yaw)
    for dx, dy, dz, sx, sy, sz in boxes:
        mesh.box((x + dx * c - dy * s, y + dx * s + dy * c, z + dz), (sx, sy, sz), yaw)
    return True


def _seed(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode())


# ---- build -------------------------------------------------------------------------


@dataclass
class World:
    scene: Scene
    agents: Scene | None
    rasters: Rasters
    surface: np.ndarray
    bbox: tuple[float, float, float, float]
    report: dict


def build_world(site, scenario=None, route_key: str | None = None,
                progress=lambda msg: None) -> World:
    from shapely.geometry import LineString

    cfg = WorldConfig.from_site(site)
    model = site.load_model()
    if model is None:
        raise ValueError(f"site {site.name!r} has no layers.yaml, so there is nothing to build")
    try:
        graph = site.load_graph()
    except FileNotFoundError:
        graph = None
    bbox = corridor_bbox(site, model, cfg)

    raster_path = site.root / cfg.rasters if cfg.rasters else None
    has_lidar = bool(raster_path and raster_path.exists())
    rasters = Rasters.load(raster_path) if has_lidar else flat_rasters(bbox)
    bbox = rasters.bounds
    x0, y0, x1, y1 = bbox

    def inside(x, y, pad=0.0) -> bool:
        return x0 + pad <= x <= x1 - pad and y0 + pad <= y <= y1 - pad

    segments = []
    if cfg.segments and (site.root / cfg.segments).exists():
        segments = json.loads((site.root / cfg.segments).read_text())
    road_lines = [(s["coords"], ROAD_WIDTH_M[s["class"]]) for s in segments
                  if s.get("class") in ROAD_WIDTH_M]
    walk_lines = []
    if graph is not None:
        walk_lines = [([graph.xy(u), graph.xy(v)], 3.0) for u, v, d in graph.g.edges(data=True)
                      if d.get("zone") != "roadside"]
        if not segments:
            road_lines = [([graph.xy(u), graph.xy(v)], 6.0)
                          for u, v, d in graph.g.edges(data=True) if d.get("zone") == "roadside"]

    progress("ground surfaces")
    surface = classify_surface(rasters, cfg, road_lines, walk_lines)
    scene = Scene(origin=((x0 + x1) / 2, (y0 + y1) / 2, float(np.nanmin(rasters.dtm))))
    scene.meta = {"site": site.name, "crs": site.crs, "bbox": [float(v) for v in bbox],
                  "lidar": has_lidar}
    add_terrain(scene, rasters, surface, cfg.terrain_res)

    # The planned route decides which buildings matter most.
    route_line, route_xy = None, []
    route_key = route_key or (next(iter(reversed(site.routes))) if site.routes else None)
    if graph is not None and route_key in site.routes:
        try:
            from .planner import CROWD_AWARE, RoutePlanner
            from .schedule import WeekTime

            planner = RoutePlanner.from_site(site)
            nodes = site.landmark_nodes(planner.graph)
            when = WeekTime.parse(scenario.time if scenario else "Mon 09:20")
            plan = planner.plan([nodes[w] for w in site.routes[route_key].waypoints], when,
                                CROWD_AWARE)
            route_xy = [planner.graph.xy(n) for n in plan.nodes]
            if len(route_xy) >= 2:
                route_line = LineString(route_xy)
        except Exception as e:   # a missing route must not block building the world
            progress(f"no route ({e}); every building is treated as background")

    progress("buildings")
    styles = load_styles(site.root / cfg.styles)
    buildings_report, used_ids = [], set()
    for b in model.buildings:
        poly = building_polygon(b)
        c = poly.centroid
        if poly.area < 6 or not inside(c.x, c.y, 3.0):
            continue
        bid = slug(b.name or f"building_{len(buildings_report)}")
        while bid in used_ids:
            bid += "_b"
        used_ids.add(bid)
        if has_lidar:
            massing = building_massing(poly, rasters, default_height=b.height_m)
        else:
            massing = box_massing(poly, float(rasters.ground_z(c.x, c.y)), b.height_m)
        year = _int(b.props.get("YEAR_BUILT") or b.props.get("year_built"))
        dist = route_line.distance(poly) if route_line is not None else None
        tier = "C" if dist is None else "A" if dist <= cfg.tier_a_m else \
            "B" if dist <= cfg.tier_b_m else "C"
        style = styles.get(bid)
        researched = style is not None
        if style is None:
            style = auto_style(bid, b.name or "", year, poly.area, massing.height, massing.pitched)
        info = generate_building(scene, f"buildings/{bid}", massing, style)
        buildings_report.append({
            "id": bid, "name": b.name, "tier": tier, "researched": researched,
            "route_distance_m": None if dist is None else round(dist, 1),
            "height_m": round(massing.height, 1), "base_z": round(massing.base_z, 1),
            "year": year, "footprint_m2": round(poly.area), **info,
        })

    # Buildings the campus map doesn't cover (neighbours, off-campus): plain background.
    extra_path = site.root / cfg.background_buildings if cfg.background_buildings else None
    n_background = 0
    if extra_path and extra_path.exists():
        from shapely import STRtree
        from shapely.geometry import Polygon

        known = [building_polygon(b) for b in model.buildings]
        tree = STRtree(known)
        for i, item in enumerate(json.loads(extra_path.read_text())):
            poly = Polygon(item["rings"][0], item["rings"][1:])
            poly = poly if poly.is_valid else poly.buffer(0)
            c = poly.centroid
            if poly.geom_type != "Polygon" or poly.area < 12 or not inside(c.x, c.y, 3.0):
                continue
            if any(known[j].intersection(poly).area > 0.2 * poly.area
                   for j in tree.query(poly)):
                continue
            massing = building_massing(poly, rasters, default_height=item.get("height") or 6.0) \
                if has_lidar else box_massing(poly, 0.0, item.get("height") or 6.0)
            style = auto_style(f"bg_{i}", item.get("name") or "", None, poly.area,
                               massing.height, massing.pitched)
            style.wall = ["#d9d2c0", "#c8c2b4", "#e2dccb", "#bfb8a8"][i % 4]
            style.roof = ["#77736c", "#8a6f5c", "#6d7478"][i % 3]
            generate_building(scene, "buildings/background", massing, style)
            n_background += 1

    progress("trees and props")
    n_trees = 0
    for t in model.trees:
        x, y = t.xy
        if not inside(x, y, 1.0):
            continue
        p = t.props
        z = float(rasters.ground_z(x, y))
        height = p.get("height_m")
        if not height and has_lidar:
            r, c = rasters.cell(x, y)
            top = rasters.dsm_veg[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3]
            height = float(np.nanmax(top) - z) if np.isfinite(top).any() else None
        height = float(min(max(height or 6.0, 1.0), 40.0))
        radius = float(min(max(p.get("canopy_radius_m") or height * 0.3, 0.6), 14.0))
        add_tree(scene, x, y, z, height, radius, float(p.get("trunk_diameter_m") or 0.3),
                 str(p.get("family") or ""), str(p.get("habit") or ""),
                 _seed(round(x, 1), round(y, 1)))
        n_trees += 1

    from .campus_model import inside_any_building

    n_props: dict[str, int] = {}
    for prop in model.props:
        x, y = prop.xy
        if prop.cls in INDOOR_PROPS or not inside(x, y, 1.0) or inside_any_building(model, (x, y)):
            continue
        if add_prop(scene, prop.cls, x, y, float(rasters.ground_z(x, y)), _seed(x, y)):
            n_props[prop.cls] = n_props.get(prop.cls, 0) + 1

    agents_scene, agent_counts = None, {}
    if scenario is not None and graph is not None:
        progress("people, bikes and vehicles")
        from .agents import populate

        agents_scene = Scene(origin=scene.origin, meta={**scene.meta, "scenario": scenario.name,
                                                        "time": scenario.time})

        def surface_at(x, y):
            r, c = rasters.cell(x, y)
            return SURFACE_NAMES[int(surface[r, c])]

        agent_counts = populate(agents_scene, site, graph, model, rasters.ground_z, bbox, scenario,
                                road_segments=segments, surface=surface_at)

    share = {name: round(float((surface == code).mean()), 3)
             for code, name in SURFACE_NAMES.items()}
    report = {
        **scene.meta, "origin": [float(v) for v in scene.origin],
        "size_m": [round(x1 - x0), round(y1 - y0)],
        "elevation_m": [round(float(np.nanmin(rasters.dtm)), 1),
                        round(float(np.nanmax(rasters.dtm)), 1)],
        "surface_share": share, "background_buildings": n_background, "trees": n_trees,
        "props": n_props, "agents": agent_counts,
        "route": {"key": route_key, "length_m": round(route_line.length) if route_line else None,
                  "xy": [[round(x, 2), round(y, 2)] for x, y in route_xy]},
        "buildings": sorted(buildings_report,
                            key=lambda r: (r["tier"], r["route_distance_m"] or 0)),
        "mesh": scene.stats(),
    }
    return World(scene, agents_scene, rasters, surface, bbox, report)


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---- exporters ---------------------------------------------------------------------


def export_glb(scene: Scene, path: Path) -> None:
    """Binary glTF. glTF is Y-up, so (x, y, z) is written as (x, z, -y).
    Node names are "group|class|material", which the viewer reads back."""
    import trimesh

    out = trimesh.Scene()
    ox, oy, oz = scene.origin
    for (group, cls, material), mesh in sorted(scene.parts.items()):
        verts, faces = mesh.arrays()
        if len(faces) == 0:
            continue
        v = np.c_[verts[:, 0] - ox, verts[:, 2] - oz, -(verts[:, 1] - oy)].astype(np.float32)
        m = scene.materials[material]
        tm = trimesh.Trimesh(vertices=v, faces=faces, process=False)
        tm.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            name=material, baseColorFactor=[*m["color"], m["opacity"]],
            roughnessFactor=m["roughness"], metallicFactor=m["metallic"]))
        name = f"{group}|{cls}|{material}"
        out.add_geometry(tm, node_name=name, geom_name=name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.export(file_type="glb"))


def export_usd(scene: Scene, path: Path, collision: bool = True) -> None:
    """OpenUSD for Isaac Sim: Z up, meters, a semantic label and a preview material on
    every mesh, collision on the static world."""
    from .usd_export import _require_usd

    Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, Vt = _require_usd()
    from pxr import UsdShade

    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    ox, oy, oz = scene.origin
    stage.GetRootLayer().customLayerData = {
        "campus:site": str(scene.meta.get("site", "")),
        "campus:crs": str(scene.meta.get("crs", "")),
        "campus:originOffset": Gf.Vec3d(ox, oy, oz),
        "campus:note": "world xyz = CRS xyz - originOffset; Z up; meters",
    }
    looks = {}
    for name, m in scene.materials.items():
        mat = UsdShade.Material.Define(stage, f"/World/Looks/{_usd_name(name)}")
        shader = UsdShade.Shader.Define(stage, f"/World/Looks/{_usd_name(name)}/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*m["color"]))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(m["roughness"])
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(m["metallic"])
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        looks[name] = mat
    for (group, cls, material), mesh in sorted(scene.parts.items()):
        verts, faces = mesh.arrays()
        if len(faces) == 0:
            continue
        prim_path = "/World/" + "/".join(_usd_name(p) for p in group.split("/"))
        for depth in range(2, prim_path.count("/") + 1):
            UsdGeom.Xform.Define(stage, "/".join(prim_path.split("/")[:depth + 1]))
        m = UsdGeom.Mesh.Define(stage, f"{prim_path}/{_usd_name(cls)}__{_usd_name(material)}")
        local = (verts - np.array([ox, oy, oz])).astype(np.float32)
        m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(local))
        m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(faces), 3, dtype=np.int32)))
        m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces.astype(np.int32).ravel()))
        m.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        m.CreateDoubleSidedAttr().Set(cls in ("tree",))
        m.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(*scene.materials[material]["color"])]))
        m.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*local.min(axis=0).tolist()),
                                          Gf.Vec3f(*local.max(axis=0).tolist())]))
        UsdSemantics.LabelsAPI.Apply(m.GetPrim(), "class").CreateLabelsAttr().Set([cls])
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(looks[material])
        if collision:
            UsdPhysics.CollisionAPI.Apply(m.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr().Set("none")
    stage.GetRootLayer().Save()


def _usd_name(text: str) -> str:
    import re

    name = re.sub(r"[^A-Za-z0-9_]", "_", text)
    return name if name and not name[0].isdigit() else f"_{name}"


def write_world(world: World, out_dir: Path, usd: bool = True) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"world.glb": out_dir / "world.glb"}
    export_glb(world.scene, files["world.glb"])
    if usd:
        files["world.usd"] = out_dir / "world.usdc"
        export_usd(world.scene, files["world.usd"])
    if world.agents is not None:
        name = world.agents.meta.get("scenario", "scenario")
        files["agents.glb"] = out_dir / f"agents_{name}.glb"
        export_glb(world.agents, files["agents.glb"])
        if usd:
            files["agents.usd"] = out_dir / f"agents_{name}.usdc"
            export_usd(world.agents, files["agents.usd"], collision=False)
    if "agents.glb" in files:
        world.report["agents_file"] = files["agents.glb"].name
    files["world.json"] = out_dir / "world.json"
    files["world.json"].write_text(json.dumps(world.report, indent=1))
    # The browser viewer sits next to the files it shows.
    import shutil

    viewer = Path(__file__).parent / "viewer"
    shutil.copy(viewer / "index.html", out_dir / "index.html")
    shutil.copytree(viewer / "vendor", out_dir / "vendor", dirs_exist_ok=True)
    files["viewer"] = out_dir / "index.html"
    return files


__all__ = ["World", "WorldConfig", "build_world", "write_world", "export_glb", "export_usd",
           "classify_surface", "corridor_bbox", "flat_rasters", "fill_holes", "Massing", "Style"]
