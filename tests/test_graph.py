import math

import pytest

from campus_rover.graph import CampusGraph, Closure


def small_graph() -> CampusGraph:
    return CampusGraph.from_dict(
        {
            "nodes": {
                "a": {"xy": [0, 0], "label": "A"},
                "b": {"xy": [30, 40]},
                "c": {"xy": [100, 0]},
            },
            "edges": [
                {"u": "a", "v": "b"},
                {"u": "b", "v": "c", "kind": "stairs", "length": 12},
                {"u": "a", "v": "c", "zone": "mall", "slope": 0.05},
            ],
        }
    )


def test_lengths_default_to_straight_line():
    g = small_graph().g
    assert g.edges["a", "b"]["length"] == pytest.approx(50.0)
    assert g.edges["b", "c"]["length"] == 12.0
    assert g.edges["a", "c"]["kind"] == "walkway"
    assert g.edges["a", "c"]["closed"] is False


def test_rejects_unknown_node_and_kind():
    with pytest.raises(ValueError, match="unknown node"):
        CampusGraph.from_dict({"nodes": {"a": {"xy": [0, 0]}}, "edges": [{"u": "a", "v": "z"}]})
    with pytest.raises(ValueError, match="unknown kind"):
        CampusGraph.from_dict(
            {
                "nodes": {"a": {"xy": [0, 0]}, "b": {"xy": [1, 0]}},
                "edges": [{"u": "a", "v": "b", "kind": "zipline"}],
            }
        )


def test_round_trip():
    g1 = small_graph()
    g1.landmarks["home"] = "a"
    g2 = CampusGraph.from_dict(g1.to_dict())
    assert set(g2.g.edges) == set(g1.g.edges)
    assert g2.g.edges["b", "c"]["kind"] == "stairs"
    assert g2.landmarks == {"home": "a"}


def test_closures():
    g = small_graph()
    n = g.apply_closures([Closure(reason="event", edge=("a", "b"))], {})
    assert n == 1
    assert g.g.edges["a", "b"]["closed_reason"] == "event"

    g = small_graph()
    g.apply_closures([Closure(reason="construction", near_landmark="lm", radius_m=10)], {"lm": "c"})
    assert g.g.edges["b", "c"]["closed"] and g.g.edges["a", "c"]["closed"]
    assert not g.g.edges["a", "b"]["closed"]


def test_nearest_node_and_landmarks():
    g = small_graph()
    assert g.nearest_node(95, 5) == "c"
    assert g.resolve_landmark("x", {"node": "b"}) == "b"
    with pytest.raises(KeyError):
        g.resolve_landmark("x", {"osm_name": "Nowhere"})
    assert math.isclose(g.midpoint("a", "c")[0], 50)
