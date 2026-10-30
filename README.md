# PeakAge Smart Assign

*Find the right care provider for every visit.*

A standalone prototype that recommends the most suitable home-visit provider for a Bengaluru visit. The coordinator enters an address, a service, a date, a start time and a duration. The system checks every provider, removes the ones who cannot do the visit, ranks the rest with a transparent score and shows the top three with reasons.

**It is not connected to the PeakAge app or any production data.** All parents and providers are synthetic, and the prototype has no production authentication.

## What it does

- Geocodes the address and asks the coordinator to confirm it. It never silently picks one.
- Hard filters first (role, approval, verification, credentials, mode, coverage, hours, leave, conflicts, workload), then travel feasibility using the provider's *previous appointment* as the origin, then ranking.
- Score out of 100: travel 30, continuity 20, reliability 20, schedule fit 15, locality 10, workload 5. The weights live in `backend/app/config.py` and are starting hypotheses, not validated values.
- Confirming an assignment re-checks everything inside a write-locked transaction, so two coordinators cannot double-book a provider.
- Provider directory, visit list with assignment history, and an evaluation dashboard.

## Architecture

```
frontend/    React + TypeScript + Vite, Leaflet map (OpenStreetMap tiles), plain CSS from the PeakAge design system
backend/
  app/config.py              weights, limits, service catalogue, localities, settings (one place to tune)
  app/services/engine.py     eligibility -> feasibility -> scoring -> explanations (pure, deterministic, no LLM)
  app/services/routing.py    RoutingProvider: Demo, OlaMaps, Unavailable
  app/services/geocoding.py  GeocodingProvider: demo gazetteer and Ola Maps
  app/services/assignment.py recommendation runs and the concurrency-safe assign path
  app/api.py                 REST endpoints (OpenAPI at /docs)
  app/seed.py                deterministic Bengaluru demo data (90 providers, 30 parents)
  tests/                     pytest
render.yaml, build.sh        Render deployment
```

In production one FastAPI service serves both the API and the built frontend, so there is no CORS to configure. The engine reaches routing only through the `RoutingProvider` interface, so it can be lifted into another application and called through the API later.

## Run locally

Requirements: Python 3.12, Node 20 or newer.

```bash
# terminal 1: backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env          # optional, the defaults run in demo mode
uvicorn app.main:app_factory --factory --reload --port 8000

# terminal 2: frontend
cd frontend
npm install
npm run dev                       # http://localhost:5173, proxies /api to :8000
```

The database is created and seeded on first start. Helper commands (run from `backend/`):

```bash
python -m app.cli init-db     # create tables
python -m app.cli seed        # seed if empty
python -m app.cli reset       # drop everything and restore the original demo data
python -m app.cli check-ola   # one live geocode and distance-matrix call, prints raw responses
```

The dataset can also be reset from **Insights > Reset demo data**.

### Tests and production build

```bash
cd backend && python -m pytest        # 64 tests
cd frontend && npm test && npm run build
```

## Demo mode and Ola Maps

| | Demo mode (default) | Live mode |
|---|---|---|
| Setting | `ROUTING_MODE=demo` | `ROUTING_MODE=live` and `OLA_MAPS_API_KEY=...` |
| Geocoding | Gazetteer of the 12 demo localities. Coordinates are approximate and labelled so | Ola Maps geocoding. Results outside Bengaluru are discarded |
| Routing | Deterministic simulation from straight-line distance with a peak-hour factor | Ola Maps Distance Matrix (road distance and duration) |
| Labelling | "Demo routing, not live traffic" wherever an ETA appears | "Live routing" |

If `ROUTING_MODE=live` and the key is missing, the app **does not fall back to demo**. Address lookup and travel times fail with a clear message, and no provider is recommended without a verified route.

### Ola Maps: what is verified and what is not

Verified from Ola's public material: base URL `https://api.olamaps.io`, `GET /places/v1/geocode`, `GET /routing/v1/distanceMatrix` with `origins` and `destinations` as `lat,lng|lat,lng`, and authentication through the `api_key` query parameter (sent from the backend only).

**Could not be verified when this was built** (Ola's API reference renders client-side): exact response field names and units, per-request element limits, rate limits, and whether traffic-aware ETAs exist on your plan. The code therefore:

- reads distance as metres and duration as seconds, then rejects any element that is physically implausible against the straight-line distance, so a wrong unit assumption fails closed instead of producing wrong ETAs;
- batches at `OLA_MATRIX_MAX_ELEMENTS` (default 25), with an 8 second timeout and 2 retries with backoff;
- labels every live ETA as standard (`traffic_aware: false`) until you confirm traffic support.

**Before relying on live mode, run `python -m app.cli check-ola` once with your key** and compare the printed responses with the parsers in `routing.py` and `geocoding.py`. If a field name differs, the fix is a one-line change in `_element` or `_parse`.

## Deploy to Render

1. Push this folder to a GitHub repository.
2. In Render choose **New > Blueprint** and select the repository. `render.yaml` creates one web service.
3. In the service's **Environment** tab:
   - set `OLA_MAPS_API_KEY` (optional) and change `ROUTING_MODE` to `live` when ready;
   - read the generated `APP_ACCESS_TOKEN`. Anyone opening the app is asked for it as a passcode.
4. Open the service URL. The first start creates and seeds the database.

**Storage.** SQLite lives on a persistent disk at `/var/data`. That needs a paid instance (as in the Blueprint) and a **single instance**. On the free tier there is no disk, so the filesystem and the data reset on every deploy and restart. To avoid that, or to run more than one instance, create a Render Postgres database, delete the `disk` block and set `DATABASE_URL` to its internal connection string. The code takes a row lock (`SELECT ... FOR UPDATE`) on that path, but it has not been run against a real Postgres in this build, so test it first.

### Environment variables

| Variable | Purpose |
|---|---|
| `ROUTING_MODE` | `demo` or `live` |
| `OLA_MAPS_API_KEY` | Ola Maps key. Backend only, never sent to the browser |
| `DATABASE_URL` | SQLAlchemy URL. Default `sqlite:///./data/smart_assign.db` |
| `APP_ACCESS_TOKEN` | Optional shared passcode for all `/api` calls (`X-Access-Token` header) |
| `FRONTEND_ORIGIN` | Comma-separated extra CORS origins. Leave empty when one service serves both |
| `STATIC_DIR` | Folder of the built frontend to serve (set by the Blueprint) |
| `SCORING_WEIGHTS` | Optional JSON override of the six weights (must sum to 1) |
| `OLA_MATRIX_MAX_ELEMENTS`, `OLA_TIMEOUT_SECONDS`, `OLA_MAX_RETRIES` | Ola tuning |

## API

Interactive docs at `/docs`. Every `/api` route except `/api/config` needs the `X-Access-Token` header when `APP_ACCESS_TOKEN` is set.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness and database check |
| GET | `/api/config` | weights, services, routing mode (never secrets) |
| POST | `/api/geocode` | address candidates for confirmation |
| GET | `/api/parents` | demo parents |
| GET, POST | `/api/visits` | list (`?status=`) and create |
| GET, PATCH | `/api/visits/{id}` | read, or edit while unassigned (invalidates earlier recommendations) |
| POST | `/api/visits/{id}/recommendations` | rank providers. **Never assigns** |
| POST | `/api/visits/{id}/assign` | confirm a provider from a recommendation run |
| POST | `/api/visits/{id}/unassign` | release the provider |
| GET | `/api/visits/{id}/assignment-history` | every assignment with score breakdown and route |
| GET | `/api/providers` | search and filter (`q`, `role`, `verification`, `locality`, `active`) |
| GET, PATCH | `/api/providers/{id}` | profile, hours, activate or deactivate |
| POST, DELETE | `/api/providers/{id}/leave[/{leave_id}]` | leave and blocked time |
| GET | `/api/providers/{id}/eligibility?visit_id=` | why a provider is or is not recommendable |
| GET | `/api/analytics/summary` | evaluation metrics |
| POST | `/api/admin/reset-demo` | restore the original dataset |

Assignment errors return `{"detail": {"code", "message"}}` with status 409 (conflict or changed schedule), 422 (not in the shortlist) or 404.

## How the ranking works

1. **Hard filters** never mix with scoring. A failing provider is excluded with a structured reason (`Role mismatch`, `Verification incomplete`, `Required credential expired`, `Outside service coverage`, `Outside working hours`, `Existing appointment conflict`, `Insufficient travel buffer`, `Workload limit reached` and others) and can never be recommended, whatever it would have scored.
2. **Feasibility.** Travel from the previous appointment (or the base) plus the provider's buffer must fit before the visit starts. The visit plus travel to the *next* appointment must fit before that one. If travel to the next appointment cannot be routed it becomes a warning. If travel *to this visit* cannot be routed, the provider is excluded.
3. **Score.** Each factor is 0 to 100. Travel time is banded to 5 minutes so small differences in uncertain ETAs do not matter. Reliability is shrunk towards a neutral prior until a provider has 10 completed visits. No history gives continuity a neutral 50. On-call visits skip travel and locality, and the remaining weights are renormalised.
4. **Ties** break by lower travel time, then lower workload that day, then provider ID.

## Known limitations

- Prototype only: synthetic data, a shared passcode instead of accounts, simulated notifications.
- Ola Maps parsing is not confirmed against live responses (see above), and live ETAs are standard rather than traffic-aware.
- Route lines are not drawn because no route geometry is requested. Markers show origins, not paths.
- Coverage uses the provider's locality list or a straight-line radius, which is an approximation.
- Seeded appointments only block time. They are not guaranteed to be travel-feasible with each other.
- The analytics describe prototype usage. **There is no baseline, so nothing here shows that Smart Assign improves fulfilment or reduces travel time.**
- SQLite is single-instance. The Postgres path exists in code but is untested here.
- The interface follows the PeakAge design system and passes component tests and a production build, but it was not exercised in a real browser in this environment.
