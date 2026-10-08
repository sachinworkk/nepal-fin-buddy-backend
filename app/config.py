from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://finbuddy:finbuddy@localhost:5433/finbuddy"
    jwt_secret: str = "dev-secret"
    jwt_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    extraction_model: str = "qwen/qwen3-vl-8b-instruct"
    chat_model: str = "google/gemma-4-26b-a4b-it"

    # Must match document_chunks.embedding dim (Vector(1024)).
    # Qwen/Qwen3-Embedding-0.6B has no OpenRouter endpoints; bge-m3 is 1024-dim.
    embed_model: str = "baai/bge-m3"
    embed_dim: int = 1024

    # Local cross-encoder reranker (in-process, CUDA when available). rerank_enabled=0
    # disables it; rag.retrieve falls back to plain candidate ordering.
    rerank_enabled: bool = True
    rerank_max_candidates: int = 24

    # prize.ird.gov.np ("test" uses the local mock; "live" is refused unless APP_ENV=production)
    prize_mode: str = "test"
    prize_mock_url: str = "http://localhost:8002/api/v1"
    prize_live_url: str = "https://prize.ird.gov.np/api/v1"
    # Where users complete a hand-off submission (IRD's widget rejects third-party origins).
    prize_portal_url: str = "https://prize.ird.gov.np"
    # Cloudflare Turnstile widget rendered by the frontend in test mode only. Defaults are
    # Cloudflare's published test keys, which pass on any domain (e.g. localhost).
    prize_turnstile_sitekey: str = "1x00000000000000000000AA"
    prize_turnstile_secret_key: str = "1x0000000000000000000000000000000AA"
    prize_min_form_age_seconds: float = 5.0
    prize_max_form_age_seconds: float = 86400.0
    # Minimum seconds between two submissions by the same user; IRD throttles bursts
    # (they surface as misleading "form expired" errors).
    prize_user_submit_interval_seconds: float = 8.0

    # MLflow tracing (metadata only, never prompt text). Off unless mlflow_enabled.
    mlflow_enabled: bool = False
    mlflow_tracking_uri: str = ""
    mlflow_experiment: str = "nepal-fin-buddy"
    # Also record chat query/answer text on the chat.turn span. Contains financial data.
    mlflow_log_content: bool = False

    storage_dir: str = "./storage"
    eval_judge_model: str = "openai/gpt-oss-120b"

    @property
    def prize_base_url(self) -> str:
        if self.prize_mode == "live":
            if self.app_env != "production":
                raise RuntimeError("PRIZE_MODE=live is refused unless APP_ENV=production")
            return self.prize_live_url
        return self.prize_mock_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
