"""
Geographic utility functions.

These operate on Coordinate objects and do pure maths — no API calls.
"""

import math
from src.models import Coordinate


def haversine_km(a: Coordinate, b: Coordinate) -> float:
    """
    Calculate the great-circle distance between two coordinates in kilometres.

    The Haversine formula accounts for Earth's curvature. For cycling routes
    (up to a few hundred km) it's accurate to well within 1%.

    Earth is not a perfect sphere, so this is an approximation — but good
    enough for our purposes.
    """
    R = 6371.0  # Earth's mean radius in km

    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)

    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(h))


def bearing(a: Coordinate, b: Coordinate) -> float:
    """
    Compass bearing from point a to point b, in degrees (0=N, 90=E, 180=S, 270=W).

    Uses the spherical law of cosines. Accurate for the distances involved
    in cycling routes.
    """
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)

    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)

    return (math.degrees(math.atan2(x, y)) + 360) % 360


def route_distance_km(waypoints: list[Coordinate]) -> float:
    """
    Sum the Haversine distances between consecutive waypoints.
    Returns 0.0 for routes with fewer than 2 points.
    """
    if len(waypoints) < 2:
        return 0.0
    return sum(
        haversine_km(waypoints[i], waypoints[i + 1])
        for i in range(len(waypoints) - 1)
    )
