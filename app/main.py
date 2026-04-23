"""
KomootLayer API server.

Run with:
    uvicorn app.main:app --reload

Endpoints:
    POST /api/route             — plan a route via ORS, return coords + sample points
    POST /api/analyze           — analyse pre-fetched wind data (pure maths, no HTTP)
    POST /api/upload            — parse an uploaded GPX file + return sample points
    GET  /api/geocode           — address search via ORS geocoder
    GET  /api/closures          — active road closures for all of NL (cached)
    POST /api/refresh-closures  — force refresh the closure cache (token-protected)

All Open-Meteo requests are made by the browser, not the server.
This distributes requests across visitor IPs and avoids server-side rate limits.
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
from src.weather.wind_client import (
    sample_route,
    interpolate_wind_at_time,
    timed_wind_from_forecasts,
    departure_scores_from_forecasts,
)
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

class DepartureScoreItem(BaseModel):
    hour:  int    # 0–23
    score: float  # mean headwind_ms along route (negative = net tailwind)


# --- /api/geocode  (address search via ORS) --------------------------------

class GeocodeSuggestion(BaseModel):
    name:     str
    full:     str
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
        return []
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


# --- /api/route  (ORS only — no wind) -------------------------------------
# Returns the route geometry and a list of sample points for the browser
# to fetch wind data from Open-Meteo directly.

class RouteRequest(BaseModel):
    waypoints:        list[WaypointModel]
    avoid_geometries: list = []

class RouteDataResponse(BaseModel):
    coords:            list[list[float]]    # [[lat, lon], ...] full ORS polyline
    sample_points:     list[WaypointModel]  # subsampled for Open-Meteo fetch
    total_distance_km: float
    distance_km:       float
    elevations:        list[float]  = []
    surfaces:          list[SurfaceModel] = []
    warnings:          list[str]    = []

@app.post("/api/route", response_model=RouteDataResponse)
async def get_route(request: RouteRequest):
    api_key = os.getenv("ORS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ORS_API_KEY is not set")
    if len(request.waypoints) < 2:
        raise HTTPException(status_code=400, detail="At least 2 waypoints are required.")

    avoid = _geometries_to_avoid_polygons(request.avoid_geometries)

    try:
        result = await get_cycling_route(
            waypoints=[Coordinate(lat=w.lat, lon=w.lon) for w in request.waypoints],
            api_key=api_key,
            avoid_polygons=avoid,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"ORS error: {e.response.text}")

    sampled, total_km = sample_route(result.waypoints)

    return RouteDataResponse(
        coords=[[w.lat, w.lon] for w in result.waypoints],
        sample_points=[WaypointModel(lat=w.lat, lon=w.lon) for w in sampled],
        total_distance_km=round(total_km, 2),
        distance_km=round(route_distance_km(result.waypoints), 2),
        elevations=result.elevations,
        surfaces=[SurfaceModel(name=s.name, distance_km=s.distance_km, percentage=s.percentage)
                  for s in result.surfaces],
        warnings=result.warnings,
    )


# --- /api/analyze  (pure maths on pre-fetched wind) -----------------------
# The browser fetches 48-h Open-Meteo forecasts for each sample point and
# posts them here together with the full route and departure time.
# No HTTP calls are made — this is pure Python maths.

class AnalyzeRequest(BaseModel):
    waypoints:         list[WaypointModel]  # full ORS route coords
    sample_points:     list[WaypointModel]  # same points that were sent to Open-Meteo
    forecasts:         list[dict]           # 48-h Open-Meteo responses, one per sample point
    departure_at:      str                  # ISO 8601 datetime
    speed_kmh:         Optional[float] = None
    total_distance_km: float

class AnalyzeResponse(BaseModel):
    segments:         list[SegmentModel]
    wind_arrows:      list[WindArrowModel]
    duration_min:     Optional[int]         = None
    departure_scores: list[DepartureScoreItem] = []

@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_wind(request: AnalyzeRequest):
    at        = _parse_datetime(request.departure_at)
    waypoints = [Coordinate(lat=w.lat, lon=w.lon) for w in request.waypoints]
    sampled   = [Coordinate(lat=w.lat, lon=w.lon) for w in request.sample_points]

    if not request.forecasts or len(request.forecasts) != len(sampled):
        raise HTTPException(status_code=400,
            detail=f"Expected {len(sampled)} forecast(s), got {len(request.forecasts)}.")

    # Extract wind at departure time from the pre-fetched forecasts
    if request.speed_kmh:
        wind_samples, duration_min = timed_wind_from_forecasts(
            sampled, request.forecasts, at, request.speed_kmh, request.total_distance_km,
        )
    else:
        wind_samples = [interpolate_wind_at_time(f, at) for f in request.forecasts]
        duration_min = None

    segments       = analyse_route_wind(waypoints, wind_samples)
    display_arrows = generate_display_arrows(waypoints, wind_samples)

    # Departure scores (pure Python, same forecasts — no extra API calls)
    hourly = departure_scores_from_forecasts(
        sampled, request.forecasts, at, request.speed_kmh, request.total_distance_km,
    )
    dep_scores = []
    for hour, ws in hourly:
        segs = analyse_route_wind(sampled, ws)
        mean_hw = sum(s.headwind_ms for s in segs) / len(segs) if segs else 0.0
        dep_scores.append(DepartureScoreItem(hour=hour, score=round(mean_hw, 2)))

    return AnalyzeResponse(
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
        departure_scores=dep_scores,
    )


# --- /api/upload  (parse GPX, return sample points for wind fetch) --------

class UploadResponse(BaseModel):
    waypoints:         list[WaypointModel]
    sample_points:     list[WaypointModel]  # subsampled for Open-Meteo fetch
    total_distance_km: float
    distance_km:       float

@app.post("/api/upload", response_model=UploadResponse)
async def upload_gpx(file: UploadFile = File(...)):
    """Parse an uploaded GPX file and return waypoints + sample points for wind fetch."""
    if not file.filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="Only .gpx files are supported.")

    content = await file.read()
    try:
        waypoints = parse_gpx(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    sampled, total_km = sample_route(waypoints)

    return UploadResponse(
        waypoints=[WaypointModel(lat=w.lat, lon=w.lon) for w in waypoints],
        sample_points=[WaypointModel(lat=w.lat, lon=w.lon) for w in sampled],
        total_distance_km=round(total_km, 2),
        distance_km=round(route_distance_km(waypoints), 2),
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
    geometry:         list          = []
    warning:          Optional[str] = None
    project_name:     Optional[str] = None
    url:              Optional[str] = None
    bicycle_specific: bool          = False

@app.get("/api/closures", response_model=list[ClosureModel])
async def list_closures():
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
            [min(lons) - pad, min(lats) - pad],
        ]
        polygons.append([ring])

    return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None


def _parse_datetime(datetime_iso: str) -> datetime:
    try:
        return datetime.fromisoformat(datetime_iso)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime. Use ISO 8601: 2024-06-01T10:00:00")


# --- Frontend -------------------------------------------------------------

app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
async def serve_frontend():
    return FileResponse("app/static/index.html")
