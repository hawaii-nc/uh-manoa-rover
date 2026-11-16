"""Read UH Planning's floor-plan sheets and place them on the world.

Each sheet is a vector PDF page drawn at a stated scale: room polygons filled by
occupancy, grey "non-assignable" areas (corridors, stairs, mechanical), column
symbols, and a label per room (type, number, area, organisation). This module:

1. `read_sheet`  turns a page into rooms, columns and an outline, in meters;
2. `register`    finds where that outline sits on the lidar roof of the building,
                 by sliding and turning it until the two agree, and reports how
                 well they agree;
3. the result feeds `planbuild`, which builds the real layout of each floor.

Sheets are for information only and UH asks that they stay private, so only
derived facts (floor counts, registered layouts) are kept in the repository.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

FT = 0.3048
PT_PER_IN = 72.0
CLOSE_M = 2.0                     # gaps up to twice this between rooms count as floor
OPEN_TYPES = {"CORR"}              # circulation that is open to the air in tropical plans
WHITE = (1.0, 1.0, 1.0)
_SCALE = re.compile(r"(\d+)(?:/(\d+))?\"\s*=\s*(\d+)'")
_CODE = re.compile(r"\b(\d{3,4}[A-Z]?)-(\d{3})([A-Z]{0,3})\b")


@dataclass
class Room:
    poly: object                 # shapely polygon, meters in sheet coordinates (y up)
    kind: str                    # label type, e.g. CORR, OFC, STAIR ("" when unlabelled)
    number: str = ""
    area_sf: float | None = None
    org: str = ""
    fill: tuple = ()


@dataclass
class Sheet:
    code: str                    # e.g. "1163-200E"
    level: int                   # 100, 200, ... (0 = basement)
    wing: str                    # "E", "W", "" ...
    m_per_pt: float
    rooms: list[Room] = field(default_factory=list)
    columns: list[tuple[float, float]] = field(default_factory=list)
    outline: object = None       # union of every room and slab shape of the main drawing
    column_m: float = 0.5        # side of a column symbol, meters


def parse_scale(text: str) -> float | None:
    """Meters per PDF point from a drawing scale such as '1" = 20'-0"' or '3/64" = 1'-0"'."""
    m = _SCALE.search(text.replace("’", "'").replace("”", '"'))
    if not m:
        return None
    inches = int(m.group(1)) / int(m.group(2) or 1)
    return (int(m.group(3)) / inches) * FT / PT_PER_IN


def _path_polygons(items):
    """Closed shapes from a PyMuPDF drawing's items, as lists of (x, y) points."""
    shapes, current = [], []
    for it in items:
        kind = it[0]
        if kind == "re" or kind == "qu":
            if current:
                shapes.append(current)
                current = []
            r = it[1]
            if kind == "re":
                shapes.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
            else:
                shapes.append([(r.ul.x, r.ul.y), (r.ur.x, r.ur.y), (r.lr.x, r.lr.y),
                               (r.ll.x, r.ll.y)])
        elif kind == "l":
            a, b = (it[1].x, it[1].y), (it[2].x, it[2].y)
            if current and math.dist(current[-1], a) > 0.05:
                shapes.append(current)
                current = []
            if not current:
                current = [a]
            current.append(b)
        elif kind == "c":      # curve: keep the end points, good enough for outlines
            a, b = (it[1].x, it[1].y), (it[4].x, it[4].y)
            if current and math.dist(current[-1], a) > 0.05:
                shapes.append(current)
                current = []
            if not current:
                current = [a]
            current.append(b)
    if current:
        shapes.append(current)
    return [s for s in shapes if len(s) >= 3]


def read_sheet(page) -> Sheet | None:
    """Rooms, columns and outline of one floor-plan page. None if it is not a plan."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    text = page.get_text()
    scale = parse_scale(text)
    code = _CODE.search(text)
    if scale is None or code is None:
        return None
    k = scale
    height = page.rect.height

    def to_m(p):
        return (p[0] * k, (height - p[1]) * k)

    words = page.get_text("words")
    rooms, columns, shapes, sizes = [], [], [], []
    for d in page.get_drawings():
        fill = d.get("fill")
        for pts in _path_polygons(d["items"]):
            poly = Polygon([to_m(p) for p in pts])
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            w = (poly.bounds[2] - poly.bounds[0]) / k
            h = (poly.bounds[3] - poly.bounds[1]) / k
            if fill is not None and tuple(round(c, 2) for c in fill) == WHITE:
                # A column symbol: a small white square with no outline (about 0.4-1 m).
                if 5.0 <= w <= 12.0 and abs(w - h) < 0.6 and len(pts) <= 5:
                    columns.append(((poly.bounds[0] + poly.bounds[2]) / 2,
                                    (poly.bounds[1] + poly.bounds[3]) / 2))
                    sizes.append(w * k)
                continue
            if fill is None or poly.area / (k * k) < 12:
                continue
            shapes.append((poly, tuple(round(c, 2) for c in fill)))
    # Words fall in the smallest shape that contains them; the top word is the room type.
    for poly, fill in shapes:
        x0, y0, x1, y1 = poly.bounds
        inside = [w for w in words if x0 / k <= w[0] and w[2] <= x1 / k + 1e-6
                  and (height - w[3]) * k >= y0 - 1e-6 and (height - w[1]) * k <= y1 + 1e-6]
        inside = [w for w in inside if poly.contains(
            Polygon([to_m((w[0], w[1])), to_m((w[2], w[1])), to_m((w[2], w[3])),
                     to_m((w[0], w[3]))]).centroid)]
        inside.sort(key=lambda w: (round(w[1]), w[0]))
        tokens = [w[4] for w in inside]
        kind = next((t for t in tokens if re.fullmatch(r"[A-Z]{2,9}", t)
                     and t not in ("SF", "UP", "DN")), "")
        number = next((t for t in tokens if re.fullmatch(r"\d{3}[A-Z0-9]*", t)), "")
        area = next((float(t.replace(",", "")) for i, t in enumerate(tokens)
                     if i + 1 < len(tokens) and tokens[i + 1] == "SF"
                     and re.fullmatch(r"[\d,]+", t)), None)
        org = tokens[-1] if tokens and tokens[-1] != kind and re.fullmatch(
            r"[A-Z]{2,9}", tokens[-1]) and tokens[-1] not in ("UP", "DN") else ""
        rooms.append(Room(poly, kind, number, area, org, fill))
    if not rooms:
        return None
    # The main drawing is the biggest connected cluster; legends and key plans are apart.
    merged = unary_union([r.poly.buffer(3 * k) for r in rooms])
    parts = list(getattr(merged, "geoms", [merged]))
    main = max(parts, key=lambda g: g.area)
    rooms = [r for r in rooms if main.contains(r.poly.representative_point())]
    outline = unary_union([r.poly.buffer(0.02) for r in rooms]).buffer(-0.02)
    # Plans leave walls and some corridors unfilled; gaps narrower than this are slab.
    outline = outline.buffer(CLOSE_M, join_style="mitre").buffer(-CLOSE_M, join_style="mitre")
    columns = [c for c in columns if main.contains(_pt(c))]
    c = code.groups()
    level = int(c[1])
    sheet = Sheet(f"{c[0]}-{c[1]}{c[2]}", level, c[2], k, rooms, columns, outline,
                  column_m=float(np.median(sizes)) if sizes else 0.5)
    return sheet


def _pt(xy):
    from shapely.geometry import Point

    return Point(xy)


# ---- registration ---------------------------------------------------------------------


@dataclass
class Placement:
    """Where a sheet sits in the world: world = R(angle) * sheet + (dx, dy)."""

    angle_deg: float
    dx: float
    dy: float
    score: float                 # 1.0 = every cell of the sheet's window agrees with the roof
    agree_plan_cells: float      # share of the sheet's own cells that lie on a roof

    def apply(self, geom):
        from shapely import affinity

        g = affinity.rotate(geom, self.angle_deg, origin=(0, 0), use_radians=False)
        return affinity.translate(g, self.dx, self.dy)

    def point(self, xy) -> tuple[float, float]:
        a = math.radians(self.angle_deg)
        c, s = math.cos(a), math.sin(a)
        return (xy[0] * c - xy[1] * s + self.dx, xy[0] * s + xy[1] * c + self.dy)


def _raster(poly, x0, y0, res, shape):
    """Boolean grid of cells whose centres are inside `poly`."""
    import shapely

    rows, cols = shape
    gx, gy = np.meshgrid(x0 + (np.arange(cols) + 0.5) * res, y0 + (np.arange(rows) + 0.5) * res)
    return shapely.contains_xy(poly, gx, gy)


def wing_ok(wing: str, cx: float, cy: float, centre: tuple[float, float]) -> bool:
    """Does a plan centred at (cx, cy) lie on the side its sheet name says? 'E' is east
    of the building centre, 'W' west, 'N' north, 'S' south (the last letter decides)."""
    side = wing[-1:] if wing else ""
    return {"E": cx >= centre[0], "W": cx <= centre[0], "N": cy >= centre[1],
            "S": cy <= centre[1]}.get(side, True)


def register(sheet: Sheet, roof_mask: np.ndarray, x0: float, y0: float, res: float = 1.0,
             angles=None, centre: tuple[float, float] | None = None) -> Placement | None:
    """Place a sheet on a roof mask (True where the building has a roof).

    `centre` is the building centre: sheets named East/West/North/South must land on that
    side of it. The sheet is turned through `angles` and slid over the mask. A turn and slide
    scores by how many cells of the sheet's rotated bounding window agree: roof
    where the sheet has floor, no roof where it has a void (a courtyard)."""
    from scipy.signal import fftconvolve
    from shapely import affinity
    from shapely.geometry import box

    best, best_score = None, -2.0
    angles = np.arange(-180, 180, 1.0) if angles is None else angles
    T = np.where(roof_mask, 1.0, -1.0)
    for ang in angles:
        plan = affinity.rotate(sheet.outline, ang, origin=(0, 0))
        window = affinity.rotate(box(*sheet.outline.bounds), ang, origin=(0, 0))
        wx0, wy0, wx1, wy1 = window.bounds
        shape = (int(math.ceil((wy1 - wy0) / res)) + 1, int(math.ceil((wx1 - wx0) / res)) + 1)
        inside = _raster(plan, wx0, wy0, res, shape)
        in_window = _raster(window, wx0, wy0, res, shape)
        P = np.where(inside, 1.0, np.where(in_window, -1.0, 0.0))
        if not in_window.any():
            continue
        # Pad the roof mask with "no roof" so a sheet may overhang its edge (or be larger).
        py, px = P.shape
        corr = fftconvolve(np.pad(T, ((py, py), (px, px)), constant_values=-1.0),
                           P[::-1, ::-1], mode="valid")
        if corr.size == 0:
            continue
        if centre is not None and sheet.wing:
            # Keep only slides that leave the plan on the side its name gives.
            pc = plan.centroid
            cx = x0 + ((np.arange(corr.shape[1]) - px) * res) + (pc.x - wx0)
            cy = y0 + ((np.arange(corr.shape[0]) - py) * res) + (pc.y - wy0)
            ok = np.array([[wing_ok(sheet.wing, x, y, centre) for x in cx] for y in cy])
            corr = np.where(ok, corr, -1e9)
        iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
        score = corr[iy, ix] / max(in_window.sum(), 1)
        if score > best_score:
            best_score, best = score, (ang, wx0, wy0, ix - px, iy - py, inside)
    if best is None:
        return None
    ang, wx0, wy0, ix, iy, inside = best
    # Window origin lands on mask cell (ix, iy): world = rotated sheet + shift.
    dx = x0 + ix * res - wx0
    dy = y0 + iy * res - wy0
    rows, cols = inside.shape
    padded = np.pad(roof_mask, ((rows, rows), (cols, cols)), constant_values=False)
    sub = padded[iy + rows:iy + 2 * rows, ix + cols:ix + 2 * cols]
    agree = float(sub[inside].mean()) if inside.any() else 0.0
    return Placement(float(ang), float(dx), float(dy), float(best_score), agree)


def anchor_to_end(placement: Placement, sheet: Sheet, roof_extent: tuple[float, float, float, float]
                  ) -> Placement:
    """Slide a placed wing sheet so its outer edge sits at the end of the building.

    A sheet named East ends where the building ends, so its east edge belongs at the
    roof's east extent (likewise West, North, South). This removes the one-bay
    ambiguity that a long repetitive building leaves after fitting."""
    side = sheet.wing[-1:] if sheet.wing else ""
    if side not in ("E", "W", "N", "S"):
        return placement
    x0, y0, x1, y1 = placement.apply(sheet.outline).bounds
    ex0, ey0, ex1, ey1 = roof_extent
    dx = {"E": ex1 - x1, "W": ex0 - x0}.get(side, 0.0)
    dy = {"N": ey1 - y1, "S": ey0 - y0}.get(side, 0.0)
    return Placement(placement.angle_deg, placement.dx + dx, placement.dy + dy,
                     placement.score, placement.agree_plan_cells)


# ---- layout: every level of one building in one frame ---------------------------------


@dataclass
class Level:
    level: int                    # 100, 200, ...
    slab: object                  # shapely polygon(s): the whole floor, voids left out
    enclosed: object              # shapely polygon(s): rooms with walls
    columns: list[tuple[float, float]]
    rooms: list[Room] = field(default_factory=list)


@dataclass
class Layout:
    number: str
    levels: list[Level]
    angle_deg: float
    quality: dict                 # score / agreement numbers, for the report and the gate

    def to_json(self) -> dict:
        return {
            "number": self.number, "angle_deg": self.angle_deg, "quality": self.quality,
            "levels": [{
                "level": lv.level, "slab": lv.slab.wkt, "enclosed": lv.enclosed.wkt,
                "columns": [[round(x, 3), round(y, 3)] for x, y in lv.columns],
                "rooms": [{"kind": r.kind, "number": r.number, "area_sf": r.area_sf,
                           "org": r.org, "poly": r.poly.wkt} for r in lv.rooms]}
                for lv in self.levels]}

    @classmethod
    def from_json(cls, data: dict) -> Layout:
        from shapely import wkt

        levels = [Level(d["level"], wkt.loads(d["slab"]), wkt.loads(d["enclosed"]),
                        [tuple(c) for c in d["columns"]],
                        [Room(wkt.loads(r["poly"]), r["kind"], r["number"], r["area_sf"],
                              r["org"]) for r in d["rooms"]]) for d in data["levels"]]
        return cls(data["number"], levels, data["angle_deg"], data["quality"])


def is_open(room: Room) -> bool:
    """A corridor, or an unlabelled grey area: no walls around it."""
    return room.kind in OPEN_TYPES or (room.kind == "" and room.fill == (0.93, 0.92, 0.9))


def _match_columns(src, ref, search=8.0, step=0.1, tol=0.35):
    """Shift (sx, sy) that puts most of `src` columns on `ref` columns, with the count."""
    from scipy.spatial import cKDTree

    if len(src) < 4 or len(ref) < 4:
        return (0.0, 0.0), 0
    tree = cKDTree(np.asarray(ref))
    a = np.asarray(src)
    best = (0, 0.0, 0.0)
    for sx in np.arange(-search, search + 1e-9, step):
        for sy in np.arange(-search / 2, search / 2 + 1e-9, step):
            d, _ = tree.query(a + (sx, sy))
            n = int((d < tol).sum())
            if n > best[0]:
                best = (n, float(sx), float(sy))
    if best[0] == 0:
        return (0.0, 0.0), 0
    # Every shift in a small plateau matches the same columns; take the exact average.
    d, idx = tree.query(a + (best[1], best[2]))
    near = d < tol
    resid = np.asarray(ref)[idx[near]] - (a[near] + (best[1], best[2]))
    return (best[1] + float(resid[:, 0].mean()), best[2] + float(resid[:, 1].mean())), best[0]


def build_layout(pdf_path, number: str, roof_mask: np.ndarray, x0: float, y0: float,
                 res: float, centre: tuple[float, float], angle_range=6.0,
                 min_rooms: int = 4, angles=None) -> Layout | None:
    """Read every sheet of one building's PDF and fit them all onto its roof.

    1. Each sheet is slid over the roof mask (plans are drawn north-up, so only
       a few degrees of turn are tried) and the sheets agree on one angle.
    2. Sheets named East/West are anchored so their outer edge meets the roof's end.
    3. Every level is shifted so its columns sit on the columns of the top full
       level, because a building's structure stacks.
    4. East and west halves of a level are joined at the middle of their overlap."""
    import pymupdf
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    doc = pymupdf.open(str(pdf_path))
    sheets = [s for s in (read_sheet(doc[i]) for i in range(len(doc)))
              if s is not None and s.level >= 100 and not s.wing.startswith("M")
              and len(s.rooms) >= 1]
    if not sheets or not roof_mask.any():
        return None
    fitted = [s for s in sheets if len(s.rooms) >= min_rooms]
    area0 = {lv: sum(s.outline.area for s in sheets if s.level == lv)
             for lv in {s.level for s in sheets}}
    judged0 = [s for s in fitted if area0[s.level] >= 0.5 * max(area0.values())] or fitted
    # Sheets may be drawn north-up or turned a quarter turn: try each family of angles and
    # keep the one that fits the floors with slabs best.
    families = [0.0, 90.0, 180.0, -90.0] if angles is None else [None]
    best_mean, first = -9.0, []
    for fam in families:
        fam_angles = angles if fam is None else fam + np.arange(
            -angle_range, angle_range + 1e-9, 0.5)
        tried = [(s, register(s, roof_mask, x0, y0, res, fam_angles, centre)) for s in judged0]
        tried = [(s, p) for s, p in tried if p is not None]
        if not tried:
            continue
        mean = float(np.mean([p.score for s, p in tried]))
        if mean > best_mean:
            best_mean, first = mean, tried
        if fam == 0.0 and mean > 0.78:
            break                                  # north-up fits well: no need to look further
    if not first:
        return None
    strong = [p.angle_deg for s, p in first if p.score > 0.75] or [p.angle_deg for s, p in first]
    angle = float(np.median(strong))
    yy, xx = np.nonzero(roof_mask)
    extent = (x0 + xx.min() * res, y0 + yy.min() * res, x0 + (xx.max() + 1) * res,
              y0 + (yy.max() + 1) * res)
    placed = []
    for s in sheets:
        p = register(s, roof_mask, x0, y0, res, [angle], centre)
        if p is None:
            continue
        placed.append((s, anchor_to_end(p, s, extent), p))
    if not placed:
        return None
    levels_all = sorted({s.level for s, _, _ in placed})
    area = {lv: sum(s.outline.area for s, _, _ in placed if s.level == lv) for lv in levels_all}
    full = [lv for lv in levels_all if area[lv] >= 0.5 * max(area.values())]
    ref_level = max(full)

    def columns_of(s, p, shift=(0.0, 0.0)):
        return [(q[0] + shift[0], q[1] + shift[1]) for q in (p.point(c) for c in s.columns)]

    ref_cols = {w: columns_of(s, p) for s, p, _ in placed if s.level == ref_level
                for w in [s.wing]}
    shifts, matched = {}, {}
    for s, p, _ in placed:
        key = (s.level, s.wing)
        if s.level == ref_level or s.wing not in ref_cols:
            shifts[key], matched[key] = (0.0, 0.0), len(s.columns)
            continue
        sh, n = _match_columns(columns_of(s, p), ref_cols[s.wing])
        enough = n >= 6 and n >= 0.5 * min(len(s.columns), len(ref_cols[s.wing]))
        shifts[key], matched[key] = (sh if enough else (0.0, 0.0)), (n if enough else 0)

    from shapely import affinity

    levels = []
    for lv in levels_all:
        parts = []        # (sheet, rooms, columns) per wing, in world meters
        for s, p, _ in placed:
            if s.level != lv:
                continue
            sh = shifts[(lv, s.wing)]
            rooms = [Room(affinity.translate(p.apply(r.poly), *sh), r.kind, r.number,
                          r.area_sf, r.org, r.fill) for r in s.rooms]
            parts.append((s, rooms, columns_of(s, p, sh)))
        # Join halves: keep each wing's rooms on its own side of the overlap's middle.
        keep = []
        by_side = {s.wing[-1:]: rooms for s, rooms, _ in parts}
        if "E" in by_side and "W" in by_side:
            e0 = min(r.poly.bounds[0] for r in by_side["E"])
            w1 = max(r.poly.bounds[2] for r in by_side["W"])
            xm = (e0 + w1) / 2
            keep = [r for r in by_side["E"] if r.poly.representative_point().x >= xm]
            keep += [r for r in by_side["W"] if r.poly.representative_point().x < xm]
            cols = [c for s, _, cs in parts for c in cs
                    if (c[0] >= xm) == (s.wing[-1:] == "E")]
        else:
            keep = [r for _, rooms, _ in parts for r in rooms]
            cols = [c for _, _, cs in parts for c in cs]
        # Columns of a level that could not be matched to the structure are not trusted.
        if lv != ref_level and not any(matched.get((lv, s.wing)) for s, _, _ in parts):
            cols = []
        if not keep:
            continue
        slab = unary_union([r.poly.buffer(0.05) for r in keep]).buffer(-0.05)
        enclosed_parts = [r.poly for r in keep if not is_open(r)]
        enclosed = unary_union(enclosed_parts) if enclosed_parts else Polygon()
        levels.append(Level(lv, slab, enclosed.intersection(slab), cols, keep))
    # Only floors with a real slab judge the fit: a mostly open ground level cannot be fitted.
    judged = [(s, p) for s, _, p in placed if s.level in full and len(s.rooms) >= min_rooms]
    scores = [p.score for s, p in judged]
    agree = [p.agree_plan_cells for s, p in judged]
    below = [s for s, _, _ in placed if s.level < ref_level and s.level in full]
    aligned = sum(1 for s in below if matched[(s.level, s.wing)])
    sparse_unaligned = sum(1 for s, _, _ in placed if s.level < ref_level and s.level not in full
                           and not matched[(s.level, s.wing)])
    # How far the stacked slabs reach beyond (or fall short of) the lidar roof, per side.
    full_slabs = unary_union([lv.slab for lv in levels if lv.level in full])
    sx0, sy0, sx1, sy1 = full_slabs.bounds
    extent_err = max(abs(sx0 - extent[0]), abs(sy0 - extent[1]), abs(sx1 - extent[2]),
                     abs(sy1 - extent[3]))
    quality = {"sheets": len(placed), "levels": len(levels), "full_levels": len(full),
               "extent_error_m": round(float(extent_err), 1),
               "angle_deg": angle, "min_score": round(float(min(scores)), 3) if scores else 0,
               "min_agreement": round(float(min(agree)), 3) if agree else 0,
               "column_aligned_sheets": aligned, "column_alignment_needed": len(below),
               "sparse_unaligned": sparse_unaligned}
    return Layout(number, levels, angle, quality)


def roof_mask_for(poly, rasters, pad: float = 40.0, res: float = 1.0, min_height: float = 2.2):
    """(mask, x0, y0): True where the lidar shows a roof within a footprint (+2 m), on a
    grid of `res` meters that extends `pad` meters beyond the footprint."""
    import shapely

    x0, y0, x1, y1 = poly.bounds
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    nx, ny = int((x1 - x0) / res), int((y1 - y0) / res)
    gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * res, y0 + (np.arange(ny) + 0.5) * res)
    cols = np.clip(((gx - rasters.x0) / rasters.res).astype(int), 0, rasters.shape[1] - 1)
    rows = np.clip(((gy - rasters.y0) / rasters.res).astype(int), 0, rasters.shape[0] - 1)
    height = (rasters.dsm_bldg - rasters.dtm)[rows, cols]
    mask = np.isfinite(height) & (height >= min_height) & shapely.contains_xy(
        poly.buffer(2.0), gx, gy)
    return mask, x0, y0


def layout_ok(quality: dict, corridors: str | None = None) -> bool:
    """Is a registered layout good enough to build from? Every full floor must agree with
    the roof, the stacked slabs must reach the roof's extent, and every level must have
    found the building's column grid."""
    if (not quality or quality.get("min_score", 0) < 0.6 or quality.get("min_agreement", 0) < 0.85
            or quality.get("extent_error_m", 99) > 6.0):
        return False
    # A sparse level (a mostly open ground floor) that found no columns cannot be placed
    # reliably. That only matters when its rooms would show, i.e. with open corridors.
    if quality.get("sparse_unaligned", 0) and corridors != "enclosed":
        return False
    need = quality.get("column_alignment_needed", 0)
    return need == 0 or quality.get("column_aligned_sheets", 0) >= 0.75 * need
