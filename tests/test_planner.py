import pytest

from campus_rover.crowd import CrowdModel
from campus_rover.graph import CampusGraph
from campus_rover.planner import SHORTEST, NoRouteError, RoutePlanner, compare
from campus_rover.schedule import ClassSchedule, WeekTime
from campus_rover.site import PlannerParams

QUIET = WeekTime.parse("Mon 09:00")
RUSH = WeekTime.parse("Mon 09:20")  # rover reaches the mall right at the 09:23 peak


def route_a(planner, t, **kw):
    return planner.plan(["eng", "center"], t, **kw)


def test_quiet_time_takes_the_mall(demo_planner):
    plan = route_a(demo_planner, QUIET)
    assert "mall_w" in plan.nodes and "s1" not in plan.nodes


def test_class_change_detours_south(demo_planner):
    plan = route_a(demo_planner, RUSH)
    assert "s1" in plan.nodes and "mall_w" not in plan.nodes


def test_never_uses_stairs_closed_or_steep_edges(demo_planner):
    for t in [QUIET, RUSH, WeekTime.parse("Tue 12:05"), WeekTime.parse("Sat 07:00")]:
        for mode in ("crowd_aware", SHORTEST):
            plan = demo_planner.plan(["eng", "center", "design"], t, mode=mode)
            for leg in plan.legs:
                data = demo_planner.graph.g.edges[leg.u, leg.v]
                assert demo_planner.why_impassable(data) is None, (str(t), leg)


def test_stairs_allowed_gives_shortest_path(demo_site):
    demo_site.planner.allow_stairs = True
    planner = RoutePlanner.from_site(demo_site)
    plan = planner.plan(["eng", "center"], WeekTime.parse("Sat 07:00"), mode=SHORTEST)
    assert "st" in plan.nodes
    assert plan.distance_m == pytest.approx(500.0)


def test_lunch_detours_route_b(demo_planner):
    lunch = demo_planner.plan(["center", "design"], WeekTime.parse("Tue 12:05"))
    morning = demo_planner.plan(["center", "design"], WeekTime.parse("Tue 08:00"))
    assert "c2" in lunch.nodes
    assert "plaza" in morning.nodes


def test_multi_waypoint_chains_time(demo_planner):
    plan = demo_planner.plan(["eng", "center", "design"], QUIET)
    assert plan.nodes[0] == "eng" and plan.nodes[-1] == "design" and "center" in plan.nodes
    # Each leg starts when the previous one ended.
    for prev, nxt in zip(plan.legs, plan.legs[1:], strict=False):
        assert nxt.enter == prev.enter.plus_seconds(prev.duration_s)
    assert plan.arrive == QUIET.plus_seconds(plan.duration_s)


def test_crowds_slow_the_rover(demo_planner):
    calm = demo_planner.evaluate("mall_w", "mall_e", QUIET)
    busy = demo_planner.evaluate("mall_w", "mall_e", WeekTime.parse("Mon 09:23"))
    assert busy.duration_s > calm.duration_s
    assert busy.cost > calm.cost


def test_comparison_explains_detour(demo_planner):
    result = compare(demo_planner, ["eng", "center"], RUSH)
    assert not result.same_route
    assert result.chosen.crowd_exposure < result.baseline.crowd_exposure
    assert result.chosen.cost < result.baseline.cost
    assert any("Central Mall" in r for r in result.reasons)

    quiet = compare(demo_planner, ["eng", "center"], QUIET)
    assert quiet.same_route


def test_no_route_raises():
    graph = CampusGraph.from_dict(
        {
            "nodes": {"a": {"xy": [0, 0]}, "b": {"xy": [10, 0]}},
            "edges": [{"u": "a", "v": "b", "kind": "stairs"}],
        }
    )
    crowd = CrowdModel(graph, ClassSchedule([]), [], {})
    planner = RoutePlanner(graph, crowd, PlannerParams())
    with pytest.raises(NoRouteError):
        planner.plan(["a", "b"], QUIET)
    with pytest.raises(NoRouteError):
        planner.plan(["a", "zzz"], QUIET)
    with pytest.raises(ValueError):
        planner.plan(["a"], QUIET)
