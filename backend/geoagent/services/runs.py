from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select

from geoagent.config import settings
from geoagent.db.models import MonitoringRun, RunStatus
from geoagent.db.session import db_session
from geoagent.pipeline.monitor import execute_run, make_run_key


def create_run(params: dict[str, Any], requested_by: str = "api") -> MonitoringRun:
    with db_session() as db:
        run = MonitoringRun(run_key=make_run_key(), requested_by=requested_by, status=RunStatus.queued, **params)
        db.add(run)
        db.flush()
        db.refresh(run)
        return run


def execute_now(run_id: str) -> MonitoringRun:
    return execute_run(run_id)


def find_run(ref: str) -> MonitoringRun | None:
    with db_session() as db:
        return db.execute(
            select(MonitoringRun).where(or_(MonitoringRun.id == ref, MonitoringRun.run_key == ref))
        ).scalar_one_or_none()


def list_runs(limit: int = 20, offset: int = 0, status: RunStatus | None = None) -> list[MonitoringRun]:
    with db_session() as db:
        q = select(MonitoringRun).order_by(MonitoringRun.created_at.desc()).offset(offset).limit(limit)
        if status:
            q = q.where(MonitoringRun.status == status)
        return list(db.execute(q).scalars())


def delete_run(ref: str) -> bool:
    import shutil

    with db_session() as db:
        run = db.execute(
            select(MonitoringRun).where(or_(MonitoringRun.id == ref, MonitoringRun.run_key == ref))
        ).scalar_one_or_none()
        if run is None:
            return False
        if run.output_dir:
            shutil.rmtree(run.output_dir, ignore_errors=True)
        shutil.rmtree(settings.PREVIEW_DIR / run.run_key, ignore_errors=True)
        db.delete(run)
        return True


def read_artifact(run: MonitoringRun, name: str) -> dict | None:
    if not run.output_dir:
        return None
    p = Path(run.output_dir) / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def summarise(run: MonitoringRun, brief: bool = False) -> dict[str, Any]:
    out = {
        "id": run.id,
        "run_key": run.run_key,
        "status": run.status.value,
        "label": run.label,
        "bbox": list(run.bbox),
        "risk_level": run.risk_level.value if run.risk_level else None,
        "changed_percent": run.changed_percent,
        "n_change_polygons": run.n_change_polygons,
        "n_near_infra": run.n_near_infra,
        "created_at": run.created_at,
        "is_mock": run.is_mock,
    }
    if brief:
        return out
    out.update({
        "t1_window": run.t1_window, "t2_window": run.t2_window,
        "t1_scene_count": run.t1_scene_count, "t2_scene_count": run.t2_scene_count,
        "t1_latest_date": run.t1_latest_date, "t2_latest_date": run.t2_latest_date,
        "max_risk_score": run.max_risk_score,
        "infra_feature_count": run.infra_feature_count,
        "threshold": run.threshold, "buffer_m": run.buffer_m,
        "narrative": run.narrative, "error": run.error,
        "timings": run.timings, "duration_s": run.duration_s,
        "top_polygons": [
            {"area_m2": p.area_m2, "risk_score": p.risk_score, "near_infra": p.near_infra,
             "distance_to_infra_m": p.distance_to_infra_m, "nearest_infra_type": p.nearest_infra_type,
             "lat": p.centroid_lat, "lon": p.centroid_lon}
            for p in sorted(run.polygons, key=lambda p: p.risk_score, reverse=True)[:5]
        ],
    })
    return out
