"""Validated environment-based settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    opspilot_env: str = "development"
    opspilot_data_dir: Path = Path("./data")
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://opspilot:opspilot_dev@localhost:5432/opspilot"
    )
    chroma_mode: str = "embedded"
    chroma_host: str = "localhost"
    chroma_port: int = Field(default=8001, ge=1, le=65535)
    chroma_ssl: bool = False
    chroma_persist_directory: Path = Path("./data/chroma")
    chroma_collection_prefix: str = "opspilot"
    embedding_provider: str = "local_hash"
    embedding_model: str = "hashing-tfidf-v1"
    embedding_dimensions: int = Field(default=1024, ge=128, le=8192)
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_embedding_model: str | None = None
    session_ttl_hours: int = Field(default=72, ge=1, le=720)
    log_level: str = "INFO"

    @property
    def safe_database_url(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
