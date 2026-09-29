from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://finbuddy:finbuddy@localhost:5433/finbuddy"
    jwt_secret: str = "dev-secret"
    jwt_expire_minutes: int = 60 * 24
    field_encryption_key: str = ""
    cors_origins: str = "http://localhost:5173"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    extraction_model: str = "google/gemini-2.5-flash"
    chat_model: str = "google/gemini-2.5-flash"

    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embed_dim: int = 1024

    cbms_mode: str = "test"  # test | live
    cbms_mock_url: str = "http://localhost:8001"
    cbms_live_url: str = "https://cbapi.ird.gov.np"
    storage_dir: str = "./storage"

    @property
    def cbms_base_url(self) -> str:
        if self.cbms_mode == "live":
            if self.app_env != "production":
                raise RuntimeError("CBMS_MODE=live is refused unless APP_ENV=production")
            return self.cbms_live_url
        return self.cbms_mock_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
