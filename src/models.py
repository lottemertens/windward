"""
Shared data types used across src/ modules.

Keeping them here means routing, weather, and analysis modules stay
independent — none needs to import from the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Coordinate:
    lat: float
    lon: float


@dataclass
class WindSample:
    lat: float
    lon: float
    speed_ms: float        # wind speed in metres per second
    direction_deg: float   # direction wind is coming FROM (0=N, 90=E, 180=S, 270=W)


@dataclass
class SegmentWind:
    start: Coordinate
    end: Coordinate
    headwind_ms: float     # positive = headwind (against you), negative = tailwind (with you)


@dataclass
class ClosureRecord:
    lat:         float
    lon:         float
    source:      str           # road manager, e.g. "Gemeente Waterland"
    start:       str           # ISO date string, e.g. "2026-04-12"
    end:         Optional[str] # ISO date string, or None if open-ended
    description: Optional[str] # free-text from the NDW feed, or None
