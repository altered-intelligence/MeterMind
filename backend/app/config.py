"""Application configuration (pydantic-settings).

Settings read from environment variables. The canonical names are unprefixed
(``DATABASE_URL``, ``PDF_STORAGE_PATH``, ``APP_SECRET``, ``DEBUG_PARSER``,
``ENABLE_LOCAL_LLM``, ``ENABLE_WEATHER``, ``ENABLE_COSERV_PORTAL_CONNECTOR``). Older
``HUI_``-prefixed / ``OLLAMA_`` names are still accepted for back-compat. Nothing here
holds secrets — the hashing secret lives in its own file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ directory (this file is backend/app/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[1]
VAR_DIR = BACKEND_DIR / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUI_", env_file=".env", extra="ignore")

    app_name: str = "Home Utility Intelligence"
    app_version: str = "0.1.0"

    # Where uploaded PDFs are stored for the MVP (local filesystem).
    storage_dir: Path = Field(
        default=VAR_DIR / "bills",
        validation_alias=AliasChoices("PDF_STORAGE_PATH", "HUI_STORAGE_DIR"),
    )
    # SQLite for the MVP; swap for a postgresql:// URL to migrate later.
    database_url: str = Field(
        default=f"sqlite:///{VAR_DIR / 'home_utility.db'}",
        validation_alias=AliasChoices("DATABASE_URL", "HUI_DATABASE_URL"),
    )
    # Secret-material file (auto-generated on first run if absent). Never commit this.
    secret_key_file: Path = VAR_DIR / ".secret_key"
    # Application secret used to HMAC-hash sensitive values (account/meter numbers).
    # If unset, the contents of secret_key_file are used so hashes are stable per machine.
    app_secret: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("APP_SECRET", "HUI_APP_SECRET")
    )

    # When true, parser logging is more verbose (still never logs raw text or PII).
    debug_parser: bool = Field(default=False, validation_alias="DEBUG_PARSER")

    # Frontend dev origin(s) allowed by CORS.
    cors_origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ---- Feature flags (all default OFF) ----
    enable_local_llm: bool = Field(
        default=False, validation_alias=AliasChoices("ENABLE_LOCAL_LLM", "OLLAMA_ENABLED")
    )
    enable_weather: bool = Field(default=False, validation_alias="ENABLE_WEATHER")
    enable_coserv_portal_connector: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ENABLE_COSERV_PORTAL_CONNECTOR", "HUI_ENABLE_COSERV_PORTAL_CONNECTOR"
        ),
    )
    enable_browser_automation: bool = Field(
        default=False, validation_alias="ENABLE_BROWSER_AUTOMATION"
    )
    # Whether encrypted SmartHub session cookies may be persisted (only if the user opts in).
    # Off by default; no automation exists yet, so this is purely forward-looking.
    allow_session_cookie_storage: bool = Field(
        default=False, validation_alias="ALLOW_SESSION_COOKIE_STORAGE"
    )

    # Local privacy mode. When true, no external/outbound calls are made (live weather,
    # geocoding) and sensitive export is blocked — only local features (incl. local Ollama)
    # are allowed. OFF by default to preserve existing behavior; recommended ON for privacy.
    privacy_mode_local_only: bool = Field(
        default=False, validation_alias="PRIVACY_MODE_LOCAL_ONLY"
    )

    # Local-LLM (Ollama) connection details. Gated by enable_local_llm.
    ollama_model: str = Field(
        default="llama3.1", validation_alias=AliasChoices("OLLAMA_MODEL", "LLM_MODEL")
    )
    ollama_host: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("OLLAMA_HOST", "OLLAMA_BASE_URL"),
    )
    ollama_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices("OLLAMA_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"),
    )

    # Weather provider: "open-meteo" (live, key-less HTTP) or "mock" (deterministic/offline).
    weather_provider: str = Field(default="open-meteo", validation_alias="WEATHER_PROVIDER")
    weather_timeout_seconds: float = Field(
        default=30.0, validation_alias="WEATHER_TIMEOUT_SECONDS"
    )
    # ZIP/postal geocoder: "zippopotam" (live, key-less HTTP) or "mock" (deterministic/offline).
    geocoder: str = Field(default="zippopotam", validation_alias="GEOCODER")

    # Allow exports to include sensitive fields (account last4, redacted address) when the
    # caller also passes include_sensitive=true. OFF by default — exports are scrubbed.
    allow_sensitive_export: bool = Field(
        default=False, validation_alias="ALLOW_SENSITIVE_EXPORT"
    )

    @property
    def ollama_enabled(self) -> bool:
        """Back-compat alias used across the codebase for the local-LLM flag."""
        return self.enable_local_llm


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.secret_key_file.parent.mkdir(parents=True, exist_ok=True)
    return settings
