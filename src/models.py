"""
Shared data types used across src/ modules.

Keeping them here means routing, weather, and analysis modules stay
independent — none needs to import from the others.
"""

from dataclasses import dataclass


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
