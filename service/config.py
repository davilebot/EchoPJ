from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_dsn: str
    app_username: str = ""
    app_password: str = ""
    auth_database_path: str = "/data/auth.sqlite"
    saas_database_path: str = "/data/saas.sqlite"
    auth_session_days: int = 30
    auth_password_reset_minutes: int = 30
    app_public_url: str = "https://plataforma-receita-matcher.ztnbow.easypanel.host"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_ssl: bool = True
    website_cache_path: str = "/data/website-cache.sqlite"
    website_timeout_seconds: float = 3.0
    website_workers: int = 10
    database_workers: int = 8
    database_statement_timeout_ms: int = 1800
    max_batch_size: int = 200
    max_job_size: int = 10000
    job_database_path: str = "/data/jobs.sqlite"
    saas_trial_credits: int = 100
    saas_internal_organization_id: int = 1
    saas_internal_organization_name: str = "EchoHub"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
