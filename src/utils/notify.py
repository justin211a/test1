"""Alert notifications via Jandi webhook and email."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from src.config import get_settings

logger = logging.getLogger(__name__)


def send_jandi_message(title: str, body: str, color: str = "#FAC11B") -> bool:
    """Send a message to Jandi via Incoming Webhook.

    Args:
        title: Message title.
        body: Message body (supports markdown).
        color: Sidebar color. Green=#2ECC71, Red=#E74C3C, Yellow=#FAC11B.

    Returns:
        True if sent successfully, False otherwise.
    """
    settings = get_settings()
    webhook_url = settings.alert.jandi_webhook_url
    if not webhook_url:
        logger.warning("Jandi webhook URL not configured. Skipping notification.")
        return False

    payload = {
        "body": title,
        "connectColor": color,
        "connectInfo": [
            {
                "title": "상세 내용",
                "description": body,
            }
        ],
    }

    try:
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Accept": "application/vnd.tosslab.jandi-v2+json", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"Jandi notification sent: {title}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Jandi notification: {e}")
        return False


def send_email_alert(subject: str, body: str) -> bool:
    """Send an email alert via SMTP.

    Args:
        subject: Email subject.
        body: Email body (plain text).

    Returns:
        True if sent successfully, False otherwise.
    """
    settings = get_settings()
    if not settings.alert.smtp_user or not settings.alert.alert_email_to:
        logger.warning("Email settings not configured. Skipping email alert.")
        return False

    msg = MIMEMultipart()
    msg["From"] = settings.alert.smtp_user
    msg["To"] = settings.alert.alert_email_to
    msg["Subject"] = f"[마케팅 파이프라인] {subject}"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(settings.alert.smtp_host, settings.alert.smtp_port) as server:
            server.starttls()
            server.login(settings.alert.smtp_user, settings.alert.smtp_password)
            server.send_message(msg)
        logger.info(f"Email alert sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email alert: {e}")
        return False


# ──────────────────────────────────────────────
# High-level notification functions
# ──────────────────────────────────────────────

def notify_pipeline_success(channel: str, date_range: str, rows_loaded: int):
    """Notify successful pipeline completion (Jandi only)."""
    send_jandi_message(
        title=f"[성공] {channel} 데이터 수집 완료",
        body=f"기간: {date_range}\n적재 건수: {rows_loaded:,}건",
        color="#2ECC71",
    )


def notify_pipeline_failure(channel: str, date_range: str, error: str):
    """Notify pipeline failure (Jandi + Email)."""
    body = f"채널: {channel}\n기간: {date_range}\n에러: {error}"
    send_jandi_message(title=f"[실패] {channel} 데이터 수집 실패", body=body, color="#E74C3C")
    send_email_alert(subject=f"{channel} 파이프라인 실패", body=body)


def notify_token_expiry_warning(channel: str, days_remaining: int):
    """Notify about upcoming token expiration (Jandi + Email)."""
    body = f"채널: {channel}\n토큰 만료까지 {days_remaining}일 남았습니다.\n빠른 갱신이 필요합니다."
    send_jandi_message(title=f"[경고] {channel} 토큰 만료 임박", body=body, color="#E74C3C")
    send_email_alert(subject=f"{channel} 토큰 만료 경고 ({days_remaining}일 남음)", body=body)


def notify_data_anomaly(channel: str, report_date: str, today_count: int, prev_count: int):
    """Notify about abnormal data volume change (Jandi only)."""
    change_pct = ((today_count - prev_count) / prev_count * 100) if prev_count else 0
    body = (
        f"채널: {channel}\n날짜: {report_date}\n"
        f"금일: {today_count:,}건 / 전일: {prev_count:,}건\n"
        f"변동: {change_pct:+.1f}%"
    )
    send_jandi_message(title=f"[이상감지] {channel} 데이터 건수 변동", body=body, color="#FAC11B")
