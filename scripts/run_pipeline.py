"""CLI wrapper for running the marketing data pipeline."""

import sys
sys.path.insert(0, ".")

from datetime import date, timedelta

import click


@click.command()
@click.option("--channel", default=None, help="Specific channel (meta, google_ads, naver_sa, gfa). Omit for all.")
@click.option("--date", "target_date", default=None, help="Target date (YYYY-MM-DD) or 'yesterday'")
@click.option("--date-start", default=None, help="Start date for custom range (YYYY-MM-DD)")
@click.option("--date-end", default=None, help="End date for custom range (YYYY-MM-DD)")
@click.option("--csv", "csv_path", default=None, help="CSV file path (for GFA or manual upload)")
def main(channel, target_date, date_start, date_end, csv_path):
    """Run the marketing data ETL pipeline.

    Examples:
        python scripts/run_pipeline.py                                    # All channels, rolling 7-day
        python scripts/run_pipeline.py --channel meta                     # Meta only
        python scripts/run_pipeline.py --channel meta --date yesterday    # Meta, yesterday only
        python scripts/run_pipeline.py --channel gfa --csv uploads/gfa.csv --date 2026-03-30
    """
    from src.config import get_settings
    from src.pipeline import run_all_channels, run_channel
    from src.schemas import DataSource

    settings = get_settings()

    # Determine date range
    if date_start and date_end:
        d_start = date.fromisoformat(date_start)
        d_end = date.fromisoformat(date_end)
    elif target_date:
        if target_date == "yesterday":
            d_end = date.today() - timedelta(days=1)
        else:
            d_end = date.fromisoformat(target_date)
        d_start = d_end
    else:
        d_end = date.today() - timedelta(days=1)
        d_start = d_end - timedelta(days=settings.app.backfill_days - 1)

    click.echo(f"Date range: {d_start} ~ {d_end}")

    # CSV mode
    if csv_path:
        if not channel:
            click.echo("Error: --channel is required when using --csv")
            sys.exit(1)

        click.echo(f"Loading CSV: {csv_path} for channel: {channel}")
        from src.channels.gfa import GFAChannel
        from src.loader import BigQueryLoader

        gfa = GFAChannel()
        import pandas as pd
        from pathlib import Path

        records = gfa._parse_csv_file(Path(csv_path), d_start)
        loader = BigQueryLoader()
        rows = loader.load_ad_performance(records, channel, d_start, d_end)
        click.echo(f"Loaded {rows} rows from CSV")
        return

    # API mode
    if channel:
        result = run_channel(channel, d_start, d_end)
        click.echo(f"\n{result.channel}: {result.status.value} | Rows: {result.rows_loaded}")
        if result.error_message:
            click.echo(f"  Error: {result.error_message}")
    else:
        results = run_all_channels(d_start, d_end)
        click.echo("\n=== Results ===")
        for r in results:
            status_icon = "OK" if r.status.value == "success" else "FAIL"
            click.echo(f"  [{status_icon}] {r.channel}: {r.rows_loaded} rows")
            if r.error_message:
                click.echo(f"       Error: {r.error_message}")


if __name__ == "__main__":
    main()
