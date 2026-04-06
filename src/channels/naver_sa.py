"""Naver Search Ad channel: extraction and transformation with async report workflow."""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, datetime

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.channels.base import BaseChannel
from src.config import get_settings
from src.schemas import AdPerformance, Channel, DataSource, MatchType, RecordType
from src.utils.auth import get_naver_sa_headers

logger = logging.getLogger(__name__)

NAVER_SA_BASE_URL = "https://api.searchad.naver.com"

# Stat report field mapping (TSV columns)
STAT_FIELDS = [
    "impCnt", "clkCnt", "salesAmt", "ccnt", "convAmt",
]


class NaverSAChannel(BaseChannel):
    """Naver Search Ad extractor and transformer.

    Supports multiple account groups (different logins with different API keys).
    """

    channel = Channel.NAVER_SA

    def __init__(self, api_key: str = "", secret_key: str = ""):
        super().__init__()
        self._api_key = api_key
        self._secret_key = secret_key

    @property
    def default_rate_limit(self) -> float:
        return 5.0

    # ──────────────────────────────────────────
    # Master data fetchers
    # ──────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _api_get(self, uri: str, customer_id: str, params: dict | None = None) -> list | dict:
        """Make GET request to Naver SA API."""
        self.rate_limiter.wait()
        headers = get_naver_sa_headers("GET", uri, customer_id, self._api_key, self._secret_key)
        resp = requests.get(f"{NAVER_SA_BASE_URL}{uri}", headers=headers, params=params, timeout=30)
        if not resp.ok:
            logger.error(f"Naver SA API error: {resp.status_code} {resp.text}")
        resp.raise_for_status()
        return resp.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _api_post(self, uri: str, customer_id: str, body: dict) -> dict:
        """Make POST request to Naver SA API."""
        self.rate_limiter.wait()
        headers = get_naver_sa_headers("POST", uri, customer_id, self._api_key, self._secret_key)
        resp = requests.post(f"{NAVER_SA_BASE_URL}{uri}", headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _fetch_campaigns(self, customer_id: str) -> dict[str, str]:
        """Fetch campaign ID → name mapping."""
        data = self._api_get("/ncc/campaigns", customer_id)
        return {item["nccCampaignId"]: item.get("name", "") for item in data}

    def _fetch_adgroups(self, customer_id: str) -> dict[str, dict]:
        """Fetch ad group data: ID → {name, campaign_id}."""
        data = self._api_get("/ncc/adgroups", customer_id)
        return {
            item["nccAdgroupId"]: {
                "name": item.get("name", ""),
                "campaign_id": item.get("nccCampaignId", ""),
            }
            for item in data
        }

    def _fetch_keywords(self, customer_id: str, adgroup_id: str) -> list[dict]:
        """Fetch keywords for a specific ad group."""
        try:
            data = self._api_get(
                "/ncc/keywords",
                customer_id,
                params={"nccAdgroupId": adgroup_id},
            )
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # ──────────────────────────────────────────
    # Async stat report workflow
    # ──────────────────────────────────────────

    def _create_stat_report(
        self, customer_id: str, date_start: date, date_end: date
    ) -> str:
        """Step 1: Request stat report creation. Returns report ID."""
        body = {
            "reportTp": "AD",
            "statDt": date_start.strftime("%Y%m%d"),
            "endDt": date_end.strftime("%Y%m%d"),
            "statTpCd": "AD_DETAIL",  # Detailed breakdown
        }
        result = self._api_post("/stat-reports", customer_id, body)
        report_id = result.get("reportJobId")
        logger.info(f"Naver SA: created stat report {report_id}")
        return report_id

    def _poll_report_status(self, customer_id: str, report_id: str, timeout: int = 300) -> str:
        """Step 2: Poll until report is ready. Returns download URL."""
        start = time.time()
        while time.time() - start < timeout:
            result = self._api_get(f"/stat-reports/{report_id}", customer_id)
            status = result.get("status")
            logger.info(f"Naver SA: poll response: {result}")

            if status == "BUILT":
                download_url = result.get("reportFileUrl") or result.get("downloadUrl")
                logger.info(f"Naver SA: report ready, URL: {download_url}")
                if not download_url:
                    raise RuntimeError(f"Naver SA report BUILT but no download URL in response: {result}")
                return download_url
            elif status in ("REGIST", "RUNNING"):
                time.sleep(10)
            elif status == "NONE":
                logger.warning(f"Naver SA: report status NONE (no data for period)")
                return None
            else:
                raise RuntimeError(f"Naver SA report failed with status: {status}")

        raise TimeoutError(f"Naver SA report {report_id} timed out after {timeout}s")

    def _download_report(self, download_url: str, customer_id: str) -> list[dict]:
        """Step 3: Download and parse TSV report."""
        headers = get_naver_sa_headers("GET", "/report-download", customer_id, self._api_key, self._secret_key)
        resp = requests.get(download_url, headers=headers, timeout=60)
        resp.raise_for_status()

        # Naver SA reports are TSV encoded in EUC-KR or UTF-8
        content = resp.content
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("euc-kr")

        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        rows = list(reader)
        logger.info(f"Naver SA: downloaded {len(rows)} report rows")
        return rows

    # ──────────────────────────────────────────
    # Main extract + transform
    # ──────────────────────────────────────────

    def extract_and_transform(
        self,
        account_id: str,
        date_start: date,
        date_end: date,
    ) -> list[AdPerformance]:
        """Extract from Naver SA API (async report) and transform."""
        # Fetch master data
        campaigns = self._fetch_campaigns(account_id)
        adgroups = self._fetch_adgroups(account_id)

        # Create and download stat report
        report_id = self._create_stat_report(account_id, date_start, date_end)
        download_url = self._poll_report_status(account_id, report_id)
        if download_url is None:
            logger.info(f"Naver SA: no report data for {account_id} ({date_start} ~ {date_end})")
            return []
        raw_rows = self._download_report(download_url, account_id)

        records = []
        for row in raw_rows:
            report_date_str = row.get("statDt", row.get("date", ""))
            if report_date_str:
                try:
                    report_date = date.fromisoformat(report_date_str.replace(".", "-").replace("/", "-"))
                except ValueError:
                    report_date = date_start
            else:
                report_date = date_start

            campaign_id = row.get("nccCampaignId", row.get("campaignId", ""))
            adgroup_id = row.get("nccAdgroupId", row.get("adgroupId", ""))
            keyword = row.get("keyword", "")

            campaign_name = campaigns.get(campaign_id, "")
            adgroup_info = adgroups.get(adgroup_id, {})
            adgroup_name = adgroup_info.get("name", "")

            impressions = self._safe_int(row.get("impCnt", row.get("impressions", 0)))
            clicks = self._safe_int(row.get("clkCnt", row.get("clicks", 0)))
            cost = self._safe_float(row.get("salesAmt", row.get("cost", 0)))
            conversions = self._safe_float(row.get("ccnt", row.get("conversions", 0)))
            conversion_value = self._safe_float(row.get("convAmt", row.get("conversionValue", 0)))

            record_type = RecordType.KEYWORD if keyword else RecordType.CAMPAIGN
            record_id_key = f"{adgroup_id}_{keyword}" if keyword else adgroup_id

            record = AdPerformance(
                id=AdPerformance.make_id(self.channel.value, account_id, record_id_key, report_date),
                date=report_date,
                channel=self.channel,
                account_id=account_id,
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                ad_group_id=adgroup_id,
                ad_group_name=adgroup_name,
                record_type=record_type,
                keyword=keyword if keyword else None,
                match_type=None,
                impressions=impressions,
                clicks=clicks,
                cost=cost,  # Naver SA is already KRW
                cost_original=cost,
                cost_currency="KRW",
                exchange_rate=1.0,
                conversions=conversions,
                conversion_value=conversion_value,
                data_source=DataSource.API,
                ingested_at=datetime.utcnow(),
                raw_payload=row,
            )
            records.append(record)

        logger.info(f"Naver SA: total {len(records)} records for {account_id}")
        return records

    def test_connection(self, account_id: str) -> bool:
        """Test Naver SA API connectivity."""
        try:
            self._fetch_campaigns(account_id)
            return True
        except Exception as e:
            logger.error(f"Naver SA connection test failed for {account_id}: {e}")
            return False
