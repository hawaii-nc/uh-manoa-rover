"""Campus walkway graph: nodes in metric x/y, edges with walkway attributes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import yaml

EDGE_KINDS = {"walkway", "stairs", "ramp", "crosswalk"}

EDGE_DEFAULTS = {
    "kind": "walkway",
    "zone": None,
    "slope": 0.0,  # rise/run; sign ignored for now
    "complexity": 0.0,  # 0 = trivial to read, 1 = blind corners, mixed traffic, exits
    "name": None,
    "closed": False,
    "closed_reason": None,
}


@dataclass(frozen=True)
class Closure:
    reason: str
    edge: tuple[str, str] | None = None
    node: str | None = None
    near_landmark: str | None = None
    radius_m: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> Closure:
        edge = tuple(data["edge"]) if "edge" in data else None
        return cls(
            reason=data.get("reason", "closed"),
            edge=edge,
            node=data.get("node"),
            near_landmark=data.get("near_landmark"),
            radius_m=float(data.get("radius_m", 0.0)),
        )


class CampusGraph:
    """Undirected walkway graph. Lengths are meters."""

    def __init__(self, graph: nx.Graph, landmarks: dict[str, str] | None = None) -> None:
        self.g = graph
        # Landmark key -> node id, filled by importers (e.g. OSM name matching).
        self.landmarks = dict(landmarks or {})

    # ---- construction / IO -------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> CampusGraph:
        g = nx.Graph()
        for node_id, attrs in (data.get("nodes") or {}).items():
            x, y = attrs["xy"]
            g.add_node(str(node_id), x=float(x), y=float(y), label=attrs.get("label"))
        for edge in data.get("edges") or []:
            u, v = str(edge["u"]), str(edge["v"])
            for n in (u, v):
                if n not in g:
                    raise ValueError(f"edge {u}-{v} references unknown node {n!r}")
            attrs = {**EDGE_DEFAULTS, **{k: val for k, val in edge.items() if k not in ("u", "v")}}
            if attrs["kind"] not in EDGE_KINDS:
                raise ValueError(f"edge {u}-{v}: unknown kind {attrs['kind']!r}")
            if "length" not in edge:
                attrs["length"] = _dist(g.nodes[u], g.nodes[v])
            attrs["length"] = float(attrs["length"])
            attrs["slope"] = float(attrs["slope"])
            attrs["complexity"] = float(attrs["complexity"])
            g.add_edge(u, v, **attrs)
        return cls(g, data.get("landmarks"))

    @classmethod
    def load(cls, path: Path) -> CampusGraph:
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    def to_dict(self) -> dict:
        nodes = {}
        for n, a in self.g.nodes(data=True):
            entry = {"xy": [round(a["x"], 2), round(a["y"], 2)]}
            if a.get("label"):
                entry["label"] = a["label"]
            nodes[n] = entry
        edges = []
        for u, v, a in self.g.edges(data=True):
            entry = {"u": u, "v": v, "length": round(a["length"], 2)}
            for key, default in EDGE_DEFAULTS.items():
                if a.get(key, default) != default and key not in ("closed", "closed_reason"):
                    entry[key] = a[key]
            edges.append(entry)
        data = {"nodes": nodes, "edges": edges}
        if self.landmarks:
            data["landmarks"] = dict(self.landmarks)
        return data

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False, allow_unicode=True)

    # ---- geometry ----------------------------------------------------------

    def xy(self, node: str) -> tuple[float, float]:
        a = self.g.nodes[node]
        return a["x"], a["y"]

    def midpoint(self, u: str, v: str) -> tuple[float, float]:
        (x1, y1), (x2, y2) = self.xy(u), self.xy(v)
        return (x1 + x2) / 2, (y1 + y2) / 2

    def nearest_node(self, x: float, y: float) -> str:
        return min(self.g.nodes, key=lambda n: math.dist(self.xy(n), (x, y)))

    # ---- landmarks & closures ---------------------------------------------

    def resolve_landmark(self, key: str, spec: dict) -> str:
        node = spec.get("node") or self.landmarks.get(key)
        if node is None:
            raise KeyError(
                f"landmark {key!r} has no node. Set `node:` in site.yaml or re-run the importer."
            )
        if node not in self.g:
            raise KeyError(f"landmark {key!r} points at unknown node {node!r}")
        return node

    def apply_closures(self, closures: list[Closure], landmark_nodes: dict[str, str]) -> int:
        """Mark edges closed. Returns the number of edges newly closed."""
        closed = 0

        def close(u: str, v: str, reason: str) -> None:
            nonlocal closed
            data = self.g.edges[u, v]
            if not data["closed"]:
                data["closed"] = True
                data["closed_reason"] = reason
                closed += 1

        for c in closures:
            if c.edge:
                close(*c.edge, c.reason)
            if c.node:
                for nbr in list(self.g.neighbors(c.node)):
                    close(c.node, nbr, c.reason)
            if c.near_landmark:
                cx, cy = self.xy(landmark_nodes[c.near_landmark])
                for u, v in list(self.g.edges):
                    near_u = math.dist(self.xy(u), (cx, cy)) <= c.radius_m
                    near_v = math.dist(self.xy(v), (cx, cy)) <= c.radius_m
                    near_mid = math.dist(self.midpoint(u, v), (cx, cy)) <= c.radius_m
                    if near_u or near_v or near_mid:
                        close(u, v, c.reason)
        return closed


def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])
