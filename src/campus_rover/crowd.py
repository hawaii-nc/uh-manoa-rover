"""Time-dependent crowd prior: expected pedestrian density on each walkway.

density(edge, t) = zone baseline(t)
                 + sum over buildings of
                     people_per_change * surge(t) * density_per_person * exp(-d / decay_m)

d is the walking distance from the building's door to the edge. Units are
roughly "people per 100 m of walkway". This is a prior: the parameters
should be calibrated against real crowd counts, and live perception should
override it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx

from .graph import CampusGraph
from .schedule import ClassSchedule, WeekTime, parse_days, parse_hhmm


@dataclass(frozen=True)
class ZoneWindow:
    days: frozenset[int]
    start: int
    end: int
    density: float

    def active(self, t: WeekTime) -> bool:
        return t.day in self.days and self.start <= t.minute_of_day < self.end


@dataclass(frozen=True)
class BuildingSource:
    key: str
    node: str
    people_per_change: float


class CrowdModel:
    def __init__(
        self,
        graph: CampusGraph,
        schedule: ClassSchedule,
        buildings: list[BuildingSource],
        zones: dict[str, list[ZoneWindow]],
        decay_m: float = 120.0,
        density_per_person: float = 0.1,
    ) -> None:
        self.graph = graph
        self.schedule = schedule
        self.buildings = buildings
        self.zones = zones
        self.decay_m = decay_m
        self.density_per_person = density_per_person
        # Per-edge proximity weight to each building: exp(-d / decay).
        self._proximity = self._precompute_proximity()

    @classmethod
    def from_site(cls, site, graph: CampusGraph | None = None) -> CrowdModel:
        graph = graph or site.load_graph()
        cfg = site.crowd
        landmark_nodes = site.landmark_nodes(graph)
        buildings = [
            BuildingSource(key, landmark_nodes[key], float(b["people_per_change"]))
            for key, b in (cfg.get("buildings") or {}).items()
        ]
        zones = {
            zone: [
                ZoneWindow(
                    days=parse_days(w.get("days", "daily")),
                    start=parse_hhmm(w["from"]),
                    end=parse_hhmm(w["to"]),
                    density=float(w["density"]),
                )
                for w in windows
            ]
            for zone, windows in (cfg.get("zones") or {}).items()
        }
        return cls(
            graph,
            site.schedule,
            buildings,
            zones,
            decay_m=float(cfg.get("decay_m", 120.0)),
            density_per_person=float(cfg.get("density_per_person", 0.1)),
        )

    def _precompute_proximity(self) -> dict[tuple[str, str], list[float]]:
        g = self.graph.g
        cutoff = 6 * self.decay_m  # beyond this exp(-d/decay) < 0.25%
        dists = [
            nx.single_source_dijkstra_path_length(g, b.node, cutoff=cutoff, weight="length")
            for b in self.buildings
        ]
        proximity = {}
        for u, v in g.edges:
            weights = []
            for d in dists:
                du, dv = d.get(u), d.get(v)
                if du is None and dv is None:
                    weights.append(0.0)
                    continue
                # Distance to the edge's nearer end, plus a quarter of its length,
                # as a cheap stand-in for "average distance along the edge".
                near = min(x for x in (du, dv) if x is not None)
                weights.append(math.exp(-(near + g.edges[u, v]["length"] / 4) / self.decay_m))
            proximity[_key(u, v)] = weights
        return proximity

    def zone_density(self, zone: str | None, t: WeekTime) -> float:
        if zone is None:
            return 0.0
        # Highest active window wins (e.g. a lunch peak overrides the all-day baseline).
        return max((w.density for w in self.zones.get(zone, []) if w.active(t)), default=0.0)

    def density(self, u: str, v: str, t: WeekTime) -> float:
        """Expected people per 100 m on edge (u, v) at time t."""
        data = self.graph.g.edges[u, v]
        total = self.zone_density(data.get("zone"), t)
        surge = self.schedule.surge(t)
        if surge > 0:
            for b, w in zip(self.buildings, self._proximity[_key(u, v)], strict=True):
                total += b.people_per_change * surge * self.density_per_person * w
        return total


def _key(u: str, v: str) -> tuple[str, str]:
    return (u, v) if u <= v else (v, u)
