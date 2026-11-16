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
from .physics import MODEL_COLLISION, collision_for, collision_stats
from .style import Style, background_style, load_styles, slug, unverified_style

GRASS, WALKWAY, ROAD = 0, 1, 2
SURFACE_NAMES = {GRASS: "grass", WALKWAY: "walkway", ROAD: "road"}
SURFACE_COLORS = {"grass": (0.33, 0.47, 0.22), "walkway": (0.70, 0.68, 0.63),
                  "road": (0.20, 0.20, 0.21)}
SURFACE_TEXTURES = {"grass": "grass", "walkway": "concrete", "road": "asphalt"}
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
    vertical_shift_m: float = 0.0       # added to lidar heights to match official contours
    open_data: str | None = None        # folder of official open data (see opendata.py)
    landcover: dict | None = None       # surveyed ground polygons: file, field, classes
    assets: str | None = "cache/assets"  # prepared free models (campus-rover fetch-assets)
    light_poles: dict | None = None     # surveyed pole points: file, status_field, keep
    floor_plans: str | None = None      # URL of per-building floor-plan PDFs, with {number}
    plans: str | None = "cache/plans"   # registered floor-plan layouts (campus-rover build-plans)
    ground_lines: dict | None = None    # painted lines: file, colour_field, categories
    fences: dict | None = None          # fence and gate lines: file, type_field
    accessible_stalls: str | None = None   # surveyed accessible parking points (geojson)
    crossings: str | None = None        # crossings found by build-walknet (json)
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


def load_landcover(path: Path, cfg: dict) -> list[tuple[object, str, str | None]]:
    """Land-cover features -> [(polygon, semantic class, paint colour or None)]."""
    from shapely.geometry import shape as to_shape

    classes = cfg.get("classes") or {}
    out = []
    for f in json.loads(path.read_text()):
        props = f.get("properties") or {}
        cls = classes.get(str(props.get(cfg.get("field", "class"))))
        if cls is None or not f.get("geometry"):
            continue
        geom = to_shape(f["geometry"])
        geom = geom if geom.is_valid else geom.buffer(0)
        paint = str(props.get(cfg.get("paint_field", "")) or "").split(" ")[0].lower() or None
        if not geom.is_empty:
            out.append((geom, cls, paint))
    return out


def load_fences(path: Path, cfg: dict) -> list[tuple[list, str]]:
    """Fence and gate lines -> [(coords, type)]."""
    out = []
    for f in json.loads(path.read_text()):
        kind = str((f.get("properties") or {}).get(cfg.get("type_field", "ESRI_TYPE")))
        geom = f.get("geometry") or {}
        parts = geom.get("coordinates") or []
        if geom.get("type") == "LineString":
            parts = [parts]
        out += [([(float(p[0]), float(p[1])) for p in part], kind) for part in parts]
    return out


def fence_polygons(site, cfg: WorldConfig, half_width_m: float = 0.15):
    """Fences (not gates) as thin polygons, for the network and the traversability labels:
    [(polygon, "fence", None)]. Gates stay open: they are the places a fence can be passed."""
    from shapely.geometry import LineString

    fence_cfg = cfg.fences or {}
    path = site.root / fence_cfg.get("file", "") if fence_cfg else None
    if not path or not path.exists():
        return []
    return [(LineString(c).buffer(half_width_m, cap_style="flat"), "fence", None)
            for c, kind in load_fences(path, fence_cfg) if kind != "Gate" and len(c) >= 2]


def load_ground_lines(path: Path, cfg: dict) -> list[tuple[list, str]]:
    keep = set(cfg.get("categories") or [])
    out = []
    for f in json.loads(path.read_text()):
        props, geom = f.get("properties") or {}, f.get("geometry") or {}
        if keep and props.get(cfg.get("category_field", "")) not in keep:
            continue
        colour = str(props.get(cfg.get("colour_field", "")) or "white").lower()
        parts = geom.get("coordinates") or []
        if geom.get("type") == "LineString":
            parts = [parts]
        for part in parts:
            out.append(([(float(p[0]), float(p[1])) for p in part],
                        colour if colour != "none" else "white"))
    return out


def add_terrain(scene: Scene, rasters: Rasters, surface: np.ndarray, res: float,
                skip: np.ndarray | None = None) -> None:
    """Terrain mesh from the grids. Cells where `skip` is True are left out (they
    are covered by surveyed ground polygons)."""
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
    cell_class = surface[np.ix_(rs[:-1] + step // 2, cs[:-1] + step // 2)].astype(int)
    if skip is not None:
        cell_class[skip[np.ix_(rs[:-1] + step // 2, cs[:-1] + step // 2)]] = -1
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
        mat = scene.material(name, SURFACE_COLORS[name], roughness=1.0,
                             texture=SURFACE_TEXTURES[name])
        scene.mesh("terrain", name, mat).add(verts[used], remap.reshape(-1, 3))


def add_tree(scene: Scene, x, y, z, height, radius, trunk_d, family: str, habit: str,
             seed: int, max_detail: int = 2) -> None:
    bark = scene.material("bark", (0.45, 0.36, 0.27), texture="bark")
    greens = [(0.20, 0.44, 0.17), (0.27, 0.51, 0.20), (0.16, 0.37, 0.18), (0.34, 0.54, 0.22)]
    leaves = scene.material(f"leaves_{seed % 4}", greens[seed % 4], texture="grass")
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
               detail=min(2 if radius > 5 else 1, max_detail), wobble=0.14)


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
                progress=lambda msg: None, lite: bool = False) -> World:
    """Build the world. `lite` makes a lighter copy for sharing: simpler tree crowns and no
    painted ground lines (the same ground, buildings and data)."""
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
    if has_lidar and cfg.vertical_shift_m:
        rasters.shift_z(cfg.vertical_shift_m)
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
                      if d.get("zone") != "roadside" and d.get("tier") != "candidate"]
        if not segments:
            road_lines = [([graph.xy(u), graph.xy(v)], 6.0)
                          for u, v, d in graph.g.edges(data=True) if d.get("zone") == "roadside"]

    progress("ground surfaces")
    surface = classify_surface(rasters, cfg, road_lines, walk_lines)
    scene = Scene(origin=((x0 + x1) / 2, (y0 + y1) / 2, float(np.nanmin(rasters.dtm))))
    scene.meta = {"site": site.name, "crs": site.crs, "bbox": [float(v) for v in bbox],
                  "lidar": has_lidar}
    ground_report = {}
    covered = None
    if cfg.landcover and (site.root / cfg.landcover["file"]).exists():
        progress("ground detail: surfaces, curbs, stairs, walls")
        from .ground import add_landcover, add_painted_lines, coarse_grid

        features = load_landcover(site.root / cfg.landcover["file"], cfg.landcover)
        grid, covered = coarse_grid(features, rasters)
        surface = np.where(covered, grid, surface).astype(np.uint8)
        ground_report = add_landcover(scene, features, rasters, bbox)
        lines_cfg = cfg.ground_lines or {}
        stall_lines = []
        if lines_cfg and (site.root / lines_cfg["file"]).exists():
            stall_lines = load_ground_lines(site.root / lines_cfg["file"], lines_cfg)
            if not lite:
                ground_report["painted_line_segments"] = add_painted_lines(
                    scene, stall_lines, rasters, bbox)
        from .ground import add_accessible_stalls, add_crosswalk_paint, add_fences

        stalls = site.root / cfg.accessible_stalls if cfg.accessible_stalls else None
        if stalls and stalls.exists():
            pts = [tuple(f["geometry"]["coordinates"][:2])
                   for f in json.loads(stalls.read_text()).get("features", [])
                   if (f.get("geometry") or {}).get("type") == "Point"]
            ground_report["accessible_stalls"] = add_accessible_stalls(
                scene, pts, rasters, bbox, stall_lines)
        marked = site.root / cfg.crossings if cfg.crossings else None
        if marked and marked.exists():
            found = json.loads(marked.read_text())
            ground_report["crosswalk_paint"] = add_crosswalk_paint(
                scene, [(c["a"], c["b"]) for c in found
                        if c["tier"] in ("marked", "confirmed")], rasters, bbox)
        fence_cfg = cfg.fences or {}
        if fence_cfg and (site.root / fence_cfg["file"]).exists():
            ground_report["fence_segments"] = add_fences(
                scene, load_fences(site.root / fence_cfg["file"], fence_cfg), rasters, bbox)
    # Lidar-classified terrain fills whatever the land-cover layer does not cover.
    add_terrain(scene, rasters, surface, cfg.terrain_res, skip=covered)

    # The planned route decides which buildings matter most.
    route_line, route_xy, route_blocked = None, [], False
    route_key = route_key or (next(iter(reversed(site.routes))) if site.routes else None)
    if graph is not None and route_key in site.routes:
        try:
            from .planner import NoRouteError, RoutePlanner, landmark_goals

            planner = RoutePlanner.from_site(site)
            goals = landmark_goals(site, planner)
            names = site.routes[route_key].waypoints
            # A building is reached anywhere along its walls. The least-blocked path says where
            # a rover cannot go; with no blocked stretch it is a route a rover can drive.
            route_xy, route_blocked = [], False
            for a, b in zip(names, names[1:], strict=False):
                try:
                    info = planner.blockers(goals[a], goals[b])
                except NoRouteError:
                    route_blocked = True
                    continue
                route_blocked |= bool(info["stretches"])
                part = [planner.graph.xy(n) for n in info["path"]]
                route_xy += part if not route_xy else part[1:]
            if route_blocked:
                progress("the route needs ground a rover cannot use; "
                         "showing the least-blocked path")
            if len(route_xy) >= 2:
                route_line = LineString(route_xy)
        except Exception as e:   # a missing route must not block building the world
            progress(f"no route ({e}); every building is treated as background")

    progress("buildings")
    styles = load_styles(site.root / cfg.styles)
    roof_scale = None
    if has_lidar and rasters.roof_intensity is not None:
        roof_scale = float(np.nanpercentile(rasters.roof_intensity, 97))
    doors = [e.xy for e in model.entrances if e.cls == "automatic_door"]
    buildings_report, used_ids, open_areas = [], set(), []

    def near_route(poly) -> float | None:
        return None if route_line is None else float(route_line.distance(poly))

    def add_building(poly, bid, name, default_height, props, campus: bool) -> None:
        c = poly.centroid
        if has_lidar:
            massing = building_massing(poly, rasters, default_height=default_height)
        else:
            massing = box_massing(poly, float(rasters.ground_z(c.x, c.y)), default_height)
        dist = near_route(poly)
        tier = "C" if dist is None else "A" if dist <= cfg.tier_a_m else \
            "B" if dist <= cfg.tier_b_m else "C"
        style = styles.get(bid)
        if style is not None and not style.solid:
            open_areas.append(bid)      # courts, amphitheatres: the terrain already has them
            return
        if style is not None:
            source = "sheet"
        elif tier == "C" or route_line is None:
            # Far from the route: generic background is allowed.
            style, source = background_style(bid, name, poly.area, massing.height), "procedural"
        else:
            # In the rover's corridor nothing is guessed: plain measured shape.
            style, source = unverified_style(bid, name), "unverified"
        group = f"buildings/{bid}" if campus else "buildings/background"
        near = [d for d in doors if poly.distance(_point(d)) <= 8.0] if source == "sheet" else []
        layout = (_layout_for(site, cfg, props, style.corridors)
                  if campus and source == "sheet" and style.corridors else None)
        if layout is not None and has_lidar:
            from .planbuild import generate_from_plan

            main = max(massing.blocks, key=lambda k: k.polygon.area).height
            info = generate_from_plan(
                scene, group, layout, style, rasters.ground_z,
                massing.base_z + main, massing.base_z + massing.height,
                flat_roof=not massing.pitched)
            if massing.pitched and massing.roof is not None:
                from .buildgen import _pitched_roof

                roof_tint = style.roof or roof_grey(poly, rasters, roof_scale) or "#8c8c88"
                _pitched_roof(scene.mesh(group, "roof", scene.color_material(
                    "roof", roof_tint, roughness=0.95, texture="roof_tiles")), massing)
            info.update(floors=info["levels"], blocks=len(layout.levels), windows=info["windows"],
                        doors=0, pitched=False, floor_height=info["floor_height"],
                        plan_layout=layout.quality)
        else:
            info = generate_building(scene, group, massing, style, door_points=near,
                                     roof_color=roof_grey(poly, rasters, roof_scale))
        buildings_report.append({
            "id": bid, "name": name, "tier": tier, "style_source": source, "campus": campus,
            "floors_source": "plan" if any("/planning/" in u for u in style.sources) else
            "photo" if style.floors else "height",
            "abbrev": props.get("ABBREV2") or props.get("ABBREV1"),
            "route_distance_m": None if dist is None else round(dist, 1),
            "height_m": round(massing.height, 1), "base_z": round(massing.base_z, 1),
            "year": _int(props.get("YEAR_BUILT")), "footprint_m2": round(poly.area),
            "number": props.get("PROP_NUM"), "sqft": _int(props.get("SQ_FT")),
            "centroid": [round(c.x, 1), round(c.y, 1)],
            "ring": [[round(x, 1), round(y, 1)] for x, y in poly.simplify(0.8).exterior.coords],
            "style": {"confidence": style.confidence, "corridors": style.corridors,
                      "windows": style.windows, "wall": style.wall,
                      "notes": (style.notes or "")[:500], "sources": list(style.sources)},
            **info,
        })

    campus_polys = []
    for b in model.buildings:
        poly = building_polygon(b)
        c = poly.centroid
        if poly.area < 6 or not inside(c.x, c.y, 3.0):
            continue
        bid = slug(b.name or f"building_{len(buildings_report)}")
        while bid in used_ids:
            bid += "_b"
        used_ids.add(bid)
        before = len(buildings_report)
        add_building(poly, bid, b.name or "", b.height_m, b.props, campus=True)
        if len(buildings_report) > before:
            campus_polys.append(poly)

    # Buildings the campus map doesn't cover: official city footprints first, then
    # OpenStreetMap ones the city layer lacks. Their shapes are measured like the rest.
    from shapely import STRtree
    from shapely.geometry import Polygon
    from shapely.geometry import shape as to_shape

    extra: list[tuple[object, str, float]] = []
    cch_path = site.root / cfg.open_data / "cch_footprints.json" if cfg.open_data else None
    cch_heights: list[tuple[object, float]] = []
    if cch_path and cch_path.exists():
        from .opendata import footprint_height

        for f in json.loads(cch_path.read_text()):
            if not f.get("geometry"):
                continue
            geom = to_shape(f["geometry"]).buffer(0)
            height = footprint_height(f.get("properties") or {})
            if height:
                cch_heights.append((geom, height))
            for poly in getattr(geom, "geoms", [geom]):
                extra.append((poly, "cch", height or 6.0))
    extra_path = site.root / cfg.background_buildings if cfg.background_buildings else None
    if extra_path and extra_path.exists():
        for item in json.loads(extra_path.read_text()):
            poly = Polygon(item["rings"][0], item["rings"][1:])
            extra.append((poly if poly.is_valid else poly.buffer(0), "osm",
                          item.get("height") or 6.0))
    n_background = 0
    placed = list(campus_polys)
    for i, (poly, origin, height) in enumerate(extra):
        c = poly.centroid
        if poly.geom_type != "Polygon" or poly.area < 12 or not inside(c.x, c.y, 3.0):
            continue
        tree = STRtree(placed)
        if any(placed[int(k)].intersection(poly).area > 0.2 * poly.area for k in tree.query(poly)):
            continue
        placed.append(poly)
        add_building(poly, f"{origin}_{i}", "", height, {}, campus=False)
        n_background += 1

    progress("trees and props")
    n_trees = 0
    tree_size = {"surveyed": 0, "lidar": 0, "default": 0}   # where each tree's height came from
    for t in model.trees:
        x, y = t.xy
        if not inside(x, y, 1.0):
            continue
        p = t.props
        z = float(rasters.ground_z(x, y))
        height = p.get("height_m")
        tree_size["surveyed" if height else "lidar" if has_lidar else "default"] += 1
        if not height and has_lidar:
            r, c = rasters.cell(x, y)
            top = rasters.dsm_veg[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3]
            height = float(np.nanmax(top) - z) if np.isfinite(top).any() else None
        height = float(min(max(height or 6.0, 1.0), 40.0))
        radius = float(min(max(p.get("canopy_radius_m") or height * 0.3, 0.6), 14.0))
        add_tree(scene, x, y, z, height, radius, float(p.get("trunk_diameter_m") or 0.3),
                 str(p.get("family") or ""), str(p.get("habit") or ""),
                 _seed(round(x, 1), round(y, 1)), max_detail=1 if lite else 2)
        n_trees += 1

    from .assets import load_assets
    from .campus_model import inside_any_building

    library = load_assets(site.root / cfg.assets if cfg.assets else None)
    n_props: dict[str, int] = {}
    n_models: dict[str, int] = {}
    prop_models = {a.cls: a for a in library.values() if a.cls not in ("car", "street_light")}
    for prop in model.props:
        x, y = prop.xy
        if prop.cls in INDOOR_PROPS or not inside(x, y, 1.0) or inside_any_building(model, (x, y)):
            continue
        z = float(rasters.ground_z(x, y))
        if prop.cls in prop_models:        # a real model exists for this kind of object
            scene.place(prop_models[prop.cls], "props", x, y, z,
                        (_seed(x, y) % 8) * math.pi / 4)
            n_models[prop.cls] = n_models.get(prop.cls, 0) + 1
            n_props[prop.cls] = n_props.get(prop.cls, 0) + 1
        elif add_prop(scene, prop.cls, x, y, z, _seed(x, y)):
            n_props[prop.cls] = n_props.get(prop.cls, 0) + 1
    # Street lights only where the campus surveyed one, and only with a real model.
    poles = cfg.light_poles or {}
    if poles and "street_light" in library and (site.root / poles["file"]).exists():
        keep = set(poles.get("keep") or [])
        for f in json.loads((site.root / poles["file"]).read_text()):
            props, geom = f.get("properties") or {}, f.get("geometry") or {}
            if geom.get("type") != "Point" or (keep and props.get(
                    poles.get("status_field", "STATUS")) not in keep):
                continue
            if "floor" in str(props.get(poles.get("notes_field", "NOTES")) or "").lower():
                continue                # mounted on an upper deck of a parking structure
            x, y = geom["coordinates"][:2]
            if not inside(x, y, 1.0) or inside_any_building(model, (x, y)):
                continue
            # The survey gives the position; the lidar gives the height. A pole the lidar
            # did not see as 2-12 m tall (bollard lights, wall lights) is not drawn.
            height = pole_height(rasters, x, y) if has_lidar else None
            if height is None:
                n_models["street_light_skipped"] = n_models.get("street_light_skipped", 0) + 1
                continue
            angle = props.get(poles.get("angle_field", "ANGLE")) or 0.0
            lamp = library["street_light"]
            scene.place(lamp, "props", x, y, float(rasters.ground_z(x, y)),
                        math.radians(90.0 - float(angle)), scale=height / float(lamp.size[2]))
            n_models["street_light"] = n_models.get("street_light", 0) + 1

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
                                road_segments=segments, surface=surface_at, library=library)

    share = {name: round(float((surface == code).mean()), 3)
             for code, name in SURFACE_NAMES.items()}
    report = {
        **scene.meta, "origin": [float(v) for v in scene.origin],
        "size_m": [round(x1 - x0), round(y1 - y0)],
        "elevation_m": [round(float(np.nanmin(rasters.dtm)), 1),
                        round(float(np.nanmax(rasters.dtm)), 1)],
        "surface_share": share, "background_buildings": n_background, "trees": n_trees,
        "tree_height_source": tree_size, "ground": ground_report, "models": n_models,
        "model_sources": {name: {"cls": a.cls, **a.meta} for name, a in {
            **scene.assets, **(agents_scene.assets if agents_scene else {})}.items()},
        "props": n_props, "agents": agent_counts,
        "route": {"key": route_key, "passable": not route_blocked and route_line is not None,
                  "length_m": round(route_line.length) if route_line else None,
                  "xy": [[round(x, 2), round(y, 2)] for x, y in route_xy]},
        "open_areas": open_areas,
        "checks": _checks(site, cfg, rasters, buildings_report, campus_polys, cch_heights),
        "sources": _sources(site, cfg, has_lidar),
        "buildings": sorted(buildings_report,
                            key=lambda r: (r["tier"], r["route_distance_m"] or 0)),
        "mesh": scene.stats(),
    }
    return World(scene, agents_scene, rasters, surface, bbox, report)


def _layout_for(site, cfg, props: dict, corridors: str | None = None):
    """The registered floor-plan layout of a building, if one is cached and good enough."""
    number = str(props.get("PROP_NUM") or "")
    if not cfg.plans or not number:
        return None
    path = site.root / cfg.plans / f"{number}.json"
    if not path.exists():
        return None
    from .plans import Layout, layout_ok

    layout = Layout.from_json(json.loads(path.read_text()))
    return layout if layout_ok(layout.quality, corridors) else None


def pole_height(rasters: Rasters, x: float, y: float) -> float | None:
    """Height of a pole at (x, y): the lowest solid lidar returns around it, above the
    ground. Returns None when the lidar shows nothing 2-12 m tall there."""
    if rasters.dsm_low is None:
        return None
    r, c = rasters.cell(x, y)
    window = (rasters.dsm_low - rasters.dtm)[max(r - 1, 0):r + 2, max(c - 1, 0):c + 2]
    values = window[np.isfinite(window) & (window >= 2.0) & (window <= 12.0)]
    return float(np.median(values)) if len(values) else None


def _point(xy):
    from shapely.geometry import Point

    return Point(xy)


def roof_grey(poly, rasters: Rasters, scale: float | None) -> str | None:
    """Neutral grey whose lightness is the measured brightness of the roof's lidar
    returns. Hue is not measured, so none is invented."""
    import shapely

    if scale is None or rasters.roof_intensity is None:
        return None
    minx, miny, maxx, maxy = poly.bounds
    r0, c0 = rasters.cell(minx, miny)
    r1, c1 = rasters.cell(maxx, maxy)
    window = rasters.roof_intensity[r0:r1 + 1, c0:c1 + 1]
    xs = rasters.x0 + (np.arange(c0, c1 + 1) + 0.5) * rasters.res
    ys = rasters.y0 + (np.arange(r0, r1 + 1) + 0.5) * rasters.res
    gx, gy = np.meshgrid(xs, ys)
    values = window[shapely.contains_xy(poly, gx, gy) & np.isfinite(window)]
    if len(values) < 4:
        return None
    level = 0.25 + 0.55 * float(np.clip(np.median(values) / scale, 0, 1))
    v = round(level * 255)
    return f"#{v:02x}{v:02x}{v:02x}"


def _checks(site, cfg, rasters, report, polys, cch_heights) -> dict:
    """Compare the world with official data it was not built from."""
    folder = site.root / cfg.open_data if cfg.open_data else None
    if folder is None or not folder.exists():
        return {}
    from .opendata import FT, contour_check, contour_lines, height_check

    out = {"vertical_shift_applied_m": cfg.vertical_shift_m}
    for key, fld in (("cch_contours_5ft", "ELEVATION"), ("state_contours_100ft", "contour")):
        path = folder / f"{key}.json"
        if path.exists():
            out[key] = contour_check(rasters, contour_lines(json.loads(path.read_text()), fld, FT))
    campus = [r for r in report if r["campus"]]
    out["heights_vs_cch"] = height_check(
        [(p, r["height_m"]) for p, r in zip(polys, campus, strict=False)], cch_heights)
    from .opendata import region_summary

    out["region"] = region_summary(folder)
    return out


def _sources(site, cfg, has_lidar: bool) -> dict:
    """Where every part of the world came from."""
    out = {}
    folder = site.root / cfg.open_data if cfg.open_data else None
    if folder is not None and (folder / "provenance.json").exists():
        out.update(json.loads((folder / "provenance.json").read_text()))
    if has_lidar:
        out["lidar"] = {"title": "Airborne lidar point cloud", "tiles": cfg.lidar_tiles}
    out["campus_map"] = {"title": "Campus map layers (buildings, trees, props, doors)",
                         "file": "layers.yaml"}
    out["style_sheets"] = {"title": "Building style sheets, one cited photo each",
                           "folder": cfg.styles}
    return out


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---- exporters ---------------------------------------------------------------------


TEXTURE_DIR = Path(__file__).parent / "textures"
PLANAR_GROUPS = ("ground", "terrain")     # textured straight down; everything else per face


def texture_info() -> dict:
    return json.loads((TEXTURE_DIR / "textures.json").read_text())


def part_arrays(scene: Scene, key, tiles: dict, textured: bool = True):
    """(positions, faces, uv or None) for one part, in local Z-up meters.

    Ground parts keep their vertices and get top-down texture coordinates. Other
    textured parts get one vertex per face corner, mapped along the face, so a
    wall's texture runs along the wall and up it."""
    group, _, material = key
    verts, faces = scene.parts[key].arrays()
    local = verts - np.asarray(scene.origin)
    texture = scene.materials[material].get("texture")
    if texture is None or len(faces) == 0 or not textured:
        return local, faces, None
    tile = float(tiles[texture]["tile_m"])
    if group.split("/")[0] in PLANAR_GROUPS:
        return local, faces, local[:, :2] / tile
    if group.split("/")[0] == "trees":       # rounded shapes: wrap around, keep vertices shared
        return local, faces, np.c_[(local[:, 0] + local[:, 1]) * 0.7, local[:, 2]] / tile
    tri = local[faces]                                         # (m, 3, 3)
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normal /= np.linalg.norm(normal, axis=1, keepdims=True) + 1e-12
    flat = np.abs(normal[:, 2]) >= 0.7
    tangent = np.c_[-normal[:, 1], normal[:, 0]]
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True) + 1e-12
    along = tri[:, :, 0] * tangent[:, None, 0] + tri[:, :, 1] * tangent[:, None, 1]
    u = np.where(flat[:, None], tri[:, :, 0], along)
    v = np.where(flat[:, None], tri[:, :, 1], tri[:, :, 2])
    uv = np.stack([u, v], axis=-1).reshape(-1, 2) / tile
    return tri.reshape(-1, 3), np.arange(len(tri) * 3).reshape(-1, 3), uv


def export_glb(scene: Scene, path: Path, lite: bool = False, only=None,
               instances: bool = True) -> None:
    """Binary glTF with embedded textures. glTF is Y-up, so (x, y, z) is written as
    (x, z, -y). Node names are "group|class|material", which the viewer reads back.

    `lite` keeps textures on the ground only (walls and roofs become flat colour, which
    also lets their vertices be shared: the file is far smaller). `only(key)` keeps just
    the parts whose (group, class, material) key it accepts; `instances=False` leaves out
    shared models."""
    import struct

    tiles = texture_info()
    blob = bytearray()
    views, accessors, meshes, nodes, materials, images, textures = [], [], [], [], [], [], []
    material_index: dict[str, int] = {}
    texture_index: dict[str, int] = {}

    def view(data: bytes, target: int | None = None) -> int:
        while len(blob) % 4:
            blob.append(0)
        entry = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target:
            entry["target"] = target
        blob.extend(data)
        views.append(entry)
        return len(views) - 1

    def accessor(array: np.ndarray, kind: str, component: int, target: int,
                 bounds: bool = False) -> int:
        entry = {"bufferView": view(array.tobytes(), target), "componentType": component,
                 "count": int(len(array)), "type": kind}
        if bounds:
            entry["min"] = [float(v) for v in array.min(axis=0)]
            entry["max"] = [float(v) for v in array.max(axis=0)]
        accessors.append(entry)
        return len(accessors) - 1

    def material_id(name: str, textured: bool = True) -> int:
        key = (name, textured)
        if key in material_index:
            return material_index[key]
        m = scene.materials[name]
        pbr = {"baseColorFactor": [*m["color"], m["opacity"]],
               "roughnessFactor": m["roughness"], "metallicFactor": m["metallic"]}
        if m.get("texture") and textured:
            tex = m["texture"]
            if tex not in texture_index:
                data = (TEXTURE_DIR / f"{tex}.jpg").read_bytes()
                images.append({"bufferView": view(data), "mimeType": "image/jpeg", "name": tex})
                textures.append({"source": len(images) - 1, "sampler": 0})
                texture_index[tex] = len(textures) - 1
            pbr["baseColorTexture"] = {"index": texture_index[tex]}
        materials.append({"name": name, "pbrMetallicRoughness": pbr})
        material_index[key] = len(materials) - 1
        return material_index[key]

    for key in sorted(scene.parts):
        if only is not None and not only(key):
            continue
        textured = (not lite) or key[0].split("/")[0] in PLANAR_GROUPS
        pos, faces, uv = part_arrays(scene, key, tiles, textured)
        if len(faces) == 0:
            continue
        yup = np.c_[pos[:, 0], pos[:, 2], -pos[:, 1]].astype(np.float32)
        attributes = {"POSITION": accessor(yup, "VEC3", 5126, 34962, bounds=True)}
        if uv is not None:
            attributes["TEXCOORD_0"] = accessor(uv.astype(np.float32), "VEC2", 5126, 34962)
        indices = accessor(faces.astype(np.uint32).ravel(), "SCALAR", 5125, 34963)
        name = "|".join(key)
        meshes.append({"name": name, "primitives": [{
            "attributes": attributes, "indices": indices,
            "material": material_id(key[2], textured)}]})
        nodes.append({"name": name, "mesh": len(meshes) - 1})

    # Shared models: each written once, then one node per placement.
    ox, oy, oz = scene.origin
    asset_mesh: dict[str, int] = {}
    for inst in (scene.instances if instances else []):
        asset = scene.assets[inst.asset]
        if inst.asset not in asset_mesh:
            prims = []
            for i, p in enumerate(asset.prims):
                yup = np.c_[p.verts[:, 0], p.verts[:, 2], -p.verts[:, 1]].astype(np.float32)
                attributes = {"POSITION": accessor(yup, "VEC3", 5126, 34962, bounds=True)}
                pbr = {"baseColorFactor": [*p.color, 1.0], "roughnessFactor": 0.85,
                       "metallicFactor": 0.0}
                if p.uv is not None and p.image is not None:
                    uv = np.c_[p.uv[:, 0], 1.0 - p.uv[:, 1]].astype(np.float32)
                    attributes["TEXCOORD_0"] = accessor(uv, "VEC2", 5126, 34962)
                    images.append({"bufferView": view(p.image), "mimeType": "image/jpeg",
                                   "name": f"{asset.name}_{i}"})
                    textures.append({"source": len(images) - 1, "sampler": 0})
                    pbr["baseColorTexture"] = {"index": len(textures) - 1}
                materials.append({"name": f"{asset.name}_{i}", "pbrMetallicRoughness": pbr})
                prims.append({"attributes": attributes, "material": len(materials) - 1,
                              "indices": accessor(p.faces.astype(np.uint32).ravel(), "SCALAR",
                                                  5125, 34963)})
            meshes.append({"name": f"asset|{asset.cls}|{asset.name}", "primitives": prims})
            asset_mesh[inst.asset] = len(meshes) - 1
        half = inst.yaw / 2.0
        nodes.append({"name": f"{inst.group}|{asset.cls}|{asset.name}",
                      "mesh": asset_mesh[inst.asset],
                      "translation": [inst.x - ox, inst.z - oz, -(inst.y - oy)],
                      "rotation": [0.0, math.sin(half), 0.0, math.cos(half)],
                      "scale": [inst.scale] * 3})

    doc = {"asset": {"version": "2.0", "generator": "campus-rover"}, "scene": 0,
           "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes, "meshes": meshes,
           "materials": materials, "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(blob)}]}
    if textures:
        doc.update(textures=textures, images=images,
                   samplers=[{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497,
                              "wrapT": 10497}])
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    while len(blob) % 4:
        blob.append(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(text) + 8 + len(blob)))
        f.write(struct.pack("<I4s", len(text), b"JSON") + text)
        f.write(struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))


def export_usd(scene: Scene, path: Path, collision: bool = True) -> None:
    """OpenUSD for Isaac Sim: Z up, meters, a semantic label and a preview material
    (with its texture) on every mesh, collision on the static world. Textures are
    copied to a `textures` folder next to the file."""
    import shutil

    from .usd_export import _require_usd

    Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, Vt = _require_usd()
    from pxr import UsdShade

    tiles = texture_info()
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
        base = f"/World/Looks/{_usd_name(name)}"
        mat = UsdShade.Material.Define(stage, base)
        shader = UsdShade.Shader.Define(stage, f"{base}/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(m["roughness"])
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(m["metallic"])
        if m.get("texture"):
            (path.parent / "textures").mkdir(exist_ok=True)
            shutil.copy(TEXTURE_DIR / f"{m['texture']}.jpg", path.parent / "textures")
            reader = UsdShade.Shader.Define(stage, f"{base}/st")
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            tex = UsdShade.Shader.Define(stage, f"{base}/Texture")
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(f"./textures/{m['texture']}.jpg")
            tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
            tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
            tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*m["color"], 1.0))
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                reader.ConnectableAPI(), "result")
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            diffuse.ConnectToSource(tex.ConnectableAPI(), "rgb")
        else:
            diffuse.Set(Gf.Vec3f(*m["color"]))
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        looks[name] = mat
    for key in sorted(scene.parts):
        group, cls, material = key
        local, faces, uv = part_arrays(scene, key, tiles)
        if len(faces) == 0:
            continue
        prim_path = "/World/" + "/".join(_usd_name(p) for p in group.split("/"))
        for depth in range(2, prim_path.count("/") + 1):
            UsdGeom.Xform.Define(stage, "/".join(prim_path.split("/")[:depth + 1]))
        m = UsdGeom.Mesh.Define(stage, f"{prim_path}/{_usd_name(cls)}__{_usd_name(material)}")
        local = local.astype(np.float32)
        m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(local))
        m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(faces), 3, dtype=np.int32)))
        m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces.astype(np.int32).ravel()))
        m.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        m.CreateDoubleSidedAttr().Set(cls in ("tree",))
        m.CreateDisplayColorPrimvar().Set(
            Vt.Vec3fArray([Gf.Vec3f(*scene.materials[material]["color"])]))
        m.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*local.min(axis=0).tolist()),
                                          Gf.Vec3f(*local.max(axis=0).tolist())]))
        if uv is not None:
            st = UsdGeom.PrimvarsAPI(m.GetPrim()).CreatePrimvar(
                "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
            st.Set(Vt.Vec2fArray.FromNumpy(uv.astype(np.float32)))
        UsdSemantics.LabelsAPI.Apply(m.GetPrim(), "class").CreateLabelsAttr().Set([cls])
        UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(looks[material])
        _tag_traversability(m.GetPrim(), cls, Sdf)
        approx = collision_for(cls, material) if collision else None
        if approx:
            UsdPhysics.CollisionAPI.Apply(m.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr().Set(approx)
            _bind_friction(stage, m.GetPrim(), cls, UsdShade, UsdPhysics)
    # Shared models: one prototype each, referenced by every placement.
    for name, asset in scene.assets.items():
        proto = f"/World/Prototypes/{_usd_name(name)}"
        UsdGeom.Xform.Define(stage, "/World/Prototypes")
        UsdGeom.Xform.Define(stage, proto)
        for i, p in enumerate(asset.prims):
            m = UsdGeom.Mesh.Define(stage, f"{proto}/part_{i}")
            pts = p.verts.astype(np.float32)
            m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
            m.CreateFaceVertexCountsAttr(
                Vt.IntArray.FromNumpy(np.full(len(p.faces), 3, dtype=np.int32)))
            m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(p.faces.astype(np.int32).ravel()))
            m.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
            m.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(*p.color)]))
            m.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*pts.min(axis=0).tolist()),
                                              Gf.Vec3f(*pts.max(axis=0).tolist())]))
            mat = UsdShade.Material.Define(stage, f"{proto}/Looks/part_{i}")
            shader = UsdShade.Shader.Define(stage, f"{proto}/Looks/part_{i}/Shader")
            shader.CreateIdAttr("UsdPreviewSurface")
            diffuse = shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f)
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.85)
            if p.uv is not None and p.image is not None:
                (path.parent / "textures").mkdir(exist_ok=True)
                (path.parent / "textures" / f"{name}_{i}.jpg").write_bytes(p.image)
                st = UsdGeom.PrimvarsAPI(m.GetPrim()).CreatePrimvar(
                    "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
                st.Set(Vt.Vec2fArray.FromNumpy(p.uv.astype(np.float32)))
                reader = UsdShade.Shader.Define(stage, f"{proto}/Looks/part_{i}/st")
                reader.CreateIdAttr("UsdPrimvarReader_float2")
                reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
                tex = UsdShade.Shader.Define(stage, f"{proto}/Looks/part_{i}/Texture")
                tex.CreateIdAttr("UsdUVTexture")
                tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
                    f"./textures/{name}_{i}.jpg")
                tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
                    reader.ConnectableAPI(), "result")
                tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
                diffuse.ConnectToSource(tex.ConnectableAPI(), "rgb")
            else:
                diffuse.Set(Gf.Vec3f(*p.color))
            mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
            if collision and asset.cls in MODEL_COLLISION:
                UsdPhysics.CollisionAPI.Apply(m.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(m.GetPrim()).CreateApproximationAttr().Set(
                    MODEL_COLLISION[asset.cls])
        UsdGeom.Imageable(stage.GetPrimAtPath(proto)).MakeInvisible()
    for n, inst in enumerate(scene.instances):
        asset = scene.assets[inst.asset]
        parent = "/World/" + "/".join(_usd_name(p) for p in inst.group.split("/"))
        for depth in range(2, parent.count("/") + 1):
            UsdGeom.Xform.Define(stage, "/".join(parent.split("/")[:depth + 1]))
        xf = UsdGeom.Xform.Define(stage, f"{parent}/{_usd_name(inst.asset)}_{n}")
        prim = xf.GetPrim()
        prim.GetReferences().AddInternalReference(f"/World/Prototypes/{_usd_name(inst.asset)}")
        UsdGeom.Imageable(prim).MakeVisible()
        xf.AddTranslateOp().Set(Gf.Vec3d(inst.x - ox, inst.y - oy, inst.z - oz))
        xf.AddRotateZOp().Set(math.degrees(inst.yaw))
        xf.AddScaleOp().Set(Gf.Vec3f(inst.scale, inst.scale, inst.scale))
        prim.SetInstanceable(True)
        UsdSemantics.LabelsAPI.Apply(prim, "class").CreateLabelsAttr().Set([asset.cls])
        _tag_traversability(prim, asset.cls, Sdf)
    stage.GetRootLayer().Save()


def _tag_traversability(prim, cls: str, Sdf) -> None:
    """Record the class's traversability level on the prim, for per-pixel label export."""
    from .traversal import level_of

    prim.CreateAttribute("campus:traversability", Sdf.ValueTypeNames.Token).Set(level_of(cls))


def _bind_friction(stage, prim, cls: str, UsdShade, UsdPhysics) -> None:
    """Bind a physics material (friction) to a collider, one shared material per class."""
    from .physics import friction_for

    path = f"/World/PhysicsMaterials/{_usd_name(cls)}"
    if not stage.GetPrimAtPath(path).IsValid():
        mat = UsdShade.Material.Define(stage, path)
        api = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        static, dynamic = friction_for(cls)
        api.CreateStaticFrictionAttr(static)
        api.CreateDynamicFrictionAttr(dynamic)
        api.CreateRestitutionAttr(0.0)
    material = UsdShade.Material(stage.GetPrimAtPath(path))
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(
        material, UsdShade.Tokens.weakerThanDescendants, "physics")


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
    from .traversal import save_lut

    save_lut(out_dir / "traversability.json")
    world.report["physics"] = collision_stats(world.scene.parts)
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
