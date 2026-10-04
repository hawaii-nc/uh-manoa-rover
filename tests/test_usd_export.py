import pytest

pxr = pytest.importorskip("pxr")

from pxr import Usd, UsdGeom, UsdPhysics, UsdSemantics  # noqa: E402

from campus_rover.campus_model import CampusModel, LayerSpec, add_layer  # noqa: E402
from campus_rover.site import load_site  # noqa: E402
from campus_rover.usd_export import choose_origin, export_usd, safe_name  # noqa: E402


def labels(stage):
    out = {}
    for prim in stage.Traverse():
        if prim.HasAPI(UsdSemantics.LabelsAPI, "class"):
            out[str(prim.GetPath())] = list(
                UsdSemantics.LabelsAPI(prim, "class").GetLabelsAttr().Get()
            )
    return out


def test_demo_export(sites_dir, tmp_path):
    site = load_site("demo_campus", sites_dir)
    graph = site.load_graph()
    result = export_usd(
        site.load_model(), graph, tmp_path / "demo.usda", site.name, site.landmark_nodes(graph)
    )
    assert result["buildings"] == 6 and result["trees"] == 28

    stage = Usd.Stage.Open(str(tmp_path / "demo.usda"))
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert stage.GetDefaultPrim().GetPath() == "/World"
    lab = labels(stage)
    assert lab["/World/Buildings/Engineering_Hall"] == ["building"]
    assert lab["/World/Walkways/stairs"] == ["stairs"]
    assert lab["/World/Construction"] == ["construction_barrier"]
    assert lab["/World/Ground"] == ["grass"]
    assert sum(v == ["bench"] for v in lab.values()) == 5

    building = stage.GetPrimAtPath("/World/Buildings/Engineering_Hall")
    assert building.HasAPI(UsdPhysics.CollisionAPI)
    extent = UsdGeom.Mesh(building).GetExtentAttr().Get()
    assert extent[1][2] == pytest.approx(14.0)  # 4 floors x 3.5 m
    assert stage.GetPrimAtPath("/World/Markers/Landmarks/engineering").IsValid()
    assert stage.GetPrimAtPath("/World/Markers/GCPs/GCP_0").IsValid()


def test_projected_crs_is_recentred(tmp_path):
    m = CampusModel(crs="EPSG:32604")
    e, n = 621_000.0, 2_356_000.0
    ring = [[[e, n], [e + 40, n], [e + 40, n + 30], [e, n + 30], [e, n]]]
    add_layer(
        m,
        LayerSpec("b", "building"),
        [
            {
                "properties": {"NAME": "Holmes Hall"},
                "geometry": {"type": "Polygon", "coordinates": ring},
            }
        ],
    )
    assert choose_origin(m, None) == (621_000.0, 2_356_000.0)
    export_usd(m, None, tmp_path / "uh.usda", "uh")
    stage = Usd.Stage.Open(str(tmp_path / "uh.usda"))
    pts = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Buildings/Holmes_Hall")).GetPointsAttr().Get()
    assert max(abs(p[0]) for p in pts) < 100  # small local coordinates
    data = stage.GetRootLayer().customLayerData
    assert data["campus:crs"] == "EPSG:32604"
    assert tuple(data["campus:originOffset"]) == (621_000.0, 2_356_000.0)


def test_safe_name():
    assert safe_name("Hale Mānoa / East", "x") == "Hale_M_noa___East"
    assert safe_name("", "Building_3") == "Building_3"
    assert safe_name("1st Hall", "x") == "_1st_Hall"
