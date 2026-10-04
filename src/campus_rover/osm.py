"""Import an OpenStreetMap walkway graph into the campus graph format.

`fetch_site_graph` needs network access (Overpass API) and `osmnx`.
The conversion functions are pure, so they're tested offline.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

import networkx as nx

from .graph import EDGE_DEFAULTS, CampusGraph

# Ways a sidewalk robot may use. Campus sidewalks are often not mapped
# separately, so minor roads are included as "roadside" (their sidewalk),
# with a higher complexity cost.
OSM_FILTER = (
    '["highway"~"footway|pedestrian|path|steps|living_street|service|cycleway|'
    'residential|unclassified|tertiary"]["access"!~"private|no"]'
)
ROADSIDE = {"service", "residential", "unclassified", "tertiary", "living_street"}

_PERCENT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")
_DEGREES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*°\s*$")


def parse_incline(value) -> float:
    """OSM incline tag -> slope (rise/run). 'up'/'down'/unknown -> 0 (unknown)."""
    if value is None:
        return 0.0
    if isinstance(value, list):
        return max((parse_incline(v) for v in value), key=abs, default=0.0)
    text = str(value)
    if m := _PERCENT.match(text):
        return float(m.group(1)) / 100.0
    if m := _DEGREES.match(text):
        return math.tan(math.radians(float(m.group(1))))
    return 0.0


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _edge_attrs(data: dict) -> dict:
    highway = data.get("highway")
    highways = set(highway) if isinstance(highway, list) else {highway}
    attrs = dict(EDGE_DEFAULTS)
    attrs["length"] = float(data["length"])
    attrs["name"] = _first(data.get("name"))
    attrs["slope"] = parse_incline(data.get("incline"))
    if "steps" in highways:
        attrs["kind"] = "stairs"
    elif data.get("footway") == "crossing" or data.get("crossing"):
        attrs["kind"] = "crosswalk"
        attrs["complexity"] = 0.6
    elif data.get("ramp") == "yes":
        attrs["kind"] = "ramp"
    if highways & ROADSIDE:
        attrs["zone"] = "roadside"
        attrs["complexity"] = max(attrs["complexity"], 0.5)
    return attrs


def osm_to_campus_graph(osm_graph: nx.MultiDiGraph) -> CampusGraph:
    """Convert a *projected* osmnx graph (meters) to an undirected CampusGraph.

    Parallel edges between the same two nodes keep the shortest one.
    """
    g = nx.Graph()
    for n, data in osm_graph.nodes(data=True):
        g.add_node(str(n), x=float(data["x"]), y=float(data["y"]), label=None)
    for u, v, data in osm_graph.edges(data=True):
        u, v = str(u), str(v)
        if u == v:
            continue
        attrs = _edge_attrs(data)
        if g.has_edge(u, v) and g.edges[u, v]["length"] <= attrs["length"]:
            continue
        g.add_edge(u, v, **attrs)
    return CampusGraph(g)


def match_landmarks(
    features: Iterable[tuple[str, float, float]],
    landmark_specs: dict[str, dict],
    graph: CampusGraph,
) -> tuple[dict[str, str], list[str]]:
    """Match landmark osm_names to named features, then to the nearest graph node.

    features: (name, x, y) in the graph's CRS.
    Returns (landmark key -> node id, list of unmatched keys).
    """
    feats = [(name, x, y) for name, x, y in features if name]
    found: dict[str, str] = {}
    missing: list[str] = []
    for key, spec in landmark_specs.items():
        if "node" in spec:
            continue
        target = spec.get("osm_name", "").casefold()
        exact = [f for f in feats if f[0].casefold() == target]
        partial = [f for f in feats if target and target in f[0].casefold()]
        match = (exact or sorted(partial, key=lambda f: len(f[0])) or [None])[0]
        if match is None:
            missing.append(key)
            continue
        found[key] = graph.nearest_node(match[1], match[2])
    return found, missing


def fetch_site_graph(site) -> tuple[CampusGraph, list[str]]:  # pragma: no cover - network
    """Download walkways + named buildings for the site's bbox. Returns (graph, unmatched)."""
    try:
        import osmnx as ox
    except ImportError as e:
        raise ImportError('OSM import needs osmnx: pip install -e ".[osm]"') from e

    bbox = tuple(site.raw["graph"]["osm"]["bbox"])  # (west, south, east, north)
    osm = ox.graph_from_bbox(bbox, custom_filter=OSM_FILTER, simplify=True, retain_all=False)
    osm = ox.project_graph(osm, to_crs=site.crs)
    graph = osm_to_campus_graph(osm)

    feats = ox.features_from_bbox(bbox, tags={"building": True, "amenity": True})
    feats = ox.projection.project_gdf(feats, to_crs=site.crs)
    named = feats[feats["name"].notna()] if "name" in feats else feats.iloc[0:0]
    points = [
        (row["name"], geom.centroid.x, geom.centroid.y)
        for (_, row), geom in zip(named.iterrows(), named.geometry, strict=True)
    ]
    found, missing = match_landmarks(points, site.landmarks, graph)
    graph.landmarks.update(found)
    for key, node in found.items():
        graph.g.nodes[node]["label"] = site.landmark_label(key)
    return graph, missing
