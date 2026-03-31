"""Cloud Run entry point: HTTP server for ingest API + CLI for pipeline execution."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, timedelta

import click
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request

# Setup structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.basicConfig(format="%(message)s", level=logging.INFO)

# ──────────────────────────────────────────────
# FastAPI app (Cloud Run Service for ingest API)
# ──────────────────────────────────────────────

app = FastAPI(title="Marketing Data Pipeline", version="0.1.0")


@app.post("/api/ingest/gfa")
async def ingest_gfa(request: Request, x_api_key: str = Header(None)):
    """Receive GFA data from Chrome extension and load to BigQuery."""
    from src.config import get_settings

    settings = get_settings()
    if settings.app.ingest_api_key and x_api_key != settings.app.ingest_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()
    rows = body.get("rows", [])
    report_date = body.get("date")

    if not rows:
        raise HTTPException(status_code=400, detail="No data rows provided")

    try:
        from src.channels.gfa import GFAChannel
        from src.loader import BigQueryLoader
        from src.schemas import DataSource

        channel = GFAChannel()
        target_date = date.fromisoformat(report_date) if report_date else date.today() - timedelta(days=1)

        records = channel.transform_extension_data(rows, target_date)
        loader = BigQueryLoader()
        loaded = loader.load_ad_performance(records, "gfa", target_date, target_date)

        return {"status": "success", "rows_loaded": loaded}
    except Exception as e:
        logging.error(f"GFA ingest error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ingest/csv")
async def ingest_csv(request: Request, x_api_key: str = Header(None)):
    """Receive CSV data and load to BigQuery."""
    from src.config import get_settings

    settings = get_settings()
    if settings.app.ingest_api_key and x_api_key != settings.app.ingest_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()
    channel_name = body.get("channel")
    rows = body.get("rows", [])
    report_date = body.get("date")

    if not channel_name or not rows:
        raise HTTPException(status_code=400, detail="channel and rows are required")

    try:
        from src.channels.gfa import GFAChannel
        from src.loader import BigQueryLoader

        target_date = date.fromisoformat(report_date) if report_date else date.today() - timedelta(days=1)

        # Use GFA transformer for CSV data (can be extended for other channels)
        channel = GFAChannel()
        records = channel.transform_extension_data(rows, target_date)
        loader = BigQueryLoader()
        loaded = loader.load_ad_performance(records, channel_name, target_date, target_date)

        return {"status": "success", "rows_loaded": loaded}
    except Exception as e:
        logging.error(f"CSV ingest error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


# ──────────────────────────────────────────────
# CLI entry point (Cloud Run Job / local execution)
# ──────────────────────────────────────────────

@click.group()
def cli():
    """Marketing Data Pipeline CLI."""
    pass


@cli.command()
@click.option("--channel", type=str, default=None, help="Specific channel to run (meta, google_ads, naver_sa)")
@click.option("--date-start", type=str, default=None, help="Start date (YYYY-MM-DD)")
@click.option("--date-end", type=str, default=None, help="End date (YYYY-MM-DD)")
def run(channel: str | None, date_start: str | None, date_end: str | None):
    """Run the ETL pipeline for one or all channels."""
    from src.pipeline import run_all_channels, run_channel

    d_start = date.fromisoformat(date_start) if date_start else None
    d_end = date.fromisoformat(date_end) if date_end else None

    if channel:
        if d_end is None:
            d_end = date.today() - timedelta(days=1)
        if d_start is None:
            from src.config import get_settings
            settings = get_settings()
            d_start = d_end - timedelta(days=settings.app.backfill_days - 1)
        result = run_channel(channel, d_start, d_end)
        click.echo(f"Result: {result.status.value} | Rows: {result.rows_loaded}")
    else:
        results = run_all_channels(d_start, d_end)
        for r in results:
            click.echo(f"{r.channel}: {r.status.value} | Rows: {r.rows_loaded}")


@cli.command()
@click.option("--host", default="0.0.0.0", help="Server host")
@click.option("--port", default=8080, type=int, help="Server port")
def serve(host: str, port: int):
    """Start the ingest API server (for Cloud Run Service)."""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    # If RUN_MODE=server, start HTTP server; otherwise run CLI
    if os.environ.get("RUN_MODE") == "server":
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    else:
        cli()
