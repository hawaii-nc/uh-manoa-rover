"""Building style sheets: the short list of settings that says what a building looks like.

Shape comes from data (footprint + lidar). Style is the part that needs a
person or an AI to look the building up, so it is kept tiny, checked by
code, and saved once per building in `sites/<site>/buildings/<id>.yaml`.
Buildings without a sheet get `auto_style`, a guess from the year built
and the size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WINDOW_PATTERNS = {"grid", "ribbon", "curtain", "slit", "none"}
SHADING = {"none", "fins", "eyebrows", "lanai"}
GROUND_FLOORS = {"solid", "arcade", "storefront"}
SIDES = {"N", "S", "E", "W"}
CONFIDENCE = {"low", "medium", "high"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Entrance:
    side: str = "S"
    kind: str = "double"       # single | double | wide
    covered: bool = False


@dataclass
class Style:
    id: str
    name: str = ""
    tier: str = "C"                    # A = on the route, B = seen from it, C = background
    floors: int | None = None          # None = work it out from the lidar height
    wall: str = "#c9c2b2"
    trim: str = "#b3ad9f"              # slab edges, fins, columns, canopies
    glass: str = "#3a4a55"
    roof: str = "#8c8c88"
    door: str = "#4a3b2e"
    windows: str = "grid"
    bay_m: float = 3.2                 # spacing of windows / columns along a wall
    window_ratio: float = 0.55         # share of a bay's width that is glass
    shading: str = "none"
    ground_floor: str = "solid"
    entrances: list[Entrance] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    confidence: str = "low"
    notes: str = ""

    def validate(self) -> None:
        def check(ok: bool, message: str) -> None:
            if not ok:
                raise ValueError(f"style {self.id!r}: {message}")

        check(self.tier in {"A", "B", "C"}, f"tier must be A, B or C, got {self.tier!r}")
        check(self.windows in WINDOW_PATTERNS, f"windows must be one of {sorted(WINDOW_PATTERNS)}")
        check(self.shading in SHADING, f"shading must be one of {sorted(SHADING)}")
        check(self.ground_floor in GROUND_FLOORS,
              f"ground_floor must be one of {sorted(GROUND_FLOORS)}")
        check(self.confidence in CONFIDENCE, f"confidence must be one of {sorted(CONFIDENCE)}")
        check(self.floors is None or 1 <= self.floors <= 60, "floors must be 1-60")
        check(1.0 <= self.bay_m <= 12.0, "bay_m must be 1-12 m")
        check(0.1 <= self.window_ratio <= 0.95, "window_ratio must be 0.1-0.95")
        for key in ("wall", "trim", "glass", "roof", "door"):
            check(bool(_HEX.match(getattr(self, key))), f"{key} must be a colour like #c9c2b2")
        for e in self.entrances:
            check(e.side in SIDES, f"entrance side must be one of {sorted(SIDES)}")
            check(e.kind in {"single", "double", "wide"}, "entrance kind: single, double or wide")

    @classmethod
    def from_dict(cls, data: dict) -> Style:
        data = dict(data)
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"style {data.get('id')!r}: unknown fields {sorted(unknown)}")
        data["entrances"] = [Entrance(**e) for e in data.get("entrances") or []]
        style = cls(**data)
        style.validate()
        return style

    def to_dict(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "entrances"}
        out["entrances"] = [vars(e) for e in self.entrances]
        return out


def slug(name: str) -> str:
    """'HOLMES HALL' -> 'holmes_hall'. Used as the style id and the scene group name."""
    text = re.sub(r"[^a-z0-9]+", "_", name.casefold().replace("ʻ", "").replace("`", ""))
    return text.strip("_") or "building"


def load_styles(folder: Path) -> dict[str, Style]:
    styles: dict[str, Style] = {}
    if folder.is_dir():
        for path in sorted(folder.glob("*.yaml")):
            style = Style.from_dict(yaml.safe_load(path.read_text()))
            if style.id != path.stem:
                raise ValueError(f"{path}: id {style.id!r} must match the file name")
            styles[style.id] = style
    return styles


def auto_style(building_id: str, name: str = "", year: int | None = None,
               area_m2: float = 500.0, height_m: float = 10.0, pitched: bool = False) -> Style:
    """A reasonable guess when nobody has looked the building up yet.

    UH's buildings follow their decade closely: plastered pre-war halls with
    tile roofs, 1950s-60s concrete with sun shades, 1970s exposed concrete with
    open-air corridors, and glassier buildings from the 1990s on.
    """
    style = Style(id=building_id, name=name, notes="auto: guessed from year and size")
    if area_m2 < 120 or height_m < 3.6:      # sheds, kiosks, shelters
        style.windows, style.wall = "none", "#b8b4a8"
    elif year is not None and year < 1946:
        style.wall, style.trim, style.windows = "#e3dcc8", "#d6ceb8", "grid"
        style.bay_m, style.window_ratio = 3.0, 0.4
    elif year is not None and year < 1966:
        style.wall, style.trim = "#d8d2c2", "#c4bdab"
        style.windows, style.shading = "ribbon", "eyebrows"
    elif year is not None and year < 1986:
        style.wall, style.trim = "#bdb6a6", "#a9a292"
        style.windows, style.shading, style.bay_m = "grid", "fins", 3.6
    elif year is not None:
        style.wall, style.trim = "#d9d4c7", "#9aa3a6"
        style.windows, style.window_ratio = "grid", 0.65
    if pitched:
        style.roof = "#7a4a36" if (year or 2000) < 1960 else "#5d6b62"
    return style
