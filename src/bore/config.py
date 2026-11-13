"""Configuración de la aplicación (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str = ""
    bore_allow_paid_smoke: bool = False
    bore_model_research: str = ""
    bore_model_analysis: str = ""
    bore_model_review: str = ""
    openai_agents_disable_tracing: bool = True
    bore_tracing_enabled: bool = False
    bore_trace_include_sensitive_data: bool = False
    bore_search_context_size: str = "medium"
    bore_agent_max_turns: int = 10
    bore_heartbeat_stale_seconds: int = 120
    bore_requeue_after_seconds: int = 300
    bore_reaper_interval_seconds: int = 60

    bore_max_usd_per_day: float = 25.0
    bore_min_seconds_between_runs: int = 60

    verifier_user_agent: str = "BORE-SourceVerifier/1.0 (+research bot)"
    verifier_timeout_seconds: float = 15.0
    verifier_max_bytes: int = 2 * 1024 * 1024
    verifier_max_redirects: int = 5
    verifier_respect_robots: bool = True
    verifier_per_host_min_interval: float = 1.0

    bore_api_url: str = "http://127.0.0.1:8000"
    # Firma del token CSRF de la UI; vacío -> clave aleatoria por proceso.
    bore_secret_key: str = ""
    bore_allowed_hosts: str = "127.0.0.1,localhost,api,testserver,test"
    bore_allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"

    database_url: str = "postgresql+psycopg://bore:bore@postgres:5432/bore"
    redis_url: str = "redis://redis:6379/0"

    postgres_user: str = "bore"
    postgres_password: str = "bore"  # noqa: S105
    postgres_db: str = "bore"

    @property
    def allowed_hosts(self) -> list[str]:
        return [h.strip() for h in self.bore_allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.bore_allowed_origins.split(",") if o.strip()]

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
