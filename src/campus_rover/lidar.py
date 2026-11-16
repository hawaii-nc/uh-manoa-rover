"""Turn airborne lidar tiles into the rasters the 3D world is built from.

Input: classified LAZ tiles in the site's projected CRS (meters), e.g. the
2022-2023 NOAA/USGS Hawaiʻi lidar. Output: one `.npz` of aligned grids over
a bounding box:

- `dtm`        bare-ground elevation, holes (under buildings) filled
- `dsm_bldg`   highest solid-surface return per cell: roofs (NaN where there is none)
- `dsm_veg`    highest vegetation return per cell (NaN where there is none)
- `intensity`  mean return strength of ground points (tells asphalt from grass)
- `ground_n`   how many ground points fell in each cell
- `dsm_low`    lowest solid return per cell: the top of low walls, under any canopy
- `roof_intensity`  mean return strength of roof points (how light or dark a roof is)

Reading LAZ needs `laspy[lazrs]`; everything after the read is plain NumPy,
so the gridding is tested offline with made-up points.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

UNCLASSIFIED = 1
GROUND = 2
VEGETATION = (3, 4, 5)
BUILDING = 6


@dataclass
class Rasters:
    """Aligned grids. Row 0 is the south edge; cell (r, c) covers
    x0 + c*res .. x0 + (c+1)*res and y0 + r*res .. y0 + (r+1)*res."""

    x0: float
    y0: float
    res: float
    dtm: np.ndarray
    dsm_bldg: np.ndarray
    dsm_veg: np.ndarray
    intensity: np.ndarray
    ground_n: np.ndarray
    roof_intensity: np.ndarray | None = None   # mean return strength of roof points
    dsm_low: np.ndarray | None = None          # lowest solid return per cell (wall tops)

    @property
    def shape(self) -> tuple[int, int]:
        return self.dtm.shape

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        rows, cols = self.shape
        return (self.x0, self.y0, self.x0 + cols * self.res, self.y0 + rows * self.res)

    def cell(self, x: float, y: float) -> tuple[int, int]:
        rows, cols = self.shape
        c = min(max(int((x - self.x0) / self.res), 0), cols - 1)
        r = min(max(int((y - self.y0) / self.res), 0), rows - 1)
        return r, c

    def ground_z(self, x, y):
        """Bilinear ground elevation at point(s) (x, y)."""
        return bilinear(self.dtm, (np.asarray(x) - self.x0) / self.res - 0.5,
                        (np.asarray(y) - self.y0) / self.res - 0.5)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path, x0=self.x0, y0=self.y0, res=self.res, dtm=self.dtm.astype(np.float32),
            dsm_bldg=self.dsm_bldg.astype(np.float32), dsm_veg=self.dsm_veg.astype(np.float32),
            intensity=self.intensity.astype(np.float32), ground_n=self.ground_n.astype(np.uint16),
            **({} if self.roof_intensity is None
               else {"roof_intensity": self.roof_intensity.astype(np.float32)}),
            **({} if self.dsm_low is None else {"dsm_low": self.dsm_low.astype(np.float32)}),
        )

    def shift_z(self, dz: float) -> None:
        """Move every elevation by dz meters (to change the height reference)."""
        self.dtm = self.dtm + dz
        self.dsm_bldg = self.dsm_bldg + dz
        self.dsm_veg = self.dsm_veg + dz
        if self.dsm_low is not None:
            self.dsm_low = self.dsm_low + dz

    @classmethod
    def load(cls, path: Path) -> Rasters:
        d = np.load(path)
        return cls(float(d["x0"]), float(d["y0"]), float(d["res"]), d["dtm"].astype(float),
                   d["dsm_bldg"].astype(float), d["dsm_veg"].astype(float),
                   d["intensity"].astype(float), d["ground_n"],
                   d["roof_intensity"].astype(float) if "roof_intensity" in d else None,
                   d["dsm_low"].astype(float) if "dsm_low" in d else None)


def bilinear(grid: np.ndarray, col, row):
    rows, cols = grid.shape
    col = np.clip(col, 0, cols - 1.001)
    row = np.clip(row, 0, rows - 1.001)
    c0 = np.floor(col).astype(int)
    r0 = np.floor(row).astype(int)
    fc, fr = col - c0, row - r0
    return (grid[r0, c0] * (1 - fc) * (1 - fr) + grid[r0, c0 + 1] * fc * (1 - fr)
            + grid[r0 + 1, c0] * (1 - fc) * fr + grid[r0 + 1, c0 + 1] * fc * fr)


class Gridder:
    """Accumulates points tile by tile, so no tile has to stay in memory."""

    def __init__(self, bbox: tuple[float, float, float, float], res: float = 0.5) -> None:
        self.x0, self.y0 = bbox[0], bbox[1]
        self.res = res
        self.cols = int(np.ceil((bbox[2] - bbox[0]) / res))
        self.rows = int(np.ceil((bbox[3] - bbox[1]) / res))
        n = self.rows * self.cols
        self.g_sum = np.zeros(n)
        self.g_n = np.zeros(n)
        self.i_sum = np.zeros(n)
        self.i_n = np.zeros(n)
        self.r_sum = np.zeros(n)
        self.r_n = np.zeros(n)
        self.low = np.full(n, np.inf)
        self.bldg = np.full(n, -np.inf)
        self.veg = np.full(n, -np.inf)

    def add(self, x, y, z, cls, intensity, num_returns=None) -> int:
        """Add points (arrays of equal length). Returns how many fell inside.

        Some datasets only label ground (2) and leave the rest unclassified (1).
        Then `num_returns` splits the rest: a pulse with one return hit something
        solid (a roof), a pulse with several returns went through leaves.
        """
        c = np.floor((np.asarray(x) - self.x0) / self.res).astype(np.int64)
        r = np.floor((np.asarray(y) - self.y0) / self.res).astype(np.int64)
        ok = (c >= 0) & (c < self.cols) & (r >= 0) & (r < self.rows)
        idx = (r * self.cols + c)[ok]
        z = np.asarray(z, dtype=float)[ok]
        cls = np.asarray(cls)[ok]
        intensity = np.asarray(intensity, dtype=float)[ok]
        other = cls == UNCLASSIFIED
        if num_returns is None:
            solid, leafy = np.zeros_like(other), np.zeros_like(other)
        else:
            single = np.asarray(num_returns)[ok] <= 1
            solid, leafy = other & single, other & ~single

        g = cls == GROUND
        np.add.at(self.g_sum, idx[g], z[g])
        np.add.at(self.g_n, idx[g], 1)
        # Ground seen through leaves returns a weak signal, which would make shaded
        # grass look like asphalt. Only count pulses that reached the ground directly.
        clear = g if num_returns is None else g & (np.asarray(num_returns)[ok] <= 1)
        np.add.at(self.i_sum, idx[clear], intensity[clear])
        np.add.at(self.i_n, idx[clear], 1)
        b = (cls == BUILDING) | solid
        np.maximum.at(self.bldg, idx[b], z[b])
        np.add.at(self.r_sum, idx[b], intensity[b])
        np.add.at(self.r_n, idx[b], 1)
        np.minimum.at(self.low, idx[b], z[b])
        v = np.isin(cls, VEGETATION) | leafy
        np.maximum.at(self.veg, idx[v], z[v])
        return int(ok.sum())

    def finish(self) -> Rasters:
        shape = (self.rows, self.cols)
        n = self.g_n.reshape(shape)
        with np.errstate(invalid="ignore", divide="ignore"):
            dtm = np.where(n > 0, self.g_sum.reshape(shape) / n, np.nan)
            i_n = self.i_n.reshape(shape)
            inten = np.where(i_n > 0, self.i_sum.reshape(shape) / i_n, np.nan)
        bldg = self.bldg.reshape(shape).copy()
        bldg[~np.isfinite(bldg)] = np.nan
        veg = self.veg.reshape(shape).copy()
        veg[~np.isfinite(veg)] = np.nan
        low = self.low.reshape(shape).copy()
        low[~np.isfinite(low)] = np.nan
        with np.errstate(invalid="ignore", divide="ignore"):
            r_n = self.r_n.reshape(shape)
            roof_i = np.where(r_n > 0, self.r_sum.reshape(shape) / r_n, np.nan)
        return Rasters(self.x0, self.y0, self.res, fill_holes(dtm), bldg, veg,
                       fill_holes(inten), n, roof_i, low)


def fill_holes(grid: np.ndarray, smooth_passes: int = 2) -> np.ndarray:
    """Fill NaN cells from their nearest valid cell, then soften the filled part.

    The ground under a building has no ground returns. Nearest-cell filling
    followed by a small blur gives a plausible floor without inventing slopes.
    """
    from scipy import ndimage

    holes = ~np.isfinite(grid)
    if not holes.any():
        return grid
    if holes.all():
        return np.zeros_like(grid)
    idx = ndimage.distance_transform_edt(holes, return_distances=False, return_indices=True)
    out = grid[tuple(idx)]
    for _ in range(smooth_passes):
        blurred = ndimage.uniform_filter(out, size=5, mode="nearest")
        out = np.where(holes, blurred, out)
    return out


def rasterize_tiles(paths: list[Path], bbox, res: float = 0.5,
                    progress=None) -> Rasters:  # pragma: no cover - needs laspy + data
    import laspy

    grid = Gridder(bbox, res)
    for path in paths:
        with laspy.open(str(path)) as reader:
            h = reader.header
            if h.maxs[0] < bbox[0] or h.mins[0] > bbox[2] or h.maxs[1] < bbox[1] \
                    or h.mins[1] > bbox[3]:
                continue
            kept = 0
            for chunk in reader.chunk_iterator(4_000_000):
                kept += grid.add(chunk.x, chunk.y, chunk.z,
                                 np.asarray(chunk.classification), chunk.intensity,
                                 np.asarray(chunk.number_of_returns))
        if progress:
            progress(f"{path.name}: {kept:,} points in box")
    return grid.finish()
