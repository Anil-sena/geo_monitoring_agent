-- GeoAgent Monitor — PostgreSQL schema
-- The application creates these tables automatically via SQLAlchemy on first
-- start. This file is the reference DDL for DBAs / migrations and mirrors
-- geoagent/db/models.py exactly.
--
--   psql -U geoagent -d geoagent -f db/schema.sql

BEGIN;

CREATE TYPE run_status AS ENUM (
    'queued', 'fetching_imagery', 'detecting_change',
    'assessing_risk', 'completed', 'failed'
);
CREATE TYPE risk_level AS ENUM ('NONE', 'LOW', 'MEDIUM', 'HIGH');

CREATE TABLE aoi_presets (
    id           VARCHAR(32)  PRIMARY KEY,
    name         VARCHAR(120) NOT NULL UNIQUE,
    description  TEXT,
    minx         DOUBLE PRECISION NOT NULL,
    miny         DOUBLE PRECISION NOT NULL,
    maxx         DOUBLE PRECISION NOT NULL,
    maxy         DOUBLE PRECISION NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT aoi_presets_bbox_chk CHECK (minx < maxx AND miny < maxy)
);

CREATE TABLE monitoring_runs (
    id                  VARCHAR(32)  PRIMARY KEY,
    run_key             VARCHAR(40)  NOT NULL UNIQUE,
    status              run_status   NOT NULL DEFAULT 'queued',
    label               VARCHAR(160),
    requested_by        VARCHAR(120),

    minx                DOUBLE PRECISION NOT NULL,
    miny                DOUBLE PRECISION NOT NULL,
    maxx                DOUBLE PRECISION NOT NULL,
    maxy                DOUBLE PRECISION NOT NULL,

    sensor              VARCHAR(8)   NOT NULL DEFAULT 'S2',
    days_back_t1        INTEGER      NOT NULL DEFAULT 60,
    days_back_t2        INTEGER      NOT NULL DEFAULT 15,
    window_days         INTEGER      NOT NULL DEFAULT 20,
    threshold           DOUBLE PRECISION NOT NULL DEFAULT 0.35,
    buffer_m            DOUBLE PRECISION NOT NULL DEFAULT 200,
    method              VARCHAR(32)  NOT NULL DEFAULT 'spectral',

    t1_window           VARCHAR(32),
    t2_window           VARCHAR(32),
    t1_scene_count      INTEGER,
    t2_scene_count      INTEGER,
    t1_latest_date      VARCHAR(10),
    t2_latest_date      VARCHAR(10),
    is_mock             BOOLEAN      NOT NULL DEFAULT FALSE,

    changed_percent     DOUBLE PRECISION,
    n_change_polygons   INTEGER,
    n_near_infra        INTEGER,
    max_risk_score      DOUBLE PRECISION,
    risk_level          risk_level,
    infra_feature_count INTEGER,
    narrative           TEXT,
    error               TEXT,

    output_dir          VARCHAR(400),
    timings             JSONB,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,

    CONSTRAINT monitoring_runs_bbox_chk CHECK (minx < maxx AND miny < maxy),
    CONSTRAINT monitoring_runs_windows_chk CHECK (days_back_t1 > days_back_t2)
);
CREATE INDEX ix_monitoring_runs_created_at ON monitoring_runs (created_at DESC);
CREATE INDEX ix_monitoring_runs_status     ON monitoring_runs (status);

CREATE TABLE change_polygons (
    id                   BIGSERIAL PRIMARY KEY,
    run_id               VARCHAR(32) NOT NULL REFERENCES monitoring_runs(id) ON DELETE CASCADE,
    geometry             JSONB NOT NULL,          -- GeoJSON geometry, EPSG:4326
    area_m2              DOUBLE PRECISION,
    centroid_lon         DOUBLE PRECISION,
    centroid_lat         DOUBLE PRECISION,
    risk_score           DOUBLE PRECISION NOT NULL DEFAULT 0,
    near_infra           BOOLEAN NOT NULL DEFAULT FALSE,
    distance_to_infra_m  DOUBLE PRECISION,
    nearest_infra_type   VARCHAR(40),
    nearest_infra_name   VARCHAR(200)
);
CREATE INDEX ix_change_polygons_run_risk ON change_polygons (run_id, risk_score DESC);

CREATE TABLE infrastructure_cache (
    cache_key        VARCHAR(40) PRIMARY KEY,
    minx             DOUBLE PRECISION NOT NULL,
    miny             DOUBLE PRECISION NOT NULL,
    maxx             DOUBLE PRECISION NOT NULL,
    maxy             DOUBLE PRECISION NOT NULL,
    categories       JSONB NOT NULL,
    feature_count    INTEGER NOT NULL DEFAULT 0,
    source_endpoint  VARCHAR(200),
    geojson_path     VARCHAR(400) NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_sessions (
    id          VARCHAR(32) PRIMARY KEY,
    channel     VARCHAR(20) NOT NULL DEFAULT 'web',
    title       VARCHAR(200),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  VARCHAR(32) NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(12) NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content     TEXT NOT NULL,
    run_id      VARCHAR(32) REFERENCES monitoring_runs(id) ON DELETE SET NULL,
    latency_ms  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_chat_messages_session ON chat_messages (session_id, created_at);

-- Seed presets used by the dashboard
INSERT INTO aoi_presets (id, name, description, minx, miny, maxx, maxy) VALUES
  (md5('vizag'),      'Visakhapatnam',        'Port city, refinery and steel plant corridor', 83.08405, 17.54740, 83.40865, 17.87425),
  (md5('vizag-test'), 'Visakhapatnam (small)', 'Quick test tile over the harbour',            83.15,    17.65,    83.35,    17.80),
  (md5('noida'),      'Noida / Greater Noida', 'Fast-growing NCR construction belt',          77.50,    28.50,    77.70,    28.70)
ON CONFLICT (name) DO NOTHING;

COMMIT;
