"""Weekly marketing performance report generation."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from pydantic import BaseModel

from src.config import get_settings
from src.loader import BigQueryLoader
from src.utils.notify import send_html_email, send_slack_message

logger = logging.getLogger(__name__)

CHANNEL_LABELS = {
    "meta": "Meta (Facebook)",
    "google_ads": "Google Ads",
    "naver_sa": "네이버 SA",
    "gfa": "GFA",
}


# ──────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────


class ChannelWeeklySummary(BaseModel):
    channel: str
    channel_label: str = ""
    impressions: int = 0
    clicks: int = 0
    cost: float = 0.0
    conversions: float = 0.0
    conversion_value: float = 0.0
    ctr: float = 0.0
    cpc: float = 0.0
    roas: float = 0.0
    impressions_wow: float | None = None
    clicks_wow: float | None = None
    cost_wow: float | None = None
    conversions_wow: float | None = None
    roas_wow: float | None = None


class CampaignSummary(BaseModel):
    channel: str
    channel_label: str = ""
    campaign_name: str = ""
    cost: float = 0.0
    conversions: float = 0.0
    roas: float = 0.0
    cost_wow: float | None = None


class WeeklyReport(BaseModel):
    report_date: date
    week_start: date
    week_end: date
    prev_week_start: date
    prev_week_end: date
    channel_summaries: list[ChannelWeeklySummary] = []
    total_summary: ChannelWeeklySummary
    top_campaigns: list[CampaignSummary] = []
    anomalies: list[str] = []
    pipeline_health: list[str] = []


# ──────────────────────────────────────────────
# Date helpers
# ──────────────────────────────────────────────


def _get_week_range(reference_date: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the week containing reference_date."""
    monday = reference_date - timedelta(days=reference_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _get_previous_week(week_start: date) -> tuple[date, date]:
    prev_monday = week_start - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    return prev_monday, prev_sunday


def _calc_wow(current: float, previous: float) -> float | None:
    """Calculate week-over-week percentage change."""
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


# ──────────────────────────────────────────────
# BigQuery queries
# ──────────────────────────────────────────────


def _query_channel_summary(
    client: bigquery.Client,
    table_ref: str,
    week_start: date,
    week_end: date,
) -> list[dict[str, Any]]:
    """Query weekly aggregated metrics per channel."""
    query = f"""
    SELECT
        channel,
        SUM(impressions) AS impressions,
        SUM(clicks) AS clicks,
        SUM(cost) AS cost,
        SUM(conversions) AS conversions,
        SUM(conversion_value) AS conversion_value,
        SAFE_DIVIDE(SUM(clicks), SUM(impressions)) AS ctr,
        SAFE_DIVIDE(SUM(cost), SUM(clicks)) AS cpc,
        SAFE_DIVIDE(SUM(conversion_value), SUM(cost)) AS roas
    FROM `{table_ref}`
    WHERE date BETWEEN @week_start AND @week_end
    GROUP BY channel
    ORDER BY cost DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("week_start", "DATE", week_start),
            bigquery.ScalarQueryParameter("week_end", "DATE", week_end),
        ]
    )
    result = client.query(query, job_config=job_config).result()
    return [dict(row) for row in result]


def _query_top_campaigns(
    client: bigquery.Client,
    table_ref: str,
    week_start: date,
    week_end: date,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Query top campaigns by cost."""
    query = f"""
    SELECT
        channel,
        campaign_name,
        SUM(cost) AS cost,
        SUM(conversions) AS conversions,
        SAFE_DIVIDE(SUM(conversion_value), SUM(cost)) AS roas
    FROM `{table_ref}`
    WHERE date BETWEEN @week_start AND @week_end
    GROUP BY channel, campaign_name
    HAVING SUM(cost) > 0
    ORDER BY cost DESC
    LIMIT {limit}
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("week_start", "DATE", week_start),
            bigquery.ScalarQueryParameter("week_end", "DATE", week_end),
        ]
    )
    result = client.query(query, job_config=job_config).result()
    return [dict(row) for row in result]


def _query_pipeline_health(
    client: bigquery.Client,
    project_id: str,
    dataset_id: str,
    week_start: date,
    week_end: date,
) -> list[str]:
    """Check pipeline run statuses for the week."""
    table_ref = f"{project_id}.{dataset_id}.pipeline_runs"
    query = f"""
    SELECT channel, status, COUNT(*) as cnt,
           MAX(error_message) as last_error
    FROM `{table_ref}`
    WHERE report_date_start >= @week_start
      AND report_date_end <= @week_end
    GROUP BY channel, status
    ORDER BY channel
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("week_start", "DATE", week_start),
            bigquery.ScalarQueryParameter("week_end", "DATE", week_end),
        ]
    )
    messages = []
    try:
        result = client.query(query, job_config=job_config).result()
        for row in result:
            if row.status == "failed":
                messages.append(
                    f"{CHANNEL_LABELS.get(row.channel, row.channel)}: "
                    f"{row.cnt}건 실패 — {row.last_error or '(상세 없음)'}"
                )
    except Exception as e:
        logger.warning(f"Pipeline health query failed: {e}")
    return messages


# ──────────────────────────────────────────────
# Report generation
# ──────────────────────────────────────────────


def generate_weekly_report(week_end: date | None = None) -> WeeklyReport:
    """Generate a complete weekly report by querying BigQuery.

    Args:
        week_end: End date (Sunday) of the reporting week.
                  Defaults to the most recent past Sunday.
    """
    settings = get_settings()
    today = date.today()

    if week_end is None:
        days_since_sunday = (today.weekday() + 1) % 7
        if days_since_sunday == 0:
            days_since_sunday = 7
        week_end = today - timedelta(days=days_since_sunday)

    week_start, week_end = _get_week_range(week_end)
    prev_week_start, prev_week_end = _get_previous_week(week_start)

    loader = BigQueryLoader()
    client = loader.client
    table_ref = f"{loader.project_id}.{loader.dataset_id}.ad_performance"

    # Current week
    current_data = _query_channel_summary(client, table_ref, week_start, week_end)
    # Previous week
    prev_data = _query_channel_summary(client, table_ref, prev_week_start, prev_week_end)
    prev_map = {r["channel"]: r for r in prev_data}

    # Build channel summaries
    channel_summaries = []
    total = dict(impressions=0, clicks=0, cost=0.0, conversions=0.0, conversion_value=0.0)
    prev_total = dict(impressions=0, clicks=0, cost=0.0, conversions=0.0, conversion_value=0.0)

    for row in current_data:
        ch = row["channel"]
        prev = prev_map.get(ch, {})

        summary = ChannelWeeklySummary(
            channel=ch,
            channel_label=CHANNEL_LABELS.get(ch, ch),
            impressions=int(row.get("impressions") or 0),
            clicks=int(row.get("clicks") or 0),
            cost=float(row.get("cost") or 0),
            conversions=float(row.get("conversions") or 0),
            conversion_value=float(row.get("conversion_value") or 0),
            ctr=float(row.get("ctr") or 0),
            cpc=float(row.get("cpc") or 0),
            roas=float(row.get("roas") or 0),
            impressions_wow=_calc_wow(row.get("impressions") or 0, prev.get("impressions") or 0),
            clicks_wow=_calc_wow(row.get("clicks") or 0, prev.get("clicks") or 0),
            cost_wow=_calc_wow(row.get("cost") or 0, prev.get("cost") or 0),
            conversions_wow=_calc_wow(row.get("conversions") or 0, prev.get("conversions") or 0),
            roas_wow=_calc_wow(row.get("roas") or 0, prev.get("roas") or 0),
        )
        channel_summaries.append(summary)

        for k in total:
            total[k] += float(row.get(k) or 0)
        for k in prev_total:
            prev_total[k] += float(prev.get(k) or 0)

    # Total summary
    total_summary = ChannelWeeklySummary(
        channel="total",
        channel_label="전체 합계",
        impressions=int(total["impressions"]),
        clicks=int(total["clicks"]),
        cost=total["cost"],
        conversions=total["conversions"],
        conversion_value=total["conversion_value"],
        ctr=total["clicks"] / total["impressions"] if total["impressions"] else 0,
        cpc=total["cost"] / total["clicks"] if total["clicks"] else 0,
        roas=total["conversion_value"] / total["cost"] if total["cost"] else 0,
        impressions_wow=_calc_wow(total["impressions"], prev_total["impressions"]),
        clicks_wow=_calc_wow(total["clicks"], prev_total["clicks"]),
        cost_wow=_calc_wow(total["cost"], prev_total["cost"]),
        conversions_wow=_calc_wow(total["conversions"], prev_total["conversions"]),
        roas_wow=_calc_wow(
            total["conversion_value"] / total["cost"] if total["cost"] else 0,
            prev_total["conversion_value"] / prev_total["cost"] if prev_total["cost"] else 0,
        ),
    )

    # Top campaigns (current week + WoW)
    top_campaign_limit = settings.report.top_campaigns_count
    current_campaigns = _query_top_campaigns(client, table_ref, week_start, week_end, top_campaign_limit)
    prev_campaigns = _query_top_campaigns(client, table_ref, prev_week_start, prev_week_end, 50)
    prev_camp_map = {(r["channel"], r["campaign_name"]): r for r in prev_campaigns}

    top_campaigns = []
    for row in current_campaigns:
        key = (row["channel"], row["campaign_name"])
        prev = prev_camp_map.get(key, {})
        top_campaigns.append(CampaignSummary(
            channel=row["channel"],
            channel_label=CHANNEL_LABELS.get(row["channel"], row["channel"]),
            campaign_name=row["campaign_name"] or "(이름 없음)",
            cost=float(row.get("cost") or 0),
            conversions=float(row.get("conversions") or 0),
            roas=float(row.get("roas") or 0),
            cost_wow=_calc_wow(row.get("cost") or 0, prev.get("cost") or 0),
        ))

    # Anomaly detection (>30% WoW change)
    anomalies = []
    for s in channel_summaries:
        if s.cost_wow is not None and abs(s.cost_wow) > 30:
            direction = "증가" if s.cost_wow > 0 else "감소"
            anomalies.append(f"{s.channel_label} 비용 {abs(s.cost_wow):.1f}% {direction} (전주 대비)")
        if s.roas_wow is not None and abs(s.roas_wow) > 30:
            direction = "개선" if s.roas_wow > 0 else "하락"
            anomalies.append(f"{s.channel_label} ROAS {abs(s.roas_wow):.1f}% {direction} (전주 대비)")

    # Pipeline health
    pipeline_health = _query_pipeline_health(
        client, loader.project_id, loader.dataset_id, week_start, week_end
    )

    return WeeklyReport(
        report_date=today,
        week_start=week_start,
        week_end=week_end,
        prev_week_start=prev_week_start,
        prev_week_end=prev_week_end,
        channel_summaries=channel_summaries,
        total_summary=total_summary,
        top_campaigns=top_campaigns,
        anomalies=anomalies,
        pipeline_health=pipeline_health,
    )


# ──────────────────────────────────────────────
# Rendering & sending
# ──────────────────────────────────────────────


def render_report_html(report: WeeklyReport) -> str:
    """Render WeeklyReport to HTML using Jinja2 template."""
    from jinja2 import Environment, FileSystemLoader

    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    env.filters["krw"] = lambda v: f"{v:,.0f}"
    env.filters["pct"] = lambda v: f"{v:.2f}%" if v is not None else "-"
    env.filters["wow_arrow"] = _wow_arrow
    template = env.get_template("weekly_report.html")
    return template.render(report=report, now=datetime.now().strftime("%Y-%m-%d %H:%M"))


def _wow_arrow(value: float | None) -> str:
    """Format WoW change with arrow and color."""
    if value is None:
        return '<span style="color:#999">-</span>'
    if value > 0:
        return f'<span style="color:#E74C3C">+{value:.1f}%</span>'
    elif value < 0:
        return f'<span style="color:#2980B9">{value:.1f}%</span>'
    return '<span style="color:#999">0.0%</span>'


def send_weekly_report(report: WeeklyReport, dry_run: bool = False) -> bool | str:
    """Render and send the weekly report.

    Args:
        report: Generated WeeklyReport data.
        dry_run: If True, return HTML string instead of sending.

    Returns:
        True/False if sending, or HTML string if dry_run.
    """
    html = render_report_html(report)

    if dry_run:
        return html

    subject = (
        f"주간 마케팅 성과 보고서 "
        f"({report.week_start.strftime('%m/%d')} ~ {report.week_end.strftime('%m/%d')})"
    )

    settings = get_settings()
    to = settings.report.report_email_to
    cc = settings.report.report_email_cc

    email_sent = send_html_email(subject, html, to=to, cc=cc)

    # Slack summary
    total = report.total_summary
    slack_body = (
        f"*기간*: {report.week_start} ~ {report.week_end}\n"
        f"*총 비용*: {total.cost:,.0f}원\n"
        f"*총 클릭*: {total.clicks:,}\n"
        f"*총 전환*: {total.conversions:,.0f}\n"
        f"*ROAS*: {total.roas:.2f}\n"
    )
    if report.anomalies:
        slack_body += "\n*이상 감지:*\n" + "\n".join(f"  - {a}" for a in report.anomalies)

    send_slack_message(
        title=f"주간 마케팅 보고서 ({report.week_start} ~ {report.week_end})",
        body=slack_body,
        color="#3498DB",
    )

    return email_sent
