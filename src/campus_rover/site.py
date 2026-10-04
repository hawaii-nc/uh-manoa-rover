"""Site configuration: everything that differs between campuses lives in sites/<name>/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .graph import CampusGraph, Closure
from .schedule import ClassSchedule

DEFAULT_SITES_DIR = Path(os.environ.get("CAMPUS_ROVER_SITES", "sites"))


@dataclass
class PlannerParams:
    speed_mps: float = 1.2
    max_slope: float = 0.083
    allow_stairs: bool = False
    # Rover slows in crowds: speed = speed_mps / (1 + density / crowd_halfspeed_density)
    crowd_halfspeed_density: float = 20.0
    w_distance: float = 1.0
    w_crowd: float = 0.05
    w_complexity: float = 0.3
    w_slope: float = 0.5

    @classmethod
    def from_dict(cls, data: dict) -> PlannerParams:
        weights = data.get("weights", {})
        return cls(
            speed_mps=float(data.get("speed_mps", cls.speed_mps)),
            max_slope=float(data.get("max_slope", cls.max_slope)),
            allow_stairs=bool(data.get("allow_stairs", cls.allow_stairs)),
            crowd_halfspeed_density=float(
                data.get("crowd_halfspeed_density", cls.crowd_halfspeed_density)
            ),
            w_distance=float(weights.get("distance", cls.w_distance)),
            w_crowd=float(weights.get("crowd", cls.w_crowd)),
            w_complexity=float(weights.get("complexity", cls.w_complexity)),
            w_slope=float(weights.get("slope", cls.w_slope)),
        )


@dataclass
class Route:
    key: str
    waypoints: list[str]
    label: str


@dataclass
class Site:
    name: str
    root: Path
    description: str
    crs: str
    raw: dict
    landmarks: dict[str, dict]
    routes: dict[str, Route]
    closures: list[Closure]
    crowd: dict
    planner: PlannerParams
    schedule: ClassSchedule
    _graph: CampusGraph | None = field(default=None, repr=False)
    _model: object | None = field(default=None, repr=False)

    @property
    def graph_path(self) -> Path:
        return self.root / self.raw["graph"]["path"]

    def load_graph(self) -> CampusGraph:
        """Load the walkway graph and apply closures. Cached per Site."""
        if self._graph is None:
            spec = self.raw["graph"]
            if spec.get("source", "file") != "file":
                raise ValueError(f"unsupported graph source: {spec['source']!r}")
            if not self.graph_path.exists():
                hint = ""
                if "osm" in spec:
                    hint = (
                        f"\nImport it first: campus-rover import-osm --site {self.name}"
                        " (needs network access to overpass-api.de)"
                    )
                raise FileNotFoundError(f"walkway graph not found: {self.graph_path}{hint}")
            graph = CampusGraph.load(self.graph_path)
            model = self.load_model()
            if model is not None:
                found, _ = model.resolve_building_landmarks(graph, self.landmarks)
                graph.landmarks.update(found)  # building footprints beat OSM name matches
                model.apply_to_graph(graph)
                zones = self.crowd.setdefault("zones", {}) or {}
                self.crowd["zones"] = zones
                for zone, windows in model.crowd_zone_windows().items():
                    zones.setdefault(zone, []).extend(windows)
            graph.apply_closures(self.closures, self.landmark_nodes(graph))
            self._graph = graph
        return self._graph

    def load_model(self):
        """Campus map model from sites/<name>/layers.yaml, or None if the site has none."""
        if self._model is None and (self.root / "layers.yaml").exists():
            from .campus_model import build_model

            self._model = build_model(self)
        return self._model

    def landmark_nodes(self, graph: CampusGraph) -> dict[str, str]:
        return {key: graph.resolve_landmark(key, spec) for key, spec in self.landmarks.items()}

    def landmark_label(self, key: str) -> str:
        return self.landmarks.get(key, {}).get("label", key)


def find_site_file(name_or_path: str, sites_dir: Path = DEFAULT_SITES_DIR) -> Path:
    candidate = Path(name_or_path)
    if candidate.is_file():
        return candidate
    if candidate.is_dir() and (candidate / "site.yaml").is_file():
        return candidate / "site.yaml"
    by_name = sites_dir / name_or_path / "site.yaml"
    if by_name.is_file():
        return by_name
    raise FileNotFoundError(f"no site {name_or_path!r} (looked in {sites_dir.resolve()})")


def load_site(name_or_path: str, sites_dir: Path = DEFAULT_SITES_DIR) -> Site:
    path = find_site_file(name_or_path, sites_dir)
    root = path.parent
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    routes = {
        key: Route(key=key, waypoints=list(r["waypoints"]), label=r.get("label", key))
        for key, r in (raw.get("routes") or {}).items()
    }
    landmarks = raw.get("landmarks") or {}
    for route in routes.values():
        for wp in route.waypoints:
            if wp not in landmarks:
                raise ValueError(f"route {route.key!r} uses unknown landmark {wp!r}")

    sched = raw.get("schedule")
    if isinstance(sched, str):
        schedule = ClassSchedule.load(root / sched)
    else:
        schedule = ClassSchedule.from_dict(sched or {})

    return Site(
        name=raw.get("name", root.name),
        root=root,
        description=raw.get("description", ""),
        crs=raw.get("crs", "local"),
        raw=raw,
        landmarks=landmarks,
        routes=routes,
        closures=[Closure.from_dict(c) for c in raw.get("closures") or []],
        crowd=raw.get("crowd") or {},
        planner=PlannerParams.from_dict(raw.get("planner") or {}),
        schedule=schedule,
    )


def list_sites(sites_dir: Path = DEFAULT_SITES_DIR) -> list[str]:
    if not sites_dir.is_dir():
        return []
    return sorted(p.parent.name for p in sites_dir.glob("*/site.yaml"))
