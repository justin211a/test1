"""Exchange rate fetcher using Korea Exim Bank API (한국수출입은행)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import get_settings

logger = logging.getLogger(__name__)

KOREAEXIM_API_URL = "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON"

# Fallback rates (updated manually as a safety net)
FALLBACK_RATES = {
    "USD": 1350.0,
    "EUR": 1470.0,
    "JPY": 9.0,  # per 1 JPY
    "KRW": 1.0,
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_exchange_rates_from_api(search_date: date) -> dict[str, float]:
    """Fetch exchange rates from Korea Exim Bank API for a given date."""
    settings = get_settings()
    params = {
        "authkey": settings.app.exchange_rate_api_key,
        "searchdate": search_date.strftime("%Y%m%d"),
        "data": "AP01",
    }

    resp = requests.get(KOREAEXIM_API_URL, params=params, timeout=15, verify=False)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        raise ValueError(f"No exchange rate data for {search_date} (may be a holiday)")

    rates = {"KRW": 1.0}
    for item in data:
        currency = item.get("cur_unit", "")
        # deal_bas_r is the base rate, formatted with commas
        rate_str = item.get("deal_bas_r", "0").replace(",", "")
        try:
            rate = float(rate_str)
            # JPY(100) is reported per 100 yen
            if currency == "JPY(100)":
                rates["JPY"] = rate / 100.0
            else:
                rates[currency] = rate
        except ValueError:
            continue

    return rates


@lru_cache(maxsize=32)
def get_exchange_rate(currency: str, target_date: date | None = None) -> float:
    """Get exchange rate to KRW for a given currency.

    Tries the target date first, falls back to previous business days,
    then to hardcoded fallback rates.

    Args:
        currency: ISO currency code (e.g., "USD", "EUR").
        target_date: Date for the exchange rate. Defaults to today.

    Returns:
        Exchange rate to KRW (e.g., 1350.0 for USD).
    """
    if currency == "KRW":
        return 1.0

    if target_date is None:
        target_date = date.today()

    # Try target date and up to 5 previous days (weekends/holidays)
    for offset in range(6):
        try_date = target_date - timedelta(days=offset)
        try:
            rates = _fetch_exchange_rates_from_api(try_date)
            if currency in rates:
                logger.info(f"Exchange rate {currency}/KRW = {rates[currency]} (date: {try_date})")
                return rates[currency]
        except Exception as e:
            logger.debug(f"Exchange rate fetch failed for {try_date}: {e}")
            continue

    # Fallback to hardcoded rates
    fallback = FALLBACK_RATES.get(currency, 1.0)
    logger.warning(f"Using fallback exchange rate {currency}/KRW = {fallback}")
    return fallback
