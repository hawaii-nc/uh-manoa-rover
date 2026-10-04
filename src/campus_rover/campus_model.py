"""The campus map model: UH map layers -> one georeferenced model for planning and sim.

Each layer in sites/<site>/layers.yaml has a *role* that decides how it's used:

  building             footprints -> extruded 3D buildings; landmark matching by name
  construction         polygons -> closed walkways (planner) + barriers (sim)
  tree                 points -> tree assets (sim), with species when available
  prop                 points -> static objects: benches, call boxes, bike racks, ... (sim)
  accessible_entrance  points (elevators, automatic doors) -> preferred building entrances
  crowd_source         points/polygons (eateries, bus stops, forum areas) -> crowd windows
  gcp                  survey benchmarks -> ground control points for georeferencing splats
  vehicle_spawn        parking -> where simulated cars/carts appear

Layer data is GeoJSON already in the site's CRS (meters). See arcgis.py for the
downloader and docs/UH_MAP_MODEL.md for how to find the UH layer URLs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .geometry import (
    Point,
    centroid,
    open_ring,
    point_in_polygon,
    point_ring_distance,
    point_segment_distance,
    segment_touches_polygon,
)
from .graph import CampusGraph

ROLES = {
    "building",
    "construction",
    "tree",
    "prop",
    "accessible_entrance",
    "crowd_source",
    "gcp",
    "vehicle_spawn",
}

NAME_FIELDS = ["name", "bldg_name", "building_name", "buildingname", "label", "title", "facility"]
FLOORS_FIELDS = ["floors", "num_floors", "numfloors", "stories", "levels", "building_levels"]
SPECIES_FIELDS = ["common_name", "commonname", "species", "sci_name", "scientific_name", "name"]


@dataclass
class LayerSpec:
    key: str
    role: str
    title: str = ""
    url: str | None = None
    file: str | None = None
    where: str = "1=1"
    name_field: str | None = None
    height_field: str | None = None
    height_units: str = "m"
    floors_field: str | None = None
    floor_height_m: float = 3.5
    default_height_m: float = 10.0
    species_field: str | None = None
    prop_class: str | None = None
    zone: str | None = None
    radius_m: float = 40.0
    windows: list[dict] = field(default_factory=list)
    reason: str = "construction"

    @classmethod
    def from_dict(cls, key: str, data: dict) -> LayerSpec:
        role = data.get("role")
        if role not in ROLES:
            raise ValueError(f"layer {key!r}: role must be one of {sorted(ROLES)}, got {role!r}")
        known = set(cls.__dataclass_fields__) - {"key"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"layer {key!r}: unknown fields {sorted(unknown)}")
        return cls(key=key, **data)


@dataclass
class Building:
    name: str | None
    rings: list[list[Point]]
    height_m: float
    layer: str


@dataclass
class Area:
    rings: list[list[Point]]
    reason: str
    layer: str


@dataclass
class PointFeature:
    xy: Point
    cls: str
    name: str | None
    layer: str
    props: dict = field(default_factory=dict)


@dataclass
class CrowdSource:
    zone: str
    points: list[Point]
    radius_m: float
    windows: list[dict]


@dataclass
class CampusModel:
    crs: str
    buildings: list[Building] = field(default_factory=list)
    closures: list[Area] = field(default_factory=list)
    trees: list[PointFeature] = field(default_factory=list)
    props: list[PointFeature] = field(default_factory=list)
    entrances: list[PointFeature] = field(default_factory=list)
    crowd_sources: list[CrowdSource] = field(default_factory=list)
    gcps: list[PointFeature] = field(default_factory=list)
    vehicle_spawns: list[PointFeature] = field(default_factory=list)
    missing_layers: list[str] = field(default_factory=list)

    # ---- queries -----------------------------------------------------------------

    def bounds(self) -> tuple[float, float, float, float] | None:
        pts: list[Point] = []
        for b in self.buildings:
            pts.extend(b.rings[0])
        for a in self.closures:
            pts.extend(a.rings[0])
        for group in (self.trees, self.props, self.entrances, self.gcps, self.vehicle_spawns):
            pts.extend(p.xy for p in group)
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def find_building(self, name: str) -> Building | None:
        target = name.casefold()
        named = [b for b in self.buildings if b.name]
        exact = [b for b in named if b.name.casefold() == target]
        if exact:
            return exact[0]
        partial = sorted(
            (b for b in named if target in b.name.casefold()), key=lambda b: len(b.name)
        )
        return partial[0] if partial else None

    def summary(self) -> dict[str, int]:
        return {
            "buildings": len(self.buildings),
            "closures": len(self.closures),
            "trees": len(self.trees),
            "props": len(self.props),
            "entrances": len(self.entrances),
            "crowd_sources": sum(len(c.points) for c in self.crowd_sources),
            "gcps": len(self.gcps),
            "vehicle_spawns": len(self.vehicle_spawns),
        }

    # ---- planner integration -----------------------------------------------------

    def building_entrance_node(
        self, building: Building, graph: CampusGraph, entrance_radius_m: float = 15.0
    ) -> str:
        """Walkway node for a building: nearest to an accessible entrance if one is
        mapped near the footprint, otherwise nearest to the footprint itself."""
        outer = building.rings[0]
        doors = [
            e.xy for e in self.entrances if point_ring_distance(e.xy, outer) <= entrance_radius_m
        ]
        nodes = list(graph.g.nodes)
        if doors:
            return min(nodes, key=lambda n: min(math.dist(graph.xy(n), d) for d in doors))
        return min(nodes, key=lambda n: point_ring_distance(graph.xy(n), outer))

    def resolve_building_landmarks(
        self, graph: CampusGraph, landmark_specs: dict[str, dict]
    ) -> tuple[dict[str, str], list[str]]:
        """Landmarks with `building: <name>` -> walkway nodes. Returns (found, unmatched)."""
        found, missing = {}, []
        for key, spec in landmark_specs.items():
            if "building" not in spec or "node" in spec:
                continue
            building = self.find_building(spec["building"])
            if building is None:
                missing.append(key)
                continue
            found[key] = self.building_entrance_node(building, graph)
        return found, missing

    def apply_to_graph(self, graph: CampusGraph) -> dict[str, int]:
        """Close walkways that touch closure areas; tag walkways near crowd sources."""
        closed = tagged = 0
        for u, v, data in graph.g.edges(data=True):
            a, b = graph.xy(u), graph.xy(v)
            for area in self.closures:
                if segment_touches_polygon(a, b, area.rings):
                    if not data["closed"]:
                        data["closed"] = True
                        data["closed_reason"] = area.reason
                        closed += 1
                    break
            for source in self.crowd_sources:
                if any(point_segment_distance(p, a, b) <= source.radius_m for p in source.points):
                    pois = data.setdefault("pois", [])
                    if source.zone not in pois:
                        pois.append(source.zone)
                        tagged += 1
        return {"closed": closed, "crowd_tagged": tagged}

    def crowd_zone_windows(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for source in self.crowd_sources:
            out.setdefault(source.zone, []).extend(source.windows)
        return out


# ---- loading ---------------------------------------------------------------------


def load_layer_specs(site) -> list[LayerSpec]:
    path = site.root / "layers.yaml"
    if not path.exists():
        return []
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return [LayerSpec.from_dict(key, spec) for key, spec in (raw.get("layers") or {}).items()]


def layer_path(site, spec: LayerSpec) -> Path:
    if spec.file:
        return site.root / spec.file
    return site.root / "cache" / "layers" / f"{spec.key}.geojson"


def build_model(site, specs: list[LayerSpec] | None = None) -> CampusModel:
    specs = load_layer_specs(site) if specs is None else specs
    model = CampusModel(crs=site.crs)
    for spec in specs:
        path = layer_path(site, spec)
        if not path.exists():
            model.missing_layers.append(spec.key)
            continue
        with open(path) as f:
            features = json.load(f).get("features", [])
        add_layer(model, spec, features)
    return model


def add_layer(model: CampusModel, spec: LayerSpec, features: list[dict]) -> None:
    for feat in features:
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom:
            continue
        name = _pick(props, spec.name_field, NAME_FIELDS)
        name = str(name).strip() if name not in (None, "") else None

        if spec.role == "building":
            for rings in _polygons(geom):
                model.buildings.append(Building(name, rings, _height(spec, props), spec.key))
        elif spec.role == "construction":
            for rings in _polygons(geom):
                model.closures.append(Area(rings, spec.reason, spec.key))
        elif spec.role == "crowd_source":
            pts = _points(geom)
            if pts:
                _crowd_source(model, spec).points.extend(pts)
        else:
            cls = {
                "tree": "tree",
                "prop": spec.prop_class or spec.key,
                "accessible_entrance": spec.prop_class or "entrance",
                "gcp": "gcp",
                "vehicle_spawn": "vehicle_spawn",
            }[spec.role]
            extra = {}
            if spec.role == "tree":
                species = _pick(props, spec.species_field, SPECIES_FIELDS)
                if species:
                    extra["species"] = str(species)
            target = {
                "tree": model.trees,
                "prop": model.props,
                "accessible_entrance": model.entrances,
                "gcp": model.gcps,
                "vehicle_spawn": model.vehicle_spawns,
            }[spec.role]
            for xy in _points(geom):
                target.append(PointFeature(xy, cls, name, spec.key, extra))


def _crowd_source(model: CampusModel, spec: LayerSpec) -> CrowdSource:
    zone = spec.zone or spec.key
    for source in model.crowd_sources:
        if source.zone == zone and source.radius_m == spec.radius_m:
            return source
    source = CrowdSource(zone, [], spec.radius_m, list(spec.windows))
    model.crowd_sources.append(source)
    return source


def _pick(props: dict, explicit: str | None, candidates: list[str]):
    if explicit:
        return props.get(explicit)
    lower = {k.lower(): v for k, v in props.items()}
    for c in candidates:
        if lower.get(c) not in (None, ""):
            return lower[c]
    return None


def _height(spec: LayerSpec, props: dict) -> float:
    # Height is only read from an explicitly named field: units vary between datasets.
    h = _number(props.get(spec.height_field)) if spec.height_field else None
    if h:
        return h * (0.3048 if spec.height_units == "ft" else 1.0)
    floors = _number(_pick(props, spec.floors_field, FLOORS_FIELDS))
    if floors:
        return floors * spec.floor_height_m
    return spec.default_height_m


def _number(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _polygons(geom: dict) -> list[list[list[Point]]]:
    kind, coords = geom.get("type"), geom.get("coordinates")
    if kind == "Polygon":
        polys = [coords]
    elif kind == "MultiPolygon":
        polys = coords
    else:
        return []
    out = []
    for rings in polys:
        cleaned = [open_ring([tuple(p[:2]) for p in ring]) for ring in rings]
        if cleaned and len(cleaned[0]) >= 3:
            out.append(cleaned)
    return out


def _points(geom: dict) -> list[Point]:
    """Point-like locations of any geometry (polygon -> centroid)."""
    kind, coords = geom.get("type"), geom.get("coordinates")
    if kind == "Point":
        return [tuple(coords[:2])]
    if kind == "MultiPoint":
        return [tuple(p[:2]) for p in coords]
    if kind in ("Polygon", "MultiPolygon"):
        return [centroid(rings[0]) for rings in _polygons(geom)]
    if kind == "LineString":
        mid = coords[len(coords) // 2]
        return [tuple(mid[:2])]
    return []


def inside_any_building(model: CampusModel, xy: Point) -> bool:
    return any(point_in_polygon(xy, b.rings) for b in model.buildings)
