"""Ground detail from a surveyed land-cover layer: surfaces, curbs, stairs, walls.

UH's campus map publishes every paved area, lawn, planter, curb, wall, ramp and
single stair tread as a polygon. This module drapes those polygons on the lidar
terrain and raises the ones that stand up:

- flat surfaces (road, walkway, grass, ...) follow the ground
- curbs stand one standard curb height above it
- stair treads are flat, each at the ground height measured under it, so a
  flight of stairs comes out as real steps
- walls rise to the height the lidar measured, or stay flat when it measured none

Lines painted on the ground (parking stalls, sports fields) become thin strips.
"""

from __future__ import annotations

import math

import numpy as np

from .meshkit import Scene, triangulate_polygon

CURB_HEIGHT_M = 0.15     # standard 6-inch curb; the lidar grid is too coarse to measure it
STAIR_SKIRT_M = 0.7      # how far each tread's sides reach down, to close the risers
WALL_RANGE_M = (0.2, 3.5)   # measured wall heights outside this are not trusted
GRID_M = 3.0             # large polygons are cut into pieces this size to follow slopes

# semantic class -> (texture, tint, how it is built)
CLASSES = {
    "road":         ("asphalt",   (0.27, 0.27, 0.28), "flat"),
    "walkway":      ("concrete",  (0.80, 0.78, 0.73), "flat"),
    "ramp":         ("concrete",  (0.78, 0.76, 0.71), "flat"),
    "stairs":       ("concrete",  (0.74, 0.72, 0.68), "stairs"),
    "curb":         ("concrete",  (0.70, 0.69, 0.66), "curb"),
    "wall":         ("plaster",   (0.72, 0.70, 0.66), "wall"),
    "rock_wall":    ("rock_wall", (0.45, 0.42, 0.40), "wall"),
    "grass":        ("grass",     (0.38, 0.55, 0.24), "flat"),
    "planter":      ("soil",      (0.30, 0.42, 0.22), "flat"),
    "stepstone":    ("concrete",  (0.74, 0.72, 0.68), "flat"),
    "building_slab": ("concrete", (0.72, 0.70, 0.66), "flat"),
    "gravel":       ("gravel",    (0.62, 0.59, 0.54), "flat"),
    "rock":         ("rock_wall", (0.42, 0.40, 0.38), "flat"),
    "dirt":         ("soil",      (0.50, 0.40, 0.30), "flat"),
    "water":        (None,        (0.20, 0.42, 0.55), "flat"),
    "sports_court": (None,        (0.22, 0.45, 0.36), "flat"),
}
PAINT = {"red": (0.72, 0.12, 0.10), "yellow": (0.90, 0.75, 0.10), "green": (0.15, 0.50, 0.25),
         "blue": (0.12, 0.30, 0.70), "orange": (0.92, 0.50, 0.10), "white": (0.93, 0.93, 0.90)}
# What each class counts as in the coarse road / walkway / grass grid.
COARSE = {"road": 2, "walkway": 1, "ramp": 1, "stairs": 1, "curb": 1, "gravel": 1,
          "sports_court": 1, "stepstone": 1, "building_slab": 1}


def split_by_grid(poly, cell: float = GRID_M) -> list:
    """Cut a polygon into pieces no larger than `cell`, so each can tilt with the ground."""
    import shapely

    out, stack = [], [poly]
    while stack:
        g = stack.pop()
        if g.is_empty:
            continue
        if g.geom_type != "Polygon":
            stack.extend(part for part in getattr(g, "geoms", []) if part.geom_type == "Polygon")
            continue
        x0, y0, x1, y1 = g.bounds
        w, h = x1 - x0, y1 - y0
        if w <= cell and h <= cell:
            if g.area > 1e-4:
                out.append(g)
            continue
        if w >= h:
            mid = math.floor((x0 + x1) / 2 / cell + 0.5) * cell
            mid = mid if x0 + 1e-6 < mid < x1 - 1e-6 else (x0 + x1) / 2
            stack.append(shapely.clip_by_rect(g, x0 - 1, y0 - 1, mid, y1 + 1))
            stack.append(shapely.clip_by_rect(g, mid, y0 - 1, x1 + 1, y1 + 1))
        else:
            mid = math.floor((y0 + y1) / 2 / cell + 0.5) * cell
            mid = mid if y0 + 1e-6 < mid < y1 - 1e-6 else (y0 + y1) / 2
            stack.append(shapely.clip_by_rect(g, x0 - 1, y0 - 1, x1 + 1, mid))
            stack.append(shapely.clip_by_rect(g, x0 - 1, mid, x1 + 1, y1 + 1))
    return out


def drape(mesh, poly, ground_z, lift: float = 0.0, cell: float = GRID_M) -> None:
    """Fill `poly` with triangles that follow the ground, `lift` meters above it."""
    for piece in split_by_grid(poly, cell):
        verts, tris = triangulate_polygon(piece)
        if len(tris):
            z = np.asarray(ground_z(verts[:, 0], verts[:, 1])) + lift
            mesh.add(np.c_[verts, z], tris)


def raise_polygon(mesh, poly, ground_z, height: float, skirt: float = 0.1,
                  cell: float = GRID_M) -> None:
    """A kerb- or wall-like solid: top `height` above the ground, sides down to it."""
    drape(mesh, poly, ground_z, lift=height, cell=cell)
    from .meshkit import polygon_rings

    for ring in polygon_rings(poly):
        pts = _densify(ring, cell)
        z = np.asarray(ground_z(np.array([p[0] for p in pts]), np.array([p[1] for p in pts])))
        n = len(pts)
        for i in range(n):
            j = (i + 1) % n
            mesh.sloped_wall(pts[i], pts[j], z[i] - skirt, z[i] + height,
                             z[j] - skirt, z[j] + height)


def _densify(ring, step: float) -> list:
    out = []
    for p, q in zip(ring, ring[1:] + ring[:1], strict=True):
        n = max(1, math.ceil(math.dist(p, q) / step))
        out.extend((p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n) for k in range(n))
    return out


def add_stair_tread(mesh, poly, ground_z) -> float:
    """One flat tread at the ground height measured under it. Returns that height."""
    pts = np.array(poly.exterior.coords)
    z = float(np.median(ground_z(pts[:, 0], pts[:, 1])))
    mesh.polygon_cap(poly, z)
    mesh.prism_walls(poly, z - STAIR_SKIRT_M, z)
    return z


def measured_height(poly, rasters) -> float | None:
    """Height of a low wall: the lowest solid lidar returns inside it, above the ground."""
    import shapely

    if getattr(rasters, "dsm_low", None) is None:
        return None
    x0, y0, x1, y1 = poly.bounds
    r0, c0 = rasters.cell(x0, y0)
    r1, c1 = rasters.cell(x1, y1)
    window = (rasters.dsm_low - rasters.dtm)[r0:r1 + 1, c0:c1 + 1]
    xs = rasters.x0 + (np.arange(c0, c1 + 1) + 0.5) * rasters.res
    ys = rasters.y0 + (np.arange(r0, r1 + 1) + 0.5) * rasters.res
    gx, gy = np.meshgrid(xs, ys)
    values = window[shapely.contains_xy(poly.buffer(0.15), gx, gy) & np.isfinite(window)]
    if len(values) < 3:
        return None
    h = float(np.median(values))
    return h if WALL_RANGE_M[0] <= h <= WALL_RANGE_M[1] else None


def add_landcover(scene: Scene, features: list[tuple[object, str, str | None]], rasters,
                  bbox) -> dict:
    """Build the ground from land-cover polygons.

    features: [(shapely polygon in site meters, semantic class, paint colour or None)].
    Returns counts, including how many walls had a measurable height."""
    import shapely

    x0, y0, x1, y1 = bbox
    counts: dict[str, int] = {}
    walls = {"measured": 0, "flat": 0}
    stair_z = []

    for poly, cls, paint in features:
        if cls not in CLASSES:
            continue
        poly = shapely.clip_by_rect(poly, x0, y0, x1, y1)
        if poly.is_empty:
            continue
        how = CLASSES[cls][2]
        label = "wall" if cls == "rock_wall" else cls
        texture, tint, _ = CLASSES[cls]
        for part in getattr(poly, "geoms", [poly]):
            if part.geom_type != "Polygon" or part.area < 1e-3:
                continue
            if paint in PAINT:
                mat = scene.material(f"paint_{paint}", PAINT[paint], roughness=0.7)
            else:
                mat = scene.material(f"ground_{cls}", tint, roughness=1.0, texture=texture)
            mesh = scene.mesh("ground", label, mat)
            if how == "flat":
                drape(mesh, part, rasters.ground_z)
            elif how == "curb":
                raise_polygon(mesh, part, rasters.ground_z, CURB_HEIGHT_M)
            elif how == "stairs":
                stair_z.append(add_stair_tread(mesh, part, rasters.ground_z))
            elif how == "wall":
                h = measured_height(part, rasters)
                walls["measured" if h else "flat"] += 1
                if h:
                    raise_polygon(mesh, part, rasters.ground_z, h)
                else:
                    drape(mesh, part, rasters.ground_z, lift=0.02)
            counts[label] = counts.get(label, 0) + 1
    return {"polygons": counts, "walls": walls, "stair_treads": len(stair_z)}


def add_painted_lines(scene: Scene, lines: list[tuple[list, str]], rasters, bbox,
                      width: float = 0.1) -> int:
    """Painted ground lines as thin strips. lines: [(coords, colour name)]."""
    x0, y0, x1, y1 = bbox
    n = 0
    for coords, colour in lines:
        mat = scene.material(f"paint_{colour}", PAINT.get(colour, PAINT["white"]), roughness=0.7)
        mesh = scene.mesh("ground", "road_marking", mat)
        for p, q in zip(coords, coords[1:], strict=False):
            if not (x0 < p[0] < x1 and y0 < p[1] < y1 and x0 < q[0] < x1 and y0 < q[1] < y1):
                continue
            length = math.dist(p, q)
            if length < 0.05:
                continue
            nx, ny = (q[1] - p[1]) / length * width / 2, -(q[0] - p[0]) / length * width / 2
            zp = float(rasters.ground_z(p[0], p[1])) + 0.03
            zq = float(rasters.ground_z(q[0], q[1])) + 0.03
            mesh.quad((p[0] + nx, p[1] + ny, zp), (q[0] + nx, q[1] + ny, zq),
                      (q[0] - nx, q[1] - ny, zq), (p[0] - nx, p[1] - ny, zp))
            n += 1
    return n


def add_crosswalk_paint(scene: Scene, crossings: list, rasters, bbox, width: float = 3.0,
                        bar: float = 0.5, gap: float = 0.6) -> int:
    """Zebra bars across each crossing. `crossings`: [((x, y), (x, y))]. Only crossings the map
    data itself says are crosswalks (marked by OpenStreetMap or confirmed by you) are given
    paint: a crossing that is only suggested by ramps or the shape of the road gets none.
    The bar size is an assumption (a common US pattern), not a measurement."""
    x0, y0, x1, y1 = bbox
    mat = scene.material("paint_white", PAINT["white"], roughness=0.7)
    mesh = scene.mesh("ground", "road_marking", mat)
    n = 0
    for a, b in crossings:
        length = math.dist(a, b)
        if length < 2.0:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length   # along the walking direction
        vx, vy = -uy, ux
        # leave the curb zone clear: bars start 0.6 m into the road
        t = 0.6
        while t + bar <= length - 0.6:
            for s0, s1 in ((t, t + bar),):
                corners = []
                for along, side in ((s0, -1), (s1, -1), (s1, 1), (s0, 1)):
                    x = a[0] + ux * along + vx * side * width / 2
                    y = a[1] + uy * along + vy * side * width / 2
                    if not (x0 < x < x1 and y0 < y < y1):
                        break
                    corners.append((x, y, float(rasters.ground_z(x, y)) + 0.03))
                else:
                    mesh.quad(*corners)
                    n += 1
            t += bar + gap
    return n


def add_accessible_stalls(scene: Scene, points: list, rasters, bbox, lines=()) -> int:
    """A blue square with a white centre (the accessible-parking symbol, simplified) at each
    surveyed accessible stall. Turned to match the nearest painted stall line."""
    x0, y0, x1, y1 = bbox
    blue = scene.mesh("ground", "road_marking", scene.material("paint_blue", PAINT["blue"], 0.7))
    white = scene.mesh("ground", "road_marking", scene.material("paint_white", PAINT["white"], 0.7))
    segs = [(p, q) for coords, _c in lines for p, q in zip(coords, coords[1:], strict=False)]
    n = 0
    for x, y in points:
        if not (x0 < x < x1 and y0 < y < y1):
            continue
        yaw = 0.0
        if segs:
            near = min(segs, key=lambda s: math.dist(((s[0][0] + s[1][0]) / 2,
                                                       (s[0][1] + s[1][1]) / 2), (x, y)))
            yaw = math.atan2(near[1][1] - near[0][1], near[1][0] - near[0][0])
        c, sn = math.cos(yaw), math.sin(yaw)
        z = float(rasters.ground_z(x, y))
        for mesh, half, lift in ((blue, 0.6, 0.035), (white, 0.28, 0.04)):
            pts = [(x + (dx * c - dy * sn) * half, y + (dx * sn + dy * c) * half, z + lift)
                   for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            mesh.quad(*pts)
        n += 1
    return n


# Fence and gate heights are assumptions (UH's layer gives type and line, not height).
FENCE_HEIGHT_M = {"ChainLinkFence": 1.8, "MetalFence": 1.2, "Gate": 1.5}


def add_fences(scene: Scene, fences: list, rasters, bbox) -> int:
    """Fences and gates standing on the ground. fences: [(coords, ESRI type)]. A chain-link
    fence is drawn see-through, a gate in its own colour so the openings can be found."""
    x0, y0, x1, y1 = bbox
    mats = {
        "ChainLinkFence": scene.material("fence_chainlink", (0.55, 0.57, 0.58), 0.6, 0.4, 0.35),
        "MetalFence": scene.material("fence_metal", (0.18, 0.2, 0.22), 0.6, 0.5),
        "Gate": scene.material("fence_gate", (0.15, 0.45, 0.28), 0.6, 0.3),
    }
    n = 0
    for coords, kind in fences:
        if kind not in mats:
            continue
        mesh = scene.mesh("props", "gate" if kind == "Gate" else "fence", mats[kind])
        h = FENCE_HEIGHT_M[kind]
        for p, q in zip(coords, coords[1:], strict=False):
            if not (x0 < p[0] < x1 and y0 < p[1] < y1 and x0 < q[0] < x1 and y0 < q[1] < y1):
                continue
            if math.dist(p, q) < 0.05:
                continue
            z0 = float(rasters.ground_z(p[0], p[1]))
            z1 = float(rasters.ground_z(q[0], q[1]))
            top_p, top_q = (p[0], p[1], z0 + h), (q[0], q[1], z1 + h)
            mesh.quad((p[0], p[1], z0), (q[0], q[1], z1), top_q, top_p)
            mesh.quad((p[0], p[1], z0), top_p, top_q, (q[0], q[1], z1))
            n += 1
    return n


def coarse_grid(features, rasters, default: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(class grid, covered mask) at the raster's resolution: 0 grass, 1 walkway, 2 road.
    Used for the surface report, for placing animals on grass, and to know where
    the land-cover layer has no data."""
    from PIL import Image, ImageDraw

    rows, cols = rasters.shape
    img = Image.new("L", (cols, rows), 255)
    draw = ImageDraw.Draw(img)
    ordered = sorted(features, key=lambda f: -f[0].area)      # small shapes drawn last
    for poly, cls, _ in ordered:
        if cls not in CLASSES:
            continue
        for part in getattr(poly, "geoms", [poly]):
            if part.geom_type != "Polygon":
                continue
            px = [((x - rasters.x0) / rasters.res, (y - rasters.y0) / rasters.res)
                  for x, y in part.exterior.coords]
            if len(px) >= 3:
                draw.polygon(px, fill=COARSE.get(cls, default))
    grid = np.asarray(img).copy()
    covered = grid != 255
    grid[~covered] = default
    return grid, covered


def curb_reveal(z, valid, x0: float, y0: float, res: float, curbs, road, walk,
                min_cells: int = 8) -> dict:
    """How far the lidar ground steps up from road to sidewalk at each mapped curb.

    z / valid: a fine ground grid (row 0 = south) and which cells have ground points.
    For every curb polygon the median ground height is taken on the road side, on the
    sidewalk side and on the curb itself, in strips 0.35-1.3 m away, and the differences
    are summarised over all curbs. A control compares two strips of road at different
    distances from the curb: its spread is the noise floor, so a reading near it means
    the lidar cannot resolve the curb."""
    import shapely

    def cells(poly):
        if poly.is_empty:
            return np.array([])
        minx, miny, maxx, maxy = poly.bounds
        c0, c1 = max(int((minx - x0) / res), 0), min(int((maxx - x0) / res) + 1, z.shape[1])
        r0, r1 = max(int((miny - y0) / res), 0), min(int((maxy - y0) / res) + 1, z.shape[0])
        if c1 <= c0 or r1 <= r0:
            return np.array([])
        gx, gy = np.meshgrid(x0 + (np.arange(c0, c1) + 0.5) * res,
                             y0 + (np.arange(r0, r1) + 0.5) * res)
        inside = shapely.contains_xy(poly, gx, gy) & valid[r0:r1, c0:c1]
        return z[r0:r1, c0:c1][inside]

    side, top, control = [], [], []
    for c in curbs:
        ring = c.buffer(1.3).difference(c.buffer(0.35))
        r, w, t = cells(ring.intersection(road)), cells(ring.intersection(walk)), cells(c)
        far = cells(c.buffer(2.2).difference(c.buffer(1.4)).intersection(road))
        if len(r) >= min_cells and len(w) >= min_cells:
            side.append(float(np.median(w) - np.median(r)))
        if len(r) >= min_cells and len(t) >= 4:
            top.append(float(np.median(t) - np.median(r)))
        if len(r) >= min_cells and len(far) >= min_cells:
            control.append(float(np.median(far) - np.median(r)))

    def summary(values):
        a = np.asarray(values)
        if not len(a):
            return {"n": 0}
        return {"n": int(len(a)), "median_m": round(float(np.median(a)), 3),
                "p25_m": round(float(np.percentile(a, 25)), 3),
                "p75_m": round(float(np.percentile(a, 75)), 3)}

    return {"sidewalk_minus_road": summary(side), "curb_top_minus_road": summary(top),
            "control_road_minus_road": summary(control)}
