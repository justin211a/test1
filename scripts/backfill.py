"""Backfill historical data for a channel in date-range chunks."""

import sys
sys.path.insert(0, ".")

from datetime import date, timedelta

import click

from src.pipeline import run_channel


@click.command()
@click.option("--channel", required=True, help="Channel to backfill (meta, google_ads, naver_sa, gfa)")
@click.option("--start", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--end", required=True, help="End date (YYYY-MM-DD)")
@click.option("--chunk-days", default=7, help="Days per chunk to avoid API quota limits (default: 7)")
def backfill(channel: str, start: str, end: str, chunk_days: int):
    """Backfill historical marketing data in chunks.

    Example:
        python scripts/backfill.py --channel meta --start 2026-01-01 --end 2026-03-30
        python scripts/backfill.py --channel google_ads --start 2026-01-01 --end 2026-03-30 --chunk-days 14
    """
    date_start = date.fromisoformat(start)
    date_end = date.fromisoformat(end)

    if date_start > date_end:
        click.echo("Error: start date must be before end date")
        sys.exit(1)

    total_days = (date_end - date_start).days + 1
    click.echo(f"Backfilling {channel}: {date_start} ~ {date_end} ({total_days} days in {chunk_days}-day chunks)")

    chunk_start = date_start
    chunk_num = 0
    total_rows = 0
    failures = 0

    while chunk_start <= date_end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), date_end)
        chunk_num += 1

        click.echo(f"\n--- Chunk {chunk_num}: {chunk_start} ~ {chunk_end} ---")

        result = run_channel(channel, chunk_start, chunk_end)

        if result.status.value == "success":
            total_rows += result.rows_loaded
            click.echo(f"  OK: {result.rows_loaded} rows loaded")
        else:
            failures += 1
            click.echo(f"  FAILED: {result.error_message}")

        chunk_start = chunk_end + timedelta(days=1)

    click.echo(f"\n=== Backfill complete ===")
    click.echo(f"Total rows: {total_rows:,}")
    click.echo(f"Chunks: {chunk_num} ({failures} failed)")


if __name__ == "__main__":
    backfill()
