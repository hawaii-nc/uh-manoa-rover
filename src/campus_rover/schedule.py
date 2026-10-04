"""Week-relative time and class schedules.

Times are week-relative (day of week + minute of day). Crowd priors repeat
weekly, and this keeps the planner independent of calendars and time zones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = frozenset(range(5))
MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY

_TIME_RE = re.compile(r"^\s*([A-Za-z]{3})[a-z]*\s+(\d{1,2}):(\d{2})\s*$")


def parse_hhmm(text: str) -> int:
    """'09:24' -> minutes since midnight."""
    hours, minutes = text.strip().split(":")
    value = int(hours) * 60 + int(minutes)
    if not 0 <= value < MINUTES_PER_DAY:
        raise ValueError(f"time out of range: {text!r}")
    return value


def parse_days(spec: str | Iterable[str]) -> frozenset[int]:
    """'weekdays' | 'daily' | 'weekend' | ['Mon', 'Wed'] -> day indices."""
    if isinstance(spec, str):
        named = {
            "weekdays": WEEKDAYS,
            "daily": frozenset(range(7)),
            "weekend": frozenset({5, 6}),
        }
        if spec.lower() in named:
            return named[spec.lower()]
        spec = [spec]
    return frozenset(_day_index(day) for day in spec)


def _day_index(name: str) -> int:
    key = name.strip()[:3].title()
    if key not in DAYS:
        raise ValueError(f"unknown day: {name!r}")
    return DAYS.index(key)


@dataclass(frozen=True, order=True)
class WeekTime:
    """A moment in a repeating week, in minutes since Monday 00:00."""

    minutes: float

    @classmethod
    def parse(cls, text: str) -> WeekTime:
        """'Mon 09:24' (also 'Monday 9:24') -> WeekTime."""
        match = _TIME_RE.match(text)
        if not match:
            raise ValueError(f"expected e.g. 'Mon 09:24', got {text!r}")
        day = _day_index(match.group(1))
        return cls.at(day, int(match.group(2)) * 60 + int(match.group(3)))

    @classmethod
    def at(cls, day: int, minute_of_day: float) -> WeekTime:
        return cls((day * MINUTES_PER_DAY + minute_of_day) % MINUTES_PER_WEEK)

    @property
    def day(self) -> int:
        return int(self.minutes // MINUTES_PER_DAY)

    @property
    def minute_of_day(self) -> float:
        return self.minutes % MINUTES_PER_DAY

    def plus_seconds(self, seconds: float) -> WeekTime:
        return WeekTime((self.minutes + seconds / 60.0) % MINUTES_PER_WEEK)

    def __str__(self) -> str:
        total = int(round(self.minute_of_day))
        return f"{DAYS[self.day]} {total // 60:02d}:{total % 60:02d}"


@dataclass(frozen=True)
class ClassBlock:
    days: frozenset[int]
    starts: tuple[int, ...]
    duration_min: int


class ClassSchedule:
    """When classes start and end, and how strong the walkway surge is.

    The surge is a triangular pulse around each class change. People pour out
    shortly after a class ends and hurry in shortly before the next starts.
    """

    def __init__(
        self,
        blocks: Iterable[ClassBlock],
        exit_peak_min: float = 3.0,
        arrive_peak_min: float = -4.0,
        half_width_min: float = 7.0,
    ) -> None:
        self.blocks = list(blocks)
        self.exit_peak_min = exit_peak_min
        self.arrive_peak_min = arrive_peak_min
        self.half_width_min = half_width_min
        self._peaks = self._build_peaks()

    @classmethod
    def from_dict(cls, data: dict) -> ClassSchedule:
        blocks = [
            ClassBlock(
                days=parse_days(block["days"]),
                starts=tuple(parse_hhmm(s) for s in block["starts"]),
                duration_min=int(block["duration_min"]),
            )
            for block in data.get("blocks", [])
        ]
        surge = data.get("surge", {})
        return cls(blocks, **surge)

    @classmethod
    def load(cls, path: Path) -> ClassSchedule:
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    def _build_peaks(self) -> list[float]:
        """Week-minute peak times of every class-change pulse, sorted."""
        peaks = []
        for block in self.blocks:
            for day in block.days:
                base = day * MINUTES_PER_DAY
                for start in block.starts:
                    peaks.append(base + start + self.arrive_peak_min)
                    peaks.append(base + start + block.duration_min + self.exit_peak_min)
        return sorted(peaks)

    def surge(self, t: WeekTime) -> float:
        """Class-change intensity in [0, 1] at time t."""
        best = 0.0
        for peak in self._peaks:
            # Shortest distance on the weekly circle (handles Sun -> Mon wrap).
            delta = abs(t.minutes - peak) % MINUTES_PER_WEEK
            delta = min(delta, MINUTES_PER_WEEK - delta)
            if delta < self.half_width_min:
                best = max(best, 1.0 - delta / self.half_width_min)
        return best
