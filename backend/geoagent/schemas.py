from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from geoagent.config import settings


class Bbox(BaseModel):
    minx: float = Field(..., ge=-180, le=180)
    miny: float = Field(..., ge=-90, le=90)
    maxx: float = Field(..., ge=-180, le=180)
    maxy: float = Field(..., ge=-90, le=90)

    @model_validator(mode="after")
    def _ordered(self):
        if self.minx >= self.maxx or self.miny >= self.maxy:
            raise ValueError("min coordinates must be smaller than max coordinates")
        if (self.maxx - self.minx) * (self.maxy - self.miny) > 1.0:
            raise ValueError("AOI larger than ~1°×1° — split it into smaller areas")
        return self


class RunCreate(Bbox):
    days_back_t1: int = Field(60, ge=20, le=365)
    days_back_t2: int = Field(15, ge=1, le=120)
    window_days: int = Field(20, ge=5, le=60)
    threshold: float = Field(settings.DEFAULT_CHANGE_THRESHOLD, ge=0.05, le=0.95)
    buffer_m: float = Field(settings.DEFAULT_BUFFER_METERS, ge=25, le=2000)
    sensor: str = Field("S2", pattern="^(S1|S2)$")
    method: str = Field("spectral", pattern="^(spectral|siamese_lite|fcsiam_diff|smp_unet)$")
    label: Optional[str] = Field(None, max_length=160)

    @model_validator(mode="after")
    def _windows(self):
        if self.days_back_t1 <= self.days_back_t2:
            raise ValueError("days_back_t1 must be larger than days_back_t2")
        return self


class PolygonOut(BaseModel):
    id: int
    area_m2: Optional[float]
    centroid_lat: Optional[float]
    centroid_lon: Optional[float]
    risk_score: float
    near_infra: bool
    distance_to_infra_m: Optional[float]
    nearest_infra_type: Optional[str]
    nearest_infra_name: Optional[str]

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    run_key: str
    status: str
    label: Optional[str]
    requested_by: Optional[str]
    minx: float
    miny: float
    maxx: float
    maxy: float
    sensor: str
    method: str
    days_back_t1: int
    days_back_t2: int
    window_days: int
    threshold: float
    buffer_m: float
    t1_window: Optional[str]
    t2_window: Optional[str]
    t1_scene_count: Optional[int]
    t2_scene_count: Optional[int]
    t1_latest_date: Optional[str]
    t2_latest_date: Optional[str]
    is_mock: bool
    changed_percent: Optional[float]
    n_change_polygons: Optional[int]
    n_near_infra: Optional[int]
    max_risk_score: Optional[float]
    risk_level: Optional[str]
    infra_feature_count: Optional[int]
    narrative: Optional[str]
    error: Optional[str]
    timings: Optional[dict[str, Any]]
    duration_s: Optional[float]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    previews: dict[str, str] = {}
    polygons: list[PolygonOut] = []

    model_config = {"from_attributes": True}


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class PresetOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    minx: float
    miny: float
    maxx: float
    maxy: float

    model_config = {"from_attributes": True}


class ChatAsk(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = None
    aoi: Optional[RunCreate] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    run_id: Optional[str]
    latency_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatAnswer(BaseModel):
    session_id: str
    message_id: int
    answer: str
    run_ids: list[str]
    latency_ms: int


class HealthOut(BaseModel):
    status: str
    env: str
    earth_engine: str
    llm_provider: Optional[str]
    llm_configured: bool
    database: str
    runs_total: int
