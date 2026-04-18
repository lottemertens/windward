"""
KomootLayer API server.

Run with:
    uvicorn app.main:app --reload

Endpoints:
    GET  /api/wind              — wind at default location for a given time
    POST /api/route             — plan a route via ORS + calculate wind
    POST /api/upload            — parse an uploaded GPX file
    POST /api/wind-overlay      — calculate wind for an already-known set of waypoints
    GET  /api/closures          — active road closures for all of NL (cached)
    POST /api/refresh-closures  — force refresh the closure cache (token-protected)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx

from src.routing.ors_client import get_cycling_route
from src.weather.wind_client import get_wind_along_route, get_wind_along_route_timed, get_wind_at
from src.analysis.wind_analysis import analyse_route_wind, generate_display_arrows
from src.gpx_parser import parse_gpx
from src.geo import route_distance_km
from src.models import Coordinate
from src.config import DEFAULT_LOCATION_LAT, DEFAULT_LOCATION_LON, DEFAULT_LOCATION_NAME, ORS_GEOCODE_URL, CLOSURE_AVOID_BUFFER_DEG
from src.closures.ndw_client import get_closures, force_refresh

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
    distance_km:  float
    surfaces:     list[SurfaceModel]   # empty for uploaded routes
    warnings:     list[str]            # empty for uploaded routes
    duration_min: Optional[int] = None # estimated riding time (timed wind only)


# --- /api/geocode  (address search via ORS) --------------------------------

class GeocodeSuggestion(BaseModel):
    name:     str    # short display name shown in the dropdown
    full:     str    # full address shown on hover / in the marker popup
    lat:      float
    lon:      float

@app.get("/api/geocode", response_model=list[GeocodeSuggestion])
async def geocode(q: str):
    """Search for a Dutch address using the ORS geocoder."""
    if len(q.strip()) < 3:
        return []
    api_key = os.getenv("ORS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ORS_API_KEY is not set")
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                ORS_GEOCODE_URL,
                params={
                    "api_key":          api_key,
                    "text":             q,
                    "boundary.country": "NLD",
                    "size":             5,
                },
                timeout=5.0,
            )
            res.raise_for_status()
    except httpx.HTTPStatusError:
        return []   # geocoder unavailable — return empty rather than 500
    features = res.json().get("features", [])
    suggestions = []
    for f in features:
        props = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        name     = props.get("name", "")
        locality = props.get("locality", "")
        short    = f"{name}, {locality}" if locality and locality != name else name
        suggestions.append(GeocodeSuggestion(
            name=short or props.get("label", ""),
            full=props.get("label", ""),
            lat=lat,
            lon=lon,
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
    waypoints:        list[WaypointModel]   # [start, optional via points..., end]
    datetime_iso:     str
    avoid_geometries: list           = []   # list of [[lat,lon],...] closure geometries to avoid
    speed_kmh:        Optional[float] = None  # if set, use time-dependent wind calculation

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

    at = _parse_datetime(request.datetime_iso)

    avoid = _geometries_to_avoid_polygons(request.avoid_geometries)

    try:
        result = await get_cycling_route(
            waypoints=[Coordinate(lat=w.lat, lon=w.lon) for w in request.waypoints],
            api_key=api_key,
            avoid_polygons=avoid,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"ORS error: {e.response.text}")

    return await _build_route_response(
        waypoints=result.waypoints,
        at=at,
        speed_kmh=request.speed_kmh,
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
    speed_kmh:    Optional[float] = None  # if set, use time-dependent wind calculation

class WindOverlayResponse(BaseModel):
    segments:     list[SegmentModel]
    wind_arrows:  list[WindArrowModel]
    duration_min: Optional[int] = None  # estimated riding time (timed wind only)

@app.post("/api/wind-overlay", response_model=WindOverlayResponse)
async def calculate_wind_overlay(request: WindOverlayRequest):
    """Calculate wind segments and arrows for a set of waypoints."""
    at = _parse_datetime(request.datetime_iso)
    waypoints = [Coordinate(lat=w.lat, lon=w.lon) for w in request.waypoints]

    try:
        if request.speed_kmh:
            wind_samples, duration_min = await get_wind_along_route_timed(waypoints, at, request.speed_kmh)
        else:
            wind_samples  = await get_wind_along_route(waypoints, at)
            duration_min  = None
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
        duration_min=duration_min,
    )


# --- /api/closures  (cached NDW road closures) ----------------------------

class ClosureModel(BaseModel):
    situation_id:     str
    lat:              float
    lon:              float
    source:           str
    start:            str
    end:              Optional[str] = None
    description:      Optional[str] = None
    geometry:         list          = []   # [[lat, lon], …] for map highlight
    warning:          Optional[str] = None # plain-Dutch summary, e.g. "Weg dicht in één richting"
    project_name:     Optional[str] = None
    url:              Optional[str] = None
    bicycle_specific: bool          = False

@app.get("/api/closures", response_model=list[ClosureModel])
async def list_closures():
    """
    Return all active and upcoming road closures (carriagewayClosures) for NL.
    Data is fetched from the NDW planning feed once per day and cached in memory.
    The first call of the day will take a few seconds while the 237 MB feed is
    downloaded and parsed; subsequent calls return instantly from cache.
    """
    try:
        records = await get_closures()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch NDW data: {e}")
    return [ClosureModel(
                situation_id=r.situation_id,
                lat=r.lat, lon=r.lon, source=r.source,
                start=r.start, end=r.end, description=r.description,
                geometry=r.geometry, warning=r.warning,
                project_name=r.project_name, url=r.url,
                bicycle_specific=r.bicycle_specific,
            ) for r in records]


@app.post("/api/refresh-closures")
async def refresh_closures(token: str):
    """
    Force-refresh the NDW closure cache. Protected by REFRESH_TOKEN env var so
    only the GitHub Actions cron job (or you) can call it.
    """
    expected = os.getenv("REFRESH_TOKEN")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid token.")
    try:
        count = await force_refresh()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Refresh failed: {e}")
    return {"ok": True, "closures_cached": count}


# --- Helpers --------------------------------------------------------------

def _geometries_to_avoid_polygons(geometries: list) -> Optional[dict]:
    """
    Convert a list of [[lat,lon],...] closure geometry arrays into an ORS
    avoid_polygons GeoJSON object (MultiPolygon).

    For each geometry we build a bounding-box polygon with CLOSURE_AVOID_BUFFER_DEG
    padding, then hand the whole set to ORS in one go.
    ORS expects [lon, lat] coordinate order (GeoJSON convention).
    """
    if not geometries:
        return None

    polygons = []
    pad = CLOSURE_AVOID_BUFFER_DEG
    for geo in geometries:
        if not geo:
            continue
        lats = [p[0] for p in geo]
        lons = [p[1] for p in geo]
        ring = [
            [min(lons) - pad, min(lats) - pad],
            [max(lons) + pad, min(lats) - pad],
            [max(lons) + pad, max(lats) + pad],
            [min(lons) - pad, max(lats) + pad],
            [min(lons) - pad, min(lats) - pad],   # close the ring
        ]
        polygons.append([ring])

    return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None


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
    speed_kmh: Optional[float] = None,
) -> RouteResponse:
    try:
        if speed_kmh:
            wind_samples, duration_min = await get_wind_along_route_timed(waypoints, at, speed_kmh)
        else:
            wind_samples  = await get_wind_along_route(waypoints, at)
            duration_min  = None
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
            duration_min=duration_min,
        ),
    )


# --- Frontend -------------------------------------------------------------

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("app/static/index.html")
