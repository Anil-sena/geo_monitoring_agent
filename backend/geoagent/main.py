from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from geoagent.api.routes import router
from geoagent.config import settings
from geoagent.db.models import AoiPreset
from geoagent.db.session import db_session, init_db
from geoagent.services.jobs import runner

# Third-party libraries are chatty at DEBUG (googleapiclient logs every Earth
# Engine URL, httpcore logs every TLS handshake). Keep the root at INFO and turn
# up only our own logger in development.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _noisy in ("urllib3", "rasterio", "fiona", "pyogrio", "httpx", "httpx2", "httpcore",
               "httpcore2", "googleapiclient", "googleapiclient.discovery", "google",
               "google_auth_httplib2", "openai", "openai._base_client", "ee", "matplotlib"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("geoagent")
log.setLevel(logging.DEBUG if settings.ENV == "development" else logging.INFO)

DEFAULT_PRESETS = [
    ("Visakhapatnam", "Port city, refinery and steel plant corridor", 83.08405, 17.54740, 83.40865, 17.87425),
    ("Visakhapatnam (small)", "Quick test tile over the harbour", 83.15, 17.65, 83.35, 17.80),
    ("Noida / Greater Noida", "Fast-growing NCR construction belt", 77.50, 28.50, 77.70, 28.70),
]


def seed_presets() -> None:
    with db_session() as db:
        existing = {p.name for p in db.execute(select(AoiPreset)).scalars()}
        for name, desc, *bbox in DEFAULT_PRESETS:
            if name not in existing:
                db.add(AoiPreset(name=name, description=desc, minx=bbox[0], miny=bbox[1], maxx=bbox[2], maxy=bbox[3]))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_presets()
    log.info("%s started (env=%s, db=%s)", settings.APP_NAME, settings.ENV, settings.DATABASE_URL.split("://")[0])
    yield
    runner.shutdown()


app = FastAPI(
    title=settings.APP_NAME,
    version="2.0.0",
    description="Sentinel-based change detection and infrastructure-risk monitoring API.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# --- web UI -------------------------------------------------------------
# `npm run build` in frontend/ exports a static site into this folder, so one
# uvicorn process serves both the API and the dashboard. If the folder is
# missing (API-only deployment, or the UI hasn't been built yet), the root
# route explains how to build it instead of 404ing.
UI_DIR = settings.WEBUI_DIR

if (UI_DIR / "index.html").exists():
    app.mount("/_next", StaticFiles(directory=UI_DIR / "_next"), name="next-assets")
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_ui(full_path: str):
        """Serve the exported page for a path, falling back to the shell."""
        candidate = (UI_DIR / full_path).resolve()
        if UI_DIR in candidate.parents or candidate == UI_DIR:
            if candidate.is_file():
                return FileResponse(candidate)
            index = candidate / "index.html"
            if index.is_file():
                return FileResponse(index)
            html = candidate.with_suffix(".html")
            if html.is_file():
                return FileResponse(html)
        return FileResponse(UI_DIR / "index.html")

    log.info("serving the dashboard from %s", UI_DIR)
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {
            "service": settings.APP_NAME,
            "docs": "/api/docs",
            "ui": "not built — run `npm install && npm run build` in frontend/, "
                  "or `python scripts/build_ui.py`",
        }
