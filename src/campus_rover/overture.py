"""Import walkways and roads from Overture Maps (which republishes OpenStreetMap).

Use this when the OSM Overpass server is unreachable: Overture's files sit in a
public S3 bucket and carry the same OSM ways. `segments_to_campus_graph` is
pure, so it's tested offline; `fetch_segments` needs `pyarrow` and network.
"""

from __future__ import annotations

import math

import networkx as nx

from .graph import EDGE_DEFAULTS, CampusGraph

BUCKET = "overturemaps-us-west-2"
WALK_CLASSES = {"footway", "pedestrian", "path", "steps", "cycleway", "living_street"}
ROADSIDE_CLASSES = {"service", "residential", "unclassified", "tertiary"}
# Typical full widths in meters, used to paint road surfaces in the 3D scene.
ROAD_WIDTH_M = {
    "motorway": 14.0, "primary": 12.0, "secondary": 10.0, "tertiary": 8.0,
    "residential": 7.0, "unclassified": 6.5, "living_street": 6.0, "service": 5.5,
}


def segment_edge_attrs(seg: dict) -> dict | None:
    """Edge attributes for one segment, or None if a sidewalk robot can't use it."""
    cls, sub = seg.get("class"), seg.get("subclass")
    if cls not in WALK_CLASSES and cls not in ROADSIDE_CLASSES:
        return None
    attrs = dict(EDGE_DEFAULTS)
    attrs["name"] = seg.get("name")
    if cls == "steps":
        attrs["kind"] = "stairs"
    elif sub == "crosswalk":
        attrs["kind"] = "crosswalk"
        attrs["complexity"] = 0.6
    if cls in ROADSIDE_CLASSES:
        attrs["zone"] = "roadside"
        attrs["complexity"] = 0.7 if sub == "parking_aisle" else 0.5
    return attrs


def segments_to_campus_graph(segments: list[dict], snap_m: float = 0.05) -> CampusGraph:
    """Build an undirected walkway graph from segments.

    Each segment is a dict with `coords` ([(x, y), ...] in meters), `class`, and
    optionally `subclass` and `name`. OSM ways share exact vertices where they
    meet, so vertices that round to the same spot become one node.
    """
    g = nx.Graph()

    def node(p) -> str:
        key = f"{round(p[0] / snap_m):d}_{round(p[1] / snap_m):d}"
        if key not in g:
            g.add_node(key, x=float(p[0]), y=float(p[1]), label=None)
        return key

    for seg in segments:
        attrs = segment_edge_attrs(seg)
        if attrs is None:
            continue
        coords = seg["coords"]
        for a, b in zip(coords, coords[1:], strict=False):
            u, v = node(a), node(b)
            if u == v:
                continue
            length = math.dist(a, b)
            if g.has_edge(u, v) and g.edges[u, v]["length"] <= length:
                continue
            g.add_edge(u, v, **{**attrs, "length": length})
    # Keep the biggest connected piece: stray bits can't be reached anyway.
    if g.number_of_nodes():
        biggest = max(nx.connected_components(g), key=len)
        g = g.subgraph(biggest).copy()
    return CampusGraph(g)


def _fetch(theme: str, kind: str, bbox, columns, release, proxy):  # pragma: no cover - network
    import os

    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    import pyarrow.fs as fs

    proxy = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    s3 = fs.S3FileSystem(anonymous=True, region="us-west-2", proxy_options=proxy,
                         request_timeout=120, connect_timeout=30)
    if release is None:
        listing = s3.get_file_info(fs.FileSelector(f"{BUCKET}/release", recursive=False))
        release = sorted(info.base_name for info in listing)[-1]
    data = ds.dataset(f"{BUCKET}/release/{release}/theme={theme}/type={kind}/",
                      filesystem=s3, format="parquet")
    west, south, east, north = bbox
    inside = ((pc.field("bbox", "xmin") < east) & (pc.field("bbox", "xmax") > west)
              & (pc.field("bbox", "ymin") < north) & (pc.field("bbox", "ymax") > south))
    return data.to_table(filter=inside, columns=columns).to_pylist()


def fetch_buildings(bbox, crs: str, release: str | None = None,
                    proxy: str | None = None) -> list[dict]:  # pragma: no cover - network
    """Building footprints inside bbox (west, south, east, north), as dicts with
    `rings` ([[(x, y), ...], ...] in `crs`), `name` and `height` (often None)."""
    from pyproj import Transformer
    from shapely import wkb

    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    out = []
    for row in _fetch("buildings", "building", bbox, ["geometry", "names", "height"],
                      release, proxy):
        geom = wkb.loads(row["geometry"])
        for poly in getattr(geom, "geoms", [geom]):
            if poly.geom_type != "Polygon":
                continue
            rings = [[project(x, y) for x, y in ring.coords]
                     for ring in [poly.exterior, *poly.interiors]]
            out.append({"rings": rings, "name": (row.get("names") or {}).get("primary"),
                        "height": row.get("height")})
    return out


def fetch_segments(bbox, crs: str, release: str | None = None,
                   proxy: str | None = None) -> list[dict]:  # pragma: no cover - network
    """Download road and path segments inside bbox (west, south, east, north).

    Returns dicts with `coords` projected into `crs`, `class`, `subclass`, `name`.
    """
    from pyproj import Transformer
    from shapely import wkb

    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    out = []
    for row in _fetch("transportation", "segment", bbox,
                      ["geometry", "class", "subclass", "names"], release, proxy):
        geom = wkb.loads(row["geometry"])
        if geom.geom_type != "LineString":
            continue
        names = row.get("names") or {}
        out.append({
            "coords": [project(x, y) for x, y in geom.coords],
            "class": row["class"], "subclass": row.get("subclass"),
            "name": names.get("primary"),
        })
    return out
