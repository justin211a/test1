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


@app.post("/api/run")
async def run_pipeline_api(request: Request, x_api_key: str = Header(None)):
    """Trigger pipeline run via HTTP (for remote execution)."""
    from src.config import get_settings

    settings = get_settings()
    if settings.app.ingest_api_key and x_api_key != settings.app.ingest_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()
    channel_name = body.get("channel")
    date_start = body.get("date_start")
    date_end = body.get("date_end")

    from src.pipeline import run_all_channels, run_channel

    d_end = date.fromisoformat(date_end) if date_end else date.today() - timedelta(days=1)
    d_start = date.fromisoformat(date_start) if date_start else d_end - timedelta(days=settings.app.backfill_days - 1)

    if channel_name:
        result = run_channel(channel_name, d_start, d_end)
        return {
            "channel": result.channel,
            "status": result.status.value,
            "rows_loaded": result.rows_loaded,
            "error": result.error_message,
            "date_range": f"{d_start} ~ {d_end}",
        }
    else:
        results = run_all_channels(d_start, d_end)
        return [
            {
                "channel": r.channel,
                "status": r.status.value,
                "rows_loaded": r.rows_loaded,
                "error": r.error_message,
            }
            for r in results
        ]


@app.post("/api/reports/weekly")
async def weekly_report_api(request: Request, x_api_key: str = Header(None)):
    """Generate and send the weekly marketing performance report."""
    from src.config import get_settings

    settings = get_settings()
    if settings.app.ingest_api_key and x_api_key != settings.app.ingest_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json() if await request.body() else {}
    week_end_str = body.get("week_end")
    dry_run = body.get("dry_run", False)

    try:
        from src.reporting import generate_weekly_report, send_weekly_report

        w_end = date.fromisoformat(week_end_str) if week_end_str else None
        report = generate_weekly_report(w_end)
        result = send_weekly_report(report, dry_run=dry_run)

        if dry_run:
            return {"status": "dry_run", "html_length": len(result), "week": f"{report.week_start} ~ {report.week_end}"}
        return {"status": "sent" if result else "send_failed", "week": f"{report.week_start} ~ {report.week_end}"}
    except Exception as e:
        logging.error(f"Weekly report error: {e}", exc_info=True)
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


@cli.command("weekly-report")
@click.option("--week-end", type=str, default=None, help="End date of report week (YYYY-MM-DD, defaults to last Sunday)")
@click.option("--dry-run", is_flag=True, help="Generate report without sending, save HTML to file")
def weekly_report(week_end: str | None, dry_run: bool):
    """Generate and send the weekly marketing performance report."""
    from src.reporting import generate_weekly_report, send_weekly_report

    w_end = date.fromisoformat(week_end) if week_end else None
    click.echo("주간 보고서 생성 중...")
    report = generate_weekly_report(w_end)
    click.echo(f"기간: {report.week_start} ~ {report.week_end}")

    result = send_weekly_report(report, dry_run=dry_run)

    if dry_run:
        output_path = f"weekly_report_{report.week_start}_{report.week_end}.html"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)
        click.echo(f"HTML 보고서 저장: {output_path}")
    elif result:
        click.echo("보고서 발송 완료")
    else:
        click.echo("보고서 발송 실패 (이메일 설정 확인 필요)")


@cli.command()
@click.option("--host", default="0.0.0.0", help="Server host")
@click.option("--port", default=8080, type=int, help="Server port")
def serve(host: str, port: int):
    """Start the ingest API server (for Cloud Run Service)."""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    # Cloud Run sets PORT env var automatically. If PORT is set, start HTTP server.
    if os.environ.get("PORT") or os.environ.get("RUN_MODE") == "server":
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    else:
        cli()
