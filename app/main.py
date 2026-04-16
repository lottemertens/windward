"""
KomootLayer API server.

Run with:
    uvicorn app.main:app --reload

Endpoints:
    GET  /api/wind           — wind at default location for a given time
    POST /api/route          — plan a route via ORS + calculate wind
    POST /api/upload         — parse an uploaded GPX file
    POST /api/wind-overlay   — calculate wind for an already-known set of waypoints
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx

from src.routing.ors_client import get_cycling_route
from src.weather.wind_client import get_wind_along_route, get_wind_at
from src.analysis.wind_analysis import analyse_route_wind, generate_display_arrows
from src.gpx_parser import parse_gpx
from src.geo import route_distance_km
from src.models import Coordinate
from src.config import DEFAULT_LOCATION_LAT, DEFAULT_LOCATION_LON, DEFAULT_LOCATION_NAME, CYCLING_PROFILE_ROAD, CYCLING_PROFILE_REGULAR

load_dotenv()

app = FastAPI(title="Windward")


# --- Shared models --------------------------------------------------------

class WaypointModel(BaseModel):
    lat: float
    lon: float

class SegmentModel(BaseModel):
    start:       WaypointModel
    end:         WaypointModel
    headwind_ms: float

class WindArrowModel(BaseModel):
    lat:           float
    lon:           float
    speed_ms:      float
    direction_deg: float

class SurfaceModel(BaseModel):
    name:        str
    distance_km: float
    percentage:  float

class RouteInfoModel(BaseModel):
    distance_km: float
    surfaces:    list[SurfaceModel]   # empty for uploaded routes
    warnings:    list[str]            # empty for uploaded routes


# --- /api/geocode  (address search via Nominatim) -------------------------

class GeocodeSuggestion(BaseModel):
    name:     str    # short display name shown in the dropdown
    full:     str    # full address shown on hover / in the marker popup
    lat:      float
    lon:      float

@app.get("/api/geocode", response_model=list[GeocodeSuggestion])
async def geocode(q: str):
    """Search for an address using the Nominatim geocoder (OpenStreetMap)."""
    if len(q.strip()) < 3:
        return []
    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 5, "addressdetails": 0},
            headers={"User-Agent": "Windward/1.0 (cycling wind planner)"},
            timeout=5.0,
        )
        res.raise_for_status()
    results = res.json()
    suggestions = []
    for r in results:
        parts = r["display_name"].split(", ")
        short = ", ".join(parts[:2])
        suggestions.append(GeocodeSuggestion(
            name=short,
            full=r["display_name"],
            lat=float(r["lat"]),
            lon=float(r["lon"]),
        ))
    return suggestions


# --- /api/wind ------------------------------------------------------------

class WindOverviewResponse(BaseModel):
    location:      str
    speed_ms:      float
    direction_deg: float

@app.get("/api/wind", response_model=WindOverviewResponse)
async def get_wind_overview(datetime_iso: str):
    """Wind speed and direction at the default location for a given time."""
    at = _parse_datetime(datetime_iso)
    try:
        sample = await get_wind_at(DEFAULT_LOCATION_LAT, DEFAULT_LOCATION_LON, at)
    except (httpx.HTTPStatusError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WindOverviewResponse(
        location=DEFAULT_LOCATION_NAME,
        speed_ms=round(sample.speed_ms, 1),
        direction_deg=round(sample.direction_deg, 1),
    )


# --- /api/route  (ORS + wind in one call) ---------------------------------

class RouteRequest(BaseModel):
    waypoints:    list[WaypointModel]   # [start, optional via points..., end]
    datetime_iso: str
    profile:      str = CYCLING_PROFILE_ROAD

class RouteResponse(BaseModel):
    segments:    list[SegmentModel]
    wind_arrows: list[WindArrowModel]
    route_info:  RouteInfoModel

@app.post("/api/route", response_model=RouteResponse)
async def calculate_route(request: RouteRequest):
    api_key = os.getenv("ORS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ORS_API_KEY is not set")

    if len(request.waypoints) < 2:
        raise HTTPException(status_code=400, detail="At least 2 waypoints are required.")

    if request.profile not in (CYCLING_PROFILE_ROAD, CYCLING_PROFILE_REGULAR):
        raise HTTPException(status_code=400, detail="Invalid profile.")

    at = _parse_datetime(request.datetime_iso)

    try:
        result = await get_cycling_route(
            waypoints=[Coordinate(lat=w.lat, lon=w.lon) for w in request.waypoints],
            api_key=api_key,
            profile=request.profile,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"ORS error: {e.response.text}")

    return await _build_route_response(
        waypoints=result.waypoints,
        at=at,
        distance_km=round(route_distance_km(result.waypoints), 2),
        surfaces=[SurfaceModel(name=s.name, distance_km=s.distance_km, percentage=s.percentage)
                  for s in result.surfaces],
        warnings=result.warnings,
    )


# --- /api/upload  (parse GPX, no ORS call) --------------------------------

class UploadResponse(BaseModel):
    waypoints:  list[WaypointModel]
    route_info: RouteInfoModel

@app.post("/api/upload", response_model=UploadResponse)
async def upload_gpx(file: UploadFile = File(...)):
    """Parse an uploaded GPX file and return its waypoints + distance."""
    if not file.filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="Only .gpx files are supported.")

    content = await file.read()
    try:
        waypoints = parse_gpx(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return UploadResponse(
        waypoints=[WaypointModel(lat=w.lat, lon=w.lon) for w in waypoints],
        route_info=RouteInfoModel(
            distance_km=round(route_distance_km(waypoints), 2),
            surfaces=[],
            warnings=[],
        ),
    )


# --- /api/wind-overlay  (wind for known waypoints) -----------------------

class WindOverlayRequest(BaseModel):
    waypoints:    list[WaypointModel]
    datetime_iso: str

class WindOverlayResponse(BaseModel):
    segments:    list[SegmentModel]
    wind_arrows: list[WindArrowModel]

@app.post("/api/wind-overlay", response_model=WindOverlayResponse)
async def calculate_wind_overlay(request: WindOverlayRequest):
    """Calculate wind segments and arrows for a set of waypoints."""
    at = _parse_datetime(request.datetime_iso)
    waypoints = [Coordinate(lat=w.lat, lon=w.lon) for w in request.waypoints]

    try:
        wind_samples   = await get_wind_along_route(waypoints, at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    segments       = analyse_route_wind(waypoints, wind_samples)
    display_arrows = generate_display_arrows(waypoints, wind_samples)

    return WindOverlayResponse(
        segments=[
            SegmentModel(
                start=WaypointModel(lat=s.start.lat, lon=s.start.lon),
                end=WaypointModel(lat=s.end.lat,     lon=s.end.lon),
                headwind_ms=round(s.headwind_ms, 2),
            )
            for s in segments
        ],
        wind_arrows=[
            WindArrowModel(
                lat=a.lat, lon=a.lon,
                speed_ms=round(a.speed_ms, 2),
                direction_deg=round(a.direction_deg, 1),
            )
            for a in display_arrows
        ],
    )


# --- Helpers --------------------------------------------------------------

def _parse_datetime(datetime_iso: str) -> datetime:
    try:
        return datetime.fromisoformat(datetime_iso)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime. Use ISO 8601: 2024-06-01T10:00:00")


async def _build_route_response(
    waypoints: list[Coordinate],
    at: datetime,
    distance_km: float,
    surfaces: list[SurfaceModel],
    warnings: list[str],
) -> RouteResponse:
    try:
        wind_samples   = await get_wind_along_route(waypoints, at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    segments       = analyse_route_wind(waypoints, wind_samples)
    display_arrows = generate_display_arrows(waypoints, wind_samples)

    return RouteResponse(
        segments=[
            SegmentModel(
                start=WaypointModel(lat=s.start.lat, lon=s.start.lon),
                end=WaypointModel(lat=s.end.lat,     lon=s.end.lon),
                headwind_ms=round(s.headwind_ms, 2),
            )
            for s in segments
        ],
        wind_arrows=[
            WindArrowModel(
                lat=a.lat, lon=a.lon,
                speed_ms=round(a.speed_ms, 2),
                direction_deg=round(a.direction_deg, 1),
            )
            for a in display_arrows
        ],
        route_info=RouteInfoModel(
            distance_km=distance_km,
            surfaces=surfaces,
            warnings=warnings,
        ),
    )


# --- Frontend -------------------------------------------------------------

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("app/static/index.html")
