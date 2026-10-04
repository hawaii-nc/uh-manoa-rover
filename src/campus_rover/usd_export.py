"""Export the campus map model + walkway graph to an OpenUSD stage for Isaac Sim.

Layout (Z up, meters, recentred near the origin for float precision):

  /World/Ground                 grass plane with collision            label: grass
  /World/Walkways/<kind>        one ribbon mesh per kind              labels: sidewalk, stairs,
                                                                       ramp, crosswalk, road
  /World/Buildings/<name>       extruded footprints with collision    label: building
  /World/Construction           barrier walls around closure areas    label: construction_barrier
  /World/Trees/Tree_<i>         trunk + canopy proxies                label: tree
  /World/Props/<class>_<i>      sized boxes (benches, call boxes, …)  label: <class>
  /World/Markers/...            landmarks, entrances, GCPs, vehicle spawns (no geometry)

Labels use UsdSemantics (SemanticsLabelsAPI:class), which Isaac Sim 5 / Replicator read
for segmentation ground truth. Proxy geometry (boxes, cylinders) is meant to be swapped
for real assets or replaced by Gaussian splats later. The ground is flat until the
lidar DEM is added.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .campus_model import CampusModel
from .geometry import ccw, triangulate
from .graph import CampusGraph

PROP_SIZES = {  # class: (width, depth, height) in meters
    "bench": (1.8, 0.6, 0.8),
    "emergency_call_box": (0.4, 0.4, 2.5),
    "bike_rack": (2.0, 0.5, 0.9),
    "skateboard_rack": (1.0, 0.4, 0.8),
    "trash_can": (0.6, 0.6, 1.0),
    "recycling_bin": (0.6, 0.6, 1.0),
    "vending_machine": (1.0, 0.8, 1.9),
    "water_refill": (0.5, 0.4, 1.1),
    "ev_charger": (0.5, 0.4, 1.5),
    "mailbox": (0.5, 0.5, 1.2),
    "bulletin_board": (1.5, 0.2, 2.0),
    "food_truck": (6.0, 2.5, 3.0),
    "bike_share_dock": (8.0, 1.5, 1.2),
    "art_piece": (1.5, 1.5, 2.5),
}
DEFAULT_PROP_SIZE = (0.5, 0.5, 1.0)

WALKWAY_STYLE = {  # kind: (width m, z offset m, semantic label, color)
    "walkway": (3.0, 0.03, "sidewalk", (0.72, 0.70, 0.66)),
    "ramp": (2.5, 0.035, "ramp", (0.65, 0.68, 0.72)),
    "stairs": (2.5, 0.035, "stairs", (0.55, 0.52, 0.50)),
    "crosswalk": (3.0, 0.04, "crosswalk", (0.95, 0.95, 0.95)),
    "road": (6.0, 0.02, "road", (0.25, 0.25, 0.27)),
}

COLORS = {
    "grass": (0.30, 0.48, 0.24),
    "building": (0.80, 0.77, 0.70),
    "construction_barrier": (0.95, 0.55, 0.10),
    "trunk": (0.40, 0.28, 0.18),
    "canopy": (0.18, 0.40, 0.16),
    "prop": (0.35, 0.35, 0.40),
}


def _require_usd():
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, Vt
    except ImportError as e:
        raise ImportError('USD export needs OpenUSD: pip install -e ".[usd]"') from e
    return Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, Vt


def safe_name(text: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", text or "").strip("_") or fallback
    return name if not name[0].isdigit() else f"_{name}"


def scene_bounds(
    model: CampusModel, graph: CampusGraph | None
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    b = model.bounds()
    if b:
        xs += [b[0], b[2]]
        ys += [b[1], b[3]]
    if graph is not None and graph.g.number_of_nodes():
        xs += [graph.xy(n)[0] for n in graph.g.nodes]
        ys += [graph.xy(n)[1] for n in graph.g.nodes]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return min(xs), min(ys), max(xs), max(ys)


def choose_origin(model: CampusModel, graph: CampusGraph | None) -> tuple[float, float]:
    """Local CRS keeps its origin. Projected CRSs (UTM: ~600 km offsets) are recentred
    to the nearest 100 m so renderer/physics floats stay precise."""
    if model.crs == "local":
        return (0.0, 0.0)
    x0, y0, x1, y1 = scene_bounds(model, graph)
    return (round((x0 + x1) / 200) * 100.0, round((y0 + y1) / 200) * 100.0)


def export_usd(
    model: CampusModel,
    graph: CampusGraph | None,
    out: Path,
    site_name: str = "",
    landmarks: dict[str, str] | None = None,
    ground_pad_m: float = 50.0,
) -> dict:
    Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, Vt = _require_usd()
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    ox, oy = choose_origin(model, graph)
    stage.GetRootLayer().customLayerData = {
        "campus:site": site_name,
        "campus:crs": model.crs,
        "campus:originOffset": Gf.Vec2d(ox, oy),
        "campus:note": "world xy = CRS xy - originOffset; Z up; meters",
    }

    def local(p):
        return (p[0] - ox, p[1] - oy)

    def label(prim, cls: str):
        UsdSemantics.LabelsAPI.Apply(prim, "class").CreateLabelsAttr().Set([cls])

    def collide(prim, mesh: bool = True):
        UsdPhysics.CollisionAPI.Apply(prim)
        if mesh:
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set("none")

    def mesh(path, points, faces, cls, color, collision=True, double_sided=False):
        m = UsdGeom.Mesh.Define(stage, path)
        m.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        m.CreateFaceVertexCountsAttr(Vt.IntArray([len(f) for f in faces]))
        m.CreateFaceVertexIndicesAttr(Vt.IntArray([i for f in faces for i in f]))
        m.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        m.CreateDoubleSidedAttr().Set(double_sided)
        m.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(*color)]))
        m.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(m.GetPointsAttr().Get()))
        label(m.GetPrim(), cls)
        if collision:
            collide(m.GetPrim())
        return m

    counts: dict[str, int] = {}

    # Ground
    x0, y0, x1, y1 = scene_bounds(model, graph)
    (gx0, gy0), (gx1, gy1) = (
        local((x0 - ground_pad_m, y0 - ground_pad_m)),
        local((x1 + ground_pad_m, y1 + ground_pad_m)),
    )
    mesh(
        "/World/Ground",
        [(gx0, gy0, 0), (gx1, gy0, 0), (gx1, gy1, 0), (gx0, gy1, 0)],
        [[0, 1, 2, 3]],
        "grass",
        COLORS["grass"],
    )

    # Walkways: one ribbon mesh per kind
    if graph is not None:
        UsdGeom.Scope.Define(stage, "/World/Walkways")
        groups: dict[str, list] = {}
        for u, v, data in graph.g.edges(data=True):
            kind = "road" if data.get("zone") == "roadside" else data["kind"]
            groups.setdefault(kind, []).append((graph.xy(u), graph.xy(v)))
        for kind, segments in groups.items():
            width, z, cls, color = WALKWAY_STYLE.get(kind, WALKWAY_STYLE["walkway"])
            pts, faces = [], []
            for a, b in segments:
                (ax, ay), (bx, by) = local(a), local(b)
                length = math.hypot(bx - ax, by - ay)
                if length < 1e-6:
                    continue
                nx, ny = -(by - ay) / length * width / 2, (bx - ax) / length * width / 2
                base = len(pts)
                pts += [
                    (ax - nx, ay - ny, z),
                    (bx - nx, by - ny, z),
                    (bx + nx, by + ny, z),
                    (ax + nx, ay + ny, z),
                ]
                faces.append([base, base + 1, base + 2, base + 3])
            if faces:
                mesh(f"/World/Walkways/{kind}", pts, faces, cls, color, collision=False)
        counts["walkway_edges"] = graph.g.number_of_edges()

    # Buildings
    UsdGeom.Scope.Define(stage, "/World/Buildings")
    used: dict[str, int] = {}
    for i, b in enumerate(model.buildings):
        ring = [local(p) for p in ccw(b.rings[0])]
        if len(ring) < 3:
            continue
        name = safe_name(b.name or "", f"Building_{i}")
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name}_{used[name]}"
        n, h = len(ring), b.height_m
        pts = [(x, y, 0.0) for x, y in ring] + [(x, y, h) for x, y in ring]
        faces = [[k, (k + 1) % n, n + (k + 1) % n, n + k] for k in range(n)]
        faces += [[n + a, n + c, n + d] for a, c, d in triangulate(ring)]
        m = mesh(f"/World/Buildings/{name}", pts, faces, "building", COLORS["building"])
        m.GetPrim().CreateAttribute("campus:name", Sdf.ValueTypeNames.String).Set(b.name or "")
        m.GetPrim().CreateAttribute("campus:height_m", Sdf.ValueTypeNames.Float).Set(h)
    counts["buildings"] = len(model.buildings)

    # Construction barriers (1.2 m walls around each closure area)
    if model.closures:
        pts, faces = [], []
        for area in model.closures:
            ring = [local(p) for p in ccw(area.rings[0])]
            n, base = len(ring), len(pts)
            pts += [(x, y, 0.0) for x, y in ring] + [(x, y, 1.2) for x, y in ring]
            faces += [
                [base + k, base + (k + 1) % n, base + n + (k + 1) % n, base + n + k]
                for k in range(n)
            ]
        mesh(
            "/World/Construction",
            pts,
            faces,
            "construction_barrier",
            COLORS["construction_barrier"],
            double_sided=True,
        )
    counts["closures"] = len(model.closures)

    # Trees
    UsdGeom.Scope.Define(stage, "/World/Trees")
    for i, tree in enumerate(model.trees):
        x, y = local(tree.xy)
        xf = UsdGeom.Xform.Define(stage, f"/World/Trees/Tree_{i}")
        xf.AddTranslateOp().Set(Gf.Vec3d(x, y, 0))
        label(xf.GetPrim(), "tree")
        if "species" in tree.props:
            xf.GetPrim().CreateAttribute("campus:species", Sdf.ValueTypeNames.String).Set(
                tree.props["species"]
            )
        trunk = UsdGeom.Cylinder.Define(stage, f"/World/Trees/Tree_{i}/Trunk")
        trunk.CreateRadiusAttr(0.25)
        trunk.CreateHeightAttr(4.0)
        trunk.CreateAxisAttr("Z")
        trunk.AddTranslateOp().Set(Gf.Vec3d(0, 0, 2.0))
        trunk.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(*COLORS["trunk"])]))
        collide(trunk.GetPrim(), mesh=False)
        canopy = UsdGeom.Sphere.Define(stage, f"/World/Trees/Tree_{i}/Canopy")
        canopy.CreateRadiusAttr(3.0)
        canopy.AddTranslateOp().Set(Gf.Vec3d(0, 0, 6.0))
        canopy.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(*COLORS["canopy"])]))
    counts["trees"] = len(model.trees)

    # Props
    UsdGeom.Scope.Define(stage, "/World/Props")
    per_class: dict[str, int] = {}
    for p in model.props:
        cls = safe_name(p.cls, "prop")
        idx = per_class.get(cls, 0)
        per_class[cls] = idx + 1
        w, d, h = PROP_SIZES.get(p.cls, DEFAULT_PROP_SIZE)
        x, y = local(p.xy)
        cube = UsdGeom.Cube.Define(stage, f"/World/Props/{cls}_{idx}")
        cube.CreateSizeAttr(1.0)
        cube.AddTranslateOp().Set(Gf.Vec3d(x, y, h / 2))
        cube.AddScaleOp().Set(Gf.Vec3f(w, d, h))
        cube.CreateDisplayColorPrimvar().Set(Vt.Vec3fArray([Gf.Vec3f(*COLORS["prop"])]))
        label(cube.GetPrim(), p.cls)
        collide(cube.GetPrim(), mesh=False)
    counts["props"] = len(model.props)

    # Markers (no geometry): landmarks for training tasks, entrances, GCPs, vehicle spawns
    UsdGeom.Scope.Define(stage, "/World/Markers")

    def marker(group: str, name: str, xy, attrs: dict):
        x, y = local(xy)
        xf = UsdGeom.Xform.Define(stage, f"/World/Markers/{group}/{name}")
        xf.AddTranslateOp().Set(Gf.Vec3d(x, y, 0))
        prim = xf.GetPrim()
        prim.CreateAttribute("campus:easting", Sdf.ValueTypeNames.Double).Set(float(xy[0]))
        prim.CreateAttribute("campus:northing", Sdf.ValueTypeNames.Double).Set(float(xy[1]))
        for key, value in attrs.items():
            prim.CreateAttribute(f"campus:{key}", Sdf.ValueTypeNames.String).Set(str(value))

    if graph is not None and landmarks:
        UsdGeom.Scope.Define(stage, "/World/Markers/Landmarks")
        for key, node in landmarks.items():
            marker("Landmarks", safe_name(key, "landmark"), graph.xy(node), {"node": node})
    for group, items in (
        ("Entrances", model.entrances),
        ("GCPs", model.gcps),
        ("VehicleSpawns", model.vehicle_spawns),
    ):
        if items:
            UsdGeom.Scope.Define(stage, f"/World/Markers/{group}")
        for i, p in enumerate(items):
            marker(group, f"{group[:-1]}_{i}", p.xy, {"name": p.name or "", "class": p.cls})
    counts["markers"] = len(model.entrances) + len(model.gcps) + len(model.vehicle_spawns)

    stage.GetRootLayer().Save()
    return {"path": str(out), "origin_offset": (ox, oy), **counts}
