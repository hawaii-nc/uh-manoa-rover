import pytest

from campus_rover.geometry import (
    ccw,
    centroid,
    point_in_polygon,
    point_ring_distance,
    segment_touches_polygon,
    segments_intersect,
    signed_area,
    triangulate,
)

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
L_SHAPE = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]  # concave


def test_area_orientation_and_centroid():
    assert signed_area(SQUARE) == 100
    assert signed_area(SQUARE[::-1]) == -100
    assert signed_area(ccw(SQUARE[::-1] + [SQUARE[-1]])) > 0
    assert centroid(SQUARE) == pytest.approx((5, 5))


def test_point_in_polygon_with_hole():
    hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
    assert point_in_polygon((2, 2), [SQUARE])
    assert not point_in_polygon((5, 5), [SQUARE, hole])
    assert not point_in_polygon((15, 5), [SQUARE])


def test_segments():
    assert segments_intersect((0, 0), (10, 10), (0, 10), (10, 0))
    assert not segments_intersect((0, 0), (1, 1), (2, 2), (3, 0))
    assert segments_intersect((0, 0), (5, 0), (5, 0), (5, 5))  # touching endpoint
    assert segment_touches_polygon((-5, 5), (15, 5), [SQUARE])  # crosses through
    assert segment_touches_polygon((2, 2), (3, 3), [SQUARE])  # fully inside
    assert not segment_touches_polygon((-5, -5), (-1, 20), [SQUARE])


def test_point_ring_distance():
    assert point_ring_distance((5, 5), SQUARE) == 0
    assert point_ring_distance((13, 5), SQUARE) == pytest.approx(3)


@pytest.mark.parametrize("ring", [SQUARE, L_SHAPE, L_SHAPE[::-1]])
def test_triangulate_covers_area(ring):
    pts = ccw(ring)
    tris = triangulate(ring)
    assert len(tris) == len(pts) - 2
    total = sum(abs(signed_area([pts[a], pts[b], pts[c]])) for a, b, c in tris)
    assert total == pytest.approx(abs(signed_area(ring)))
    assert all(signed_area([pts[a], pts[b], pts[c]]) > 0 for a, b, c in tris)  # all ccw
