import pytest

from campus_rover.arcgis import (
    epsg_code,
    esri_to_geojson,
    list_service_layers,
    list_webmap_layers,
    query_layer,
    scan_text_for_arcgis,
)

LAYER = "https://example.edu/arcgis/rest/services/UHM/Campus/FeatureServer/3"


class FakeServer:
    """Serves a layer of `n` point features, `max_records` per page."""

    def __init__(self, n=5, max_records=2, paging=True):
        self.n, self.max_records, self.paging = n, max_records, paging
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if url == LAYER:
            return {
                "maxRecordCount": self.max_records,
                "advancedQueryCapabilities": {"supportsPagination": self.paging},
            }
        assert url == f"{LAYER}/query"
        start = params.get("resultOffset", 0)
        count = params.get("resultRecordCount", self.n)
        stop = min(start + count, self.n)
        feats = [
            {"attributes": {"OBJECTID": i, "NAME": f"Bench {i}"}, "geometry": {"x": i, "y": -i}}
            for i in range(start, stop)
        ]
        return {"features": feats, "exceededTransferLimit": stop < self.n}


def test_query_layer_pages_through_everything():
    server = FakeServer(n=5, max_records=2)
    feats = query_layer(LAYER, "EPSG:32604", fetch=server)
    assert [f["properties"]["OBJECTID"] for f in feats] == [0, 1, 2, 3, 4]
    assert feats[3]["geometry"] == {"type": "Point", "coordinates": [3, -3]}
    queries = [p for u, p in server.calls if u.endswith("/query")]
    assert [q["resultOffset"] for q in queries] == [0, 2, 4]
    assert all(q["outSR"] == 32604 and q["outFields"] == "*" for q in queries)


def test_query_layer_without_paging_support():
    server = FakeServer(n=3, paging=False)
    feats = query_layer(LAYER, "EPSG:32604", fetch=server)
    assert len(feats) == 3
    assert "resultOffset" not in server.calls[-1][1]


def test_esri_polygon_rings():
    # Esri: clockwise outer ring, counter-clockwise hole, then a second clockwise outer.
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]
    outer2 = [[20, 0], [20, 5], [25, 5], [25, 0], [20, 0]]
    g = esri_to_geojson({"attributes": {}, "geometry": {"rings": [outer, hole, outer2]}})
    assert g["geometry"]["type"] == "MultiPolygon"
    first, second = g["geometry"]["coordinates"]
    assert len(first) == 2 and len(second) == 1

    single = esri_to_geojson({"geometry": {"rings": [outer]}})
    assert single["geometry"]["type"] == "Polygon"


def test_esri_other_geometries():
    assert esri_to_geojson({"geometry": {"paths": [[[0, 0], [1, 1]]]}})["geometry"]["type"] == (
        "LineString"
    )
    assert esri_to_geojson({"geometry": {"x": None, "y": None}})["geometry"] is None
    assert esri_to_geojson({"attributes": {"a": 1}})["geometry"] is None


def test_discovery():
    def fetch(url, params):
        if url.endswith("/data"):
            return {
                "operationalLayers": [
                    {"title": "Buildings", "url": "https://x/FeatureServer/0"},
                    {
                        "title": "Accessibility",
                        "layers": [{"title": "Elevators", "url": "https://x/FeatureServer/5"}],
                    },
                ]
            }
        return {"layers": [{"id": 0, "name": "Buildings", "geometryType": "esriGeometryPolygon"}]}

    assert list_webmap_layers("abc", fetch=fetch) == [
        {"title": "Buildings", "url": "https://x/FeatureServer/0"},
        {"title": "Accessibility / Elevators", "url": "https://x/FeatureServer/5"},
    ]
    layers = list_service_layers("https://x/FeatureServer/", fetch=fetch)
    assert layers[0]["url"] == "https://x/FeatureServer/0"


def test_scan_saved_page_text():
    js = """
      var config = { portalUrl: "https://uh.maps.arcgis.com",
        webmap: "0123456789abcdef0123456789ABCDEF",
        layers: ["https://services.arcgis.com/abc/arcgis/rest/services/Trees/FeatureServer/0",
                 'https://gis.example.edu/server/rest/services/UHM/Buildings/MapServer'] };
    """
    found = scan_text_for_arcgis(js)
    assert found["item_ids"] == ["0123456789abcdef0123456789abcdef"]
    assert found["portals"] == ["https://uh.maps.arcgis.com"]
    assert len(found["service_urls"]) == 2
    assert found["service_urls"][0].endswith("FeatureServer/0")


def test_epsg_code():
    assert epsg_code("EPSG:32604") == 32604
    with pytest.raises(ValueError):
        epsg_code("local")
