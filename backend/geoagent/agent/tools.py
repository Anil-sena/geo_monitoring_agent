"""LangChain tools exposed to the agent. All of them go through the same
service layer the REST API uses, so runs started from chat show up on the
dashboard and vice versa."""
from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from geoagent.config import settings
from geoagent.services import runs as run_service


class MonitorInput(BaseModel):
    minx: float = Field(..., description="AOI minimum longitude (west)")
    miny: float = Field(..., description="AOI minimum latitude (south)")
    maxx: float = Field(..., description="AOI maximum longitude (east)")
    maxy: float = Field(..., description="AOI maximum latitude (north)")
    days_back_t1: int = Field(60, description="End of the older/baseline window, days before today")
    days_back_t2: int = Field(15, description="End of the recent window, days before today")
    window_days: int = Field(20, description="Length of each compositing window in days")
    threshold: float = Field(settings.DEFAULT_CHANGE_THRESHOLD, description="Change probability threshold 0-1")
    buffer_m: float = Field(settings.DEFAULT_BUFFER_METERS, description="Infrastructure proximity buffer in metres")
    label: Optional[str] = Field(None, description="Optional human label for the run")


@tool("monitor_aoi", args_schema=MonitorInput)
def monitor_aoi(minx: float, miny: float, maxx: float, maxy: float,
                days_back_t1: int = 60, days_back_t2: int = 15, window_days: int = 20,
                threshold: float = settings.DEFAULT_CHANGE_THRESHOLD,
                buffer_m: float = settings.DEFAULT_BUFFER_METERS,
                label: Optional[str] = None) -> str:
    """Run the complete satellite monitoring workflow once for a bounding box and
    return the evidence (dates, scene counts, changed %, polygons, risk level)."""
    if not (minx < maxx and miny < maxy):
        return json.dumps({"status": "error", "message": "minx must be < maxx and miny < maxy"})
    if days_back_t1 <= days_back_t2:
        return json.dumps({"status": "error", "message": "days_back_t1 must be larger than days_back_t2"})

    run = run_service.create_run(
        dict(minx=minx, miny=miny, maxx=maxx, maxy=maxy, days_back_t1=days_back_t1,
             days_back_t2=days_back_t2, window_days=window_days, threshold=threshold,
             buffer_m=buffer_m, label=label),
        requested_by="agent",
    )
    run = run_service.execute_now(run.id)
    return json.dumps(run_service.summarise(run), default=str)


@tool("list_recent_runs")
def list_recent_runs(limit: int = 5) -> str:
    """List the most recent monitoring runs stored in the database."""
    rows = run_service.list_runs(limit=limit)
    return json.dumps([run_service.summarise(r, brief=True) for r in rows], default=str)


@tool("get_run")
def get_run(run_id: str) -> str:
    """Fetch the full result of one monitoring run by its id or run_key."""
    run = run_service.find_run(run_id)
    if run is None:
        return json.dumps({"status": "error", "message": f"run {run_id} not found"})
    return json.dumps(run_service.summarise(run), default=str)


class LulcInput(BaseModel):
    run_id: str = Field(..., description="Run id or run_key whose imagery should be classified")
    which: str = Field("t2", description="'t1' (baseline) or 't2' (recent)")
    model_name: str = Field("eurosat_pt", description="lulc9 | resnet_embedding | eurosat_pt")
    tile_size: int = Field(64, description="Tile size in pixels")


@tool("classify_lulc", args_schema=LulcInput)
def classify_lulc(run_id: str, which: str = "t2", model_name: str = "eurosat_pt", tile_size: int = 64) -> str:
    """Classify land cover per tile for a run's imagery using a bundled model."""
    from geoagent.pipeline.lulc import classify_geotiff

    run = run_service.find_run(run_id)
    if run is None or not run.output_dir:
        return json.dumps({"status": "error", "message": "run not found or has no imagery"})
    path = f"{run.output_dir}/{which}.tif"
    try:
        result = classify_geotiff(path, model_name=model_name, tile_size=tile_size)
        counts: dict[str, int] = {}
        for t in result["tiles"]:
            counts[t.get("predicted_class", t.get("error", "unknown"))] = counts.get(t.get("predicted_class", "unknown"), 0) + 1
        return json.dumps({"status": "success", "model": model_name, "n_tiles": result["n_tiles"], "class_counts": counts})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error", "message": str(exc)})


ALL_TOOLS = [monitor_aoi, list_recent_runs, get_run, classify_lulc]
