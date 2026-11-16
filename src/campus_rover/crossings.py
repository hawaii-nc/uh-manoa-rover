"""Where a wheeled rover can cross a road, and how well each crossing is supported by data.

The survey has no crosswalk layer. What it has is where the walkable ground meets the road,
the ramps UH mapped, the curbs UH mapped, and a few crossings OpenStreetMap (through Overture)
knows. From that this module finds every place where walkable ground faces walkable ground
across a road and sorts them by evidence:

- `confirmed`  you checked it on the ground (`sites/<site>/crossings.yaml`)
- `marked`     OpenStreetMap / Overture draws a crosswalk there
- `supported`  UH maps a ramp or a gap in the curb on both sides: curbs are only dropped where
               people are meant to cross
- `candidate`  the two sides line up across the road, nothing more. Never used to make a route.

A tier says how sure we are that a crossing exists, not how sure we are of anything else:
`curb_cut` follows the project rule (True only with a mapped ramp or a gap in the curb at that
end, otherwise unknown, never False).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

TIERS = ("confirmed", "marked", "supported", "candidate")
USABLE = ("confirmed", "marked", "supported")      # the planner may use these


@dataclass
class CrossingParams:
    sample_m: float = 1.0           # spacing of test points along the road edge
    edge_band_m: float = 0.75       # walkable ground this close to the road counts as its edge
    min_len_m: float = 3.0          # shorter is a driveway lip, not a crossing
    max_len_m: float = 22.0         # ASSUMPTION: longest road a crossing is looked for across
    on_road_share: float = 0.85     # share of the line that must lie on the road surface
    ramp_radius_m: float = 3.0      # a mapped ramp this close to an end is a curb ramp
    curb_gap_m: float = 1.5         # no mapped curb within this of an end is a gap in the curb
    marked_radius_m: float = 4.0    # a marked crosswalk this close to the middle backs it
    merge_m: float = 3.0            # crossings closer than this, and parallel, are one
    ends_snap_m: float = 2.0        # a confirmed crossing's ends must be this close to the walk


@dataclass
class Crossing:
    a: tuple
    b: tuple
    length_m: float
    tier: str
    evidence: list = field(default_factory=list)
    curb_cut_a: bool | None = None
    curb_cut_b: bool | None = None
    road: str | None = None
    note: str | None = None

    @property
    def curb_cut(self) -> bool | None:
        """True only when both ends have evidence of a cut. False only if you checked and
        wrote it in crossings.yaml; no evidence leaves it unknown (None)."""
        if self.curb_cut_a is False or self.curb_cut_b is False:
            return False
        return True if self.curb_cut_a and self.curb_cut_b else None

    @property
    def mid(self) -> tuple:
        return ((self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2)

    def to_dict(self) -> dict:
        return {"a": [round(self.a[0], 2), round(self.a[1], 2)],
                "b": [round(self.b[0], 2), round(self.b[1], 2)],
                "length_m": round(self.length_m, 1), "tier": self.tier,
                "evidence": list(self.evidence), "curb_cut": self.curb_cut,
                "curb_cut_ends": [self.curb_cut_a, self.curb_cut_b], "road": self.road,
                "note": self.note}


def _unit(dx: float, dy: float):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (0.0, 0.0)


def edge_samples(walk, road, params: CrossingParams):
    """[(point, unit direction pointing from the walkable ground into the road)] along every
    place the walkable ground meets the road."""
    import shapely

    band = road.buffer(params.edge_band_m)
    shapely.prepare(band)
    shapely.prepare(road)
    edge = walk.boundary.intersection(band)
    out = []
    for line in getattr(edge, "geoms", [edge]):
        if line.geom_type != "LineString" or line.length < params.sample_m:
            continue
        for d in np.arange(params.sample_m / 2, line.length, params.sample_m):
            p = line.interpolate(float(d))
            a = line.interpolate(max(0.0, float(d) - 0.5))
            b = line.interpolate(min(line.length, float(d) + 0.5))
            tx, ty = _unit(b.x - a.x, b.y - a.y)
            for nx_, ny_ in ((-ty, tx), (ty, -tx)):       # the normal that points into the road
                if shapely.contains_xy(road, p.x + nx_ * 0.4, p.y + ny_ * 0.4):
                    out.append(((p.x, p.y), (nx_, ny_)))
                    break
    return out


def detect_crossings(walk, road, curbs=None, ramps=None, marked=(), road_lines=(),
                     params: CrossingParams | None = None) -> list[Crossing]:
    """Every place the walkable surface `walk` faces itself across the `road` polygon.

    curbs, ramps: shapely geometry of the mapped curbs and ramps (or None).
    marked: coordinate lists of crosswalks drawn by OpenStreetMap / Overture.
    road_lines: [(name, LineString)] for naming the street a crossing goes over."""
    import shapely
    from shapely.geometry import LineString

    params = params or CrossingParams()
    if walk is None or walk.is_empty or road is None or road.is_empty:
        return []
    shapely.prepare(walk)
    shapely.prepare(road)
    road_grown = road.buffer(0.3)
    shapely.prepare(road_grown)
    found: list[Crossing] = []
    for p, (dx, dy) in edge_samples(walk, road, params):
        start = (p[0] + dx * 0.6, p[1] + dy * 0.6)
        end = (p[0] + dx * params.max_len_m, p[1] + dy * params.max_len_m)
        ray = LineString([start, end])
        hit = ray.intersection(walk)
        if hit.is_empty:
            continue
        best = None
        for part in getattr(hit, "geoms", [hit]):
            for c in part.coords if part.geom_type in ("LineString", "Point") else []:
                d = math.dist(p, c)
                if d >= params.min_len_m and (best is None or d < best[0]):
                    best = (d, c)
        if best is None:
            continue
        length, q = best
        mid_line = LineString([p, q])
        if mid_line.intersection(road_grown).length < params.on_road_share * mid_line.length:
            continue
        # the far side must be walkable ground we are walking onto, not a grazing edge
        if not shapely.contains_xy(walk.buffer(0.05), q[0] + dx * 0.5, q[1] + dy * 0.5):
            continue
        found.append(Crossing(tuple(p), (q[0], q[1]), float(length), "candidate"))
    merged = _merge(found, params)
    for c in merged:
        _grade_evidence(c, curbs, ramps, marked, road_lines, params)
    return sorted(merged, key=lambda c: (TIERS.index(c.tier), c.length_m))


def _merge(found: list[Crossing], params: CrossingParams) -> list[Crossing]:
    """One crossing per place: of the parallel lines closer than `merge_m`, keep the shortest."""
    kept: list[Crossing] = []
    for c in sorted(found, key=lambda c: c.length_m):
        d = _unit(c.b[0] - c.a[0], c.b[1] - c.a[1])
        for k in kept:
            kd = _unit(k.b[0] - k.a[0], k.b[1] - k.a[1])
            if abs(d[0] * kd[0] + d[1] * kd[1]) > 0.85 and \
                    math.dist(c.mid, k.mid) < params.merge_m:
                break
        else:
            kept.append(c)
    return kept


def _grade_evidence(c: Crossing, curbs, ramps, marked, road_lines, p: CrossingParams) -> None:
    """Fill in the evidence and the tier."""
    from shapely.geometry import LineString, Point

    for end, attr in ((c.a, "curb_cut_a"), (c.b, "curb_cut_b")):
        pt = Point(end)
        if ramps is not None and not ramps.is_empty and ramps.distance(pt) <= p.ramp_radius_m:
            setattr(c, attr, True)
            c.evidence.append("ramp")
        elif curbs is not None and not curbs.is_empty and curbs.distance(pt) > p.curb_gap_m:
            setattr(c, attr, True)
            c.evidence.append("curb gap")
    mid = Point(c.mid)
    for coords in marked:
        if len(coords) >= 2 and LineString(coords).distance(mid) <= p.marked_radius_m:
            c.evidence.append("marked crosswalk")
            break
    if road_lines:
        name, _ = min(road_lines, key=lambda nl: nl[1].distance(mid))
        c.road = name
    if "marked crosswalk" in c.evidence:
        c.tier = "marked"
    elif c.curb_cut_a and c.curb_cut_b:
        c.tier = "supported"
    else:
        c.tier = "candidate"


def from_marked(marked, road_lines=(), curbs=None, ramps=None,
                params: CrossingParams | None = None) -> list[Crossing]:
    """Crossings drawn by the map data itself, kept even where the detector found none."""
    params = params or CrossingParams()
    out = []
    for coords in marked:
        if len(coords) < 2:
            continue
        c = Crossing(tuple(coords[0]), tuple(coords[-1]),
                     math.dist(coords[0], coords[-1]), "candidate")
        _grade_evidence(c, curbs, ramps, [coords], road_lines, params)
        out.append(c)
    return out


def load_confirmed(path, params: CrossingParams | None = None) -> list[Crossing]:
    """Crossings you have checked on the ground. `sites/<site>/crossings.yaml`:

        crossings:
          - {from: [x, y], to: [x, y], road: "Campus Rd", curb_cut: true,
             source: "walked 2026-11-20"}

    `curb_cut` may be true, false or omitted (unknown). False is only ever set by you."""
    import yaml

    if not path.exists():
        return []
    out = []
    for item in (yaml.safe_load(path.read_text()) or {}).get("crossings", []):
        a, b = tuple(item["from"]), tuple(item["to"])
        c = Crossing(a, b, math.dist(a, b), "confirmed", ["field check"], road=item.get("road"),
                     note=item.get("source"))
        cut = item.get("curb_cut")
        c.curb_cut_a = c.curb_cut_b = cut if cut is not None else None
        out.append(c)
    return out


def combine(detected: list[Crossing], marked: list[Crossing],
            confirmed: list[Crossing], merge_m: float = 3.0) -> list[Crossing]:
    """Confirmed beats marked beats detected where they are the same crossing."""
    out = list(confirmed)
    for group in (marked, detected):
        for c in group:
            if any(math.dist(c.mid, k.mid) < merge_m for k in out):
                continue
            out.append(c)
    return sorted(out, key=lambda c: (TIERS.index(c.tier), c.length_m))
