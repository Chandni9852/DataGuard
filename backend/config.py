"""Environment-based configuration. No credentials are hardcoded."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(..., description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pw@host/db")
    log_level: str = "INFO"
    max_upload_mb: int = 25
    upload_dir: str = "uploads"
    app_name: str = "DataGuard"
    api_prefix: str = "/api/v1"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
