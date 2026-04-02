"""BigQuery loader with delete-then-insert idempotency."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

from src.config import get_settings
from src.schemas import AdPerformance, PipelineRun

logger = logging.getLogger(__name__)


class BigQueryLoader:
    """Handles all BigQuery read/write operations."""

    AD_PERFORMANCE_TABLE = "ad_performance"
    PIPELINE_RUNS_TABLE = "pipeline_runs"

    def __init__(self):
        settings = get_settings()
        bq_creds_path = settings.bigquery.bq_credentials_path

        # Cloud Run: use default credentials. Local: use service account file.
        if bq_creds_path and os.path.exists(bq_creds_path):
            credentials = service_account.Credentials.from_service_account_file(bq_creds_path)
            self.client = bigquery.Client(
                project=settings.bigquery.bq_project_id,
                credentials=credentials,
            )
        else:
            # Cloud Run automatically provides credentials via metadata server
            self.client = bigquery.Client(project=settings.bigquery.bq_project_id)

        self.dataset_id = settings.bigquery.bq_dataset_id
        self.project_id = settings.bigquery.bq_project_id

    def _table_ref(self, table_name: str) -> str:
        return f"{self.project_id}.{self.dataset_id}.{table_name}"

    def delete_existing_data(
        self, table_name: str, channel: str, date_start: date, date_end: date
    ) -> int:
        """Delete existing rows for given channel and date range. Returns rows affected."""
        query = f"""
        DELETE FROM `{self._table_ref(table_name)}`
        WHERE channel = @channel
          AND date BETWEEN @date_start AND @date_end
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("channel", "STRING", channel),
                bigquery.ScalarQueryParameter("date_start", "DATE", date_start),
                bigquery.ScalarQueryParameter("date_end", "DATE", date_end),
            ]
        )
        result = self.client.query(query, job_config=job_config).result()
        rows_deleted = result.num_dml_affected_rows or 0
        logger.info(f"Deleted {rows_deleted} rows from {table_name} for {channel} ({date_start} ~ {date_end})")
        return rows_deleted

    def load_ad_performance(
        self,
        records: list[AdPerformance],
        channel: str,
        date_start: date,
        date_end: date,
    ) -> int:
        """Delete existing data for date range, then insert new records. Returns rows loaded."""
        if not records:
            logger.warning(f"No records to load for {channel} ({date_start} ~ {date_end})")
            return 0

        # Delete existing data (idempotency)
        self.delete_existing_data(self.AD_PERFORMANCE_TABLE, channel, date_start, date_end)

        # Convert to JSON-serializable rows
        rows = []
        for r in records:
            row = r.model_dump()
            for key, val in row.items():
                if hasattr(val, "value"):
                    row[key] = val.value
                elif isinstance(val, datetime):
                    row[key] = val.isoformat()
                elif isinstance(val, date):
                    row[key] = val.isoformat()
                elif isinstance(val, dict):
                    row[key] = json.dumps(val, ensure_ascii=False, default=str)
            rows.append(row)

        # Load to BigQuery using JSON insert (avoids parquet type issues)
        table_ref = self._table_ref(self.AD_PERFORMANCE_TABLE)
        errors = self.client.insert_rows_json(table_ref, rows)
        if errors:
            logger.error(f"BigQuery insert errors: {errors}")
            raise RuntimeError(f"BigQuery insert failed: {errors}")

        logger.info(f"Loaded {len(records)} rows to {table_ref} for {channel}")
        return len(records)

    def log_pipeline_run(self, run: PipelineRun) -> None:
        """Insert a pipeline run record for monitoring."""
        row = run.model_dump()
        for key, val in row.items():
            if hasattr(val, "value"):
                row[key] = val.value
            elif isinstance(val, (date, datetime)):
                row[key] = val.isoformat()

        table_ref = self._table_ref(self.PIPELINE_RUNS_TABLE)
        errors = self.client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"Failed to log pipeline run: {errors}")

    def get_previous_day_count(self, channel: str, report_date: date) -> int | None:
        """Get row count from the previous day for anomaly detection."""
        query = f"""
        SELECT COUNT(*) as cnt
        FROM `{self._table_ref(self.AD_PERFORMANCE_TABLE)}`
        WHERE channel = @channel AND date = @prev_date
        """
        prev_date = date.fromordinal(report_date.toordinal() - 1)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("channel", "STRING", channel),
                bigquery.ScalarQueryParameter("prev_date", "DATE", prev_date),
            ]
        )
        try:
            result = self.client.query(query, job_config=job_config).result()
            for row in result:
                return row.cnt
        except Exception:
            return None
