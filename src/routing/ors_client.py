"""
OpenRouteService (ORS) client.

Responsibility: ask ORS for a cycling route between two coordinates
and return waypoints plus route metadata (surface types, warnings).

ORS docs: https://openrouteservice.org/dev/#/api-docs/v2/directions/{profile}/post
"""

import httpx
from dataclasses import dataclass, field

from src.models import Coordinate
from src.config import ORS_BASE_URL, CYCLING_PROFILE


# Maps ORS integer surface codes to human-readable names.
# Source: https://giscience.github.io/openrouteservice/documentation/extra-info/Surface
_SURFACE_NAMES: dict[int, str] = {
    0:  "Unknown",
    1:  "Paved",
    2:  "Unpaved",
    3:  "Asphalt",
    4:  "Concrete",
    5:  "Cobblestone",
    6:  "Metal",
    7:  "Wood",
    8:  "Compacted gravel",
    9:  "Fine gravel",
    10: "Gravel",
    11: "Dirt",
    12: "Ground",
    13: "Ice",
    14: "Paving stones",
    15: "Sand",
    16: "Woodchips",
    17: "Grass",
    18: "Grass paver",
}


@dataclass
class SurfaceSummary:
    name:        str
    distance_km: float
    percentage:  float   # 0–100


@dataclass
class OrsRouteResult:
    waypoints: list[Coordinate]
    surfaces:  list[SurfaceSummary] = field(default_factory=list)
    warnings:  list[str]           = field(default_factory=list)


async def get_cycling_route(
    waypoints: list[Coordinate],
    api_key: str,
) -> OrsRouteResult:
    """
    Request a cycling route from ORS through two or more waypoints.
    Returns the full route geometry plus surface breakdown and any warnings.

    Raises:
        ValueError: if fewer than 2 waypoints are provided.
        httpx.HTTPStatusError: if ORS returns an error (bad key, no route found, etc.)
    """
    if len(waypoints) < 2:
        raise ValueError("At least 2 waypoints are required to calculate a route.")

    url = f"{ORS_BASE_URL}/{CYCLING_PROFILE}/geojson"

    body = {
        # ORS expects [lon, lat] order — we flip from our internal (lat, lon).
        "coordinates": [[w.lon, w.lat] for w in waypoints],
        "extra_info": ["surface"],
    }

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=body, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

    return _parse_route_response(data)


def _parse_route_response(data: dict) -> OrsRouteResult:
    """
    Extract waypoints, surface summary, and warnings from an ORS GeoJSON response.
    """
    feature    = data["features"][0]
    properties = feature["properties"]

    waypoints = [
        Coordinate(lat=coord[1], lon=coord[0])
        for coord in feature["geometry"]["coordinates"]
    ]

    surfaces = _parse_surfaces(properties.get("extras", {}).get("surface", {}))
    warnings = [w.get("message", "") for w in properties.get("warnings", [])]

    return OrsRouteResult(waypoints=waypoints, surfaces=surfaces, warnings=warnings)


def _parse_surfaces(surface_data: dict) -> list[SurfaceSummary]:
    """
    Convert ORS surface summary entries into SurfaceSummary objects.
    ORS returns distance in metres; we convert to km.
    Only include surfaces that cover ≥ 1% of the route to avoid noise.
    """
    summaries = []
    for entry in surface_data.get("summary", []):
        pct = entry.get("amount", 0)
        if pct < 1.0:
            continue
        summaries.append(SurfaceSummary(
            name=_SURFACE_NAMES.get(entry["value"], "Unknown"),
            distance_km=round(entry["distance"] / 1000, 2),
            percentage=round(pct, 1),
        ))
    # Sort by coverage, most common first
    return sorted(summaries, key=lambda s: s.percentage, reverse=True)
