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
from datetime import datetime, timedelta, timezone
import httpx

from src.models import Coordinate, WindSample
from src.geo import haversine_km, bearing, route_distance_km
from src.config import (
    OPEN_METEO_URL,
    OPEN_METEO_ARCHIVE_URL,
    OPEN_METEO_FORECAST_DAYS,
    SAMPLE_SPACING_KM,
    MIN_SAMPLES,
    MAX_SAMPLES,
    MAX_CONCURRENT_REQUESTS,
    SPEED_HEADWIND_FACTOR,
    MIN_SPEED_KMH,
)


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
        response = await client.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

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
) -> list[WindSample]:
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
    """
    if not waypoints:
        return []

    distance_km = route_distance_km(waypoints)
    n    = int(distance_km / SAMPLE_SPACING_KM)
    n    = max(MIN_SAMPLES, min(MAX_SAMPLES, n))
    step = len(waypoints) / n
    sampled = [waypoints[int(i * step)] for i in range(n)]

    # Phase 1: parallel fetch — each location gets 2 days so we always
    # have the hour before AND after any estimated arrival time.
    raw_forecasts = await _fetch_48h_parallel(sampled, departure_at)

    # Phase 2: sequential timing pass (pure Python, no I/O)
    return _timed_wind_pass(sampled, raw_forecasts, departure_at, speed_kmh)


# ── Helpers for time-dependent wind ──────────────────────────────────────────

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
                r = await client.get(url, params=params, timeout=10.0)
                r.raise_for_status()
                return r.json()

    return list(await asyncio.gather(*[fetch_one(w) for w in sampled]))


def _timed_wind_pass(
    sampled: list[Coordinate],
    raw_forecasts: list[dict],
    departure_at: datetime,
    speed_kmh: float,
) -> list[WindSample]:
    """
    Walk sample points one by one.  At each point:
      1. Interpolate wind at the current estimated arrival time.
      2. Calculate headwind component toward the next point.
      3. Adjust effective speed and advance the clock.

    This is pure Python — no I/O, runs in microseconds.
    """
    min_speed_ms = MIN_SPEED_KMH / 3.6
    speed_ms     = speed_kmh / 3.6
    current_time = departure_at
    results: list[WindSample] = []

    for i, (point, forecast) in enumerate(zip(sampled, raw_forecasts)):
        wind = _interpolate_at_time(forecast, current_time)
        results.append(wind)

        if i < len(sampled) - 1:
            next_pt      = sampled[i + 1]
            segment_km   = haversine_km(point, next_pt)
            seg_bearing  = bearing(point, next_pt)

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

    return results


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
