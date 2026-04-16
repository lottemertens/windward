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
from datetime import datetime, timezone
import httpx

from src.models import Coordinate, WindSample
from src.geo import route_distance_km
from src.config import (
    OPEN_METEO_URL,
    OPEN_METEO_ARCHIVE_URL,
    OPEN_METEO_FORECAST_DAYS,
    SAMPLE_SPACING_KM,
    MIN_SAMPLES,
    MAX_SAMPLES,
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
    All requests run in parallel — total wait ≈ one request, not N.
    """
    if not waypoints:
        return []

    distance_km = route_distance_km(waypoints)
    n = int(distance_km / SAMPLE_SPACING_KM)
    n = max(MIN_SAMPLES, min(MAX_SAMPLES, n))

    step    = len(waypoints) / n
    sampled = [waypoints[int(i * step)] for i in range(n)]

    tasks = [get_wind_at(w.lat, w.lon, at) for w in sampled]
    return await asyncio.gather(*tasks)


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
