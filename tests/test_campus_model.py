import pytest

from campus_rover.campus_model import CampusModel, LayerSpec, add_layer, build_model
from campus_rover.graph import CampusGraph
from campus_rover.site import load_site


def feature(geom_type, coords, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": geom_type, "coordinates": coords},
    }


def square(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def test_layer_spec_validation():
    with pytest.raises(ValueError, match="role"):
        LayerSpec.from_dict("x", {"role": "spaceship"})
    with pytest.raises(ValueError, match="unknown fields"):
        LayerSpec.from_dict("x", {"role": "tree", "colour": "green"})


def test_add_layers_by_role():
    m = CampusModel(crs="local")
    add_layer(
        m,
        LayerSpec("b", "building"),
        [
            feature("Polygon", square(0, 0, 10, 10), BLDG_NAME="Holmes Hall", FLOORS=4),
            feature("Polygon", square(20, 0, 30, 10)),
        ],
    )
    add_layer(m, LayerSpec("t", "tree"), [feature("Point", [1, 2], Common_Name="Monkeypod")])
    add_layer(
        m, LayerSpec("s", "prop", prop_class="bench"), [feature("MultiPoint", [[0, 1], [2, 3]])]
    )
    add_layer(
        m,
        LayerSpec("e", "crowd_source", zone="eatery", windows=[{"density": 5}]),
        [feature("Polygon", square(0, 0, 4, 4))],
    )
    add_layer(
        m,
        LayerSpec("c", "construction", reason="renovation"),
        [feature("Polygon", square(50, 50, 60, 60))],
    )
    add_layer(m, LayerSpec("g", "gcp"), [{"properties": {}, "geometry": None}])

    holmes, unnamed = m.buildings
    assert holmes.name == "Holmes Hall" and holmes.height_m == pytest.approx(14.0)
    assert unnamed.name is None and unnamed.height_m == 10.0
    assert m.trees[0].props["species"] == "Monkeypod"
    assert [p.cls for p in m.props] == ["bench", "bench"]
    assert m.crowd_sources[0].points == [pytest.approx((2, 2))]
    assert m.closures[0].reason == "renovation"
    assert m.gcps == []
    assert m.find_building("holmes").name == "Holmes Hall"
    assert m.find_building("Nope") is None
    assert m.bounds() == (0, 0, 60, 60)


def test_explicit_height_field_in_feet():
    m = CampusModel(crs="local")
    spec = LayerSpec("b", "building", height_field="HGT", height_units="ft")
    add_layer(m, spec, [feature("Polygon", square(0, 0, 1, 1), HGT=100)])
    assert m.buildings[0].height_m == pytest.approx(30.48)


def line_graph() -> CampusGraph:
    return CampusGraph.from_dict(
        {
            "nodes": {
                "a": {"xy": [0, 0]},
                "b": {"xy": [100, 0]},
                "c": {"xy": [200, 0]},
                "d": {"xy": [100, 50]},
            },
            "edges": [{"u": "a", "v": "b"}, {"u": "b", "v": "c"}, {"u": "b", "v": "d"}],
        }
    )


def test_apply_to_graph_closes_and_tags():
    g = line_graph()
    m = CampusModel(crs="local")
    add_layer(m, LayerSpec("c", "construction"), [feature("Polygon", square(140, -10, 160, 10))])
    add_layer(
        m, LayerSpec("e", "crowd_source", zone="eatery", radius_m=15), [feature("Point", [50, 10])]
    )
    result = m.apply_to_graph(g)
    assert result == {"closed": 1, "crowd_tagged": 1}
    assert g.g.edges["b", "c"]["closed"] and g.g.edges["b", "c"]["closed_reason"] == "construction"
    assert g.g.edges["a", "b"]["pois"] == ["eatery"]
    assert "pois" not in g.g.edges["b", "d"]


def test_building_landmark_prefers_accessible_entrance():
    g = line_graph()
    m = CampusModel(crs="local")
    add_layer(
        m,
        LayerSpec("b", "building"),
        [feature("Polygon", square(80, 10, 220, 40), NAME="Campus Center")],
    )
    found, missing = m.resolve_building_landmarks(
        g, {"cc": {"building": "Campus Center"}, "x": {"building": "Nowhere"}, "y": {"node": "a"}}
    )
    assert missing == ["x"]
    assert found["cc"] in {"b", "c", "d"}  # nearest to the footprint
    add_layer(m, LayerSpec("doors", "accessible_entrance"), [feature("Point", [195, 10])])
    found, _ = m.resolve_building_landmarks(g, {"cc": {"building": "Campus Center"}})
    assert found["cc"] == "c"  # snapped to the node nearest the automatic door


def test_demo_site_model_feeds_planner(sites_dir):
    site = load_site("demo_campus", sites_dir)
    model = site.load_model()
    assert model.summary()["buildings"] == 6 and not model.missing_layers
    graph = site.load_graph()
    assert graph.g.edges["j1", "lib"]["closed_reason"] == "construction"  # from the layer
    assert "eatery" in graph.g.edges["center", "plaza"]["pois"]
    assert "eatery" in site.crowd["zones"]


def test_uh_layers_config_loads_without_data(sites_dir):
    site = load_site("uh_manoa", sites_dir)
    model = build_model(site)
    assert "buildings" in model.missing_layers and "survey_benchmarks" in model.missing_layers
    assert model.summary()["buildings"] == 0
