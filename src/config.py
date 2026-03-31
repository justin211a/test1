"""Central configuration loaded from environment variables (.env file)."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings


class MetaAdsConfig(BaseSettings):
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_ad_account_ids: list[str] = []

    @field_validator("meta_ad_account_ids", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class GoogleAdsConfig(BaseSettings):
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_customer_ids: list[str] = []
    google_ads_login_customer_id: str = ""

    @field_validator("google_ads_customer_ids", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class NaverSAConfig(BaseSettings):
    naver_sa_api_key: str = ""
    naver_sa_secret_key: str = ""
    naver_sa_customer_ids: list[str] = []

    @field_validator("naver_sa_customer_ids", mode="before")
    @classmethod
    def parse_json_list(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class BigQueryConfig(BaseSettings):
    bq_project_id: str = ""
    bq_dataset_id: str = "marketing_data"
    bq_credentials_path: str = "config/bigquery/service_account.json"


class AlertConfig(BaseSettings):
    jandi_webhook_url: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""


class AppConfig(BaseSettings):
    timezone: str = "Asia/Seoul"
    log_level: str = "INFO"
    backfill_days: int = 7
    exchange_rate_api_key: str = ""
    ingest_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


class Settings:
    """Aggregated settings singleton."""

    def __init__(self):
        self.app = AppConfig()
        self.meta = MetaAdsConfig()
        self.google_ads = GoogleAdsConfig()
        self.naver_sa = NaverSAConfig()
        self.bigquery = BigQueryConfig()
        self.alert = AlertConfig()


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
