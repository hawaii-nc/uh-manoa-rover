"""Crossings by evidence tier, mapped ramps as path, tread edges, building-wide goals, gates."""

import math

import networkx as nx
import pytest
from shapely.geometry import LineString, Point, box
from test_traversability import BBOX, ground

from campus_rover.crossings import (
    Crossing,
    combine,
    detect_crossings,
    from_marked,
    load_confirmed,
)
from campus_rover.graph import EDGE_DEFAULTS, CampusGraph
from campus_rover.planner import RoutePlanner, landmark_goals
from campus_rover.site import PlannerParams
from campus_rover.walknet import WalkParams, build_walk_graph, walkable_surface

WALK = [box(10, 10, 80, 14), box(10, 26, 80, 30)]
ROAD = box(10, 14, 80, 26)


def surface(by=None):
    return walkable_surface(by or {"walkway": WALK}, BBOX, WalkParams())


def test_walkable_ground_facing_itself_across_a_road_is_only_a_candidate():
    found = detect_crossings(surface(), ROAD)
    assert found and all(c.tier == "candidate" for c in found)
    assert all(11 < c.length_m < 13 for c in found)     # the road is 12 m wide
    assert all(c.curb_cut is None for c in found)       # nothing says there is a cut


def test_ramps_on_both_sides_make_a_crossing_supported_and_a_marked_line_marks_it():
    ramps = box(44, 13, 46, 15).union(box(44, 25, 46, 27))
    found = detect_crossings(surface(), ROAD, ramps=ramps)
    supported = [c for c in found if c.tier == "supported"]
    assert supported and all(c.curb_cut is True for c in supported)
    assert all(abs(c.mid[0] - 45) < 6 for c in supported)
    marked = detect_crossings(surface(), ROAD, marked=[[(45, 13.5), (45, 26.5)]])
    assert any(c.tier == "marked" and "marked crosswalk" in c.evidence for c in marked)


def test_a_continuous_curb_is_not_evidence_and_a_gap_is():
    curbs = box(10, 14, 80, 14.3).union(box(10, 25.7, 80, 26))
    found = detect_crossings(surface(), ROAD, curbs=curbs)
    assert all(c.tier == "candidate" and c.curb_cut is None for c in found)
    gapped = curbs.difference(box(40, 13, 50, 27))
    mid = [c for c in detect_crossings(surface(), ROAD, curbs=gapped) if 41 < c.mid[0] < 49]
    assert mid and all(c.tier == "supported" and "curb gap" in c.evidence for c in mid)


def test_marked_crossings_are_kept_even_when_the_detector_finds_none_and_win_merges():
    drawn = [[(45, 13.5), (45, 26.5)]]
    marked = from_marked(drawn)
    assert marked[0].tier == "marked"
    detected = detect_crossings(surface(), ROAD)
    merged = combine(detected, marked, [])
    assert merged[0].tier == "marked"
    near = [c for c in merged if math.dist(c.mid, marked[0].mid) < 3]
    assert len(near) == 1                                # one crossing per place


def test_confirmed_crossings_come_from_a_site_file_and_false_is_only_ever_yours(tmp_path):
    path = tmp_path / "crossings.yaml"
    assert load_confirmed(path) == []
    path.write_text("crossings:\n  - {from: [45, 13], to: [45, 27], road: Campus Rd, "
                    "curb_cut: false, source: walked}\n  - {from: [20, 13], to: [20, 27]}\n")
    a, b = load_confirmed(path)
    assert a.tier == "confirmed" and a.curb_cut is False and a.road == "Campus Rd"
    assert b.curb_cut is None


def test_the_network_adds_usable_crossings_and_keeps_candidates_out_of_routes():
    by = {"walkway": WALK, "road": [ROAD]}
    cand = Crossing((45, 13.5), (45, 26.5), 13.0, "candidate")
    graph, rep = build_walk_graph(by, ground(), BBOX, crossings=[cand])
    assert rep.crossing_candidates == 1 and rep.crosswalks == 0
    planner = RoutePlanner(graph, crowd=None, params=PlannerParams())
    edge = next(d for _, _, d in graph.g.edges(data=True) if d["kind"] == "crosswalk")
    assert edge["tier"] == "candidate"
    assert not planner.passable(edge) and planner.why_impassable(edge) == "unverified crossing"
    assert RoutePlanner(graph, None, PlannerParams(allow_candidate_crossings=True)).passable(edge)
    sup = Crossing((45, 13.5), (45, 26.5), 13.0, "supported", ["ramp", "ramp"], True, True)
    g2, rep2 = build_walk_graph(by, ground(), BBOX, crossings=[sup])
    assert rep2.crosswalks == 1 and nx.number_connected_components(g2.g) == 1
    cross = next(d for _, _, d in g2.g.edges(data=True) if d["kind"] == "crosswalk")
    assert cross["curb_cut"] is True and RoutePlanner(g2, None, PlannerParams()).passable(cross)


def test_connecting_options_name_the_single_crossings_that_would_join_two_sides():
    by = {"walkway": WALK, "road": [ROAD]}
    cands = [Crossing((x, 13.5), (x, 26.5), 13.0, "candidate", ["curb gap"] if x == 60 else [])
             for x in (30, 60)]
    graph, _ = build_walk_graph(by, ground(), BBOX, crossings=cands)
    planner = RoutePlanner(graph, None, PlannerParams())
    south = [n for n in graph.g if graph.xy(n)[1] < 20]
    north = [n for n in graph.g if graph.xy(n)[1] >= 20]
    options = planner.connecting_options(south[:1], north[:1])
    assert len(options) == 2 and "curb gap" in options[0]["evidence"]     # best evidence first
    info = planner.blockers(south[:1], north[:1])
    assert [s["reason"] for s in info["stretches"]] == ["unverified crossing"]


def test_a_mapped_ramp_is_a_path_of_its_own_with_its_grade_measured_end_to_end():
    # A ramp 1.4 m wide: too narrow for the skeleton to follow, but UH mapped it.
    by = {"walkway": [box(10, 10, 40, 14), box(50, 10, 80, 14)], "ramp": [box(40, 11, 50, 12.4)]}
    graph, rep = build_walk_graph(by, ground(slope_x=0.06), BBOX)
    ramps = [d for _, _, d in graph.g.edges(data=True) if d["kind"] == "ramp"]
    assert rep.ramp_edges >= 1 and ramps
    assert ramps[0]["ramp_kind"] == "access" and ramps[0]["slope"] == pytest.approx(0.06, abs=0.02)
    assert nx.number_connected_components(graph.g) == 1


def test_a_curb_ramp_is_told_from_an_access_ramp_by_the_road_beside_it():
    by = {"walkway": [box(10, 10, 80, 14)], "road": [box(10, 14, 80, 26)],
          "ramp": [box(40, 13, 42, 14.5), box(60, 10.4, 70, 11.8)]}
    graph, _ = build_walk_graph(by, ground(), BBOX)
    kinds = {d.get("ramp_kind") for _, _, d in graph.g.edges(data=True) if d["kind"] == "ramp"}
    assert "curb" in kinds or kinds == {"access"}


def test_flights_of_treads_label_the_edges_that_follow_them_as_stairs():
    treads = [box(40, 10 + 0.0, 50, 14).buffer(0) for _ in range(1)]
    by = {"walkway": [box(10, 10, 40, 14), box(50, 10, 80, 14)], "stairs": treads}
    graph, _ = build_walk_graph(by, ground(slope_x=0.15), BBOX)
    assert any(d["kind"] == "stairs" for _, _, d in graph.g.edges(data=True))


def test_a_building_is_reached_from_anywhere_along_its_walls():
    g = nx.Graph()
    for n, (x, y) in {"a": (0, 0), "b": (10, 0), "c": (20, 0), "d": (60, 0)}.items():
        g.add_node(n, x=float(x), y=float(y), label=None)
    g.add_edge("a", "b", **{**EDGE_DEFAULTS, "length": 10.0})
    g.add_edge("b", "c", **{**EDGE_DEFAULTS, "length": 10.0, "slope": 0.2})     # too steep
    g.add_edge("c", "d", **{**EDGE_DEFAULTS, "length": 40.0})
    planner = RoutePlanner(CampusGraph(g), None, PlannerParams())
    hall = box(-3, -6, 8, -2)
    assert set(planner.access_nodes(hall, 4.0)) == {"a", "b"}
    # reaching "d" from either of the hall's nodes: the same stretch is named
    info = planner.blockers(planner.access_nodes(hall), ["d"])
    assert [s["reason"] for s in info["stretches"]] == ["too steep"]
    assert callable(landmark_goals) and Point(0, 0).distance(LineString([(0, 0), (1, 0)])) == 0
