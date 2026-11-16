"""Walkway network, traversability labels, planner diagnosis, collision and friction."""

import math

import networkx as nx
import numpy as np
import pytest
from shapely.geometry import box

from campus_rover.graph import CampusGraph
from campus_rover.lidar import Rasters
from campus_rover.physics import (
    collision_for,
    collision_stats,
    friction_for,
    sample_friction,
)
from campus_rover.planner import NoRouteError, RoutePlanner
from campus_rover.schedule import WeekTime
from campus_rover.site import PlannerParams
from campus_rover.traversal import (
    BLOCKED,
    CONDITIONAL,
    TRAVERSABLE,
    UNKNOWN_CODE,
    Limits,
    build_traversability,
    cost_at,
    level_of,
    load_grid,
    save_grid,
    summarize,
)
from campus_rover.walknet import (
    WalkParams,
    build_walk_graph,
    clean_ground,
    landmark_connectivity,
    skeleton_chains,
)

BBOX = (0.0, 0.0, 120.0, 100.0)


def ground(slope_x: float = 0.0, res: float = 0.5, bbox=BBOX) -> Rasters:
    """A ground grid rising `slope_x` per meter along x."""
    rows, cols = int((bbox[3] - bbox[1]) / res), int((bbox[2] - bbox[0]) / res)
    xs = bbox[0] + (np.arange(cols) + 0.5) * res
    dtm = np.tile(slope_x * xs, (rows, 1))
    nan = np.full((rows, cols), np.nan)
    return Rasters(bbox[0], bbox[1], res, dtm, nan, nan.copy(), np.full((rows, cols), np.inf),
                   np.ones((rows, cols), dtype=np.uint16))


def length_of(graph: CampusGraph, kind: str) -> float:
    return sum(d["length"] for _, _, d in graph.g.edges(data=True) if d["kind"] == kind)


# ---- walkway network ---------------------------------------------------------------------


def test_l_shaped_path_becomes_one_connected_centerline():
    by = {"walkway": [box(10, 10, 70, 13), box(67, 10, 70, 70)]}          # 3 m wide, ~110 m long
    graph, rep = build_walk_graph(by, ground(), BBOX)
    assert nx.number_connected_components(graph.g) == 1
    assert 95 < length_of(graph, "walkway") < 125
    widths = [d["width"] for _, _, d in graph.g.edges(data=True) if d.get("width")]
    assert np.median(widths) == pytest.approx(3.0, abs=0.7)
    assert rep.steep_share == 0.0


def test_strips_narrower_than_the_rover_are_not_paths():
    by = {"walkway": [box(10, 10, 60, 13), box(10, 40, 60, 40.5)]}        # 3 m and 0.5 m wide
    graph, _ = build_walk_graph(by, ground(), BBOX, params=WalkParams(min_width_m=1.0))
    ys = [graph.xy(n)[1] for n in graph.g.nodes]
    assert max(ys) < 20 and len(ys) > 0                                    # only the wide one


def test_a_wall_is_never_bridged_by_gap_healing():
    by = {"walkway": [box(10, 10, 100, 13)], "wall": [box(50, 9, 50.3, 14)]}
    graph, rep = build_walk_graph(by, ground(), BBOX)
    left = [n for n in graph.g if graph.xy(n)[0] < 49]
    right = [n for n in graph.g if graph.xy(n)[0] > 51]
    assert left and right
    assert not any(min(graph.xy(u)[0], graph.xy(v)[0]) < 50.15 < max(graph.xy(u)[0],
                                                                      graph.xy(v)[0])
                   for u, v in graph.g.edges)           # no edge runs across the wall
    assert rep.gap_links == 0                                              # a wall is not grass


def test_breaks_over_grass_are_bridged_but_flagged_and_roads_are_not():
    by = {"walkway": [box(10, 10, 40, 13), box(46, 10, 80, 13), box(10, 60, 40, 63),
                      box(46, 60, 80, 63)],
          "grass": [box(40, 5, 46, 18)], "road": [box(40, 55, 46, 68)]}
    graph, rep = build_walk_graph(by, ground(), BBOX)
    gaps = [d for _, _, d in graph.g.edges(data=True) if d.get("gap")]
    assert len(gaps) == 1 and gaps[0]["surface"] == "grass" and gaps[0]["source"] == "gap_repair"
    assert rep.components_after == 3            # the grass pair joined; the road pair did not


def test_stairs_become_stairs_edges_the_planner_will_not_use():
    treads = [box(41, 40 + 0.4 * i, 44, 40.4 + 0.4 * i) for i in range(6)]   # a flight 3 m wide
    # Two paths 2.4 m apart (the flight's length), joined only by the stairs.
    by = {"walkway": [box(41, 10, 44, 40), box(41, 42.4, 44, 80)], "stairs": treads}
    graph, rep = build_walk_graph(by, ground(), BBOX)
    kinds = {d["kind"] for _, _, d in graph.g.edges(data=True)}
    assert "stairs" in kinds and rep.stairs_edges >= 1
    planner = RoutePlanner(graph, crowd=None, params=PlannerParams())
    stair = next(d for _, _, d in graph.g.edges(data=True) if d["kind"] == "stairs")
    assert not planner.passable(stair)


def test_crosswalks_join_the_two_sides_and_curb_cuts_stay_unknown_without_evidence():
    by = {"walkway": [box(10, 10, 80, 14), box(10, 26, 80, 30)], "road": [box(10, 14, 80, 26)],
          "curb": [box(10, 14, 80, 14.3), box(10, 25.7, 80, 26)]}
    graph, rep = build_walk_graph(by, ground(), BBOX, crosswalks=[[(45, 13.5), (45, 26.5)]])
    cross = [d for _, _, d in graph.g.edges(data=True) if d["kind"] == "crosswalk"]
    assert len(cross) == 1 and rep.crosswalks == 1
    assert cross[0]["curb_cut"] is None                 # a curb beside it proves nothing either way
    assert nx.number_connected_components(graph.g) == 1
    open_ends = dict(by, curb=[])
    g2, _ = build_walk_graph(open_ends, ground(), BBOX, crosswalks=[[(45, 13.5), (45, 26.5)]])
    cuts = [d["curb_cut"] for _, _, d in g2.g.edges(data=True) if d["kind"] == "crosswalk"]
    assert cuts == [True]


def test_grade_is_measured_from_the_ground_and_ramps_are_marked():
    by = {"walkway": [box(10, 10, 90, 13)], "ramp": [box(50, 10, 70, 13)]}
    graph, rep = build_walk_graph(by, ground(slope_x=0.10), BBOX)
    slopes = [d["slope"] for _, _, d in graph.g.edges(data=True)]
    assert np.median(slopes) == pytest.approx(0.10, abs=0.02)
    assert any(d["kind"] == "ramp" for _, _, d in graph.g.edges(data=True))
    assert rep.steep_share > 0.9                         # 10 % is over the 8.3 % limit


def test_ground_next_to_a_building_is_not_trusted_for_grades():
    r = ground()
    b = box(40, 40, 60, 60)
    ys, xs = (np.arange(r.dtm.shape[0]) * 0.5)[:, None], (np.arange(r.dtm.shape[1]) * 0.5)[None, :]
    r.dtm[(ys >= 38) & (ys <= 62) & (xs >= 38) & (xs <= 62)] += 3.0
    cleaned = clean_ground(r, [b], margin_m=3.0, smooth_m=0.5)
    row, col = int(39 / 0.5), int(39 / 0.5)               # 1 m outside the footprint
    assert cleaned[row, col] < r.dtm[row, col] - 2.0      # the +3 m lump beside the wall is gone


def test_skeleton_of_a_plus_shape_has_one_junction():
    mask = np.zeros((90, 90), dtype=bool)
    mask[40:50, 10:80] = True
    mask[10:80, 40:50] = True
    nodes, chains = skeleton_chains(mask, 0.25, spur_m=2.0)
    degree = np.zeros(len(nodes), dtype=int)
    for a, b, _ in chains:
        degree[a] += 1
        degree[b] += 1
    assert (degree >= 3).sum() == 1 and (degree == 1).sum() == 4


def test_landmark_connectivity_reports_pieces():
    g = nx.Graph()
    for i, (x, y) in enumerate([(0, 0), (10, 0), (50, 0), (60, 0)]):
        g.add_node(f"n{i}", x=x, y=y, label=None)
    g.add_edge("n0", "n1", kind="walkway", length=10.0)
    g.add_edge("n2", "n3", kind="walkway", length=10.0)
    graph = CampusGraph(g)
    near = landmark_connectivity(graph, {"A": box(-1, -1, 1, 1), "B": box(9, -1, 11, 1)})
    far = landmark_connectivity(graph, {"A": box(-1, -1, 1, 1), "C": box(59, -1, 61, 1)})
    assert near["all_connected"] and not far["all_connected"]


# ---- planner diagnosis -------------------------------------------------------------------


class NoCrowd:
    def density(self, u, v, t):
        return 0.0


def blocked_graph():
    g = nx.Graph()
    for i in range(5):
        g.add_node(f"n{i}", x=10.0 * i, y=0.0, label=None)
    edges = [("n0", "n1", 0.02, False), ("n1", "n2", 0.20, False), ("n2", "n3", 0.30, False),
             ("n3", "n4", 0.01, True)]
    from campus_rover.graph import EDGE_DEFAULTS

    for u, v, slope, closed in edges:
        g.add_edge(u, v, **{**EDGE_DEFAULTS, "kind": "walkway", "length": 10.0, "slope": slope,
                            "closed": closed, "closed_reason": "construction"})
    return CampusGraph(g)


def test_blockers_merge_consecutive_stretches_and_name_the_reason():
    planner = RoutePlanner(blocked_graph(), crowd=NoCrowd(), params=PlannerParams())
    info = planner.blockers("n0", "n4")
    reasons = [s["reason"] for s in info["stretches"]]
    assert reasons == ["too steep", "closed"] and info["length_m"] == pytest.approx(40.0)
    assert info["stretches"][0]["length_m"] == pytest.approx(20.0)         # 20 % and 30 % merged
    assert info["stretches"][0]["max_grade"] == pytest.approx(0.30)
    with pytest.raises(NoRouteError, match="least-blocked route is 40 m.*2 impassable"):
        planner._search("n0", "n4", WeekTime.parse("Mon 09:00"), "shortest")


def test_a_map_with_two_pieces_says_how_close_they_come():
    g = nx.Graph()
    for n, (x, y) in {"a": (0, 0), "b": (10, 0), "c": (14, 0), "d": (30, 0)}.items():
        g.add_node(n, x=float(x), y=float(y), label=None)
    from campus_rover.graph import EDGE_DEFAULTS

    g.add_edge("a", "b", **{**EDGE_DEFAULTS, "length": 10.0})
    g.add_edge("c", "d", **{**EDGE_DEFAULTS, "length": 16.0})
    planner = RoutePlanner(CampusGraph(g), crowd=None, params=PlannerParams())
    with pytest.raises(NoRouteError, match=r"within 4\.0 m"):
        planner.blockers("a", "d")


def test_narrow_edges_and_unverified_curb_cuts_follow_the_rovers_rules():
    graph = blocked_graph()
    narrow = {"kind": "walkway", "closed": False, "slope": 0.0, "width": 0.8}
    strict = RoutePlanner(graph, crowd=None, params=PlannerParams(min_width_m=1.0))
    assert not strict.passable(narrow) and "too narrow" in strict.why_impassable(narrow)
    assert RoutePlanner(graph, crowd=None, params=PlannerParams()).passable(narrow)
    cross = {"kind": "crosswalk", "closed": False, "slope": 0.0, "curb_cut": False}
    unknown = dict(cross, curb_cut=None)
    needs = RoutePlanner(graph, crowd=None, params=PlannerParams(require_curb_cut=True))
    assert not needs.passable(cross) and needs.passable(unknown)           # unknown is not "no"


# ---- traversability labels ---------------------------------------------------------------


def test_every_class_has_a_level_and_unknown_classes_are_blocked():
    assert level_of("walkway") == TRAVERSABLE and level_of("grass") == CONDITIONAL
    assert level_of("curb") == BLOCKED and level_of("stairs") == BLOCKED
    assert level_of("person") == "dynamic" and level_of("not_a_class") == BLOCKED


def test_ground_raster_labels_slope_and_inflated_cost():
    feats = [(box(0, 0, 60, 100), "grass", None), (box(0, 40, 60, 46), "walkway", None),
             (box(0, 46, 60, 46.3), "curb", None)]
    grid = build_traversability(feats, ground(), BBOX, [box(80, 20, 100, 40)], [(70, 70, 0.3)],
                                Limits(), res=0.5)
    at = lambda x, y: (int((y - grid["y0"]) / 0.5), int((x - grid["x0"]) / 0.5))   # noqa: E731
    lv = grid["level"]
    assert lv[at(30, 43)] == 2 and lv[at(30, 20)] == 1 and lv[at(30, 46.1)] == 0
    assert lv[at(90, 30)] == 0 and lv[at(70, 70)] == 0 and lv[at(110, 90)] == UNKNOWN_CODE
    assert cost_at(grid, 30, 43) < cost_at(grid, 30, 20) < cost_at(grid, 30, 46.1) == 1.0
    assert cost_at(grid, 30, 45.8) > cost_at(grid, 30, 42)       # keeps clear of the curb
    steep = build_traversability(feats, ground(slope_x=0.12), BBOX, [], [], Limits())
    assert steep["level"][at(30, 43)] == 1 and steep["walkway_downgraded_for_slope"] > 0.9
    wall = build_traversability(feats, ground(slope_x=0.2), BBOX, [], [], Limits())
    assert wall["level"][at(30, 43)] == 0 and wall["level"][at(30, 20)] == 0
    s = summarize(grid)
    assert s["traversable"] < s["conditional"] and 0 < s["unknown_share_of_grid"] < 1


def test_traversability_grid_round_trips(tmp_path):
    grid = build_traversability([(box(0, 0, 50, 50), "walkway", None)], ground(), BBOX, [], [])
    save_grid(grid, tmp_path / "t.npz")
    back = load_grid(tmp_path / "t.npz")
    assert back["names"] == grid["names"] and np.array_equal(back["level"], grid["level"])
    assert back["res"] == 0.5


# ---- collision and friction -------------------------------------------------------------


def test_only_things_at_wheel_height_collide():
    for cls in ("walkway", "road", "curb", "stairs", "wall", "building", "bench"):
        assert collision_for(cls), cls
    for cls in ("roof", "window", "door", "road_marking", "person", "car", "street_light"):
        assert collision_for(cls) is None, cls
    assert collision_for("tree", "bark_b0a0a0") == "none"
    assert collision_for("tree", "leaves_1") is None


def test_friction_tables_are_ordered_sensibly_and_wet_is_lower():
    dry, wet = friction_for("walkway")[0], friction_for("walkway", wet=True)[0]
    assert wet < dry and friction_for("grass")[0] < dry and friction_for("road")[0] >= dry
    s, d = friction_for("gravel")
    assert d < s
    rng = np.random.default_rng(1)
    draws = [sample_friction("grass", rng)[0] for _ in range(200)]
    assert min(draws) >= 0.30 and max(draws) <= 0.55 and np.std(draws) > 0.03


def test_usd_has_selective_colliders_friction_and_traversability(tmp_path):
    from pxr import Usd, UsdPhysics, UsdShade

    from campus_rover.meshkit import Scene
    from campus_rover.world import export_usd

    scene = Scene(origin=(0.0, 0.0, 0.0))
    parts = (("ground", "walkway", "ground_walkway"), ("ground", "curb", "ground_curb"),
             ("buildings/b", "roof", "roof_8c8c88"), ("buildings/b", "window", "glass_x"),
             ("trees", "tree", "bark"), ("trees", "tree", "leaves_0"))
    for group, cls, mat in parts:
        scene.material(mat, (0.5, 0.5, 0.5))
        scene.mesh(group, cls, mat).box((0, 0, 0), (2, 2, 1))
    export_usd(scene, tmp_path / "w.usda")
    stage = Usd.Stage.Open(str(tmp_path / "w.usda"))

    def prim(path):
        p = stage.GetPrimAtPath(path)
        assert p.IsValid(), path
        return p

    walk = prim("/World/ground/walkway__ground_walkway")
    assert walk.HasAPI(UsdPhysics.CollisionAPI)
    assert UsdPhysics.MeshCollisionAPI(walk).GetApproximationAttr().Get() == "none"
    assert not prim("/World/buildings/b/roof__roof_8c8c88").HasAPI(UsdPhysics.CollisionAPI)
    assert not prim("/World/buildings/b/window__glass_x").HasAPI(UsdPhysics.CollisionAPI)
    assert prim("/World/trees/tree__bark").HasAPI(UsdPhysics.CollisionAPI)
    assert not prim("/World/trees/tree__leaves_0").HasAPI(UsdPhysics.CollisionAPI)
    bound = UsdShade.MaterialBindingAPI(walk).ComputeBoundMaterial("physics")[0]
    api = UsdPhysics.MaterialAPI(bound.GetPrim())
    assert api.GetStaticFrictionAttr().Get() == pytest.approx(friction_for("walkway")[0])
    assert walk.GetAttribute("campus:traversability").Get() == TRAVERSABLE
    assert prim("/World/ground/curb__ground_curb").GetAttribute(
        "campus:traversability").Get() == BLOCKED


def test_collision_stats_count_triangles():
    from campus_rover.meshkit import Scene

    scene = Scene()
    scene.material("m", (1, 1, 1))
    scene.mesh("g", "walkway", "m").box((0, 0, 0), (1, 1, 1))
    scene.mesh("g", "roof", "m").box((0, 0, 0), (1, 1, 1))
    stats = collision_stats(scene.parts)
    assert stats["triangles"] == 24 and stats["collide"] == 12 and stats["share"] == 0.5
    assert math.isclose(stats["share"], 0.5)


# ---- curb measurement --------------------------------------------------------------------


def test_curb_reveal_reads_a_real_step_and_a_flat_control():
    from shapely.ops import unary_union

    from campus_rover.ground import curb_reveal

    res, n = 0.25, 240                                   # 60 m x 60 m
    z = np.zeros((n, n))
    xs = (np.arange(n) + 0.5) * res
    z[:, xs >= 30.0] = 0.15                              # sidewalk 15 cm above the road
    valid = np.ones((n, n), dtype=bool)
    curbs = [box(29.8, 10 + 12 * i, 30.1, 18 + 12 * i) for i in range(4)]
    road, walk = box(0, 0, 29.8, 60), box(30.1, 0, 60, 60)
    out = curb_reveal(z, valid, 0.0, 0.0, res, curbs, unary_union([road]), walk)
    assert out["sidewalk_minus_road"]["median_m"] == pytest.approx(0.15, abs=0.01)
    assert abs(out["control_road_minus_road"]["median_m"]) < 0.01
    flat = curb_reveal(np.zeros((n, n)), valid, 0.0, 0.0, res, curbs, road, walk)
    assert abs(flat["sidewalk_minus_road"]["median_m"]) < 0.01
