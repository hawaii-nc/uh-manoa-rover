"""Can a wheeled rover go here? Labels, a slope map and a cost map for training.

Every semantic class gets one of three levels:

- `traversable`  walkway, ramp: what the rover is for
- `conditional`  grass, gravel, road, stepping stones: possible but not wanted, or only
                 at a crossing
- `blocked`      stairs, curbs, walls, planters, water, buildings, trunks, furniture

Slope can lower a level: a walkway steeper than the rover's limit is conditional, one
much steeper is blocked. The same table labels every pixel of a rendered image
(`LEVELS`, saved as JSON) and every cell of the ground raster built here, so what the
camera sees and what the planner assumes are one definition.

The levels are a policy, not a measurement: whether a rover may cross a road, or climb
a given grade, is the rover's rule. The table is the single place to change it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

TRAVERSABLE, CONDITIONAL, BLOCKED = "traversable", "conditional", "blocked"
DYNAMIC = "dynamic"        # people, vehicles, animals: avoided, never a surface
CODE = {BLOCKED: 0, CONDITIONAL: 1, TRAVERSABLE: 2}
UNKNOWN_CODE = 255

LEVELS = {
    # ground
    "walkway": TRAVERSABLE, "ramp": TRAVERSABLE,
    "road": CONDITIONAL, "grass": CONDITIONAL, "gravel": CONDITIONAL, "dirt": CONDITIONAL,
    "stepstone": CONDITIONAL, "rock": CONDITIONAL, "sports_court": CONDITIONAL,
    "road_marking": CONDITIONAL, "building_slab": BLOCKED,
    "stairs": BLOCKED, "curb": BLOCKED, "wall": BLOCKED, "planter": BLOCKED, "water": BLOCKED,
    # structures and things standing on the ground
    "building": BLOCKED, "window": BLOCKED, "door": BLOCKED, "roof": BLOCKED, "tree": BLOCKED,
    "bench": BLOCKED, "trash_can": BLOCKED, "recycling_bin": BLOCKED, "bike_rack": BLOCKED,
    "skateboard_rack": BLOCKED, "bike_share_dock": BLOCKED, "emergency_call_box": BLOCKED,
    "ev_charger": BLOCKED, "mailbox": BLOCKED, "bulletin_board": BLOCKED, "art_piece": BLOCKED,
    "street_light": BLOCKED, "fence": BLOCKED, "gate": CONDITIONAL,
    # things that move
    "person": DYNAMIC, "bicycle": DYNAMIC, "skateboard": DYNAMIC, "car": DYNAMIC,
    "cart": DYNAMIC, "chicken": DYNAMIC, "cat": DYNAMIC,
}


def level_of(semantic_class: str) -> str:
    """Traversability level of a semantic class. Unknown classes are blocked, never free."""
    return LEVELS.get(semantic_class, BLOCKED)


def save_lut(path: Path) -> None:
    """The class -> level table as JSON, for the renderer that writes per-pixel labels."""
    path.write_text(json.dumps({"levels": LEVELS, "codes": CODE, "unknown": UNKNOWN_CODE},
                               indent=1))


# ---- the ground raster ---------------------------------------------------------------


class Limits:
    """The rover's slope rule. `free_slope` is the steepest grade it climbs without a second
    thought (the ADA ramp limit by default); beyond `blocked_slope` it cannot go at all."""

    def __init__(self, free_slope: float = 0.083, blocked_slope: float = 0.15,
                 rover_radius_m: float = 0.4):
        self.free_slope, self.blocked_slope, self.rover_radius_m = (
            free_slope, blocked_slope, rover_radius_m)


def build_traversability(features, rasters, bbox, building_polys=(), discs=(), limits=None,
                         res: float = 0.5) -> dict:
    """Rasterize the ground into traversability.

    features: [(polygon, land-cover class, paint)]; building_polys: footprints (blocked);
    discs: [(x, y, radius)] for trunks and furniture (blocked). Returns arrays on a grid
    of `res` meters (row 0 = south): level, slope, cost, class index, and the class names.
    """
    from PIL import Image, ImageDraw
    from scipy import ndimage

    from .walknet import clean_ground

    limits = limits or Limits()
    x0, y0, x1, y1 = bbox
    rows, cols = int(np.ceil((y1 - y0) / res)), int(np.ceil((x1 - x0) / res))
    names = ["unknown"] + sorted({c for _, c, _ in features} | {"building", "obstacle"})
    index = {n: i for i, n in enumerate(names)}
    img = Image.new("L", (cols, rows), 0)
    draw = ImageDraw.Draw(img)

    def px(poly):
        return [((x - x0) / res, (y - y0) / res) for x, y in poly.exterior.coords]

    # Big shapes first, so the small ones (curbs, stairs, walls) end up on top.
    for poly, cls, _paint in sorted(features, key=lambda f: -f[0].area):
        for part in getattr(poly, "geoms", [poly]):
            if part.geom_type == "Polygon" and len(part.exterior.coords) >= 4:
                draw.polygon(px(part), fill=index[cls])
                for hole in part.interiors:
                    draw.polygon([((x - x0) / res, (y - y0) / res) for x, y in hole.coords],
                                 fill=0)
    for poly in building_polys:
        draw.polygon(px(poly), fill=index["building"])
    for x, y, r in discs:
        cx, cy = (x - x0) / res, (y - y0) / res
        draw.ellipse([cx - r / res, cy - r / res, cx + r / res, cy + r / res],
                     fill=index["obstacle"])
    cls_idx = np.asarray(img)

    lut = np.full(len(names), UNKNOWN_CODE, dtype=np.uint8)
    for n, i in index.items():
        if n in ("building", "obstacle"):
            lut[i] = CODE[BLOCKED]
        elif n != "unknown":
            lut[i] = CODE[LEVELS.get(n, BLOCKED) if LEVELS.get(n) in CODE else BLOCKED]
    level = lut[cls_idx]
    base = level.copy()

    # Slope on a ground model that ignores the unreliable cells beside buildings.
    ground = clean_ground(_resample(rasters, res, (rows, cols), x0, y0), list(building_polys),
                          margin_m=1.5, smooth_m=1.0)
    gy, gx = np.gradient(ground, res)
    slope = np.hypot(gx, gy).astype(np.float32)
    free, cond = level == CODE[TRAVERSABLE], level == CODE[CONDITIONAL]
    level = np.where(free & (slope > limits.free_slope), CODE[CONDITIONAL], level)
    level = np.where(free & (slope > limits.blocked_slope), CODE[BLOCKED], level)
    level = np.where(cond & (slope > limits.blocked_slope), CODE[BLOCKED], level)
    level = level.astype(np.uint8)

    cost = np.select([level == CODE[TRAVERSABLE], level == CODE[CONDITIONAL],
                      level == CODE[BLOCKED]], [0.0, 0.4, 1.0], default=0.7).astype(np.float32)
    cost = np.clip(cost + 0.5 * (slope / limits.free_slope) ** 2 * (level != CODE[BLOCKED]),
                   0.0, 1.0)
    # Keep clear of blocked cells by the rover's radius (plus a little).
    away = ndimage.distance_transform_edt(level != CODE[BLOCKED]) * res
    margin = limits.rover_radius_m + 0.2
    cost = np.maximum(cost, np.clip(1.0 - away / margin, 0.0, 1.0) * 0.9).astype(np.float32)
    walk = base == CODE[TRAVERSABLE]
    downgraded = float(((level != CODE[TRAVERSABLE]) & walk).sum() / max(walk.sum(), 1))
    return {"level": level, "slope": slope, "cost": cost, "cls": cls_idx, "names": names,
            "x0": x0, "y0": y0, "res": res, "walkway_downgraded_for_slope": downgraded}


def _resample(rasters, res, shape, x0, y0):
    """The ground grid on the traversability grid (bilinear)."""
    from .lidar import Rasters

    rows, cols = shape
    gx, gy = np.meshgrid(x0 + (np.arange(cols) + 0.5) * res, y0 + (np.arange(rows) + 0.5) * res)
    z = rasters.ground_z(gx, gy)
    nan = np.full(shape, np.nan)
    return Rasters(x0, y0, res, np.asarray(z, dtype=float), nan, nan, nan,
                   np.zeros(shape, dtype=np.uint16))


def summarize(grid: dict) -> dict:
    """Shares of the mapped ground by level, and of the traversable ground by slope."""
    level = grid["level"]
    known = level != UNKNOWN_CODE
    out = {name: round(float((level[known] == code).mean()), 3) for name, code in CODE.items()}
    out["unknown_share_of_grid"] = round(float((~known).mean()), 3)
    out["walkway_downgraded_for_slope"] = round(float(grid.get("walkway_downgraded_for_slope", 0)),
                                                3)
    return out


def save_grid(grid: dict, path: Path) -> None:
    np.savez_compressed(path, level=grid["level"], slope=grid["slope"], cost=grid["cost"],
                        cls=grid["cls"], names=np.array(grid["names"]), x0=grid["x0"],
                        y0=grid["y0"], res=grid["res"])


def load_grid(path: Path) -> dict:
    d = np.load(path)
    return {"level": d["level"], "slope": d["slope"], "cost": d["cost"], "cls": d["cls"],
            "names": [str(n) for n in d["names"]], "x0": float(d["x0"]), "y0": float(d["y0"]),
            "res": float(d["res"])}


def cost_at(grid: dict, x: float, y: float) -> float:
    """Cost-map value at a world point (1.0 outside the map)."""
    r = int((y - grid["y0"]) / grid["res"])
    c = int((x - grid["x0"]) / grid["res"])
    if 0 <= r < grid["cost"].shape[0] and 0 <= c < grid["cost"].shape[1]:
        return float(grid["cost"][r, c])
    return 1.0


def preview(grid: dict, path: Path) -> None:
    """Colour picture of the levels (green free, amber conditional, red blocked, grey unknown)."""
    from PIL import Image

    colors = {CODE[TRAVERSABLE]: (76, 175, 80), CODE[CONDITIONAL]: (255, 193, 7),
              CODE[BLOCKED]: (198, 40, 40), UNKNOWN_CODE: (120, 120, 120)}
    img = np.zeros(grid["level"].shape + (3,), dtype=np.uint8)
    for code, rgb in colors.items():
        img[grid["level"] == code] = rgb
    Image.fromarray(img[::-1]).save(path)
