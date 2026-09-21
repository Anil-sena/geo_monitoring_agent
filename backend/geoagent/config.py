"""Runtime configuration.

Everything is read from the environment (or a .env file next to the backend
folder). Paths default to locations inside the backend directory so a fresh
checkout works without any setup beyond `earthengine authenticate`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- service -----------------------------------------------------------
    APP_NAME: str = "GeoAgent Monitor"
    ENV: str = "development"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:3000"
    WORKERS: int = 2  # background pipeline threads

    # --- storage -----------------------------------------------------------
    DATA_DIR: Path = BACKEND_DIR / "data"
    OUTPUT_DIR: Path = BACKEND_DIR / "data" / "outputs"
    PREVIEW_DIR: Path = BACKEND_DIR / "data" / "previews"
    OSM_CACHE_DIR: Path = BACKEND_DIR / "data" / "osm_cache"
    MODEL_DIR: Path = BACKEND_DIR / "models_store"
    # Static export of the Next.js dashboard, served by this same process.
    WEBUI_DIR: Path = BACKEND_DIR / "webui"
    # sqlite by default; point at postgres for production, e.g.
    # postgresql+psycopg://geoagent:secret@127.0.0.1:5432/geoagent
    DATABASE_URL: str = f"sqlite:///{BACKEND_DIR / 'data' / 'geoagent.db'}"

    # --- earth engine ------------------------------------------------------
    EE_PROJECT: Optional[str] = None
    EE_SERVICE_ACCOUNT: Optional[str] = None       # optional, for headless servers
    EE_PRIVATE_KEY_FILE: Optional[Path] = None
    S2_CLOUD_PCT: int = 60
    S2_CLEAR_THRESHOLD: float = 0.60
    ALLOW_MOCK_IMAGERY_FALLBACK: bool = False
    ALLOW_SYNTHETIC_INFRASTRUCTURE: bool = False
    OSM_CACHE_TTL_SECONDS: int = 3600

    # --- analysis defaults -------------------------------------------------
    DEFAULT_CHANGE_THRESHOLD: float = 0.35
    DEFAULT_BUFFER_METERS: float = 200.0
    MIN_CHANGE_AREA_M2: float = 400.0  # 4 pixels at 10 m

    # --- llm ----------------------------------------------------------------
    LLM_PROVIDER: str = "groq"  # openai | groq | openrouter | google
    LLM_MODEL: str = "openai/gpt-oss-120b"
    LLM_TEMPERATURE: float = 0.1
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None

    # --- pre-trained checkpoints -------------------------------------------
    CHANGE_MODEL_CHECKPOINT: Optional[Path] = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        key = {
            "openai": self.OPENAI_API_KEY,
            "groq": self.GROQ_API_KEY,
            "openrouter": self.OPENROUTER_API_KEY,
            "google": self.GOOGLE_API_KEY,
        }.get(self.LLM_PROVIDER.lower())
        return bool(key)

    @property
    def lulc_keras_model(self) -> Path:
        return self.MODEL_DIR / "keras" / "cnn_lulc_model_final_9.keras"

    @property
    def lulc_scaler(self) -> Path:
        return self.MODEL_DIR / "keras" / "cnn_scaler_9.joblib"

    @property
    def eurosat_checkpoint(self) -> Path:
        return self.MODEL_DIR / "eurosat.pt"

    @property
    def resnet_dir(self) -> Path:
        return self.MODEL_DIR / "resnet"


settings = Settings()

for _d in (settings.DATA_DIR, settings.OUTPUT_DIR, settings.PREVIEW_DIR, settings.OSM_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
