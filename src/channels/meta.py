"""Meta (Facebook) Ads channel: extraction and transformation."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adsinsights import AdsInsights
from facebook_business.api import FacebookAdsApi
from tenacity import retry, stop_after_attempt, wait_exponential

from src.channels.base import BaseChannel
from src.config import get_settings
from src.schemas import AdPerformance, Channel, DataSource, RecordType
from src.utils.auth import check_meta_token_expiry
from src.utils.exchange_rate import get_exchange_rate

logger = logging.getLogger(__name__)

# Meta Insights API fields
INSIGHTS_FIELDS = [
    AdsInsights.Field.campaign_id,
    AdsInsights.Field.campaign_name,
    AdsInsights.Field.adset_id,
    AdsInsights.Field.adset_name,
    AdsInsights.Field.impressions,
    AdsInsights.Field.clicks,
    AdsInsights.Field.spend,
    AdsInsights.Field.actions,
    AdsInsights.Field.action_values,
    AdsInsights.Field.date_start,
]


class MetaChannel(BaseChannel):
    """Meta (Facebook) Ads extractor and transformer."""

    channel = Channel.META

    def __init__(self):
        super().__init__()
        settings = get_settings()
        FacebookAdsApi.init(
            app_id=settings.meta.meta_app_id,
            app_secret=settings.meta.meta_app_secret,
            access_token=settings.meta.meta_access_token,
        )

    @property
    def default_rate_limit(self) -> float:
        return 3.0  # Meta rate limit is complex; be conservative

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
    def _fetch_insights(
        self, account_id: str, date_start: date, date_end: date
    ) -> list[dict]:
        """Fetch ad set level insights from Meta Marketing API."""
        self.rate_limiter.wait()
        account = AdAccount(account_id)

        params = {
            "time_range": {
                "since": date_start.isoformat(),
                "until": date_end.isoformat(),
            },
            "level": "adset",  # Ad set = ad group equivalent
            "time_increment": 1,  # Daily breakdown
        }

        insights = account.get_insights(fields=INSIGHTS_FIELDS, params=params)

        results = []
        for insight in insights:
            results.append(dict(insight))

        logger.info(f"Meta: fetched {len(results)} insight rows for {account_id}")
        return results

    def extract_and_transform(
        self,
        account_id: str,
        date_start: date,
        date_end: date,
    ) -> list[AdPerformance]:
        """Extract from Meta API and transform to unified schema."""
        raw_data = self._fetch_insights(account_id, date_start, date_end)

        records = []
        for row in raw_data:
            report_date = date.fromisoformat(row.get("date_start", date_start.isoformat()))

            # Extract conversions from actions
            conversions = 0.0
            conversion_value = 0.0
            actions = row.get("actions") or []
            for action in actions:
                if action.get("action_type") in ("offsite_conversion", "lead", "purchase"):
                    conversions += self._safe_float(action.get("value"))

            action_values = row.get("action_values") or []
            for av in action_values:
                if av.get("action_type") in ("offsite_conversion", "purchase"):
                    conversion_value += self._safe_float(av.get("value"))

            spend = self._safe_float(row.get("spend"))
            # Meta reports in account currency (usually USD for international accounts)
            cost_currency = "USD"  # Default; will be overridden if account is KRW
            exchange_rate = get_exchange_rate(cost_currency, report_date)
            cost_krw = spend * exchange_rate

            campaign_id = row.get("campaign_id", "")
            adset_id = row.get("adset_id", "")

            record = AdPerformance(
                id=AdPerformance.make_id(self.channel.value, account_id, adset_id, report_date),
                date=report_date,
                channel=self.channel,
                account_id=account_id,
                campaign_id=campaign_id,
                campaign_name=row.get("campaign_name", ""),
                ad_group_id=adset_id,
                ad_group_name=row.get("adset_name", ""),
                record_type=RecordType.CAMPAIGN,
                impressions=self._safe_int(row.get("impressions")),
                clicks=self._safe_int(row.get("clicks")),
                cost=cost_krw,
                cost_original=spend,
                cost_currency=cost_currency,
                exchange_rate=exchange_rate,
                conversions=conversions,
                conversion_value=conversion_value * exchange_rate,
                data_source=DataSource.API,
                ingested_at=datetime.utcnow(),
                raw_payload=row,
            )
            records.append(record)

        logger.info(f"Meta: transformed {len(records)} records for {account_id}")
        return records

    def test_connection(self, account_id: str) -> bool:
        """Test Meta API connectivity."""
        try:
            account = AdAccount(account_id)
            account.api_get(fields=["name", "account_status"])
            return True
        except Exception as e:
            logger.error(f"Meta connection test failed for {account_id}: {e}")
            return False

    def check_token_health(self) -> int | None:
        """Check Meta access token expiry days."""
        settings = get_settings()
        return check_meta_token_expiry(settings.meta.meta_access_token)
