"""
Open-Meteo wind data helpers.

Wind direction is the direction the wind is coming FROM (meteorological
convention): 0 = north, 90 = east, 180 = south, 270 = west. Speed is in m/s.

Open-Meteo is free and requires no API key.
Forecast API docs: https://open-meteo.com/en/docs
Archive API docs:  https://open-meteo.com/en/docs/historical-weather-api

Architecture
------------
Wind data is fetched directly from Open-Meteo by the visitor's browser
(see fetchWindForPoints() in app.js), which distributes requests across user
IPs and avoids shared-IP rate limiting on Render. The browser sends the raw
48-h forecast responses to the server via POST /api/analyze.

This module provides:
  - sample_route()                   — subsample a route for wind fetch points
  - interpolate_wind_at_time()       — extract WindSample from a forecast dict
  - timed_wind_from_forecasts()      — timed wind pass (adjusts speed per segment)
  - departure_scores_from_forecasts() — score each departure hour in pure Python

All functions are pure (no I/O). The actual HTTP calls live in the browser.
"""

import math
from datetime import datetime, timedelta
from typing import Optional

from src.models import Coordinate, WindSample
from src.geo import haversine_km, bearing, route_distance_km
from src.config import (
    SAMPLE_SPACING_KM,
    MIN_SAMPLES,
    MAX_SAMPLES,
    SPEED_HEADWIND_FACTOR,
    MIN_SPEED_KMH,
    DEPARTURE_SCORE_HOUR_START,
    DEPARTURE_SCORE_HOUR_END,
)


# ── Public API ────────────────────────────────────────────────────────────────

def sample_route(waypoints: list[Coordinate]) -> tuple[list[Coordinate], float]:
    """
    Subsample a route for wind fetch points.
    Returns (sampled_waypoints, total_distance_km).
    """
    total_km = route_distance_km(waypoints)
    n = int(total_km / SAMPLE_SPACING_KM)
    n = max(MIN_SAMPLES, min(MAX_SAMPLES, n))
    step = len(waypoints) / n
    sampled = [waypoints[int(i * step)] for i in range(n)]
    return sampled, total_km


def interpolate_wind_at_time(forecast: dict, at: datetime) -> WindSample:
    """Extract a WindSample from a pre-fetched 48-h Open-Meteo response dict."""
    return _interpolate_at_time(forecast, at)


def timed_wind_from_forecasts(
    sampled: list[Coordinate],
    raw_forecasts: list[dict],
    departure_at: datetime,
    speed_kmh: float,
    total_distance_km: float = 0.0,
) -> tuple[list[WindSample], int]:
    """Run the timed-wind pass over pre-fetched forecast data. No I/O."""
    return _timed_wind_pass(sampled, raw_forecasts, departure_at, speed_kmh, total_distance_km)


def departure_scores_from_forecasts(
    sampled: list[Coordinate],
    raw_forecasts: list[dict],
    departure_at: datetime,
    speed_kmh: Optional[float],
    total_distance_km: float,
) -> list[tuple[int, list[WindSample]]]:
    """
    Compute per-hour wind samples from pre-fetched forecast data. No I/O.
    Returns [(hour, wind_samples), ...] for hours in DEPARTURE_SCORE_HOUR_START..END.
    """
    results = []
    for hour in range(DEPARTURE_SCORE_HOUR_START, DEPARTURE_SCORE_HOUR_END):
        dt = departure_at.replace(hour=hour, minute=0, second=0, microsecond=0)
        if speed_kmh:
            wind_samples, _ = _timed_wind_pass(sampled, raw_forecasts, dt, speed_kmh, total_distance_km)
        else:
            wind_samples = [_interpolate_at_time(f, dt) for f in raw_forecasts]
        results.append((hour, wind_samples))
    return results


# ── Private helpers ───────────────────────────────────────────────────────────

def _timed_wind_pass(
    sampled: list[Coordinate],
    raw_forecasts: list[dict],
    departure_at: datetime,
    speed_kmh: float,
    total_distance_km: float = 0.0,
) -> tuple[list[WindSample], int]:
    """
    Walk sample points one by one.  At each point:
      1. Interpolate wind at the current estimated arrival time.
      2. Calculate headwind component toward the next point.
      3. Adjust effective speed and advance the clock.

    This is pure Python — no I/O, runs in microseconds.

    The sample points are spaced by crow-fly distance, which is shorter than
    the actual road distance.  When total_distance_km is provided, the raw
    duration (based on straight-line sample distances) is scaled up so the
    result reflects the full route length.

    Returns (wind_samples, duration_min).
    """
    min_speed_ms = MIN_SPEED_KMH / 3.6
    speed_ms     = speed_kmh / 3.6
    current_time = departure_at
    results: list[WindSample] = []
    sampled_km = 0.0

    for i, (point, forecast) in enumerate(zip(sampled, raw_forecasts)):
        wind = _interpolate_at_time(forecast, current_time)
        results.append(wind)

        if i < len(sampled) - 1:
            next_pt      = sampled[i + 1]
            segment_km   = haversine_km(point, next_pt)
            seg_bearing  = bearing(point, next_pt)
            sampled_km  += segment_km

            # Wind → (u, v) components (u = eastward, v = northward)
            dir_rad = math.radians(wind.direction_deg)
            u = wind.speed_ms * math.sin(dir_rad)
            v = wind.speed_ms * math.cos(dir_rad)

            # Headwind component: dot product with travel direction
            t_rad    = math.radians(seg_bearing)
            headwind = u * math.sin(t_rad) + v * math.cos(t_rad)

            # Effective speed (clamped to minimum)
            v_eff_ms = max(min_speed_ms, speed_ms - headwind * SPEED_HEADWIND_FACTOR)

            # Advance the clock
            current_time += timedelta(hours=segment_km / (v_eff_ms * 3.6))

    raw_duration_s = (current_time - departure_at).total_seconds()

    # Scale duration from crow-fly sample distance to actual road distance.
    # Without this, duration is systematically too short because sample points
    # are connected by straight lines, not road geometry.
    if sampled_km > 0 and total_distance_km > sampled_km:
        raw_duration_s *= total_distance_km / sampled_km

    duration_min = round(raw_duration_s / 60)
    return results, duration_min


def _interpolate_at_time(forecast: dict, at: datetime) -> WindSample:
    """
    Interpolate wind speed and direction between the two hourly forecasts
    that bracket `at`.  Interpolation is done in (u, v) vector space so
    that circular direction values (e.g. 350° and 10°) are handled correctly.
    """
    times  = forecast["hourly"]["time"]
    speeds = forecast["hourly"]["windspeed_10m"]
    dirs   = forecast["hourly"]["winddirection_10m"]

    floor_str = at.strftime("%Y-%m-%dT%H:00")

    # Fall back to first available hour if exact hour not found
    idx0 = times.index(floor_str) if floor_str in times else 0
    idx1 = min(idx0 + 1, len(times) - 1)

    # Fractional position within the current hour (0.0 – 1.0)
    alpha = (at.minute + at.second / 60) / 60

    # Convert to (u, v) for circular-safe interpolation
    u0 = speeds[idx0] * math.sin(math.radians(dirs[idx0]))
    v0 = speeds[idx0] * math.cos(math.radians(dirs[idx0]))
    u1 = speeds[idx1] * math.sin(math.radians(dirs[idx1]))
    v1 = speeds[idx1] * math.cos(math.radians(dirs[idx1]))

    u = u0 + alpha * (u1 - u0)
    v = v0 + alpha * (v1 - v0)

    speed     = math.sqrt(u ** 2 + v ** 2)
    direction = (math.degrees(math.atan2(u, v)) + 360) % 360

    return WindSample(
        lat=forecast["latitude"],
        lon=forecast["longitude"],
        speed_ms=speed,
        direction_deg=direction,
    )
