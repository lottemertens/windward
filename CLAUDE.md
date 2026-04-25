# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Windward is a wind-aware cycling route planner for the Netherlands. The user plans a route (by clicking the map or searching an address), and the app shows where the rider faces headwind or tailwind along the route — colour-coded, with wind arrows and a summary.

The project is built step by step as a learning exercise. Code should be explained when written. Prefer clarity over cleverness.

## Commands

```bash
# Set up environment (first time)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the development server (auto-reloads on file save)
uvicorn app.main:app --reload

# Run all tests
pytest

# Run a single test file
pytest tests/test_analysis.py -v

# Run a single test by name
pytest tests/test_analysis.py::test_name -v
```

The app is then available at http://localhost:8000. FastAPI also auto-generates interactive API docs at http://localhost:8000/docs.

## Architecture

```
WindWard/
├── src/
│   ├── routing/       # ORS client: fetch a cycling route from A to B
│   ├── weather/       # Open-Meteo helpers: sample route, interpolate pre-fetched forecasts
│   └── analysis/      # Pure maths: compute headwind/tailwind per segment
├── app/
│   ├── main.py        # FastAPI app — API endpoints + serves static files
│   └── static/        # Frontend: index.html, app.js (Leaflet), style.css
├── tests/             # pytest tests, mirrors src/ structure
├── notebooks/         # Exploration notebooks
└── data/              # Sample/cached API responses for offline dev
```

### Key design principles
- Each `src/` module has one responsibility and does not import from other `src/` modules (except `analysis/`, which uses the data types from `routing/` and `weather/`).
- `src/analysis/` is pure Python with no HTTP calls — making it trivial to test.
- `app/main.py` is the only place that wires modules together.
- **All constants live in `src/config.py`** (Python) or the constants section at the top of `app/static/app.js` (frontend). Never define magic numbers or configuration values inline — not in module files, not buried in functions, not in the middle of a class. API URLs, tuning parameters, thresholds, buffer sizes — all go in the appropriate config location.

### Data flow
```
User searches address or clicks map (app.js)

Step 1 — ORS routing (server):
  app.js → POST /api/route
    → src/routing: ORS API → list of Coordinates + sample_points
  → returns coords, sample_points, distance, surfaces, elevations

Step 2 — Wind fetch (browser):
  app.js → Open-Meteo API directly (visitor's IP, max 5 concurrent)
  → returns raw 48-h forecast per sample point

Step 3 — Wind analysis (server):
  app.js → POST /api/analyze (forecasts + full route coords)
    → src/weather: pure functions interpolate wind at each point/time
    → src/analysis: pure maths → SegmentWind list + display arrows + departure scores
  → Leaflet draws coloured route + arrows
```

### External APIs
- **OpenRouteService (ORS)**: free tier, requires API key in `.env` as `ORS_API_KEY`. Uses `cycling-regular` profile (hardcoded in `src/config.py`). Coordinates are `[lon, lat]` order (GeoJSON convention) — internal code uses `(lat, lon)` and flips before calling. Also used for address geocoding via `/api/geocode` (proxied to add the API key).
- **Open-Meteo**: no auth required. Returns hourly wind speed (m/s) and direction (degrees, 0 = north clockwise) per grid cell (~9 km resolution, ECMWF model). Past dates use the archive endpoint automatically. **Called directly from the visitor's browser** — not from the server — so requests use the visitor's IP, avoiding shared-IP rate limits on Render. Concurrent requests are capped at `MAX_WIND_CONCURRENT` (5) via a JS semaphore in `fetchWindForPoints()`.

### Frontend
Static HTML + Leaflet.js served directly by FastAPI (`app/static/`). No build step, no npm. Leaflet is loaded from a CDN.

Key frontend behaviours:
- Waypoints use `L.marker` + `L.divIcon` (not `circleMarker`) so they land in Leaflet's `markerPane` (z-index 600) above route polylines (overlayPane, z-index 400).
- New via points are inserted into the nearest segment using `distToSegmentSq`, not always appended before the end.
- Each point carries an `addedAt` counter so undo always removes the most recently added point regardless of array position.
- Wind arrows use inline SVG `<polygon transform="rotate(...)">` — CSS rotation is unreliable inside Leaflet `divIcon`.

### Deployment
Single service on Render — FastAPI serves both API and frontend. Defined in `render.yaml`. Auto-deploys on push to `main`. Work on `dev` branch and merge when ready.
