"""Application configuration — loads from environment variables."""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database — MUST point to the same DB as the main app
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/insurance_rag"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5433/insurance_rag"

    # Security — MUST match the main app's SECRET_KEY
    secret_key: str = "ins-rag-9f2k4j7m3x8b1v5n6p0w2q4r7t9y1u3"

    # CORS
    cors_origins: str = "*"

    # Server
    host: str = "0.0.0.0"
    port: int = 8001
    debug: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",")]

    class Config:
        env_file = ".env.example"
        extra = "ignore"  # CRITICAL — prevents crash on unknown env vars


@lru_cache()
def get_settings() -> Settings:
    return Settings()
