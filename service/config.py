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
    auth_signup_verification_hours: int = 24
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
    saas_low_credit_threshold: int = 20
    saas_self_signup_enabled: bool = False
    legal_operator_name: str = ""
    legal_operator_document: str = ""
    legal_operator_address: str = ""
    legal_contact_email: str = ""
    legal_privacy_email: str = ""
    legal_terms_version: str = ""
    legal_privacy_version: str = ""
    legal_effective_date: str = ""
    legal_retention_policy: str = ""
    saas_internal_organization_id: int = 1
    saas_internal_organization_name: str = "EchoHub"
    saas_heavy_requests_per_minute: int = 30
    saas_max_active_jobs_per_organization: int = 5
    saas_max_request_bytes: int = 16_777_216
    saas_billing_enabled: bool = False
    saas_billing_provider: str = "asaas"
    saas_billing_catalog_json: str = ""
    asaas_api_url: str = "https://api-sandbox.asaas.com"
    asaas_api_key: str = ""
    asaas_webhook_token: str = ""
    asaas_timeout_seconds: float = 12.0
    saas_backup_status_path: str = "/data/backup-status.json"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
