"""Small 2D geometry helpers (no shapely dependency). Coordinates are meters."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]
Ring = list[Point]


def signed_area(ring: Sequence[Point]) -> float:
    """Shoelace area. Positive = counter-clockwise (y up)."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def open_ring(ring: Sequence[Point]) -> Ring:
    """Drop the closing vertex if the ring repeats its first point."""
    pts = [(float(x), float(y)) for x, y in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def ccw(ring: Sequence[Point]) -> Ring:
    pts = open_ring(ring)
    return pts if signed_area(pts) >= 0 else pts[::-1]


def centroid(ring: Sequence[Point]) -> Point:
    pts = open_ring(ring)
    a = signed_area(pts)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    cx = cy = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6 * a), cy / (6 * a))


def point_in_ring(p: Point, ring: Sequence[Point]) -> bool:
    """Ray casting; points exactly on the boundary may go either way."""
    x, y = p
    inside = False
    pts = open_ring(ring)
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygon(p: Point, rings: Sequence[Sequence[Point]]) -> bool:
    """rings[0] is the outer ring, the rest are holes."""
    if not rings or not point_in_ring(p, rings[0]):
        return False
    return not any(point_in_ring(p, hole) for hole in rings[1:])


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True
    return (
        (d1 == 0 and _on_segment(q1, q2, p1))
        or (d2 == 0 and _on_segment(q1, q2, p2))
        or (d3 == 0 and _on_segment(p1, p2, q1))
        or (d4 == 0 and _on_segment(p1, p2, q2))
    )


def segment_touches_polygon(a: Point, b: Point, rings: Sequence[Sequence[Point]]) -> bool:
    """True if segment ab is inside the polygon or crosses any of its rings."""
    if point_in_polygon(a, rings) or point_in_polygon(b, rings):
        return True
    for ring in rings:
        pts = open_ring(ring)
        for i in range(len(pts)):
            if segments_intersect(a, b, pts[i], pts[(i + 1) % len(pts)]):
                return True
    return False


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def point_ring_distance(p: Point, ring: Sequence[Point]) -> float:
    """Distance to the ring's boundary (0 if inside)."""
    if point_in_ring(p, ring):
        return 0.0
    pts = open_ring(ring)
    return min(point_segment_distance(p, pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def _in_triangle(p: Point, a: Point, b: Point, c: Point, eps: float = 1e-9) -> bool:
    """Inside or on the boundary of ccw triangle abc."""
    return _orient(a, b, p) >= -eps and _orient(b, c, p) >= -eps and _orient(c, a, p) >= -eps


def triangulate(ring: Sequence[Point]) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon (no holes).

    Returns index triples into ccw(ring), wound counter-clockwise.
    """
    pts = ccw(ring)
    idx = list(range(len(pts)))
    tris: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * len(pts) ** 2:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if _orient(a, b, c) <= 1e-12:
                continue  # reflex or degenerate corner
            if any(
                _in_triangle(pts[j], a, b, c)
                for j in idx
                if j not in (i0, i1, i2) and pts[j] not in (a, b, c)
            ):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break  # no ear found (self-intersecting input); fall back below
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    elif len(idx) > 3:  # degenerate input: fan the rest so we still emit a surface
        tris.extend((idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1))
    return tris
