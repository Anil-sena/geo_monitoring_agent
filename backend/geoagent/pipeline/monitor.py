"""End-to-end monitoring run: imagery → change → risk → previews → DB.

`execute_run` is the single entry point used by both the API job worker and
the agent tool, so the LLM can never trigger two half-pipelines in parallel.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from geoagent.config import settings
from geoagent.db.models import ChangePolygon, MonitoringRun, RiskLevel, RunStatus
from geoagent.db.session import db_session
from geoagent.pipeline import earth_engine
from geoagent.pipeline.change_detection import load_model, run_inference
from geoagent.pipeline.geo import (
    bounds_from_meta,
    compute_risk,
    read_geotiff,
    save_geojson,
    vectorize_change_mask,
    write_geotiff,
)
from geoagent.pipeline.infrastructure import fetch_osm_infrastructure
from geoagent.pipeline.previews import build_previews

log = logging.getLogger(__name__)

StatusCallback = Callable[[RunStatus, str], None]


def make_run_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]


def date_windows(days_back_t1: int, days_back_t2: int, window_days: int) -> tuple[tuple[str, str], tuple[str, str]]:
    today = datetime.now(timezone.utc).date()
    t2_end = today - timedelta(days=days_back_t2)
    t1_end = today - timedelta(days=days_back_t1)
    t1 = ((t1_end - timedelta(days=window_days)).isoformat(), t1_end.isoformat())
    t2 = ((t2_end - timedelta(days=window_days)).isoformat(), t2_end.isoformat())
    return t1, t2


def _mock_imagery(bbox, out_dir: Path, t1, t2) -> dict[str, Any]:
    """Only reachable when ALLOW_MOCK_IMAGERY_FALLBACK=true. Marked as mock everywhere."""
    h = w = 256
    rng = np.random.default_rng(42)
    base = rng.random((4, h, w), dtype=np.float32) * 0.3 + 0.05
    after = base.copy()
    after[0, 90:150, 100:170] += 0.35   # bright new surface
    after[3, 90:150, 100:170] -= 0.20   # vegetation loss
    meta = {"driver": "GTiff", "height": h, "width": w, "count": 4, "dtype": "float32",
            "crs": "EPSG:4326", "transform": from_bounds(*bbox, w, h)}
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, arr in (("t1", base), ("t2", after)):
        with rasterio.open(out_dir / f"{name}.tif", "w", **meta) as dst:
            dst.write(arr)
    return {
        "sensor": "S2", "bands": earth_engine.S2_BANDS, "scale_m": 10,
        "t1_window": f"{t1[0]}/{t1[1]}", "t2_window": f"{t2[0]}/{t2[1]}",
        "scene_count": {"t1": 0, "t2": 0}, "scene_dates": {"t1": [], "t2": []},
        "latest_available": {"t1": None, "t2": None},
        "paths": {"t1": str(out_dir / "t1.tif"), "t2": str(out_dir / "t2.tif")},
        "is_mock": True,
    }


def build_narrative(run: MonitoringRun, top: list[ChangePolygon]) -> str:
    src = "SYNTHETIC demo imagery" if run.is_mock else f"Sentinel-2 ({run.t2_scene_count} recent scenes, latest {run.t2_latest_date})"
    lines = [
        f"**Risk level: {run.risk_level.value if run.risk_level else 'NONE'}**",
        "",
        f"Compared {src} against the {run.t1_window} baseline "
        f"({run.t1_scene_count} scenes, latest {run.t1_latest_date}).",
        f"{run.changed_percent:.2f}% of valid pixels exceeded the change threshold ({run.threshold}), "
        f"forming {run.n_change_polygons} change areas; {run.n_near_infra} lie within {run.buffer_m:.0f} m "
        f"of mapped infrastructure ({run.infra_feature_count} OSM features in the AOI).",
    ]
    if top:
        lines.append("")
        lines.append("Highest-priority areas:")
        for p in top:
            near = f"{p.distance_to_infra_m:.0f} m from {p.nearest_infra_type or 'infrastructure'}" \
                if p.distance_to_infra_m is not None else "no infrastructure nearby"
            lines.append(f"- {p.area_m2 / 10_000:.2f} ha at {p.centroid_lat:.4f}, {p.centroid_lon:.4f} — risk {p.risk_score:.2f}, {near}")
    lines.append("")
    lines.append("_Sentinel-2 provides the latest available observation (≈5-day revisit), not live imagery. "
                 "Infrastructure comes from OpenStreetMap and may be incomplete._")
    return "\n".join(lines)


def execute_run(run_id: str, on_status: StatusCallback | None = None) -> MonitoringRun:
    """Run the pipeline for an existing queued MonitoringRun row."""
    timings: dict[str, float] = {}

    def set_status(status: RunStatus, note: str = "") -> None:
        with db_session() as db:
            row = db.get(MonitoringRun, run_id)
            row.status = status
            if status not in (RunStatus.completed, RunStatus.failed) and row.started_at is None:
                row.started_at = datetime.now(timezone.utc)
        if on_status:
            on_status(status, note)

    with db_session() as db:
        run = db.get(MonitoringRun, run_id)
        if run is None:
            raise KeyError(f"run {run_id} not found")
        bbox = run.bbox
        params = dict(days_back_t1=run.days_back_t1, days_back_t2=run.days_back_t2,
                      window_days=run.window_days, threshold=run.threshold, buffer_m=run.buffer_m,
                      sensor=run.sensor, method=run.method, run_key=run.run_key)

    out_dir = settings.OUTPUT_DIR / params["run_key"]
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. imagery ---------------------------------------------------------
        set_status(RunStatus.fetching_imagery, "Querying Earth Engine")
        t0 = time.perf_counter()
        t1, t2 = date_windows(params["days_back_t1"], params["days_back_t2"], params["window_days"])
        try:
            imagery = earth_engine.fetch_image_pair(bbox, t1, t2, out_dir, sensor=params["sensor"])
        except Exception as exc:  # noqa: BLE001
            if not settings.ALLOW_MOCK_IMAGERY_FALLBACK:
                raise
            log.warning("Earth Engine failed (%s); using mock imagery because fallback is enabled", exc)
            imagery = _mock_imagery(bbox, out_dir, t1, t2)
        (out_dir / "imagery_meta.json").write_text(json.dumps(imagery, indent=2, default=str))
        timings["imagery_s"] = round(time.perf_counter() - t0, 2)

        # 2. change detection ------------------------------------------------
        set_status(RunStatus.detecting_change, "Running change detection")
        t0 = time.perf_counter()
        img1, meta1 = read_geotiff(Path(imagery["paths"]["t1"]))
        img2, _ = read_geotiff(Path(imagery["paths"]["t2"]))
        model = load_model(params["method"], params["threshold"], settings.CHANGE_MODEL_CHECKPOINT)
        result = run_inference(model, img1[:4], img2[:4])

        prob_path = write_geotiff(result["change_prob"], meta1, out_dir / "change_prob.tif", "float32")
        mask_path = write_geotiff(result["change_mask"], meta1, out_dir / "change_mask.tif", "uint8")
        valid = result.get("valid_mask")
        denom = int(valid.sum()) if valid is not None else result["change_mask"].size
        changed_pct = float(result["change_mask"].sum()) / max(denom, 1) * 100
        timings["change_s"] = round(time.perf_counter() - t0, 2)

        # 3. risk ------------------------------------------------------------
        set_status(RunStatus.assessing_risk, "Vectorising and scoring proximity")
        t0 = time.perf_counter()
        mask, mmeta = read_geotiff(mask_path)
        change_gdf = vectorize_change_mask(mask[0], mmeta["transform"], crs=str(mmeta.get("crs") or "EPSG:4326"))
        infra = fetch_osm_infrastructure(bounds_from_meta(mmeta))
        risk = compute_risk(change_gdf, infra, buffer_m=params["buffer_m"])
        if "gdf" in risk and not risk["gdf"].empty:
            save_geojson(risk["gdf"], out_dir / "change_polygons.geojson")
        if not infra.empty:
            save_geojson(infra, out_dir / "infrastructure.geojson")
        else:
            (out_dir / "infrastructure.geojson").write_text('{"type":"FeatureCollection","features":[]}')
        timings["risk_s"] = round(time.perf_counter() - t0, 2)

        # 4. previews --------------------------------------------------------
        t0 = time.perf_counter()
        build_previews(out_dir, settings.PREVIEW_DIR / params["run_key"])
        timings["preview_s"] = round(time.perf_counter() - t0, 2)

        # 5. persist ---------------------------------------------------------
        with db_session() as db:
            run = db.get(MonitoringRun, run_id)
            run.t1_window, run.t2_window = imagery["t1_window"], imagery["t2_window"]
            run.t1_scene_count = imagery["scene_count"]["t1"]
            run.t2_scene_count = imagery["scene_count"]["t2"]
            run.t1_latest_date = imagery["latest_available"]["t1"]
            run.t2_latest_date = imagery["latest_available"]["t2"]
            run.is_mock = bool(imagery.get("is_mock"))
            run.changed_percent = round(changed_pct, 3)
            run.n_change_polygons = risk["n_change_polygons"]
            run.n_near_infra = risk["n_near_infra"]
            run.max_risk_score = risk["max_risk_score"]
            run.risk_level = RiskLevel(risk["risk_level"])
            run.infra_feature_count = int(len(infra))
            run.output_dir = str(out_dir)
            run.timings = timings

            run.polygons.clear()
            for feat in risk["polygons"]:
                pr = feat["properties"]
                run.polygons.append(ChangePolygon(
                    geometry=feat["geometry"],
                    area_m2=pr.get("area_m2"),
                    centroid_lon=pr.get("centroid_lon"),
                    centroid_lat=pr.get("centroid_lat"),
                    risk_score=pr.get("risk_score", 0.0),
                    near_infra=bool(pr.get("near_infra", False)),
                    distance_to_infra_m=pr.get("distance_to_infra_m"),
                    nearest_infra_type=pr.get("nearest_infra_type"),
                    nearest_infra_name=pr.get("nearest_infra_name"),
                ))
            top = sorted(run.polygons, key=lambda p: (p.risk_score, p.area_m2 or 0), reverse=True)[:3]
            run.narrative = build_narrative(run, top)
            run.status = RunStatus.completed
            run.finished_at = datetime.now(timezone.utc)
            (out_dir / "report.md").write_text(run.narrative, encoding="utf-8")
            db.flush()
            db.refresh(run)
            finished = run

        if on_status:
            on_status(RunStatus.completed, "done")
        log.info("run %s completed in %s", params["run_key"], timings)
        return finished

    except Exception as exc:  # noqa: BLE001
        log.exception("run %s failed", run_id)
        with db_session() as db:
            run = db.get(MonitoringRun, run_id)
            run.status = RunStatus.failed
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(timezone.utc)
            run.timings = timings
            db.flush()
            db.refresh(run)
            failed = run
        if on_status:
            on_status(RunStatus.failed, str(exc))
        return failed
