"""Google Ads channel: extraction and transformation using GAQL."""

from __future__ import annotations

import logging
from datetime import date, datetime

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from tenacity import retry, stop_after_attempt, wait_exponential

from src.channels.base import BaseChannel
from src.config import get_settings
from src.schemas import AdPerformance, Channel, DataSource, MatchType, RecordType
from src.utils.exchange_rate import get_exchange_rate

logger = logging.getLogger(__name__)

# GAQL: ad group level performance
ADGROUP_QUERY = """
    SELECT
        segments.date,
        campaign.id,
        campaign.name,
        campaign.status,
        ad_group.id,
        ad_group.name,
        ad_group.status,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
    FROM ad_group
    WHERE segments.date BETWEEN '{date_start}' AND '{date_end}'
    ORDER BY segments.date
"""

# GAQL: keyword level performance
KEYWORD_QUERY = """
    SELECT
        segments.date,
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        ad_group_criterion.keyword.text,
        ad_group_criterion.keyword.match_type,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value
    FROM keyword_view
    WHERE segments.date BETWEEN '{date_start}' AND '{date_end}'
    ORDER BY segments.date
"""

MATCH_TYPE_MAP = {
    "EXACT": MatchType.EXACT,
    "PHRASE": MatchType.PHRASE,
    "BROAD": MatchType.BROAD,
    2: MatchType.EXACT,
    3: MatchType.PHRASE,
    4: MatchType.BROAD,
}


class GoogleAdsChannel(BaseChannel):
    """Google Ads extractor and transformer."""

    channel = Channel.GOOGLE_ADS

    def __init__(self):
        super().__init__()
        settings = get_settings()
        config = {
            "developer_token": settings.google_ads.google_ads_developer_token,
            "client_id": settings.google_ads.google_ads_client_id,
            "client_secret": settings.google_ads.google_ads_client_secret,
            "refresh_token": settings.google_ads.google_ads_refresh_token,
            "use_proto_plus": True,
        }
        if settings.google_ads.google_ads_login_customer_id:
            config["login_customer_id"] = settings.google_ads.google_ads_login_customer_id

        self.client = GoogleAdsClient.load_from_dict(config)

    @property
    def default_rate_limit(self) -> float:
        return 5.0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
    def _execute_query(self, customer_id: str, query: str) -> list[dict]:
        """Execute a GAQL query using SearchStream."""
        self.rate_limiter.wait()
        ga_service = self.client.get_service("GoogleAdsService")

        rows = []
        try:
            stream = ga_service.search_stream(customer_id=customer_id, query=query)
            for batch in stream:
                for row in batch.results:
                    rows.append(row)
        except GoogleAdsException as e:
            logger.error(f"Google Ads API error: {e.failure.errors[0].message if e.failure.errors else e}")
            raise

        return rows

    def _detect_currency(self, customer_id: str) -> str:
        """Detect account currency."""
        try:
            ga_service = self.client.get_service("GoogleAdsService")
            query = "SELECT customer.currency_code FROM customer LIMIT 1"
            stream = ga_service.search_stream(customer_id=customer_id, query=query)
            for batch in stream:
                for row in batch.results:
                    return row.customer.currency_code
        except Exception:
            pass
        return "KRW"

    def extract_and_transform(
        self,
        account_id: str,
        date_start: date,
        date_end: date,
    ) -> list[AdPerformance]:
        """Extract ad group + keyword data from Google Ads and transform."""
        records = []
        currency = self._detect_currency(account_id)
        exchange_rate = get_exchange_rate(currency, date_start)

        # --- Ad group level ---
        query = ADGROUP_QUERY.format(
            date_start=date_start.isoformat(),
            date_end=date_end.isoformat(),
        )
        adgroup_rows = self._execute_query(account_id, query)
        logger.info(f"Google Ads: fetched {len(adgroup_rows)} ad group rows for {account_id}")

        for row in adgroup_rows:
            report_date = date.fromisoformat(row.segments.date)
            cost_micros = row.metrics.cost_micros
            cost_original = cost_micros / 1_000_000

            record = AdPerformance(
                id=AdPerformance.make_id(
                    self.channel.value, account_id, str(row.ad_group.id), report_date
                ),
                date=report_date,
                channel=self.channel,
                account_id=account_id,
                campaign_id=str(row.campaign.id),
                campaign_name=row.campaign.name,
                ad_group_id=str(row.ad_group.id),
                ad_group_name=row.ad_group.name,
                record_type=RecordType.CAMPAIGN,
                impressions=row.metrics.impressions,
                clicks=row.metrics.clicks,
                cost=cost_original * exchange_rate,
                cost_original=cost_original,
                cost_currency=currency,
                exchange_rate=exchange_rate,
                conversions=row.metrics.conversions,
                conversion_value=row.metrics.conversions_value * exchange_rate,
                data_source=DataSource.API,
                ingested_at=datetime.utcnow(),
                raw_payload={
                    "campaign_id": str(row.campaign.id),
                    "ad_group_id": str(row.ad_group.id),
                    "cost_micros": cost_micros,
                    "currency": currency,
                },
            )
            records.append(record)

        # --- Keyword level ---
        keyword_query = KEYWORD_QUERY.format(
            date_start=date_start.isoformat(),
            date_end=date_end.isoformat(),
        )
        try:
            keyword_rows = self._execute_query(account_id, keyword_query)
            logger.info(f"Google Ads: fetched {len(keyword_rows)} keyword rows for {account_id}")

            for row in keyword_rows:
                report_date = date.fromisoformat(row.segments.date)
                cost_micros = row.metrics.cost_micros
                cost_original = cost_micros / 1_000_000
                match_type_raw = row.ad_group_criterion.keyword.match_type
                match_type = MATCH_TYPE_MAP.get(match_type_raw, MatchType.NONE)

                keyword_text = row.ad_group_criterion.keyword.text
                kw_id = f"{row.ad_group.id}_{keyword_text}"

                record = AdPerformance(
                    id=AdPerformance.make_id(self.channel.value, account_id, kw_id, report_date),
                    date=report_date,
                    channel=self.channel,
                    account_id=account_id,
                    campaign_id=str(row.campaign.id),
                    campaign_name=row.campaign.name,
                    ad_group_id=str(row.ad_group.id),
                    ad_group_name=row.ad_group.name,
                    record_type=RecordType.KEYWORD,
                    keyword=keyword_text,
                    match_type=match_type,
                    impressions=row.metrics.impressions,
                    clicks=row.metrics.clicks,
                    cost=cost_original * exchange_rate,
                    cost_original=cost_original,
                    cost_currency=currency,
                    exchange_rate=exchange_rate,
                    conversions=row.metrics.conversions,
                    conversion_value=row.metrics.conversions_value * exchange_rate,
                    data_source=DataSource.API,
                    ingested_at=datetime.utcnow(),
                    raw_payload={
                        "keyword": keyword_text,
                        "match_type": str(match_type_raw),
                        "cost_micros": cost_micros,
                    },
                )
                records.append(record)
        except Exception as e:
            logger.warning(f"Google Ads keyword extraction failed (non-fatal): {e}")

        logger.info(f"Google Ads: total {len(records)} records for {account_id}")
        return records

    def test_connection(self, account_id: str) -> bool:
        """Test Google Ads API connectivity."""
        try:
            ga_service = self.client.get_service("GoogleAdsService")
            query = "SELECT customer.id FROM customer LIMIT 1"
            stream = ga_service.search_stream(customer_id=account_id, query=query)
            for batch in stream:
                return True
            return True
        except Exception as e:
            logger.error(f"Google Ads connection test failed for {account_id}: {e}")
            return False
