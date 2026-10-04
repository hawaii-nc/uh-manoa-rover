import math

import networkx as nx
import pytest

from campus_rover.osm import match_landmarks, osm_to_campus_graph, parse_incline


@pytest.mark.parametrize(
    ("tag", "expected"),
    [(None, 0.0), ("5%", 0.05), ("-8%", -0.08), ("up", 0.0), (["3%", "-6%"], -0.06)],
)
def test_parse_incline(tag, expected):
    assert parse_incline(tag) == pytest.approx(expected)


def test_parse_incline_degrees():
    assert parse_incline("10°") == pytest.approx(math.tan(math.radians(10)))


def fake_osmnx_graph() -> nx.MultiDiGraph:
    """Mimics a projected osmnx graph: int node ids, x/y, per-direction edges."""
    g = nx.MultiDiGraph()
    for n, (x, y) in {1: (0, 0), 2: (50, 0), 3: (50, 40), 4: (0, 40)}.items():
        g.add_node(n, x=x, y=y)
    g.add_edge(1, 2, length=50.0, highway="footway", name="McCarthy Mall")
    g.add_edge(2, 1, length=50.0, highway="footway", name="McCarthy Mall")
    g.add_edge(1, 2, length=70.0, highway="service")  # longer parallel way
    g.add_edge(2, 3, length=40.0, highway="steps")
    g.add_edge(3, 4, length=50.0, highway="footway", footway="crossing")
    g.add_edge(
        4, 1, length=40.0, highway=["residential", "footway"], incline="6%", name=["Dole St", "x"]
    )
    g.add_edge(4, 4, length=1.0, highway="footway")  # self-loop
    return g


def test_osm_to_campus_graph():
    cg = osm_to_campus_graph(fake_osmnx_graph())
    g = cg.g
    assert set(g.nodes) == {"1", "2", "3", "4"}
    assert g.number_of_edges() == 4
    assert g.edges["1", "2"]["length"] == 50.0  # shortest parallel kept
    assert g.edges["1", "2"]["name"] == "McCarthy Mall"
    assert g.edges["2", "3"]["kind"] == "stairs"
    assert g.edges["3", "4"]["kind"] == "crosswalk"
    assert g.edges["4", "1"]["zone"] == "roadside"
    assert g.edges["4", "1"]["slope"] == pytest.approx(0.06)
    assert g.edges["4", "1"]["name"] == "Dole St"


def test_match_landmarks():
    cg = osm_to_campus_graph(fake_osmnx_graph())
    features = [
        ("Holmes Hall", 48, 2),
        ("School of Architecture", 1, 39),
        ("Campus Center", 49, 41),
    ]
    specs = {
        "holmes_hall": {"osm_name": "Holmes Hall"},
        "architecture": {"osm_name": "Architecture"},
        "campus_center": {"osm_name": "campus center"},
        "missing": {"osm_name": "Nowhere Hall"},
        "manual": {"node": "1"},
    }
    found, missing = match_landmarks(features, specs, cg)
    assert found == {"holmes_hall": "2", "architecture": "4", "campus_center": "3"}
    assert missing == ["missing"]
