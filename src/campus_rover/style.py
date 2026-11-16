"""Building style sheets: the short list of settings that says what a building looks like.

Shape comes from data (footprint + lidar). Style is the part that needs a
person or an AI to look the building up, so it is kept tiny, checked by
code, and saved once per building in `sites/<site>/buildings/<id>.yaml`.
A building without a sheet is never dressed up by guesswork: near the route it
is drawn as plain grey massing (`unverified_style`); far from it, as generic
background (`background_style`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WINDOW_PATTERNS = {"grid", "ribbon", "curtain", "slit", "none"}
SHADING = {"none", "fins", "eyebrows", "lanai"}
GROUND_FLOORS = {"solid", "arcade", "storefront"}
CONFIDENCE = {"low", "medium", "high"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Style:
    id: str
    name: str = ""
    tier: str = "C"                    # A = on the route, B = seen from it, C = background
    floors: int | None = None          # None = work it out from the lidar height
    wall: str = "#c9c2b2"
    trim: str = "#b3ad9f"              # slab edges, fins, columns, canopies
    glass: str = "#3a4a55"
    roof: str | None = None            # None = lightness measured from the lidar roof returns
    door: str = "#4a3b2e"
    windows: str = "grid"
    bay_m: float = 3.2                 # spacing of windows / columns along a wall
    window_ratio: float = 0.55         # share of a bay's width that is glass
    shading: str = "none"
    ground_floor: str = "solid"
    solid: bool = True                 # False = an open area (courts, amphitheatre): not built
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
            value = getattr(self, key)
            if key == "roof" and value is None:
                continue
            check(bool(_HEX.match(str(value))), f"{key} must be a colour like #c9c2b2")

    @classmethod
    def from_dict(cls, data: dict) -> Style:
        data = dict(data)
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"style {data.get('id')!r}: unknown fields {sorted(unknown)}")
        style = cls(**data)
        style.validate()
        return style

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


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


def background_style(building_id: str, name: str = "", area_m2: float = 500.0,
                     height_m: float = 10.0) -> Style:
    """Generic look for far-away background buildings nobody has looked up.

    Only for buildings outside the rover's corridor. It does not use the year
    built or any other hint to imitate a real appearance."""
    style = Style(id=building_id, name=name, notes="procedural background, not researched")
    if area_m2 < 120 or height_m < 3.6:      # sheds, kiosks, shelters
        style.windows = "none"
    return style


def unverified_style(building_id: str, name: str = "") -> Style:
    """A building near the route with no evidence of how it looks: its measured
    shape in plain grey, with no invented windows or doors."""
    return Style(id=building_id, name=name, wall="#b4b4b4", trim="#b4b4b4", windows="none",
                 notes="unverified: shape is measured, appearance is unknown")
