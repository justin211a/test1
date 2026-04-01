"""Pipeline orchestration: runs ETL for each channel with error isolation."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from uuid import uuid4

from src.channels.base import BaseChannel
from src.channels.meta import MetaChannel
from src.config import get_settings
from src.loader import BigQueryLoader
from src.schemas import DataSource, PipelineRun, PipelineStatus
from src.utils.notify import (
    notify_data_anomaly,
    notify_pipeline_failure,
    notify_pipeline_success,
    notify_token_expiry_warning,
)

logger = logging.getLogger(__name__)

# Lazy imports to avoid import errors when credentials aren't configured
CHANNEL_REGISTRY: dict[str, type[BaseChannel]] = {}


def _get_channel_class(channel_name: str) -> type[BaseChannel]:
    """Lazy-load channel classes to avoid import-time credential requirements."""
    if not CHANNEL_REGISTRY:
        from src.channels.meta import MetaChannel
        from src.channels.google_ads import GoogleAdsChannel
        from src.channels.naver_sa import NaverSAChannel
        from src.channels.gfa import GFAChannel

        CHANNEL_REGISTRY.update({
            "meta": MetaChannel,
            "google_ads": GoogleAdsChannel,
            "naver_sa": NaverSAChannel,
            "gfa": GFAChannel,
        })

    cls = CHANNEL_REGISTRY.get(channel_name)
    if cls is None:
        raise ValueError(f"Unknown channel: {channel_name}. Available: {list(CHANNEL_REGISTRY.keys())}")
    return cls


def get_account_ids(channel_name: str) -> list[str]:
    """Get configured account IDs for a channel."""
    settings = get_settings()
    mapping = {
        "meta": settings.meta.meta_ad_account_ids,
        "google_ads": settings.google_ads.google_ads_customer_ids,
        "naver_sa": settings.naver_sa.naver_sa_customer_ids,
        "gfa": [],  # GFA uses extension/CSV, no account IDs needed
    }
    return mapping.get(channel_name, [])


def run_channel(
    channel_name: str,
    date_start: date,
    date_end: date,
    data_source: DataSource = DataSource.API,
) -> PipelineRun:
    """Run ETL for a single channel across all its accounts.

    Args:
        channel_name: Channel identifier (meta, google_ads, naver_sa, gfa).
        date_start: Start date (inclusive).
        date_end: End date (inclusive).
        data_source: Data source type.

    Returns:
        PipelineRun record with execution results.
    """
    run = PipelineRun(
        run_id=str(uuid4()),
        channel=channel_name,
        report_date_start=date_start,
        report_date_end=date_end,
        status=PipelineStatus.STARTED,
        data_source=data_source,
    )

    loader = BigQueryLoader()
    total_rows = 0

    try:
        # Naver SA: special handling for multiple account groups (different logins)
        if channel_name == "naver_sa":
            from src.channels.naver_sa import NaverSAChannel
            account_groups = get_settings().naver_sa.get_account_groups()
            if not account_groups:
                raise ValueError("No Naver SA account groups configured")

            for group in account_groups:
                channel = NaverSAChannel(api_key=group.api_key, secret_key=group.secret_key)
                for account_id in group.customer_ids:
                    logger.info(f"Processing naver_sa account: {account_id} ({date_start} ~ {date_end})")
                    records = channel.extract_and_transform(account_id, date_start, date_end)
                    rows = loader.load_ad_performance(records, channel_name, date_start, date_end)
                    total_rows += rows
        else:
            # All other channels: single credential set
            channel_cls = _get_channel_class(channel_name)
            channel = channel_cls()

            # Check token health
            token_days = channel.check_token_health()
            if token_days is not None and token_days <= 7:
                notify_token_expiry_warning(channel_name, token_days)

            # Process each account
            account_ids = get_account_ids(channel_name)
            if not account_ids and channel_name != "gfa":
                raise ValueError(f"No account IDs configured for {channel_name}")

            for account_id in account_ids:
                logger.info(f"Processing {channel_name} account: {account_id} ({date_start} ~ {date_end})")
                records = channel.extract_and_transform(account_id, date_start, date_end)
                rows = loader.load_ad_performance(records, channel_name, date_start, date_end)
                total_rows += rows

                # Anomaly detection: compare with previous day
                prev_count = loader.get_previous_day_count(channel_name, date_start)
                if prev_count is not None and prev_count > 0:
                    change_ratio = abs(rows - prev_count) / prev_count
                    if change_ratio > 0.5:
                        notify_data_anomaly(channel_name, date_start.isoformat(), rows, prev_count)

        # Success
        run.status = PipelineStatus.SUCCESS
        run.rows_loaded = total_rows
        run.completed_at = datetime.utcnow()
        notify_pipeline_success(channel_name, f"{date_start} ~ {date_end}", total_rows)
        logger.info(f"Pipeline SUCCESS: {channel_name} loaded {total_rows} rows")

    except Exception as e:
        run.status = PipelineStatus.FAILED
        run.error_message = str(e)[:1000]
        run.completed_at = datetime.utcnow()
        notify_pipeline_failure(channel_name, f"{date_start} ~ {date_end}", str(e))
        logger.error(f"Pipeline FAILED: {channel_name} - {e}", exc_info=True)

    # Log the run
    try:
        loader.log_pipeline_run(run)
    except Exception as e:
        logger.error(f"Failed to log pipeline run: {e}")

    return run


def run_all_channels(
    date_start: date | None = None,
    date_end: date | None = None,
    channels: list[str] | None = None,
):
    """Run ETL for all (or specified) channels with error isolation.

    Each channel runs independently — a failure in one does not block others.

    Args:
        date_start: Start date. Defaults to D-backfill_days.
        date_end: End date. Defaults to yesterday.
        channels: List of channels to run. Defaults to all configured channels.
    """
    settings = get_settings()

    if date_end is None:
        date_end = date.today() - timedelta(days=1)
    if date_start is None:
        date_start = date_end - timedelta(days=settings.app.backfill_days - 1)

    if channels is None:
        channels = ["meta", "google_ads", "naver_sa"]  # GFA is extension-based, excluded from auto

    logger.info(f"Starting pipeline run: channels={channels}, dates={date_start} ~ {date_end}")

    results = []
    for channel_name in channels:
        run = run_channel(channel_name, date_start, date_end)
        results.append(run)

    # Summary
    success = sum(1 for r in results if r.status == PipelineStatus.SUCCESS)
    failed = sum(1 for r in results if r.status == PipelineStatus.FAILED)
    logger.info(f"Pipeline complete: {success} succeeded, {failed} failed out of {len(results)} channels")

    return results
