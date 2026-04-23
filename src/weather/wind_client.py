"""
Open-Meteo wind data client.

Responsibility: given a coordinate and a datetime, return wind speed (m/s)
and wind direction (degrees, meteorological convention: the direction the
wind is coming FROM, 0 = north, 90 = east, 180 = south, 270 = west).

Open-Meteo is free and requires no API key.
Forecast API docs: https://open-meteo.com/en/docs
Archive API docs:  https://open-meteo.com/en/docs/historical-weather-api
"""

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx

from src.models import Coordinate, WindSample
from src.geo import haversine_km, bearing, route_distance_km
from src.config import (
    OPEN_METEO_URL,
    OPEN_METEO_ARCHIVE_URL,
    OPEN_METEO_FORECAST_DAYS,
    OPEN_METEO_MAX_RETRIES,
    OPEN_METEO_RETRY_DELAY_S,
    SAMPLE_SPACING_KM,
    MIN_SAMPLES,
    MAX_SAMPLES,
    MAX_CONCURRENT_REQUESTS,
    SPEED_HEADWIND_FACTOR,
    MIN_SPEED_KMH,
    DEPARTURE_SCORE_HOUR_START,
    DEPARTURE_SCORE_HOUR_END,
)

# ── Response cache ────────────────────────────────────────────────────────────
# Keyed by (url, lat_2dp, lon_2dp, start_date, end_date).
# Open-Meteo forecast data updates hourly, so a 1-hour TTL is safe.
# This prevents redundant API calls when the same location/day is requested
# multiple times (e.g. wind overview + departure-score chart on page load).
_CACHE_TTL_S = 3600
_response_cache: dict[tuple, tuple[float, dict]] = {}  # key → (expiry, data)


def _cache_get(key: tuple) -> Optional[dict]:
    entry = _response_cache.get(key)
    if entry and time.monotonic() < entry[0]:
        return entry[1]
    return None


def _cache_set(key: tuple, data: dict) -> None:
    _response_cache[key] = (time.monotonic() + _CACHE_TTL_S, data)


async def _fetch_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """
    GET url with params, using a local TTL cache and retrying on 429
    with exponential back-off.
    Raises the last httpx.HTTPStatusError if all retries are exhausted.
    """
    cache_key = (url, params.get("latitude"), params.get("longitude"),
                 params.get("start_date"), params.get("end_date"))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    delay = OPEN_METEO_RETRY_DELAY_S
    for attempt in range(OPEN_METEO_MAX_RETRIES + 1):
        response = await client.get(url, params=params, timeout=10.0)
        if response.status_code != 429:
            response.raise_for_status()
            data = response.json()
            _cache_set(cache_key, data)
            return data
        if attempt < OPEN_METEO_MAX_RETRIES:
            await asyncio.sleep(delay)
            delay *= 2
    response.raise_for_status()  # raises after final 429


async def get_wind_at(lat: float, lon: float, at: datetime) -> WindSample:
    """
    Fetch wind data for a single coordinate at a given hour.

    Automatically uses the archive API for past dates and the forecast
    API for upcoming dates. Raises ValueError if the date is more than
    OPEN_METEO_FORECAST_DAYS in the future.
    """
    url = _pick_api_url(at)

    params = {
        "latitude":        lat,
        "longitude":       lon,
        "hourly":          "windspeed_10m,winddirection_10m",
        "wind_speed_unit": "ms",
        "start_date":      at.strftime("%Y-%m-%d"),
        "end_date":        at.strftime("%Y-%m-%d"),
        "timezone":        "auto",
    }

    async with httpx.AsyncClient() as client:
        data = await _fetch_json(client, url, params)

    return _parse_wind_response(data, at)


async def get_wind_along_route(
    waypoints: list[Coordinate],
    at: datetime,
) -> list[WindSample]:
    """
    Fetch wind data at evenly-spaced samples along a route.
    Requests are capped to MAX_CONCURRENT_REQUESTS in parallel to avoid
    hitting Open-Meteo's rate limit (429 Too Many Requests).
    """
    if not waypoints:
        return []

    distance_km = route_distance_km(waypoints)
    n = int(distance_km / SAMPLE_SPACING_KM)
    n = max(MIN_SAMPLES, min(MAX_SAMPLES, n))

    step    = len(waypoints) / n
    sampled = [waypoints[int(i * step)] for i in range(n)]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def fetch_one(w: Coordinate) -> WindSample:
        async with semaphore:
            return await get_wind_at(w.lat, w.lon, at)

    return await asyncio.gather(*[fetch_one(w) for w in sampled])


async def get_wind_along_route_timed(
    waypoints: list[Coordinate],
    departure_at: datetime,
    speed_kmh: float,
) -> tuple[list[WindSample], int]:
    """
    Fetch wind along a route accounting for the time spent riding.

    Unlike get_wind_along_route() which uses a single snapshot in time,
    this function estimates when the rider arrives at each sample point and
    interpolates between the two surrounding hourly forecasts for that moment.

    Speed is adjusted per segment based on the headwind/tailwind component:
        v_eff = max(MIN_SPEED_KMH, speed_kmh − headwind_ms × SPEED_HEADWIND_FACTOR)

    Algorithm (two phases to keep it fast):
      Phase 1 — Fetch 48 h of wind data for all sample locations in parallel,
                 just like the static version. No extra API calls.
      Phase 2 — Sequential pass: extract interpolated wind at the estimated
                 arrival time, adjust speed, advance the clock to the next point.

    Returns (wind_samples, duration_min) where duration_min is the estimated
    total riding time in minutes, accounting for headwind/tailwind speed changes.
    """
    if not waypoints:
        return [], 0
    sampled, raw_forecasts, total_km = await _sample_and_fetch(waypoints, departure_at)
    return _timed_wind_pass(sampled, raw_forecasts, departure_at, speed_kmh, total_km)


async def score_departure_times(
    waypoints: list[Coordinate],
    departure_at: datetime,
    speed_kmh: Optional[float],
) -> tuple[list[Coordinate], list[tuple[int, list[WindSample]]]]:
    """
    Compute wind samples for every departure hour across one day.

    Fetches 48 h of forecast data ONCE for all sample locations (same
    cost as a single timed-wind call), then runs the timed or static
    wind pass for each hour in pure Python — no extra API calls.

    Returns (sampled_waypoints, [(hour, wind_samples), ...]) so the
    caller can run analyse_route_wind and compute a score per hour.
    """
    if not waypoints:
        return [], []
    sampled, raw_forecasts, total_km = await _sample_and_fetch(waypoints, departure_at)
    hours = range(DEPARTURE_SCORE_HOUR_START, DEPARTURE_SCORE_HOUR_END)
    results = []
    for hour in hours:
        dt = departure_at.replace(hour=hour, minute=0, second=0, microsecond=0)
        if speed_kmh:
            wind_samples, _ = _timed_wind_pass(sampled, raw_forecasts, dt, speed_kmh, total_km)
        else:
            wind_samples = [_interpolate_at_time(f, dt) for f in raw_forecasts]
        results.append((hour, wind_samples))
    return sampled, results


# ── Pure functions for client-supplied forecast data ─────────────────────────
# The browser now fetches wind data directly from Open-Meteo and sends the
# raw 48-h responses to the server. These functions run the same maths as
# before but take pre-fetched forecast dicts instead of making HTTP calls.

def sample_route(waypoints: list[Coordinate]) -> tuple[list[Coordinate], float]:
    """
    Subsample a route for wind fetch points.
    Returns (sampled_waypoints, total_distance_km).
    Mirrors the sampling done inside _sample_and_fetch.
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


# ── Helpers for time-dependent wind ──────────────────────────────────────────

async def _sample_and_fetch(
    waypoints: list[Coordinate],
    departure_at: datetime,
) -> tuple[list[Coordinate], list[dict], float]:
    """
    Evenly sample the route and fetch 48 h forecasts for each sample point.
    Shared by get_wind_along_route_timed and score_departure_times so the
    sampling + fetch logic lives in exactly one place.

    Returns (sampled, raw_forecasts, total_distance_km) — the actual route
    distance is returned so callers can correct the timed-pass duration, which
    is otherwise based on straight-line distances between sample points.
    """
    total_distance_km = route_distance_km(waypoints)
    n    = int(total_distance_km / SAMPLE_SPACING_KM)
    n    = max(MIN_SAMPLES, min(MAX_SAMPLES, n))
    step = len(waypoints) / n
    sampled = [waypoints[int(i * step)] for i in range(n)]
    raw_forecasts = await _fetch_48h_parallel(sampled, departure_at)
    return sampled, raw_forecasts, total_distance_km


async def _fetch_48h_parallel(
    sampled: list[Coordinate],
    departure_at: datetime,
) -> list[dict]:
    """
    Fetch 48 h of hourly wind data for each sample location in parallel.
    Requesting two calendar days ensures we always have the hour before
    AND after any arrival time that might cross midnight.
    """
    end_date = (departure_at + timedelta(days=1)).date()
    url      = _pick_api_url(departure_at)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def fetch_one(w: Coordinate) -> dict:
        params = {
            "latitude":        w.lat,
            "longitude":       w.lon,
            "hourly":          "windspeed_10m,winddirection_10m",
            "wind_speed_unit": "ms",
            "start_date":      departure_at.strftime("%Y-%m-%d"),
            "end_date":        end_date.strftime("%Y-%m-%d"),
            "timezone":        "auto",
        }
        async with semaphore:
            async with httpx.AsyncClient() as client:
                return await _fetch_json(client, url, params)

    return list(await asyncio.gather(*[fetch_one(w) for w in sampled]))


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


def _pick_api_url(at: datetime) -> str:
    """
    Return the correct Open-Meteo URL for the given date.

    - Past dates           → archive API (data back to 1940)
    - Up to 16 days ahead  → forecast API
    - Beyond 16 days       → raise ValueError
    """
    # Make both datetimes naive (no timezone) for comparison
    now  = datetime.now()
    date = at.replace(tzinfo=None) if at.tzinfo else at
    days_from_now = (date.date() - now.date()).days

    if days_from_now > OPEN_METEO_FORECAST_DAYS:
        raise ValueError(
            f"Wind data is only available up to {OPEN_METEO_FORECAST_DAYS} days ahead. "
            f"Please choose a date before {(now.date().__class__.fromordinal(now.toordinal() + OPEN_METEO_FORECAST_DAYS))}."
        )

    if days_from_now < 0:
        return OPEN_METEO_ARCHIVE_URL

    return OPEN_METEO_URL


def _parse_wind_response(data: dict, at: datetime) -> WindSample:
    """
    Extract wind speed and direction for the requested hour from an
    Open-Meteo response dict.
    """
    target = at.strftime("%Y-%m-%dT%H:00")
    times  = data["hourly"]["time"]

    if target not in times:
        raise ValueError(
            f"No wind data for {target}. "
            f"Available range: {times[0]} – {times[-1]}"
        )

    idx = times.index(target)

    return WindSample(
        lat=data["latitude"],
        lon=data["longitude"],
        speed_ms=data["hourly"]["windspeed_10m"][idx],
        direction_deg=data["hourly"]["winddirection_10m"][idx],
    )
