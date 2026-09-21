"""Database tables.

Kept deliberately relational rather than PostGIS-specific so the same models
run on SQLite (dev) and PostgreSQL (prod). Geometry is stored as GeoJSON text;
the full GeoTIFF/GeoJSON artefacts live on disk under OUTPUT_DIR and the
table rows point at them.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from geoagent.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class RunStatus(str, enum.Enum):
    queued = "queued"
    fetching_imagery = "fetching_imagery"
    detecting_change = "detecting_change"
    assessing_risk = "assessing_risk"
    completed = "completed"
    failed = "failed"


class RiskLevel(str, enum.Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AoiPreset(Base):
    __tablename__ = "aoi_presets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    minx: Mapped[float] = mapped_column(Float, nullable=False)
    miny: Mapped[float] = mapped_column(Float, nullable=False)
    maxx: Mapped[float] = mapped_column(Float, nullable=False)
    maxy: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # human-friendly folder name used on disk, e.g. 20260918_091500
    run_key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued, nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))
    requested_by: Mapped[str | None] = mapped_column(String(120))  # "ui", "chat", "api"

    # AOI
    minx: Mapped[float] = mapped_column(Float, nullable=False)
    miny: Mapped[float] = mapped_column(Float, nullable=False)
    maxx: Mapped[float] = mapped_column(Float, nullable=False)
    maxy: Mapped[float] = mapped_column(Float, nullable=False)

    # request parameters
    sensor: Mapped[str] = mapped_column(String(8), default="S2")
    days_back_t1: Mapped[int] = mapped_column(Integer, default=60)
    days_back_t2: Mapped[int] = mapped_column(Integer, default=15)
    window_days: Mapped[int] = mapped_column(Integer, default=20)
    threshold: Mapped[float] = mapped_column(Float, default=0.35)
    buffer_m: Mapped[float] = mapped_column(Float, default=200.0)
    method: Mapped[str] = mapped_column(String(32), default="spectral")

    # imagery evidence
    t1_window: Mapped[str | None] = mapped_column(String(32))
    t2_window: Mapped[str | None] = mapped_column(String(32))
    t1_scene_count: Mapped[int | None] = mapped_column(Integer)
    t2_scene_count: Mapped[int | None] = mapped_column(Integer)
    t1_latest_date: Mapped[str | None] = mapped_column(String(10))
    t2_latest_date: Mapped[str | None] = mapped_column(String(10))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    # results
    changed_percent: Mapped[float | None] = mapped_column(Float)
    n_change_polygons: Mapped[int | None] = mapped_column(Integer)
    n_near_infra: Mapped[int | None] = mapped_column(Integer)
    max_risk_score: Mapped[float | None] = mapped_column(Float)
    risk_level: Mapped[RiskLevel | None] = mapped_column(Enum(RiskLevel))
    infra_feature_count: Mapped[int | None] = mapped_column(Integer)
    narrative: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    output_dir: Mapped[str | None] = mapped_column(String(400))
    # duration per stage in seconds — useful for latency tracking
    timings: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    polygons: Mapped[list["ChangePolygon"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.minx, self.miny, self.maxx, self.maxy)

    @property
    def duration_s(self) -> float | None:
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 2)
        return None


class ChangePolygon(Base):
    __tablename__ = "change_polygons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="CASCADE"), nullable=False)
    geometry: Mapped[dict] = mapped_column(JSON, nullable=False)  # GeoJSON geometry
    area_m2: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    centroid_lat: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    near_infra: Mapped[bool] = mapped_column(Boolean, default=False)
    distance_to_infra_m: Mapped[float | None] = mapped_column(Float)
    nearest_infra_type: Mapped[str | None] = mapped_column(String(40))
    nearest_infra_name: Mapped[str | None] = mapped_column(String(200))

    run: Mapped[MonitoringRun] = relationship(back_populates="polygons")


Index("ix_change_polygons_run_risk", ChangePolygon.run_id, ChangePolygon.risk_score.desc())


class InfrastructureCache(Base):
    """One row per (bbox, categories) Overpass request. Payload on disk."""

    __tablename__ = "infrastructure_cache"

    cache_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    minx: Mapped[float] = mapped_column(Float, nullable=False)
    miny: Mapped[float] = mapped_column(Float, nullable=False)
    maxx: Mapped[float] = mapped_column(Float, nullable=False)
    maxy: Mapped[float] = mapped_column(Float, nullable=False)
    categories: Mapped[list] = mapped_column(JSON, nullable=False)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    source_endpoint: Mapped[str | None] = mapped_column(String(200))
    geojson_path: Mapped[str] = mapped_column(String(400), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(20), default="web")  # web | chainlit | api
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(12), nullable=False)  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="SET NULL"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
