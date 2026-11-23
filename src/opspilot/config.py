"""Validated environment-based settings."""

import os
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_chroma_path() -> Path:
    if os.getenv("VERCEL") == "1":
        return Path(tempfile.gettempdir()) / "opspilot-chroma"
    return Path("./data/chroma")


def _default_environment() -> str:
    return "production" if os.getenv("VERCEL") == "1" else "development"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    opspilot_env: str = Field(default_factory=_default_environment)
    opspilot_data_dir: Path = Path("./data")
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://opspilot:opspilot_dev@localhost:5432/opspilot"
    )
    chroma_mode: str = "embedded"
    chroma_host: str = "localhost"
    chroma_port: int = Field(default=8001, ge=1, le=65535)
    chroma_ssl: bool = False
    chroma_persist_directory: Path = Field(default_factory=_default_chroma_path)
    chroma_collection_prefix: str = "opspilot"
    embedding_provider: str = "local_hash"
    embedding_model: str = "hashing-tfidf-v1"
    embedding_dimensions: int = Field(default=1024, ge=128, le=8192)
    groq_api_key: SecretStr | None = None
    llm_provider: str = "groq"
    groq_model: str = "openai/gpt-oss-20b"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str | None = None
    llm_timeout_seconds: int = Field(default=15, ge=3, le=60)
    max_investigations_per_session: int = Field(default=5, ge=1, le=20)
    max_active_investigation_seconds: int = Field(default=90, ge=15, le=180)
    max_tool_calls_per_investigation: int = Field(default=10, ge=1, le=10)
    max_investigation_turns: int = Field(default=8, ge=1, le=8)
    max_tokens_per_investigation: int = Field(default=20000, ge=1000, le=20000)
    max_estimated_cost_usd: float = Field(default=0.10, gt=0, le=0.10)
    approval_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    session_ttl_hours: int = Field(default=72, ge=1, le=720)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def vercel_chroma_uses_temporary_storage(self) -> "Settings":
        if os.getenv("VERCEL") == "1":
            temp_root = Path(tempfile.gettempdir()).resolve()
            chroma_path = self.chroma_persist_directory.resolve()
            if chroma_path == temp_root or temp_root not in chroma_path.parents:
                raise ValueError("Vercel Chroma storage must be inside the temporary directory")
        return self

    @property
    def safe_database_url(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
