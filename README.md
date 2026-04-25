# Windward

A wind-aware cycling route planner for the Netherlands. Plan a route on the map and see instantly where you'll face headwind or tailwind — colour-coded per segment, with wind arrows and a breakdown summary.

**Live at:** https://windward-odgb.onrender.com

---

## Features

- **Address search** — type a Dutch address to set start, end, or via points
- **Plan a route** by clicking start, end, and optional via points on the map
- **Smart via point insertion** — new points slot into the nearest segment automatically
- **Drag waypoints** to reposition them; the route recalculates automatically
- **Undo** the last-added point, or click a via marker to remove it
- **Upload a GPX** file from Strava, Komoot, or Garmin and overlay wind on your existing route
- **Wind colour scale** — green = tailwind, yellow = crosswind, red = headwind (±5 m/s)
- **Wind arrows** on the map showing direction and strength
- **Wind summary** showing % tailwind / crosswind / headwind
- **Route info** — total distance, surface breakdown (asphalt, gravel, …), ORS warnings (ferries etc.)
- **Download GPX** of the calculated route
- **Historical wind** — works for past dates using the Open-Meteo archive API
- **Mobile-friendly** layout

---

## Setup

**Requirements:** Python 3.9+, a free [OpenRouteService API key](https://openrouteservice.org/)

```bash
# 1. Clone the repo
git clone https://github.com/LotteMertens/WindWard.git
cd WindWard

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your ORS API key
echo "ORS_API_KEY=your_key_here" > .env
```

---

## Running

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

Interactive API docs are available at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Usage

### Plan tab
1. Search a Dutch address or click the map to place a **start point** (green S)
2. Search or click again to place an **end point** (red E) — the route calculates automatically
3. Add **via points** (blue 1, 2, …) by searching or clicking — inserted into the nearest segment
4. **Drag** any marker to reposition it; the route recalculates on release
5. Click a via marker to **remove** it, or use **Undo** to reverse the last action
6. Change the departure time and hit **Calculate route + wind** to update wind conditions

### Upload tab
1. Choose a `.gpx` file exported from Strava, Komoot, Garmin, or similar
2. Click **Load route** to display it on the map
3. Click **Calculate wind** to overlay wind conditions for your chosen departure time

### Export
After any route is calculated, **Download GPX** appears in the summary card. The file contains the full route geometry and can be loaded into any cycling device or app.

---

## External APIs

| API | Purpose | Auth |
|-----|---------|------|
| [OpenRouteService](https://openrouteservice.org/) | Cycling route geometry, surface types, warnings | API key in `.env` |
| [Open-Meteo](https://open-meteo.com/) | Wind speed and direction (forecast + historical archive) | None required |
| [ORS Geocoder](https://openrouteservice.org/dev/#/api-docs/geocode) | Address search (Netherlands only) | API key in `.env` |

Wind data uses the ECMWF model at ~9 km grid resolution. Forecasts are available up to 16 days ahead; past dates automatically switch to the archive endpoint. Open-Meteo is called directly from the visitor's browser (not the server), which distributes requests across user IPs and avoids rate limiting. Concurrent requests are capped at 5.

---

## Deployment

The app is deployed as a single service on [Render](https://render.com) using `render.yaml`. FastAPI serves both the API and the static frontend.

To deploy your own instance:
1. Push the repo to GitHub
2. Create a new Web Service on Render, connect the repo — it will detect `render.yaml` automatically
3. Add `ORS_API_KEY` as an environment variable in the Render dashboard

---

## Development

Work on a `dev` branch and merge to `main` when ready — Render deploys automatically on push to `main`.

```bash
git checkout dev        # or: git checkout -b dev
# ... make changes ...
git checkout main
git merge dev
git push
```

## Running tests

```bash
source venv/bin/activate
pytest                                         # all tests
pytest tests/test_analysis.py -v              # single file
pytest tests/test_analysis.py::test_name -v   # single test
```
