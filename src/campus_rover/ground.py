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
          "sports_court": 1}


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
