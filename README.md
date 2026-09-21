# GeoAgent Monitor

Near-real-time satellite change detection and critical-infrastructure risk
monitoring. Sentinel-2 composites from Google Earth Engine → robust spectral
change detection → OpenStreetMap proximity scoring → risk report, with a
LangGraph agent that can drive the same pipeline from natural language.

> Sentinel-2 is not a live feed. Every result reports the acquisition dates and
> scene counts that were actually used.

```
geoagent/
├── backend/        FastAPI + SQLAlchemy + pipeline + LangGraph agent
│   ├── geoagent/
│   │   ├── api/routes.py          REST + SSE endpoints
│   │   ├── db/models.py           ORM (SQLite dev / PostgreSQL prod)
│   │   ├── pipeline/              earth_engine, change_detection, geo, infrastructure, previews, monitor
│   │   ├── agent/                 tools, prompts, LangGraph graph
│   │   └── services/              runs, jobs (thread pool + SSE), chat
│   ├── db/schema.sql              reference PostgreSQL DDL
│   ├── models_store/              your pre-trained checkpoints (unchanged)
│   └── scripts/                   run_agent.py, train_change_detector.py
│   └── webui/                     built dashboard, served by the same process
├── frontend/       Next.js 14 source (static export → backend/webui)
├── chat/           optional Chainlit console (a second process; not required)
└── deploy/         systemd unit, nginx, install.sh / update.sh, postgres-setup.sql
```

## Run it — one server

The dashboard is exported as a static site into `backend/webui/`, so a single
uvicorn process serves both the UI and the API on one port. Node is needed once
to build the UI, never to run it.

**Linux / macOS**

```bash
cd backend
cp .env.example .env               # EE_PROJECT + one LLM key
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_ui.py         # builds the dashboard into backend/webui
earthengine authenticate           # once
uvicorn geoagent.main:app --port 8000
```

**Windows (PowerShell)**

```powershell
cd backend
copy .env.example .env
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python scripts\build_ui.py
earthengine authenticate
uvicorn geoagent.main:app --port 8000
```

Then open **http://127.0.0.1:8000** — dashboard, API and docs (`/api/docs`) all
live there. Rebuild the UI with `python scripts/build_ui.py` after any frontend
change.

No Earth Engine access yet? Set `ALLOW_MOCK_IMAGERY_FALLBACK=true` in `.env` and
the pipeline runs on clearly-labelled synthetic imagery so you can explore the
interface.

### Working on the UI

For hot reload while editing the frontend, run a second dev server alongside the
API — this is a development convenience, not a deployment requirement:

```bash
cd frontend && npm run dev     # http://localhost:3000, proxies /api to :8000
```

### Optional: Chainlit console

`chat/` holds a Chainlit client for the same API if you want a separate chat
surface. It is a second process and entirely optional — the dashboard already
has the agent built in.

```bash
cd chat && pip install -r requirements.txt && chainlit run app.py --port 8501
```

## How the interface works

The console is the product: you talk to the agent and it does the work. The map
behind the conversation is a canvas that reacts to whatever the agent produced —
ask "what changed here in the last two weeks?" and it fetches the composites,
runs detection, scores infrastructure proximity, then draws the result and
explains it with the dates and scene counts it actually used.

Manual controls (coordinates, windows, threshold, buffer, draw-an-area) sit
behind the **Settings** button for when you want to drive the pipeline directly.
**History** lists past runs; each opens a full report with the before/after
slider, per-polygon risk table and downloadable GeoTIFF/GeoJSON.

## What changed from the Streamlit version

**Accuracy**
- The old detector min-max scaled the difference image, so two identical scenes
  still produced a "100 % change" pixel. The new `SpectralChangeDetector`
  combines reflectance and NDVI differences, standardises with a median/MAD
  z-score, and cleans speckle morphologically. Tested: 896/900 px recovered on a
  synthetic change, 0 false positives on noise-only or identical inputs.
- Distances and areas are computed in the local UTM zone instead of Web
  Mercator (which is ~5 % off at 17° N).
- Each change polygon stores its nearest infrastructure type, name and distance.

**Latency**
- Earth Engine is initialised once per process, not per request.
- Runs execute in a thread pool; the UI streams stage progress over SSE.
- PNG previews are rendered once at the end of a run; the map loads images,
  never GeoTIFFs.
- OSM results are cached in the database with a TTL and mirror rotation; 429s
  are honoured.
- Per-stage timings are stored on every run (`timings` column) so you can see
  where time goes.

**Correctness**
- Removed deprecated `datetime.utcnow()` / `utcfromtimestamp()` (gone in 3.12+).
- Removed duplicate imports and the ad-hoc `sys.path` hacks; the backend is a
  proper package.
- Input validation (bbox ordering, AOI size cap, window ordering) at the API
  boundary with clear 422 messages.
- The agent has one tool that runs the whole pipeline; it cannot launch
  half-pipelines in parallel.

## Database

SQLite by default (`backend/data/geoagent.db`, WAL mode). For production set
`DATABASE_URL=postgresql+psycopg://…` in `.env`. Tables are created
automatically on first start; `backend/db/schema.sql` is the equivalent DDL
with constraints and indexes for DBAs.

| Table | Purpose |
|---|---|
| `aoi_presets` | Saved bounding boxes shown in the UI |
| `monitoring_runs` | One row per run: parameters, imagery evidence, results, timings, status |
| `change_polygons` | Vectorised change areas with risk score and nearest infrastructure |
| `infrastructure_cache` | Overpass cache index (payload on disk) |
| `chat_sessions` / `chat_messages` | Conversation history shared by web and Chainlit |

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/runs` | Start a run (202; returns the run row) |
| `GET` | `/api/runs?limit=&offset=&status=` | Paged history |
| `GET` | `/api/runs/{id}` | Full run incl. polygons and preview URLs |
| `GET` | `/api/runs/{id}/events` | SSE stage updates |
| `GET` | `/api/runs/{id}/polygons` · `/infrastructure` | GeoJSON |
| `GET` | `/api/runs/{id}/preview/{t1,t2,change}.png` | Map overlays |
| `GET` | `/api/runs/{id}/download/{file}` | GeoTIFFs, GeoJSON, report |
| `DELETE` | `/api/runs/{id}` | Remove run + files |
| `POST` | `/api/chat` | Agent (or deterministic fallback) |
| `GET` | `/api/presets` · `/api/health` | |

Interactive docs at `/api/docs`.

## Production deployment (no Docker)

Ubuntu 22.04/24.04, as root:

```bash
DOMAIN=geoagent.yourcompany.com bash deploy/install.sh
```

This installs GDAL/Node/nginx, creates the `geoagent` system user, builds the
dashboard into `backend/webui`, and installs one systemd service plus the nginx
site:

- `geoagent-api` — gunicorn with uvicorn workers on `127.0.0.1:8000`, serving
  the API and the dashboard together

Then edit `/opt/geoagent/backend/.env`, authenticate Earth Engine for the
service user (a service account is the sane choice on a headless box), restart
`geoagent-api`, and run `certbot --nginx`. For PostgreSQL run
`deploy/postgres-setup.sql` then `backend/db/schema.sql`. Redeploy with
`bash deploy/update.sh`.

Logs: `journalctl -u geoagent-api -f`.

## Optional deep-learning path

`method=siamese_lite | fcsiam_diff | smp_unet` on `POST /api/runs` uses a torch
model; install the commented packages in `backend/requirements.txt` and point
`CHANGE_MODEL_CHECKPOINT` at a checkpoint from
`scripts/train_change_detector.py`. Without a checkpoint these are near-random
and the log says so.
