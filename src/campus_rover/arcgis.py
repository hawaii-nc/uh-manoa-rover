"""Download layers from ArcGIS REST services (e.g. the UH Mānoa campus map).

The UH campus map (map.hawaii.edu/manoa) is an Esri ArcGIS app. Each layer
in its "Explore UH Mānoa" list is served by a FeatureServer or MapServer
layer URL that ends in /<number>. This module pages through a layer's
features and returns them as GeoJSON-style dicts, projected server-side
into the site's CRS (EPSG:32604 for UH), so no local reprojection is needed.

Network calls go through a `fetch(url, params) -> dict` function, so
everything here is testable offline with a fake.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

from .geometry import open_ring, signed_area

Fetch = Callable[[str, dict], dict]

USER_AGENT = "campus-rover/0.1 (research; +https://github.com/hawaii-nc/uh-manoa-rover)"


class ArcGISError(RuntimeError):
    pass


def http_get_json(url: str, params: dict, timeout: float = 30.0) -> dict:  # pragma: no cover
    query = urllib.parse.urlencode({**params, "f": params.get("f", "json")})
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if isinstance(data, dict) and "error" in data:
        raise ArcGISError(f"{url}: {data['error']}")
    return data


def epsg_code(crs: str) -> int:
    """'EPSG:32604' -> 32604."""
    if not crs.upper().startswith("EPSG:"):
        raise ValueError(f"ArcGIS needs an EPSG CRS, got {crs!r}")
    return int(crs.split(":", 1)[1])


# ---- discovery ------------------------------------------------------------------


def list_service_layers(service_url: str, fetch: Fetch = http_get_json) -> list[dict]:
    """Layers of a .../FeatureServer or .../MapServer: [{id, name, url, geometryType}]."""
    service_url = service_url.rstrip("/")
    info = fetch(service_url, {})
    out = []
    for layer in info.get("layers", []) + info.get("tables", []):
        out.append(
            {
                "id": layer["id"],
                "name": layer.get("name"),
                "url": f"{service_url}/{layer['id']}",
                "geometryType": layer.get("geometryType"),
            }
        )
    return out


def list_webmap_layers(
    item_id: str, portal: str = "https://www.arcgis.com", fetch: Fetch = http_get_json
) -> list[dict]:
    """Operational layers of an ArcGIS web map item: [{title, url}] (group layers flattened)."""
    data = fetch(f"{portal.rstrip('/')}/sharing/rest/content/items/{item_id}/data", {})
    out: list[dict] = []

    def walk(layers, prefix=""):
        for layer in layers or []:
            title = f"{prefix}{layer.get('title', '')}"
            if layer.get("url"):
                out.append({"title": title, "url": layer["url"]})
            walk(layer.get("layers"), prefix=f"{title} / ")

    walk(data.get("operationalLayers"))
    return out


# ---- querying -------------------------------------------------------------------


def query_layer(
    layer_url: str,
    out_crs: str,
    where: str = "1=1",
    fetch: Fetch = http_get_json,
    page_size: int | None = None,
    pause_s: float = 0.0,
) -> list[dict]:
    """All features of one layer as GeoJSON-style dicts in `out_crs`."""
    layer_url = layer_url.rstrip("/")
    info = fetch(layer_url, {})
    page = page_size or int(info.get("maxRecordCount") or 1000)
    supports_paging = info.get("advancedQueryCapabilities", {}).get("supportsPagination", True)
    sr = epsg_code(out_crs)

    features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": sr,
        }
        if supports_paging:
            params.update(resultOffset=offset, resultRecordCount=page)
        data = fetch(f"{layer_url}/query", params)
        batch = data.get("features", [])
        features.extend(esri_to_geojson(f) for f in batch)
        if not supports_paging or not batch:
            break
        if not data.get("exceededTransferLimit") and len(batch) < page:
            break
        offset += len(batch)
        if pause_s:
            time.sleep(pause_s)  # be polite to UH's server
    return features


def esri_to_geojson(feature: dict) -> dict:
    """Esri JSON feature -> GeoJSON-style feature (Point/LineString/Polygon/Multi*)."""
    geom = feature.get("geometry") or {}
    props = dict(feature.get("attributes") or {})
    return {"type": "Feature", "properties": props, "geometry": _esri_geometry(geom)}


def _esri_geometry(geom: dict) -> dict | None:
    if "x" in geom and "y" in geom:
        if geom["x"] is None or geom["y"] is None:
            return None
        return {"type": "Point", "coordinates": [geom["x"], geom["y"]]}
    if "points" in geom:
        return {"type": "MultiPoint", "coordinates": geom["points"]}
    if "paths" in geom:
        paths = geom["paths"]
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in geom:
        return _rings_to_polygons(geom["rings"])
    return None


def _rings_to_polygons(rings: list) -> dict | None:
    """Esri rings: clockwise = outer, counter-clockwise = hole (following an outer)."""
    polygons: list[list] = []
    for ring in rings:
        pts = open_ring([tuple(p[:2]) for p in ring])
        if len(pts) < 3:
            continue
        coords = [list(p) for p in pts] + [list(pts[0])]
        if signed_area(pts) < 0 or not polygons:  # clockwise -> new outer ring
            polygons.append([coords])
        else:
            polygons[-1].append(coords)
    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def save_geojson(features: list[dict], path, crs: str, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    collection = {
        "type": "FeatureCollection",
        "crs_note": crs,  # coordinates are in this CRS, not WGS84
        "source": source,
        "features": features,
    }
    with open(path, "w") as f:
        json.dump(collection, f)


# ---- finding layer URLs in a saved copy of the map page ---------------------------

_SERVICE_URL = re.compile(
    r"https?://[^\s\"'<>()\\]+?/rest/services/[^\s\"'<>()\\]+?/(?:FeatureServer|MapServer)(?:/\d+)?",
    re.IGNORECASE,
)
_ITEM_ID = re.compile(
    r"(?:webmap|webMap|itemId|item_id|portalItem|\bid)\W{1,6}([0-9a-f]{32})\b", re.IGNORECASE
)
_PORTAL = re.compile(r"(?:portalUrl|portal_url)\W{1,6}(https?://[^\s\"'<>\\]+)", re.IGNORECASE)


def scan_text_for_arcgis(text: str) -> dict[str, list[str]]:
    """Find ArcGIS service URLs, web map item ids and portal URLs in saved JS/HTML."""

    def uniq(items):
        return list(dict.fromkeys(items))

    return {
        "service_urls": uniq(m.group(0).rstrip("/") for m in _SERVICE_URL.finditer(text)),
        "item_ids": uniq(m.group(1).lower() for m in _ITEM_ID.finditer(text)),
        "portals": uniq(m.group(1).rstrip("/") for m in _PORTAL.finditer(text)),
    }
