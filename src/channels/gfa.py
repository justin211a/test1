"""GFA (Naver Performance Display) channel: Chrome extension data + CSV fallback."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.channels.base import BaseChannel
from src.config import get_settings
from src.schemas import AdPerformance, Channel, DataSource, RecordType

logger = logging.getLogger(__name__)

# Default CSV column mappings (Korean and English variants)
CSV_COLUMN_MAP = {
    # Korean
    "캠페인 ID": "campaign_id",
    "캠페인명": "campaign_name",
    "캠페인": "campaign_name",
    "광고그룹 ID": "ad_group_id",
    "광고그룹명": "ad_group_name",
    "광고그룹": "ad_group_name",
    "노출수": "impressions",
    "클릭수": "clicks",
    "비용": "cost",
    "총비용": "cost",
    "광고비": "cost",
    "전환수": "conversions",
    "전환매출": "conversion_value",
    "날짜": "date",
    "일자": "date",
    # English
    "Campaign ID": "campaign_id",
    "Campaign Name": "campaign_name",
    "Ad Group ID": "ad_group_id",
    "Ad Group Name": "ad_group_name",
    "Impressions": "impressions",
    "Clicks": "clicks",
    "Cost": "cost",
    "Conversions": "conversions",
    "Conversion Value": "conversion_value",
    "Date": "date",
}


class GFAChannel(BaseChannel):
    """GFA channel: processes data from Chrome extension or CSV files."""

    channel = Channel.GFA

    @property
    def default_rate_limit(self) -> float:
        return 10.0  # Not hitting an API, so rate limit is generous

    def transform_extension_data(
        self, rows: list[dict], report_date: date
    ) -> list[AdPerformance]:
        """Transform data received from Chrome extension into unified schema.

        Args:
            rows: List of dicts from extension (parsed from GFA dashboard DOM).
            report_date: The date these rows represent.

        Returns:
            List of AdPerformance records.
        """
        records = []
        for row in rows:
            campaign_id = str(row.get("campaign_id", row.get("campaignId", "")))
            campaign_name = row.get("campaign_name", row.get("campaignName", ""))
            ad_group_id = str(row.get("ad_group_id", row.get("adGroupId", campaign_id)))
            ad_group_name = row.get("ad_group_name", row.get("adGroupName", campaign_name))

            impressions = self._safe_int(row.get("impressions", row.get("노출수", 0)))
            clicks = self._safe_int(row.get("clicks", row.get("클릭수", 0)))
            cost = self._safe_float(row.get("cost", row.get("비용", 0)))
            conversions = self._safe_float(row.get("conversions", row.get("전환수", 0)))
            conversion_value = self._safe_float(
                row.get("conversion_value", row.get("전환매출", 0))
            )

            record = AdPerformance(
                id=AdPerformance.make_id(self.channel.value, "gfa", ad_group_id, report_date),
                date=report_date,
                channel=self.channel,
                account_id="gfa",
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                ad_group_id=ad_group_id,
                ad_group_name=ad_group_name,
                record_type=RecordType.CAMPAIGN,
                impressions=impressions,
                clicks=clicks,
                cost=cost,  # GFA is KRW
                cost_original=cost,
                cost_currency="KRW",
                exchange_rate=1.0,
                conversions=conversions,
                conversion_value=conversion_value,
                data_source=DataSource.EXTENSION,
                ingested_at=datetime.utcnow(),
                raw_payload=row,
            )
            records.append(record)

        logger.info(f"GFA extension: transformed {len(records)} records")
        return records

    def extract_and_transform(
        self,
        account_id: str,
        date_start: date,
        date_end: date,
    ) -> list[AdPerformance]:
        """Extract from CSV file (fallback mode).

        For GFA, this method reads a CSV file from the uploads directory.
        The file is expected to be named like: gfa_YYYY-MM-DD.csv
        """
        records = []
        uploads_dir = Path("uploads")

        # Look for CSV files matching the date range
        for single_date in pd.date_range(date_start, date_end):
            d = single_date.date()
            possible_files = [
                uploads_dir / f"gfa_{d.isoformat()}.csv",
                uploads_dir / f"gfa_{d.isoformat()}.xlsx",
                uploads_dir / f"GFA_{d.isoformat()}.csv",
            ]

            for filepath in possible_files:
                if filepath.exists():
                    file_records = self._parse_csv_file(filepath, d)
                    records.extend(file_records)
                    break

        if not records:
            logger.warning(f"GFA: no CSV files found for {date_start} ~ {date_end}")

        return records

    def _parse_csv_file(self, filepath: Path, report_date: date) -> list[AdPerformance]:
        """Parse a CSV/Excel file and transform to unified schema."""
        logger.info(f"GFA: reading file {filepath}")

        if filepath.suffix == ".xlsx":
            df = pd.read_excel(filepath)
        else:
            # Try UTF-8 first, fallback to EUC-KR
            try:
                df = pd.read_csv(filepath, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(filepath, encoding="euc-kr")

        # Rename columns using mapping
        rename_map = {}
        for col in df.columns:
            if col in CSV_COLUMN_MAP:
                rename_map[col] = CSV_COLUMN_MAP[col]
        df = df.rename(columns=rename_map)

        # Validate required columns exist
        required = {"impressions", "clicks", "cost"}
        missing = required - set(df.columns)
        if missing:
            logger.error(f"GFA CSV missing required columns: {missing}. Available: {list(df.columns)}")
            raise ValueError(f"GFA CSV missing required columns: {missing}")

        records = []
        for _, row in df.iterrows():
            campaign_id = str(row.get("campaign_id", ""))
            ad_group_id = str(row.get("ad_group_id", campaign_id))

            record = AdPerformance(
                id=AdPerformance.make_id(self.channel.value, "gfa", ad_group_id, report_date),
                date=report_date,
                channel=self.channel,
                account_id="gfa",
                campaign_id=campaign_id,
                campaign_name=str(row.get("campaign_name", "")),
                ad_group_id=ad_group_id,
                ad_group_name=str(row.get("ad_group_name", "")),
                record_type=RecordType.CAMPAIGN,
                impressions=self._safe_int(row.get("impressions")),
                clicks=self._safe_int(row.get("clicks")),
                cost=self._safe_float(row.get("cost")),
                cost_original=self._safe_float(row.get("cost")),
                cost_currency="KRW",
                exchange_rate=1.0,
                conversions=self._safe_float(row.get("conversions")),
                conversion_value=self._safe_float(row.get("conversion_value")),
                data_source=DataSource.CSV,
                ingested_at=datetime.utcnow(),
                raw_payload=row.to_dict(),
            )
            records.append(record)

        logger.info(f"GFA CSV: parsed {len(records)} records from {filepath}")
        return records

    def test_connection(self, account_id: str) -> bool:
        """GFA doesn't have an API connection to test."""
        return True
