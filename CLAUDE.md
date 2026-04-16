# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

KomootLayer is a Python project that extends Komoot route planning with additional data layers. The first feature is wind-aware routing: the user clicks start and end points on a map, and the app shows where the rider faces headwind or tailwind along the route.

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
KomootLayer/
├── src/
│   ├── routing/       # ORS client: fetch a cycling route from A to B
│   ├── weather/       # Open-Meteo client: fetch wind data at coordinates
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
- `app/main.py` is the only place that wires modules together and calls the API.
- Frontend (`app/static/`) talks to the backend only via `POST /api/route`.
- **All constants live in `src/config.py`** — never define magic numbers or configuration values inline in module files. API URLs, tuning parameters, thresholds — all go in config.

### Data flow
```
User clicks map (app.js)
  → POST /api/route  (app/main.py)
    → src/routing: ORS API  → list of Coordinates
    → src/weather: Open-Meteo API → list of WindSamples
    → src/analysis: pure maths → list of SegmentWind
  → JSON response
    → Leaflet draws coloured route (app.js)
```

### External APIs
- **OpenRouteService (ORS)**: free tier, requires API key in `.env` as `ORS_API_KEY`. Used with the `cycling-regular` profile. Coordinates are `[lon, lat]` order (GeoJSON convention) — internal code accepts `(lat, lon)` and flips before calling the API.
- **Open-Meteo**: no auth required. Returns hourly wind speed (m/s) and direction (degrees, 0 = north clockwise) per grid cell (~9 km resolution, ECMWF model).

### Frontend
Static HTML + Leaflet.js served directly by FastAPI (`app/static/`). No build step, no npm. Leaflet is loaded from a CDN. Deployed to GitHub Pages (frontend) + Render (backend) once ready.
