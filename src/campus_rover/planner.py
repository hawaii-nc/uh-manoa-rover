"""Time-dependent, crowd-aware route planner.

cost(edge, t) = length * ( w_distance
                         + w_crowd      * density(edge, t)
                         + w_complexity * complexity
                         + w_slope      * (slope / max_slope)^2 )

Stairs, closed edges and edges steeper than max_slope are impassable.
Crowd density is read at the time the rover *reaches* each edge, so a route
that passes the mall right at a class change pays for it. The rover also
slows in crowds, which shifts later arrival times.

Search: Dijkstra over cost, carrying arrival time with each label. With
time-dependent costs this is a heuristic (it doesn't consider waiting), but
it's exact when costs don't change during the trip, and it works well for
walk-length trips where conditions change over minutes.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

import numpy as np

from .crowd import CrowdModel
from .graph import CampusGraph
from .schedule import WeekTime
from .site import PlannerParams

CROWD_AWARE = "crowd_aware"
SHORTEST = "shortest"


class NoRouteError(RuntimeError):
    pass


@dataclass
class Leg:
    u: str
    v: str
    name: str | None
    zone: str | None
    kind: str
    length: float
    density: float
    enter: WeekTime
    duration_s: float
    cost: float


@dataclass
class Plan:
    mode: str
    depart: WeekTime
    waypoints: list[str]
    nodes: list[str] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)

    @property
    def distance_m(self) -> float:
        return sum(leg.length for leg in self.legs)

    @property
    def duration_s(self) -> float:
        return sum(leg.duration_s for leg in self.legs)

    @property
    def arrive(self) -> WeekTime:
        return self.depart.plus_seconds(self.duration_s)

    @property
    def cost(self) -> float:
        return sum(leg.cost for leg in self.legs)

    @property
    def crowd_exposure(self) -> float:
        """Approximate number of people passed: sum of density * length / 100."""
        return sum(leg.density * leg.length / 100.0 for leg in self.legs)

    @property
    def peak_density(self) -> float:
        return max((leg.density for leg in self.legs), default=0.0)

    def edge_set(self) -> set[frozenset[str]]:
        return {frozenset((leg.u, leg.v)) for leg in self.legs}

    def segments(self) -> list[tuple[str, float, float]]:
        """Consecutive legs grouped by name: (name, total length, max density)."""
        out: list[tuple[str, float, float]] = []
        for leg in self.legs:
            name = leg.name or leg.zone or f"{leg.u}-{leg.v}"
            if out and out[-1][0] == name:
                prev = out[-1]
                out[-1] = (name, prev[1] + leg.length, max(prev[2], leg.density))
            else:
                out.append((name, leg.length, leg.density))
        return out


class RoutePlanner:
    def __init__(self, graph: CampusGraph, crowd: CrowdModel, params: PlannerParams) -> None:
        self.graph = graph
        self.crowd = crowd
        self.params = params

    @classmethod
    def from_site(cls, site) -> RoutePlanner:
        graph = site.load_graph()
        return cls(graph, CrowdModel.from_site(site, graph), site.planner)

    # ---- edge model ----------------------------------------------------------

    def passable(self, data: dict) -> bool:
        if data["closed"]:
            return False
        if data["kind"] == "stairs" and not self.params.allow_stairs:
            return False
        if self._too_narrow(data) or self._no_curb_cut(data):
            return False
        return abs(data["slope"]) <= self.params.max_slope

    def _too_narrow(self, data: dict) -> bool:
        width = data.get("width")
        return bool(self.params.min_width_m and width is not None
                    and width < self.params.min_width_m)

    def _no_curb_cut(self, data: dict) -> bool:
        return bool(self.params.require_curb_cut and data["kind"] == "crosswalk"
                    and data.get("curb_cut") is False)

    def why_impassable(self, data: dict) -> str | None:
        if data["closed"]:
            return f"closed ({data['closed_reason']})"
        if data["kind"] == "stairs" and not self.params.allow_stairs:
            return "stairs"
        if self._too_narrow(data):
            return f"too narrow ({data['width']:.1f} m < {self.params.min_width_m:.1f} m)"
        if self._no_curb_cut(data):
            return "no curb cut"
        if abs(data["slope"]) > self.params.max_slope:
            return f"too steep ({abs(data['slope']):.0%} > {self.params.max_slope:.1%})"
        return None

    def evaluate(self, u: str, v: str, t: WeekTime, mode: str = CROWD_AWARE) -> Leg:
        data = self.graph.g.edges[u, v]
        p = self.params
        density = self.crowd.density(u, v, t)
        speed = p.speed_mps / (1.0 + density / p.crowd_halfspeed_density)
        length = data["length"]
        if mode == SHORTEST:
            cost = length
        else:
            slope_term = (abs(data["slope"]) / p.max_slope) ** 2 if p.max_slope > 0 else 0.0
            cost = length * (
                p.w_distance
                + p.w_crowd * density
                + p.w_complexity * data["complexity"]
                + p.w_slope * slope_term
            )
        return Leg(
            u=u,
            v=v,
            name=data.get("name"),
            zone=data.get("zone"),
            kind=data["kind"],
            length=length,
            density=density,
            enter=t,
            duration_s=length / speed,
            cost=cost,
        )

    # ---- diagnosis ------------------------------------------------------------

    def blockers(self, src: str, dst: str, penalty_m: float = 500.0) -> dict:
        """The route that crosses the least impassable ground, and every stretch of it that
        a rover cannot use. Used to say *why* there is no route, and where to look.

        Returns {"length_m", "path", "stretches": [{reason, kind, from, to, length_m,
        max_grade}]} with consecutive blocked edges of one reason merged."""
        g = self.graph.g
        if src not in g or dst not in g:
            raise NoRouteError(f"unknown node {src!r} or {dst!r}")
        import networkx as nx

        weights = nx.Graph()
        for u, v, d in g.edges(data=True):
            weights.add_edge(u, v, w=d["length"] + (0.0 if self.passable(d) else penalty_m))
        try:
            path = nx.shortest_path(weights, src, dst, weight="w")
        except nx.NetworkXNoPath as e:
            gap = self.closest_approach(src, dst)
            raise NoRouteError(
                f"{src!r} and {dst!r} are in different pieces of the map; the two pieces come "
                f"within {gap['distance_m']:.1f} m of each other, at ({gap['a'][0]:.0f}, "
                f"{gap['a'][1]:.0f}) and ({gap['b'][0]:.0f}, {gap['b'][1]:.0f})") from e
        stretches, length = [], 0.0
        for u, v in itertools.pairwise(path):
            d = g.edges[u, v]
            length += d["length"]
            reason = self.why_impassable(d)
            if reason is None:
                continue
            kind = reason.split(" (")[0]
            end = self.graph.xy(v)
            if stretches and stretches[-1]["reason"] == kind and stretches[-1]["_last"] == u:
                last = stretches[-1]
                last.update(to=end, _last=v, length_m=last["length_m"] + d["length"],
                            max_grade=max(last["max_grade"], abs(d["slope"])))
            else:
                stretches.append({"reason": kind, "kind": d["kind"], "from": self.graph.xy(u),
                                  "to": end, "_last": v, "length_m": d["length"],
                                  "max_grade": abs(d["slope"])})
        for st in stretches:
            st.pop("_last", None)
        return {"length_m": length, "path": path, "stretches": stretches}

    def closest_approach(self, src: str, dst: str) -> dict:
        """Where the map pieces holding two nodes come closest, ignoring edge passability."""
        import networkx as nx

        g = self.graph.g
        piece = {n: i for i, c in enumerate(nx.connected_components(g)) for n in c}
        a_nodes = [n for n in g if piece[n] == piece[src]]
        b_nodes = [n for n in g if piece[n] == piece[dst]]
        a = np.array([self.graph.xy(n) for n in a_nodes])
        b = np.array([self.graph.xy(n) for n in b_nodes])
        best = (float("inf"), None, None)
        for p in a:
            d = np.hypot(b[:, 0] - p[0], b[:, 1] - p[1])
            j = int(d.argmin())
            if d[j] < best[0]:
                best = (float(d[j]), tuple(p), tuple(b[j]))
        return {"distance_m": best[0], "a": best[1], "b": best[2]}

    def _no_route_message(self, src: str, dst: str) -> str:
        message = f"no passable route from {src!r} to {dst!r}"
        try:
            info = self.blockers(src, dst)
        except NoRouteError as e:
            return f"{message}: {e}"
        parts = [f"{s['reason']} at ({s['from'][0]:.0f}, {s['from'][1]:.0f}), "
                 f"{s['length_m']:.0f} m" for s in info["stretches"][:4]]
        more = len(info["stretches"]) - 4
        return (f"{message}. The least-blocked route is {info['length_m']:.0f} m and crosses "
                f"{len(info['stretches'])} impassable stretch(es): " + "; ".join(parts)
                + (f"; and {more} more" if more > 0 else ""))

    # ---- search --------------------------------------------------------------

    def plan(self, waypoints: list[str], depart: WeekTime, mode: str = CROWD_AWARE) -> Plan:
        """Plan through landmark-node waypoints, in order."""
        if len(waypoints) < 2:
            raise ValueError("need at least two waypoints")
        plan = Plan(mode=mode, depart=depart, waypoints=list(waypoints), nodes=[waypoints[0]])
        t = depart
        for src, dst in itertools.pairwise(waypoints):
            legs = self._search(src, dst, t, mode)
            for leg in legs:
                plan.legs.append(leg)
                plan.nodes.append(leg.v)
                t = t.plus_seconds(leg.duration_s)
        return plan

    def _search(self, src: str, dst: str, t0: WeekTime, mode: str) -> list[Leg]:
        g = self.graph.g
        for n in (src, dst):
            if n not in g:
                raise NoRouteError(f"unknown node {n!r}")
        if src == dst:
            return []
        best = {src: 0.0}
        back: dict[str, Leg] = {}
        tie = itertools.count()
        heap = [(0.0, next(tie), src, t0)]
        while heap:
            cost, _, node, t = heapq.heappop(heap)
            if node == dst:
                break
            if cost > best.get(node, float("inf")):
                continue
            for nbr in g.neighbors(node):
                if not self.passable(g.edges[node, nbr]):
                    continue
                leg = self.evaluate(node, nbr, t, mode)
                new_cost = cost + leg.cost
                if new_cost < best.get(nbr, float("inf")):
                    best[nbr] = new_cost
                    back[nbr] = leg
                    heapq.heappush(heap, (new_cost, next(tie), nbr, t.plus_seconds(leg.duration_s)))
        if dst not in back:
            raise NoRouteError(self._no_route_message(src, dst))
        legs = []
        node = dst
        while node != src:
            leg = back[node]
            legs.append(leg)
            node = leg.u
        return legs[::-1]


# ---- explanation ---------------------------------------------------------------


@dataclass
class Comparison:
    chosen: Plan
    baseline: Plan
    reasons: list[str]

    @property
    def same_route(self) -> bool:
        return self.chosen.nodes == self.baseline.nodes


def compare(planner: RoutePlanner, waypoints: list[str], depart: WeekTime) -> Comparison:
    """Crowd-aware plan vs. the shortest passable path, with plain-language reasons."""
    chosen = planner.plan(waypoints, depart, CROWD_AWARE)
    baseline = planner.plan(waypoints, depart, SHORTEST)
    # Re-score the baseline with crowd-aware costs so totals are comparable.
    baseline = _rescore(planner, baseline)
    reasons = []
    if chosen.nodes != baseline.nodes:
        avoided = [
            leg for leg in baseline.legs if frozenset((leg.u, leg.v)) not in chosen.edge_set()
        ]
        worst = max(avoided, key=lambda leg: leg.density, default=None)
        if worst is not None and worst.density > 0:
            where = worst.name or worst.zone or f"{worst.u}-{worst.v}"
            reasons.append(
                f"Avoids {where}: about {worst.density:.0f} people/100 m expected at {worst.enter}."
            )
        extra_m = chosen.distance_m - baseline.distance_m
        extra_s = chosen.duration_s - baseline.duration_s
        if extra_s >= 0:
            timing = f"{extra_s / 60:.1f} min slower"
        else:
            timing = f"{-extra_s / 60:.1f} min faster, since the rover slows down in crowds"
        reasons.append(f"Detour: {extra_m:+.0f} m and {timing}, vs. the shortest passable path.")
        if baseline.crowd_exposure > 0:
            cut = 1 - chosen.crowd_exposure / baseline.crowd_exposure
            reasons.append(
                f"Crowd exposure: {chosen.crowd_exposure:.0f} vs. {baseline.crowd_exposure:.0f}"
                f" people passed ({cut:.0%} fewer)."
            )
    else:
        reasons.append("Shortest passable path is also the best: no crowd worth avoiding.")
    return Comparison(chosen, baseline, reasons)


def _rescore(planner: RoutePlanner, plan: Plan) -> Plan:
    out = Plan(mode=plan.mode, depart=plan.depart, waypoints=plan.waypoints, nodes=[plan.nodes[0]])
    t = plan.depart
    for leg in plan.legs:
        new = planner.evaluate(leg.u, leg.v, t, CROWD_AWARE)
        out.legs.append(new)
        out.nodes.append(new.v)
        t = t.plus_seconds(new.duration_s)
    return out
