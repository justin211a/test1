"""Unified Pydantic models for marketing data across all channels."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field


class Channel(str, Enum):
    META = "meta"
    GOOGLE_ADS = "google_ads"
    NAVER_SA = "naver_sa"
    GFA = "gfa"


class DataSource(str, Enum):
    API = "api"
    CSV = "csv"
    EXTENSION = "extension"


class RecordType(str, Enum):
    CAMPAIGN = "campaign"
    KEYWORD = "keyword"
    PLACEMENT = "placement"


class MatchType(str, Enum):
    EXACT = "EXACT"
    PHRASE = "PHRASE"
    BROAD = "BROAD"
    NONE = ""


class PipelineStatus(str, Enum):
    STARTED = "started"
    SUCCESS = "success"
    FAILED = "failed"


class AdPerformance(BaseModel):
    """Unified ad performance record at ad-group level."""

    id: str = Field(description="Surrogate key: hash of channel+account+adgroup+date")
    date: date
    channel: Channel
    account_id: str
    campaign_id: str
    campaign_name: str
    ad_group_id: str
    ad_group_name: str
    record_type: RecordType = RecordType.CAMPAIGN
    keyword: Optional[str] = None
    match_type: Optional[MatchType] = None
    impressions: int = 0
    clicks: int = 0
    cost: float = 0.0  # KRW
    cost_original: float = 0.0  # Original currency
    cost_currency: str = "KRW"
    exchange_rate: float = 1.0
    conversions: float = 0.0
    conversion_value: float = 0.0  # KRW
    data_source: DataSource = DataSource.API
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    raw_payload: Optional[dict[str, Any]] = None

    @staticmethod
    def make_id(channel: str, account_id: str, ad_group_id: str, report_date: date) -> str:
        import hashlib

        key = f"{channel}_{account_id}_{ad_group_id}_{report_date.isoformat()}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]


class PipelineRun(BaseModel):
    """Pipeline execution record for monitoring."""

    run_id: str
    channel: str
    report_date_start: date
    report_date_end: date
    status: PipelineStatus = PipelineStatus.STARTED
    rows_loaded: int = 0
    error_message: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    data_source: DataSource = DataSource.API
