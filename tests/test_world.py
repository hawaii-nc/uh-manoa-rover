"""3D world builder: mesh toolkit, lidar gridding, massing, styles, facades, export."""

import json
import math

import numpy as np
import pytest
from shapely.geometry import box

from campus_rover.agents import Scenario, add_car, add_person
from campus_rover.buildgen import generate_building
from campus_rover.lidar import Gridder, Rasters, fill_holes
from campus_rover.massing import box_massing, building_massing
from campus_rover.meshkit import Mesh, Scene, hex_to_rgb, triangulate_polygon, wall_normal
from campus_rover.overture import segments_to_campus_graph
from campus_rover.site import load_site
from campus_rover.style import Style, background_style, load_styles, slug, unverified_style
from campus_rover.world import build_world, export_glb, export_usd, write_world


def mesh_area(mesh: Mesh) -> float:
    v, f = mesh.arrays()
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2)


# ---- mesh toolkit ------------------------------------------------------------------


def test_wall_faces_outward_for_counter_clockwise_ring():
    # South edge of a counter-clockwise square runs west -> east; outside is south.
    assert wall_normal((0, 0), (10, 0)) == pytest.approx((0, -1))
    mesh = Mesh()
    mesh.wall((0, 0), (10, 0), 0, 3)
    v, f = mesh.arrays()
    normal = np.cross(v[f[0, 1]] - v[f[0, 0]], v[f[0, 2]] - v[f[0, 0]])
    assert normal[1] < 0 and mesh_area(mesh) == pytest.approx(30)


def test_polygon_cap_respects_holes():
    ring = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
    verts, tris = triangulate_polygon(ring)
    mesh = Mesh()
    mesh.polygon_cap(ring, 5.0)
    assert mesh_area(mesh) == pytest.approx(96)
    assert len(tris) > 0 and verts.shape[1] == 2


def test_scene_rejects_unknown_material_and_counts_triangles():
    scene = Scene()
    with pytest.raises(KeyError):
        scene.mesh("g", "building", "nope")
    mat = scene.color_material("wall", "#ff0000")
    scene.mesh("g", "building", mat).box((0, 0, 0), (1, 1, 1))
    assert scene.stats()["by_class"] == {"building": 12}
    assert hex_to_rgb("#ff0000") == (1.0, 0.0, 0.0)


# ---- lidar ------------------------------------------------------------------------


def test_gridder_splits_ground_roofs_and_leaves():
    grid = Gridder((0, 0, 4, 4), res=1.0)
    x = np.array([0.5, 0.5, 2.5, 2.5, 9.0])
    y = np.array([0.5, 0.5, 2.5, 2.5, 9.0])
    z = np.array([10.0, 12.0, 20.0, 18.0, 99.0])
    cls = np.array([2, 2, 1, 1, 2])            # two ground, two unclassified, one outside
    returns = np.array([1, 1, 1, 3, 1])        # the last unclassified one went through leaves
    kept = grid.add(x, y, z, cls, np.array([100, 300, 0, 0, 0]), returns)
    r = grid.finish()
    assert kept == 4
    assert r.dtm[0, 0] == pytest.approx(11.0) and r.intensity[0, 0] == pytest.approx(200)
    assert r.dsm_bldg[2, 2] == pytest.approx(20.0) and r.dsm_veg[2, 2] == pytest.approx(18.0)
    assert np.isfinite(r.dtm).all()            # holes are filled
    assert r.ground_z(0.5, 0.5) == pytest.approx(11.0)


def test_fill_holes_uses_nearest_value():
    grid = np.array([[1.0, np.nan, np.nan, 5.0]])
    out = fill_holes(grid, smooth_passes=0)
    assert out.tolist() == [[1.0, 1.0, 5.0, 5.0]]


def synthetic_rasters(roof) -> Rasters:
    """40 x 40 m of flat ground at z = 100 with `roof(x, y)` heights above it."""
    res, n = 0.5, 80
    xs = (np.arange(n) + 0.5) * res
    gx, gy = np.meshgrid(xs, xs)
    heights = roof(gx, gy)
    dsm = np.where(heights > 0, 100.0 + heights, np.nan)
    return Rasters(0.0, 0.0, res, np.full((n, n), 100.0), dsm, np.full((n, n), np.nan),
                   np.full((n, n), 20000.0), np.ones((n, n), dtype=np.uint16))


def test_massing_finds_tower_on_podium():
    def roof(x, y):
        inside = (x > 10) & (x < 30) & (y > 10) & (y < 30)
        tower = (x > 10) & (x < 20) & (y > 10) & (y < 30)
        return np.where(tower, 20.0, np.where(inside, 8.0, 0.0))

    m = building_massing(box(10, 10, 30, 30), synthetic_rasters(roof))
    heights = sorted(round(b.height) for b in m.blocks)
    assert heights == [8, 20] and not m.pitched
    assert m.base_z == pytest.approx(100.0) and m.height == pytest.approx(20.0, abs=0.5)
    areas = {round(b.height): b.polygon.area for b in m.blocks}
    assert areas[20] == pytest.approx(200, rel=0.15) and areas[8] == pytest.approx(200, rel=0.15)
    assert m.height_at(15, 20) == pytest.approx(20.0) and m.height_at(35, 35) == 0.0


def test_massing_detects_pitched_roof_and_falls_back_without_lidar():
    def gable(x, y):
        inside = (x > 10) & (x < 30) & (y > 10) & (y < 30)
        return np.where(inside, 6.0 + 5.0 * (1 - np.abs(x - 20) / 10), 0.0)

    m = building_massing(box(10, 10, 30, 30), synthetic_rasters(gable))
    assert m.pitched and len(m.blocks) == 1 and 6.0 <= m.eave < 8.0

    empty = synthetic_rasters(lambda x, y: np.zeros_like(x))
    fallback = building_massing(box(10, 10, 30, 30), empty, default_height=9.0)
    assert [b.height for b in fallback.blocks] == [9.0]


# ---- styles and facades -------------------------------------------------------------


def test_style_validation_and_auto_style():
    with pytest.raises(ValueError, match="windows"):
        Style.from_dict({"id": "x", "windows": "stained"})
    with pytest.raises(ValueError, match="unknown fields"):
        Style.from_dict({"id": "x", "colour": "#ffffff"})
    with pytest.raises(ValueError, match="colour"):
        Style.from_dict({"id": "x", "wall": "beige"})
    assert slug("QUEEN LILI`UOKALANI CENTER") == "queen_liliuokalani_center"
    assert Style.from_dict({"id": "x", "roof": None}).roof is None


def test_buildings_without_evidence_are_never_dressed_up():
    # Near the route: measured shape only. Far away: generic background, no year guessing.
    plain = unverified_style("x")
    assert plain.windows == "none" and plain.shading == "none"
    assert background_style("shed", area_m2=40).windows == "none"
    assert background_style("far", area_m2=900).windows == "grid"
    scene = Scene()
    info = generate_building(scene, "b", box_massing(box(0, 0, 30, 15), 0.0, 12.0), plain)
    classes = {cls for (_, cls, _), m in scene.parts.items() if m.count}
    assert classes == {"building", "roof"} and info["windows"] == 0 and info["doors"] == 0


def test_doors_only_where_a_door_is_mapped():
    massing = box_massing(box(0, 0, 30, 15), 0.0, 12.0)
    style = Style(id="t", floors=3)
    none = generate_building(Scene(), "b", massing, style)
    assert none["doors"] == 0
    scene = Scene()
    # One door mapped just outside the south wall, one far away from any wall.
    info = generate_building(scene, "b", massing, style, door_points=[(12.0, -1.0), (90, 90)])
    assert info["doors"] == 1
    v, _ = scene.parts[("b", "door", "door_4a3b2e")].arrays()
    assert v[:, 0].mean() == pytest.approx(12.0) and v[:, 1].max() < 0.01   # on the south wall


def test_roof_colour_comes_from_sheet_or_measurement():
    massing = box_massing(box(0, 0, 30, 15), 0.0, 12.0)
    scene = Scene()
    generate_building(scene, "a", massing, Style(id="a", roof="#aa3322"), roof_color="#777777")
    generate_building(scene, "b", massing, Style(id="b"), roof_color="#777777")
    roofs = {g: m for (g, cls, m) in scene.parts if cls == "roof"}
    assert roofs == {"a": "roof_aa3322", "b": "roof_777777"}


def test_uh_style_sheets_are_valid_and_cite_sources():
    site = load_site("uh_manoa")
    styles = load_styles(site.root / "buildings")
    assert {"holmes_hall", "campus_center", "architecture_building"} <= set(styles)
    for style in styles.values():
        assert style.sources, f"{style.id} has no source"
        assert style.sources[0].startswith("https://map.hawaii.edu/images/"), style.id


@pytest.mark.parametrize("windows,shading,ground", [
    ("grid", "fins", "solid"), ("ribbon", "eyebrows", "arcade"),
    ("curtain", "none", "storefront"), ("slit", "lanai", "solid"), ("none", "none", "solid"),
])
def test_generate_building_makes_labelled_parts(windows, shading, ground):
    scene = Scene()
    style = Style(id="t", windows=windows, shading=shading, ground_floor=ground, floors=3)
    info = generate_building(scene, "buildings/t", box_massing(box(0, 0, 30, 15), 0.0, 12.0),
                             style)
    classes = {cls for (_, cls, _), m in scene.parts.items() if m.count}
    assert {"building", "roof"} <= classes and "door" not in classes
    assert info["floors"] == 3 and info["doors"] == 0
    has_glass = windows != "none" and shading != "lanai" or ground == "storefront"
    assert ("window" in classes) == has_glass
    v, _ = scene.parts[("buildings/t", "roof", "roof_8c8c88")].arrays()
    assert v[:, 2].max() == pytest.approx(12.0 - 0.45)        # roof sits below the parapet


def test_taller_block_hides_shared_wall():
    # Two blocks side by side: the low block's wall against the tower must not be built.
    def roof(x, y):
        inside = (x > 10) & (x < 30) & (y > 10) & (y < 30)
        return np.where(inside & (x < 20), 20.0, np.where(inside, 8.0, 0.0))

    m = building_massing(box(10, 10, 30, 30), synthetic_rasters(roof))
    scene = Scene()
    generate_building(scene, "b", m, Style(id="b", windows="none"))
    wall = scene.parts[("b", "building", "wall_c9c2b2")]
    # Outside walls: 80 m around x up to 8 m high (plus foundation) + the tower's extra height.
    assert mesh_area(wall) < 80 * (8 + 2.5) + 50 * 12 + 400


# ---- walkway import -----------------------------------------------------------------


def test_overture_segments_become_a_connected_graph():
    segs = [
        {"coords": [(0, 0), (10, 0), (20, 0)], "class": "footway", "subclass": None},
        {"coords": [(10, 0), (10, 10)], "class": "steps", "subclass": None},
        {"coords": [(20, 0), (30, 0)], "class": "service", "subclass": "parking_aisle"},
        {"coords": [(0, 0), (0, 50)], "class": "motorway", "subclass": None},      # not walkable
        {"coords": [(500, 500), (510, 500)], "class": "footway", "subclass": None},  # stray
    ]
    g = segments_to_campus_graph(segs).g
    assert g.number_of_nodes() == 5 and g.number_of_edges() == 4
    kinds = sorted(d["kind"] for _, _, d in g.edges(data=True))
    assert kinds == ["stairs", "walkway", "walkway", "walkway"]
    zones = [d["zone"] for _, _, d in g.edges(data=True)]
    assert zones.count("roadside") == 1


# ---- whole world on the demo campus (no lidar, no network) ---------------------------


@pytest.fixture(scope="module")
def demo_world():
    return build_world(load_site("demo_campus"), Scenario(name="test", time="Mon 09:20"))


def test_demo_world_builds_without_lidar(demo_world):
    r = demo_world.report
    assert r["lidar"] is False and len(r["buildings"]) >= 5 and r["trees"] > 0
    classes = set(r["mesh"]["by_class"])
    assert {"grass", "walkway", "building", "window", "roof", "tree"} <= classes
    assert r["surface_share"]["walkway"] > 0.01
    assert r["route"]["length_m"] > 100 and any(b["tier"] == "A" for b in r["buildings"])
    assert r["agents"]["person"] > 0
    # Looks come from a style sheet, or the building stays plain: nothing is guessed nearby.
    by_id = {b["id"]: b for b in r["buildings"]}
    assert by_id["engineering_hall"]["style_source"] == "sheet"
    assert by_id["engineering_hall"]["windows"] == 0 and by_id["student_center"]["windows"] > 0
    for b in r["buildings"]:
        if b["style_source"] == "unverified":
            assert b["tier"] in "AB" and b["windows"] == 0 and b["doors"] == 0
        if b["style_source"] == "procedural":
            assert b["tier"] == "C"


def test_agents_are_a_separate_layer(demo_world):
    static = {cls for (_, cls, _) in demo_world.scene.parts}
    moving = {cls for (_, cls, _) in demo_world.agents.parts}
    assert "person" in moving and not ({"person", "car", "bicycle"} & static)


def test_person_and_car_have_real_world_sizes():
    scene = Scene()
    rng = np.random.default_rng(0)
    add_person(scene, "a", 0, 0, 0, 0.0, rng)
    add_car(scene, "c", 10, 0, 0, math.pi / 2, rng)
    person = np.vstack([m.arrays()[0] for (g, _, _), m in scene.parts.items() if g == "a"])
    car = np.vstack([m.arrays()[0] for (g, _, _), m in scene.parts.items() if g == "c"])
    assert 1.45 < person[:, 2].max() < 2.0
    assert 4.0 < np.ptp(car[:, 1]) < 5.0 and 1.6 < np.ptp(car[:, 0]) < 2.2   # turned 90°


def test_exports_keep_labels(demo_world, tmp_path):
    import trimesh

    files = write_world(demo_world, tmp_path)
    assert {"world.glb", "world.usd", "agents.glb", "world.json", "viewer"} <= set(files)
    loaded = trimesh.load(files["world.glb"])
    names = list(loaded.geometry)
    assert any(n.split("|")[1] == "window" for n in names)
    report = json.loads(files["world.json"].read_text())
    assert report["agents_file"] == "agents_test.glb"

    from pxr import Usd, UsdGeom, UsdPhysics, UsdSemantics

    stage = Usd.Stage.Open(str(files["world.usd"]))
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    labels = set()
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            labels.update(UsdSemantics.LabelsAPI(prim, "class").GetLabelsAttr().Get())
            assert prim.HasAPI(UsdPhysics.CollisionAPI)
    assert {"building", "window", "walkway", "grass", "tree"} <= labels


def test_glb_is_y_up_and_recentred(tmp_path):
    import trimesh

    scene = Scene(origin=(1000.0, 2000.0, 50.0))
    scene.mesh("g", "building", scene.color_material("wall", "#808080")).box(
        (1000, 2000, 50), (2, 4, 10))
    export_glb(scene, tmp_path / "t.glb")
    export_usd(scene, tmp_path / "t.usda")
    mesh = next(iter(trimesh.load(tmp_path / "t.glb").geometry.values()))
    lo, hi = mesh.bounds
    assert hi[1] - lo[1] == pytest.approx(10) and lo[1] == pytest.approx(0)   # height on Y
    assert hi[2] - lo[2] == pytest.approx(4)
    assert "campus:originOffset" in (tmp_path / "t.usda").read_text()


# ---- official open data: checks ------------------------------------------------------


def test_contour_check_measures_the_offset():
    from campus_rover.opendata import FT, contour_check, contour_lines

    rasters = synthetic_rasters(lambda x, y: np.zeros_like(x))        # ground at 100 m
    feats = [{"properties": {"ELEVATION": 300}, "geometry": {
        "type": "MultiLineString", "coordinates": [[[5, 5], [20, 20], [35, 5]], [[900, 900]]]}},
        {"properties": {"ELEVATION": None}, "geometry": {"type": "LineString",
                                                         "coordinates": [[1, 1], [2, 2]]}}]
    lines = contour_lines(feats, "elevation", FT)
    assert len(lines) == 2 and lines[0][1] == pytest.approx(91.44)
    check = contour_check(rasters, lines)
    assert check["points"] == 3 and check["mean_diff_m"] == pytest.approx(100 - 91.44, abs=0.01)
    rasters.shift_z(-8.56)
    assert contour_check(rasters, lines)["median_abs_diff_m"] == pytest.approx(0, abs=0.01)


def test_height_check_and_footprint_height():
    from campus_rover.opendata import footprint_height, height_check

    holmes = footprint_height({"gis_height": 65.718, "maxht_m": None})     # feet -> meters
    assert holmes == pytest.approx(20.03, abs=0.01)
    assert footprint_height({"maxht_m": 12.0, "gis_height": 99}) == 12.0
    assert footprint_height({"gis_height": "None"}) is None
    ours = [(box(0, 0, 20, 20), 21.0), (box(100, 100, 120, 120), 9.0)]
    official = [(box(1, 1, 19, 19), 20.0), (box(500, 500, 510, 510), 5.0)]
    check = height_check(ours, official)
    assert check == {"matched": 1, "median_diff_m": 1.0, "median_abs_diff_m": 1.0, "within_3m": 1.0}


def test_gridder_records_roof_brightness():
    grid = Gridder((0, 0, 2, 2), res=1.0)
    grid.add(np.array([0.5, 0.5]), np.array([0.5, 0.5]), np.array([10.0, 12.0]),
             np.array([1, 1]), np.array([400, 600]), np.array([1, 1]))
    r = grid.finish()
    assert r.roof_intensity[0, 0] == pytest.approx(500) and np.isnan(r.roof_intensity[1, 1])


def test_query_layer_can_filter_by_box():
    from campus_rover.arcgis import query_layer

    seen = {}

    def fake(url, params):
        if url.endswith("/query"):
            seen.update(params)
            return {"features": []}
        return {"maxRecordCount": 100}

    query_layer("https://x/FeatureServer/0", "EPSG:32604", fetch=fake, bbox=(1, 2, 3, 4))
    assert seen["geometry"] == "1,2,3,4" and seen["geometryType"] == "esriGeometryEnvelope"
    assert seen["inSR"] == 32604


# ---- ground detail: surfaces, curbs, stairs, walls, textures --------------------------


def sloped_rasters(slope: float = 0.1) -> Rasters:
    """40 x 40 m of ground rising along +y: z = 100 + slope * y."""
    r = synthetic_rasters(lambda x, y: np.zeros_like(x))
    ys = (np.arange(r.shape[0]) + 0.5) * r.res
    r.dtm = 100.0 + slope * np.repeat(ys[:, None], r.shape[1], axis=1)
    return r


def test_split_by_grid_keeps_area_and_limits_piece_size():
    from campus_rover.ground import split_by_grid

    ring = box(1, 1, 21, 12).difference(box(8, 4, 12, 8))
    pieces = split_by_grid(ring, cell=3.0)
    assert sum(p.area for p in pieces) == pytest.approx(ring.area)
    assert all(p.bounds[2] - p.bounds[0] <= 3.0 + 1e-9 for p in pieces)
    assert split_by_grid(box(0, 0, 1, 1), cell=3.0)[0].area == 1.0


def test_draped_surface_follows_the_slope_and_curb_stands_above_it():
    from campus_rover.ground import CURB_HEIGHT_M, drape, raise_polygon

    rasters = sloped_rasters(0.1)
    flat, curb = Mesh(), Mesh()
    drape(flat, box(5, 5, 15, 30), rasters.ground_z)
    v, _ = flat.arrays()
    assert np.allclose(v[:, 2], 100 + 0.1 * v[:, 1], atol=0.03)
    assert mesh_area(flat) == pytest.approx(250 * math.hypot(1, 0.1), rel=0.01)
    raise_polygon(curb, box(20, 5, 20.3, 30), rasters.ground_z, CURB_HEIGHT_M)
    v, _ = curb.arrays()
    top = v[:, 2] - (100 + 0.1 * v[:, 1])
    assert top.max() == pytest.approx(CURB_HEIGHT_M, abs=0.03) and top.min() < 0


def test_stair_treads_come_out_as_flat_steps():
    from campus_rover.ground import add_stair_tread

    rasters = sloped_rasters(0.5)           # a 1-in-2 flight
    mesh, heights = Mesh(), []
    for i in range(6):                      # six treads, 0.3 m deep, climbing along +y
        tread = box(10, 10 + 0.3 * i, 12, 10 + 0.3 * (i + 1))
        heights.append(add_stair_tread(mesh, tread, rasters.ground_z))
    rises = np.diff(heights)
    assert np.allclose(rises, 0.15, atol=0.02)          # 0.3 m tread x 0.5 slope
    v, f = mesh.arrays()
    up = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])[:, 2] > 1e-9
    tops = v[f[up]][:, :, 2]
    assert np.allclose(tops.max(axis=1), tops.min(axis=1))     # every tread is level


def test_wall_height_is_measured_or_left_flat():
    from campus_rover.ground import add_landcover, measured_height

    rasters = synthetic_rasters(lambda x, y: np.zeros_like(x))
    low = np.full(rasters.shape, np.nan)
    low[20:40, 20:24] = 100.9                # a 0.9 m wall the lidar saw
    low[20:40, 60:64] = 112.0                # tree canopy over the second wall: not a wall top
    rasters.dsm_low = low
    seen, hidden = box(10, 10, 12, 20), box(30, 10, 32, 20)
    assert measured_height(seen, rasters) == pytest.approx(0.9)
    assert measured_height(hidden, rasters) is None
    scene = Scene()
    report = add_landcover(scene, [(seen, "wall", None), (hidden, "rock_wall", None),
                                   (box(0, 0, 9, 9), "walkway", None),
                                   (box(0, 20, 5, 20.3), "curb", "red"),
                                   (box(0, 30, 5, 35), "not_a_class", None)],
                           rasters, rasters.bounds)
    assert report["walls"] == {"measured": 1, "flat": 1}
    assert report["polygons"] == {"wall": 2, "walkway": 1, "curb": 1}
    classes = {(cls, mat) for (_, cls, mat), m in scene.parts.items() if m.count}
    assert ("curb", "paint_red") in classes and ("walkway", "ground_walkway") in classes
    wall_top = max(m.arrays()[0][:, 2].max() for (_, c, _), m in scene.parts.items() if c == "wall")
    assert wall_top == pytest.approx(100.9)


def test_coarse_grid_and_painted_lines():
    from campus_rover.ground import add_painted_lines, coarse_grid

    rasters = synthetic_rasters(lambda x, y: np.zeros_like(x))
    feats = [(box(0, 0, 40, 20), "road", None), (box(10, 5, 20, 10), "walkway", None),
             (box(0, 20, 20, 40), "grass", None)]
    grid, covered = coarse_grid(feats, rasters)
    assert grid[4, 4] == 2 and grid[14, 30] == 1 and grid[60, 10] == 0
    assert covered[4, 4] and not covered[70, 70]
    scene = Scene()
    n = add_painted_lines(scene, [([(5, 5), (15, 5), (15, 9)], "white"),
                                  ([(500, 500), (510, 500)], "orange")], rasters, rasters.bounds)
    assert n == 2 and ("ground", "road_marking", "paint_white") in scene.parts


def test_landcover_config_maps_source_types_to_classes(tmp_path):
    from campus_rover.world import load_ground_lines, load_landcover

    square = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    path = tmp_path / "lc.json"
    path.write_text(json.dumps([
        {"properties": {"SURFACE_TYPE": "Curbs", "NOTES": "Red - Faded"}, "geometry": square},
        {"properties": {"SURFACE_TYPE": "Stairs", "NOTES": None}, "geometry": square},
        {"properties": {"SURFACE_TYPE": "Poles"}, "geometry": square}]))
    cfg = {"field": "SURFACE_TYPE", "paint_field": "NOTES",
           "classes": {"Curbs": "curb", "Stairs": "stairs"}}
    feats = load_landcover(path, cfg)
    assert [(c, p) for _, c, p in feats] == [("curb", "red"), ("stairs", None)]
    lines = tmp_path / "lines.json"
    lines.write_text(json.dumps([
        {"properties": {"CAT": "ParkingLotLines", "D": "orange"},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}},
        {"properties": {"CAT": "FencesGates", "D": None},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}}]))
    got = load_ground_lines(lines, {"category_field": "CAT", "categories": ["ParkingLotLines"],
                                    "colour_field": "D"})
    assert got == [([(0.0, 0.0), (1.0, 0.0)], "orange")]


def test_textures_are_shared_and_carried_by_both_exports(tmp_path):
    import struct

    from pxr import Usd, UsdGeom

    scene = Scene()
    for name, hex_color in (("a", "#cc8866"), ("b", "#6688cc")):       # two tints, one texture
        mat = scene.color_material("wall", hex_color, texture="plaster")
        scene.mesh(f"buildings/{name}", "building", mat).box((0, 0, 0), (4, 2, 3))
    scene.mesh("ground", "walkway", scene.material("ground_walkway", (0.8, 0.8, 0.8),
                                                   texture="concrete")).quad(
        (0, 0, 0), (6, 0, 0), (6, 6, 0), (0, 6, 0))
    export_glb(scene, tmp_path / "t.glb")
    raw = (tmp_path / "t.glb").read_bytes()
    doc = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    assert len(doc["images"]) == 2 and len(doc["materials"]) == 3      # plaster stored once
    assert all("TEXCOORD_0" in m["primitives"][0]["attributes"] for m in doc["meshes"])
    by_name = {m["name"]: m for m in doc["meshes"]}
    wall = by_name["buildings/a|building|wall_cc8866"]["primitives"][0]["attributes"]
    assert doc["accessors"][wall["POSITION"]]["count"] == 36           # one vertex per corner
    ground = by_name["ground|walkway|ground_walkway"]["primitives"][0]["attributes"]
    assert doc["accessors"][ground["POSITION"]]["count"] == 4          # ground keeps its 4

    export_usd(scene, tmp_path / "t.usda")
    assert (tmp_path / "textures" / "plaster.jpg").exists()
    stage = Usd.Stage.Open(str(tmp_path / "t.usda"))
    mesh = stage.GetPrimAtPath("/World/ground/walkway__ground_walkway")
    st = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st").Get()
    assert len(st) == 4 and max(v[0] for v in st) == pytest.approx(2.0)   # 6 m / 3 m tile


def test_bundled_textures_are_all_cc0_with_a_source():
    from campus_rover.world import TEXTURE_DIR, texture_info

    info = texture_info()
    assert {"asphalt", "concrete", "grass", "plaster", "bark"} <= set(info)
    for name, entry in info.items():
        assert (TEXTURE_DIR / f"{name}.jpg").exists() and entry["licence"] == "CC0"
        assert entry["source"].startswith("https://polyhaven.com/a/")


# ---- free 3D models placed as shared instances ---------------------------------------


def tiny_asset(name="bench", cls="bench"):
    import io

    import trimesh
    from PIL import Image

    from campus_rover.assets import from_trimesh

    mesh = trimesh.creation.box(extents=(2.0, 1.0, 0.5))           # Y-up source: 1 m tall
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.random.default_rng(0).random((len(mesh.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            baseColorTexture=Image.new("RGB", (64, 64), (200, 120, 60))))
    asset = from_trimesh(name, cls, [mesh], meta={"licence": "CC0", "source": "test"})
    assert io.BytesIO(asset.prims[0].image).read(2) == b"\xff\xd8"    # a JPEG
    return asset


def test_asset_is_turned_z_up_and_survives_the_cache(tmp_path):
    from campus_rover.assets import Asset, load_assets

    asset = tiny_asset()
    assert asset.size.tolist() == pytest.approx([2.0, 0.5, 1.0])     # height now on Z
    assert asset.prims[0].verts[:, 2].min() == 0.0 and asset.triangles == 12
    asset.save(tmp_path)
    back = Asset.load(tmp_path, "bench")
    assert back.triangles == 12 and back.meta["licence"] == "CC0" and back.prims[0].image
    assert set(load_assets(tmp_path)) == {"bench"} and load_assets(tmp_path / "none") == {}
    assert Asset.load(tmp_path, "missing") is None


def test_big_models_are_simplified_and_flat_coloured():
    import trimesh
    from PIL import Image

    from campus_rover.assets import from_trimesh

    ball = trimesh.creation.icosphere(subdivisions=4)                # 5,120 triangles
    ball.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(ball.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            baseColorTexture=Image.new("RGB", (8, 8), (255, 0, 0))))
    asset = from_trimesh("lamp", "street_light", [ball], max_tris=400)
    assert asset.triangles <= 450 and asset.prims[0].image is None
    assert asset.prims[0].color == pytest.approx((1.0, 0.0, 0.0), abs=0.02)


def test_instances_share_one_mesh_in_glb_and_one_prototype_in_usd(tmp_path):
    import struct

    from pxr import Usd, UsdSemantics

    scene = Scene(origin=(100.0, 200.0, 10.0))
    asset = tiny_asset()
    for i in range(3):
        scene.place(asset, "props", 100.0 + 5 * i, 200.0, 10.0, yaw=math.pi / 2, scale=2.0)
    assert scene.stats()["by_class"] == {"bench": 36} and scene.stats()["instances"] == 3
    export_glb(scene, tmp_path / "t.glb")
    raw = (tmp_path / "t.glb").read_bytes()
    doc = json.loads(raw[20:20 + struct.unpack("<I", raw[12:16])[0]])
    assert len(doc["meshes"]) == 1 and len(doc["nodes"]) == 3 and len(doc["images"]) == 1
    node = doc["nodes"][1]
    assert node["name"] == "props|bench|bench" and node["translation"] == [5.0, 0.0, 0.0]
    assert node["rotation"] == pytest.approx([0, math.sin(math.pi / 4), 0, math.cos(math.pi / 4)])
    assert node["scale"] == [2.0, 2.0, 2.0]

    export_usd(scene, tmp_path / "t.usda")
    stage = Usd.Stage.Open(str(tmp_path / "t.usda"))
    inst = stage.GetPrimAtPath("/World/props/bench_1")
    assert inst.IsInstanceable() and inst.HasAuthoredReferences()
    assert UsdSemantics.LabelsAPI(inst, "class").GetLabelsAttr().Get() == ["bench"]
    assert stage.GetPrimAtPath("/World/Prototypes/bench/part_0").IsValid()
    assert (tmp_path / "textures" / "bench_0.jpg").exists()


def test_pole_height_comes_from_lidar_or_is_refused():
    from campus_rover.world import pole_height

    rasters = synthetic_rasters(lambda x, y: np.zeros_like(x))
    assert pole_height(rasters, 10, 10) is None                # no low-surface grid at all
    low = np.full(rasters.shape, np.nan)
    low[20, 20] = 106.5                                        # a 6.5 m lamp head at (10, 10)
    low[60, 60] = 100.6                                        # a 0.6 m bollard at (30, 30)
    rasters.dsm_low = low
    assert pole_height(rasters, 10.2, 10.2) == pytest.approx(6.5)
    assert pole_height(rasters, 30.2, 30.2) is None and pole_height(rasters, 5, 35) is None


def test_listed_models_have_a_permitted_source():
    from campus_rover.assets import ASSETS

    for name, spec in ASSETS.items():
        assert spec["source"] in ("polyhaven", "kenney") and spec["cls"], name   # CC0 only


def test_cars_use_real_models_when_the_library_has_them(demo_world):
    from campus_rover.agents import populate

    site = load_site("demo_campus")
    car = tiny_asset("car_sedan", "car")
    scene = Scene()
    counts = populate(scene, site, site.load_graph(), site.load_model(),
                      demo_world.rasters.ground_z, demo_world.bbox,
                      Scenario(name="t", parked_share=1.0), library={"car_sedan": car})
    placed = [i for i in scene.instances if i.asset == "car_sedan"]
    assert counts["car"] == len(placed) > 0
    assert not any(cls == "car" for (_, cls, _) in scene.parts)      # no box stand-ins left


# ---- floor counts from published floor plans ------------------------------------------


def test_floor_plan_sheet_codes_give_the_floor_count():
    from campus_rover.floorplans import levels_from_codes, sheet_codes

    pages = ["Holmes Hall Summary 1163-S", "Basement 1163-000E", "1163-100E 100 Level East",
             "1163-100ME Mezzanine", "1163-200W x 1163-200E", "1163-500W", "other 9999-700"]
    codes = sheet_codes(pages, "1163")
    assert codes == ["000E", "100E", "100ME", "200E", "200W", "500W", "S"]
    assert levels_from_codes(codes) == {"floors": 3, "top_level": 5, "basement": True,
                                        "mezzanine": True}
    assert levels_from_codes(["100", "200", "1300"])["top_level"] == 13
    assert levels_from_codes(["S", "ROOF"])["floors"] == 0


def test_plan_floor_count_is_matched_to_the_roof_level_it_explains():
    from campus_rover.buildgen import floor_height
    from campus_rover.massing import Block, Massing

    # A 7-floor tower (28 m) beside a larger 4-floor wing (16 m): floors are 4 m, not 2.3 m.
    tower_and_wing = Massing(0.0, 28.0, [Block(box(0, 0, 40, 30), 16.0),
                                         Block(box(40, 0, 60, 30), 28.0)])
    assert floor_height(tower_and_wing, Style(id="k", floors=7)) == (7, pytest.approx(4.0))
    # A penthouse must not stretch the floors: 5 floors belong to the 20 m main roof.
    with_penthouse = Massing(0.0, 25.6, [Block(box(0, 0, 60, 40), 20.3),
                                         Block(box(0, 0, 20, 15), 25.6)])
    assert floor_height(with_penthouse, Style(id="h", floors=5))[1] == pytest.approx(4.06)
    # A small 4 m annex of a 4-floor building keeps one floor.
    annex = Massing(0.0, 4.1, [Block(box(0, 0, 10, 10), 4.1)])
    assert floor_height(annex, Style(id="m", floors=4))[0] == 1
    assert floor_height(annex, Style(id="n"))[0] == 1


def test_uh_sheets_cite_a_floor_plan_where_one_exists():
    site = load_site("uh_manoa")
    styles = load_styles(site.root / "buildings")
    from_plans = [s for s in styles.values() if any("/planning/Public/PDF/" in u
                                                    for u in s.sources)]
    assert len(from_plans) >= 60 and all(s.floors for s in from_plans)
    assert styles["holmes_hall"].floors == 5 and styles["kuykendall_hall"].floors == 7
