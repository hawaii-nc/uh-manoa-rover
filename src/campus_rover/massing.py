"""Real building shapes from lidar: footprint + roof heights -> stacked blocks.

A footprint alone gives a box. Lidar shows what is on top: a tower above a
podium, a courtyard, a stair core, a pitched roof. `building_massing` reads
the roof heights inside one footprint and returns:

- `blocks`  flat-roofed pieces (polygon + roof height), one per roof level
- `pitched` True when the roof slopes; then `roof_grid` holds the roof surface
- `height_at(x, y)` roof height above the building's base at any point,
  used to hide walls that are buried inside a taller neighbouring block

Everything is in meters. Heights are measured above `base_z`, the ground
level at the building.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MIN_BUILDING_HEIGHT = 2.2   # lower than this inside a footprint = open courtyard
LEVEL_WINDOW = 1.25         # roof heights within this of a level belong to it


@dataclass
class Block:
    polygon: object          # shapely Polygon
    height: float            # roof height above base_z


@dataclass
class Massing:
    base_z: float
    height: float                      # tallest roof level above base_z
    blocks: list[Block] = field(default_factory=list)
    pitched: bool = False
    eave: float = 0.0
    # Roof surface samples for pitched roofs and for height_at().
    x0: float = 0.0
    y0: float = 0.0
    res: float = 0.5
    roof: np.ndarray | None = None     # height above base_z, 0 outside the building

    def height_at(self, x: float, y: float) -> float:
        if self.roof is None:
            return 0.0
        c = int((x - self.x0) / self.res)
        r = int((y - self.y0) / self.res)
        if 0 <= r < self.roof.shape[0] and 0 <= c < self.roof.shape[1]:
            return float(self.roof[r, c])
        return 0.0


def box_massing(polygon, base_z: float, height: float) -> Massing:
    """Fallback when there is no lidar: one block at a given height."""
    return Massing(base_z, height, [Block(polygon, height)])


def building_massing(polygon, rasters, max_levels: int = 5, min_area_m2: float = 14.0,
                     default_height: float = 10.0) -> Massing:
    """Split one footprint into roof levels using the lidar roof and ground grids."""
    import shapely
    from scipy import ndimage

    res = rasters.res
    minx, miny, maxx, maxy = polygon.bounds
    rows, cols = rasters.shape
    c0 = max(int((minx - rasters.x0) / res) - 1, 0)
    c1 = min(int((maxx - rasters.x0) / res) + 2, cols)
    r0 = max(int((miny - rasters.y0) / res) - 1, 0)
    r1 = min(int((maxy - rasters.y0) / res) + 2, rows)
    if c1 - c0 < 2 or r1 - r0 < 2:
        return box_massing(polygon, 0.0, default_height)

    xs = rasters.x0 + (np.arange(c0, c1) + 0.5) * res
    ys = rasters.y0 + (np.arange(r0, r1) + 0.5) * res
    gx, gy = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(polygon, gx, gy)
    if inside.sum() < 4:
        return box_massing(polygon, float(np.nanmean(rasters.dtm[r0:r1, c0:c1])), default_height)

    # Ground level: a low percentile of the ground just around the footprint.
    ring = ndimage.binary_dilation(inside, iterations=4) & ~inside
    ground = rasters.dtm[r0:r1, c0:c1]
    base_z = float(np.percentile(ground[ring if ring.any() else inside], 20))

    roof = rasters.dsm_bldg[r0:r1, c0:c1] - base_z
    valid = np.isfinite(roof) & inside
    if valid.sum() < 0.3 * inside.sum():
        return box_massing(polygon, base_z, default_height)
    # Fill gaps from the nearest roof cell, then drop single-cell noise (antennas, birds).
    idx = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
    roof = ndimage.median_filter(roof[tuple(idx)], size=5)
    roof = np.where(inside, np.clip(roof, 0.0, None), 0.0)

    solid = inside & (roof >= MIN_BUILDING_HEIGHT)
    if solid.sum() < 4:
        return box_massing(polygon, base_z, default_height)
    out = Massing(base_z, float(np.percentile(roof[solid], 98)), x0=xs[0] - res / 2,
                  y0=ys[0] - res / 2, res=res, roof=np.where(solid, roof, 0.0))

    # Pitched roof? Then most of the roof is sloping, not flat.
    gy_, gx_ = np.gradient(ndimage.uniform_filter(roof, size=3), res)
    slope = np.hypot(gx_, gy_)
    core = ndimage.binary_erosion(solid, iterations=3)
    if core.sum() > 40 and np.mean((slope[core] > 0.2) & (slope[core] < 1.6)) > 0.5:
        out.pitched = True
        out.eave = float(np.percentile(roof[solid], 12))
        out.blocks = [Block(polygon, out.eave)]
        return out

    levels = _roof_levels(roof[solid], max_levels)
    label = np.full(roof.shape, -1)
    label[solid] = np.abs(roof[solid][:, None] - np.array(levels)[None, :]).argmin(axis=1)
    label = _absorb_small(label, solid, int(min_area_m2 / res ** 2))

    for k in np.unique(label[label >= 0]):
        height = float(np.median(roof[label == k]))
        comps, n = ndimage.label(label == k)
        for i in range(1, n + 1):
            cells = comps == i
            rr, cc = np.nonzero(cells)
            boxes = shapely.box(out.x0 + cc * res, out.y0 + rr * res,
                                out.x0 + (cc + 1) * res, out.y0 + (rr + 1) * res)
            shape = shapely.union_all(boxes).buffer(res * 0.6, join_style="mitre")
            shape = shape.simplify(res * 1.2).intersection(polygon)
            for piece in getattr(shape, "geoms", [shape]):
                if piece.geom_type == "Polygon" and piece.area >= min_area_m2 * 0.5:
                    out.blocks.append(Block(piece, height))
    if not out.blocks:
        out.blocks = [Block(polygon, out.height)]
    out.height = max(b.height for b in out.blocks)
    return out


def _roof_levels(heights: np.ndarray, max_levels: int) -> list[float]:
    """Pick the main roof heights: repeatedly take the most common height and
    claim everything within LEVEL_WINDOW of it."""
    left = heights.copy()
    levels: list[float] = []
    while len(levels) < max_levels and len(left) > 0.03 * len(heights):
        bins = np.arange(left.min(), left.max() + 1.0, 0.5)
        if len(bins) < 2:
            levels.append(float(np.median(left)))
            break
        hist, edges = np.histogram(left, bins=bins)
        peak = edges[hist.argmax()] + 0.25
        near = np.abs(left - peak) <= LEVEL_WINDOW
        levels.append(float(np.median(left[near])) if near.any() else float(peak))
        left = left[~near]
    return levels or [float(np.median(heights))]


def _absorb_small(label: np.ndarray, solid: np.ndarray, min_cells: int) -> np.ndarray:
    """Merge patches smaller than min_cells into the nearest larger patch."""
    from scipy import ndimage

    keep = np.zeros(label.shape, dtype=bool)
    for k in np.unique(label[label >= 0]):
        comps, n = ndimage.label(label == k)
        if n == 0:
            continue
        sizes = ndimage.sum(np.ones_like(comps), comps, index=np.arange(1, n + 1))
        keep |= np.isin(comps, np.nonzero(sizes >= min_cells)[0] + 1)
    if not keep.any():
        return label
    idx = ndimage.distance_transform_edt(~keep, return_distances=False, return_indices=True)
    return np.where(solid, label[tuple(idx)], -1)
