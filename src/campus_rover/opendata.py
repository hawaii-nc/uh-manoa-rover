"""Official open data used to build and to check the 3D world.

Every source here is queried over the network and cached; nothing is made up.
Each one has a single job:

| Source | Job |
|---|---|
| Honolulu building footprints (CCH) | Footprints for buildings UH's map lacks; height check |
| Honolulu 5 ft contours             | Check the lidar terrain |
| State of Hawaiʻi 100 ft contours   | Check the lidar terrain |
| Honolulu zoning                    | Record the land-use zones in the corridor |
| PacIOOS GeoServer                  | Government land ownership, watershed, streams |

`fetch_open_data` writes one JSON file per source plus `provenance.json`, which
says what was asked, when, and how many features came back. The checks
(`contour_check`, `height_check`) are pure functions, tested offline.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

FT = 0.3048
_CCH = "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services"
SOURCES = {
    "cch_footprints": {
        "title": "Building Footprints (CCH)",
        "publisher": "City and County of Honolulu, Dept. of Planning and Permitting (HoLIS)",
        "portal": "https://honolulu-cchnl.opendata.arcgis.com",
        "url": f"{_CCH}/Structures_Facilities/FeatureServer/0",
    },
    "cch_contours_5ft": {
        "title": "Elevation 5ft (urban contours)",
        "publisher": "City and County of Honolulu (HoLIS)",
        "portal": "https://honolulu-cchnl.opendata.arcgis.com",
        "url": f"{_CCH}/Elevation_5ft/FeatureServer/0",
    },
    "cch_zoning": {
        "title": "Zoning - Land Use Ordinance",
        "publisher": "City and County of Honolulu, Dept. of Planning and Permitting",
        "portal": "https://honolulu-cchnl.opendata.arcgis.com",
        "url": f"{_CCH}/Zoning_Reg/FeatureServer/0",
    },
    "state_contours_100ft": {
        "title": "Oahu Elevation Contours 100ft",
        "publisher": "USGS; Hawaii Statewide GIS Program",
        "portal": "https://geoportal.hawaii.gov",
        "url": "https://geodata.hawaii.gov/arcgis/rest/services/Elevation/MapServer/8",
    },
}
PACIOOS_WFS = "https://geo.pacioos.hawaii.edu/geoserver/PACIOOS/ows"
PACIOOS_LAYERS = {
    "pacioos_gov_land": ("PACIOOS:hi_hcgg_all_govjurisdiction",
                         "Government Land Ownership - Hawaii"),
    "pacioos_watersheds": ("PACIOOS:hi_sohdop_all_wshed", "Watersheds - Hawaii"),
    "pacioos_streams": ("PACIOOS:hi_hcgg_all_darstreams", "Streams - Hawaii"),
}


# ---- checks (pure) -----------------------------------------------------------------


def contour_lines(features: list[dict], field: str, unit_m: float) -> list[tuple[list, float]]:
    """GeoJSON-style contour features -> [(coords, elevation in meters)]."""
    out = []
    for f in features:
        props = {str(k).lower(): v for k, v in (f.get("properties") or {}).items()}
        geom = f.get("geometry") or {}
        try:
            elev = float(props[field.lower()]) * unit_m
        except (KeyError, TypeError, ValueError):
            continue
        parts = geom.get("coordinates") or []
        if geom.get("type") == "LineString":
            parts = [parts]
        for part in parts:
            out.append(([(float(p[0]), float(p[1])) for p in part], elev))
    return out


def contour_check(rasters, lines: list[tuple[list, float]]) -> dict:
    """How far the lidar ground is from published contour lines.

    Samples the ground grid under every contour vertex inside the grid. A
    contour drawn at 200 ft should sit on ground that is 200 ft high."""
    x0, y0, x1, y1 = rasters.bounds
    diffs, levels = [], set()
    for coords, elev in lines:
        pts = np.array([p for p in coords if x0 < p[0] < x1 and y0 < p[1] < y1])
        if len(pts) == 0:
            continue
        diffs.append(np.asarray(rasters.ground_z(pts[:, 0], pts[:, 1])) - elev)
        levels.add(round(elev, 1))
    if not diffs:
        return {"points": 0}
    d = np.concatenate(diffs)
    return {"points": int(len(d)), "levels_m": sorted(levels),
            "mean_diff_m": round(float(d.mean()), 2),
            "median_abs_diff_m": round(float(np.median(np.abs(d))), 2),
            "p90_abs_diff_m": round(float(np.percentile(np.abs(d), 90)), 2)}


def height_check(buildings: list[tuple[object, float]], footprints: list[tuple[object, float]],
                 min_overlap: float = 0.5) -> dict:
    """Compare our lidar building heights with an independent set of heights.

    buildings / footprints: [(shapely polygon, height in meters)]. Each of our
    buildings is matched to the published footprints that lie mostly inside it."""
    from shapely import STRtree

    if not buildings or not footprints:
        return {"matched": 0}
    polys = [p for p, _ in footprints]
    tree = STRtree(polys)
    diffs = []
    for poly, height in buildings:
        best = None
        for j in tree.query(poly):
            other, other_h = footprints[int(j)]
            if other_h and other.intersection(poly).area >= min_overlap * other.area:
                best = other_h if best is None else max(best, other_h)
        if best is not None:
            diffs.append(height - best)
    if not diffs:
        return {"matched": 0}
    d = np.array(diffs)
    return {"matched": int(len(d)), "median_diff_m": round(float(np.median(d)), 2),
            "median_abs_diff_m": round(float(np.median(np.abs(d))), 2),
            "within_3m": round(float((np.abs(d) <= 3).mean()), 2)}


def footprint_height(props: dict) -> float | None:
    """Building height in meters from a CCH footprint's attributes."""
    lower = {str(k).lower(): v for k, v in props.items()}
    for key, unit in (("maxht_m", 1.0), ("gis_height", FT), ("nga_height", FT)):
        try:
            value = float(lower.get(key))
        except (TypeError, ValueError):
            continue
        if 1.5 < value * unit < 200:
            return value * unit
    return None


# ---- network -----------------------------------------------------------------------


def _pacioos_json(type_name: str, bbox_lonlat, crs: str) -> dict:  # pragma: no cover - network
    """WFS GetFeature. This server only offers an old key exchange, so the cipher
    level is lowered for it alone; its certificate is still checked."""
    import ssl
    import urllib.parse
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    west, south, east, north = bbox_lonlat
    query = urllib.parse.urlencode({
        "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typeName": type_name,
        "outputFormat": "application/json", "srsName": crs,
        "bbox": f"{west},{south},{east},{north},EPSG:4326",
    })
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    with opener.open(f"{PACIOOS_WFS}?{query}", timeout=90) as resp:
        return json.load(resp)


def fetch_open_data(site, bbox, out_dir: Path, force: bool = False,
                    progress=print) -> dict:  # pragma: no cover - network
    """Download every source for bbox (x0, y0, x1, y1 in the site CRS)."""
    from pyproj import Transformer

    from .arcgis import query_layer

    out_dir.mkdir(parents=True, exist_ok=True)
    prov_path = out_dir / "provenance.json"
    prov = json.loads(prov_path.read_text()) if prov_path.exists() else {}
    today = dt.date.today().isoformat()
    for key, src in SOURCES.items():
        path = out_dir / f"{key}.json"
        if path.exists() and not force:
            continue
        try:
            feats = query_layer(src["url"], site.crs, bbox=bbox, pause_s=0.3)
        except Exception as e:      # one source being down must not lose the others
            progress(f"{key:<22} FAILED: {e}")
            prov[key] = {**src, "retrieved": today, "error": str(e)[:200]}
            continue
        path.write_text(json.dumps(feats))
        prov[key] = {**src, "retrieved": today, "features": len(feats)}
        progress(f"{key:<22}{len(feats):>6} features -> {path}")
    to_lonlat = Transformer.from_crs(site.crs, "EPSG:4326", always_xy=True).transform
    west, south = to_lonlat(bbox[0], bbox[1])
    east, north = to_lonlat(bbox[2], bbox[3])
    for key, (type_name, title) in PACIOOS_LAYERS.items():
        path = out_dir / f"{key}.json"
        if path.exists() and not force:
            continue
        src = {"title": title, "publisher": "PacIOOS (Pacific Islands Ocean Observing System)",
               "portal": "https://geo.pacioos.hawaii.edu/geoserver", "url": PACIOOS_WFS,
               "layer": type_name}
        try:
            data = _pacioos_json(type_name, (west, south, east, north), site.crs)
        except Exception as e:
            progress(f"{key:<22} FAILED: {e}")
            prov[key] = {**src, "retrieved": today, "error": str(e)[:200]}
            continue
        feats = data.get("features", [])
        path.write_text(json.dumps(feats))
        prov[key] = {**src, "retrieved": today, "features": len(feats)}
        progress(f"{key:<22}{len(feats):>6} features -> {path}")
    # UH Mānoa MAGIS (library map collection): a reference, not a data service. Record
    # that it was consulted; the campus building index itself comes from UH's campus map.
    if "uh_magis" not in prov or force:
        import urllib.request

        url = "https://guides.library.manoa.hawaii.edu/magis"
        entry = {"title": "Maps, Aerial Photographs, and GIS (MAGIS)",
                 "publisher": "University of Hawaiʻi at Mānoa Library", "url": url,
                 "retrieved": today,
                 "note": "Research guide; it serves no machine-readable campus layers. Building "
                         "names, numbers and abbreviations come from the UH campus map."}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "campus-rover/0.1"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                entry["http_status"] = resp.status
        except Exception as e:
            entry["error"] = str(e)[:200]
        prov["uh_magis"] = entry
    prov_path.write_text(json.dumps(prov, indent=1))
    return prov


def region_summary(folder: Path) -> dict:
    """Names from the regional layers: who owns the land, which watershed, which zones."""
    def names(key: str, field: str) -> list[str]:
        path = folder / f"{key}.json"
        if not path.exists():
            return []
        feats = json.loads(path.read_text())
        return sorted({str((f.get("properties") or {}).get(field)) for f in feats} - {"None"})

    return {"land_owners": names("pacioos_gov_land", "MajorOwner"),
            "watersheds": names("pacioos_watersheds", "wuname"),
            "streams": names("pacioos_streams", "STREAM_NAM"),
            "zoning": names("cch_zoning", "zone_class")}
