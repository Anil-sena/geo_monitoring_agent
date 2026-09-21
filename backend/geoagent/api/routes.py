from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from geoagent import schemas
from geoagent.config import settings
from geoagent.db.models import AoiPreset, ChatMessage, MonitoringRun, RunStatus
from geoagent.db.session import get_db
from geoagent.services import chat as chat_service
from geoagent.services import runs as run_service
from geoagent.services.jobs import runner

router = APIRouter(prefix="/api")


# --- helpers -------------------------------------------------------------------

def _preview_urls(run: MonitoringRun) -> dict[str, str]:
    d = settings.PREVIEW_DIR / run.run_key
    return {n: f"/api/runs/{run.id}/preview/{n}.png" for n in ("t1", "t2", "change") if (d / f"{n}.png").exists()}


def _to_out(run: MonitoringRun) -> schemas.RunOut:
    out = schemas.RunOut.model_validate(run)
    out.previews = _preview_urls(run)
    return out


def _get_run_or_404(ref: str, db: Session) -> MonitoringRun:
    run = db.execute(
        select(MonitoringRun).where((MonitoringRun.id == ref) | (MonitoringRun.run_key == ref))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run '{ref}' not found")
    return run


# --- health --------------------------------------------------------------------

@router.get("/health", response_model=schemas.HealthOut)
def health(db: Session = Depends(get_db)):
    from geoagent.pipeline import earth_engine

    ee_state = "ready" if earth_engine._initialised else ("configured" if settings.EE_PROJECT else "not configured")
    total = db.execute(select(func.count(MonitoringRun.id))).scalar_one()
    return schemas.HealthOut(
        status="ok", env=settings.ENV, earth_engine=ee_state,
        llm_provider=settings.LLM_PROVIDER if settings.llm_configured else None,
        llm_configured=settings.llm_configured,
        database=settings.DATABASE_URL.split("://")[0], runs_total=total,
    )


# --- presets -------------------------------------------------------------------

@router.get("/presets", response_model=list[schemas.PresetOut])
def presets(db: Session = Depends(get_db)):
    return db.execute(select(AoiPreset).order_by(AoiPreset.name)).scalars().all()


# --- runs ----------------------------------------------------------------------

@router.post("/runs", response_model=schemas.RunOut, status_code=status.HTTP_202_ACCEPTED)
def create_run(payload: schemas.RunCreate):
    run = run_service.create_run(payload.model_dump(), requested_by="ui")
    runner.submit(run.id)
    return _to_out(run)


@router.get("/runs", response_model=schemas.RunListOut)
def list_runs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
              status_filter: RunStatus | None = Query(None, alias="status"),
              db: Session = Depends(get_db)):
    q = select(MonitoringRun)
    if status_filter:
        q = q.where(MonitoringRun.status == status_filter)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = db.execute(q.order_by(MonitoringRun.created_at.desc()).offset(offset).limit(limit)).scalars().all()
    return schemas.RunListOut(items=[_to_out(r) for r in rows], total=total)


@router.get("/runs/{ref}", response_model=schemas.RunOut)
def get_run(ref: str, db: Session = Depends(get_db)):
    return _to_out(_get_run_or_404(ref, db))


@router.delete("/runs/{ref}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(ref: str):
    if not run_service.delete_run(ref):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")


@router.get("/runs/{ref}/events")
def run_events(ref: str, db: Session = Depends(get_db)):
    """Server-sent events with pipeline stage updates."""
    run = _get_run_or_404(ref, db)

    def stream():
        yield f"event: status\ndata: {json.dumps({'status': run.status.value, 'note': 'connected'})}\n\n"
        if run.status in (RunStatus.completed, RunStatus.failed):
            return
        for st, note in runner.events(run.id):
            if st == "heartbeat":
                yield ": keep-alive\n\n"
            else:
                yield f"event: status\ndata: {json.dumps({'status': st, 'note': note})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{ref}/polygons")
def run_polygons(ref: str, db: Session = Depends(get_db)):
    run = _get_run_or_404(ref, db)
    feats = [
        {"type": "Feature", "geometry": p.geometry,
         "properties": {"id": p.id, "area_m2": p.area_m2, "risk_score": p.risk_score, "near_infra": p.near_infra,
                        "distance_to_infra_m": p.distance_to_infra_m, "nearest_infra_type": p.nearest_infra_type,
                        "nearest_infra_name": p.nearest_infra_name}}
        for p in run.polygons
    ]
    return {"type": "FeatureCollection", "features": feats}


@router.get("/runs/{ref}/infrastructure")
def run_infrastructure(ref: str, db: Session = Depends(get_db)):
    run = _get_run_or_404(ref, db)
    data = run_service.read_artifact(run, "infrastructure.geojson")
    return data or {"type": "FeatureCollection", "features": []}


@router.get("/runs/{ref}/preview/{name}.png")
def run_preview(ref: str, name: str, db: Session = Depends(get_db)):
    if name not in ("t1", "t2", "change"):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    run = _get_run_or_404(ref, db)
    p = settings.PREVIEW_DIR / run.run_key / f"{name}.png"
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "preview not generated")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/runs/{ref}/download/{name}")
def run_download(ref: str, name: str, db: Session = Depends(get_db)):
    allowed = {"t1.tif", "t2.tif", "change_prob.tif", "change_mask.tif",
               "change_polygons.geojson", "infrastructure.geojson", "report.md", "imagery_meta.json"}
    if name not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    run = _get_run_or_404(ref, db)
    p = Path(run.output_dir or "") / name
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    return FileResponse(p, filename=f"{run.run_key}_{name}")


# --- chat ----------------------------------------------------------------------

@router.post("/chat", response_model=schemas.ChatAnswer)
def chat(payload: schemas.ChatAsk):
    session = chat_service.get_or_create_session(payload.session_id, channel="web")
    aoi = payload.aoi.model_dump() if payload.aoi else None
    return chat_service.ask(session.id, payload.message, aoi=aoi,
                            provider=payload.provider, model=payload.model)


@router.get("/chat/{session_id}", response_model=list[schemas.ChatMessageOut])
def chat_history(session_id: str, db: Session = Depends(get_db)):
    return db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
    ).scalars().all()
