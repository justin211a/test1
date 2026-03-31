"""Abstract base class for all marketing channel extractors/transformers."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.schemas import AdPerformance, Channel, DataSource

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, calls_per_second: float = 5.0):
        self.min_interval = 1.0 / calls_per_second
        self._last_call = 0.0

    def wait(self):
        """Block until rate limit allows the next call."""
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()


class BaseChannel(ABC):
    """All channel implementations extend this class."""

    channel: Channel
    rate_limiter: RateLimiter

    def __init__(self):
        self.rate_limiter = RateLimiter(calls_per_second=self.default_rate_limit)

    @property
    @abstractmethod
    def default_rate_limit(self) -> float:
        """Max API calls per second."""
        ...

    @abstractmethod
    def extract_and_transform(
        self,
        account_id: str,
        date_start: date,
        date_end: date,
    ) -> list[AdPerformance]:
        """Extract data from the channel API and transform to unified schema.

        Args:
            account_id: Channel-specific account identifier.
            date_start: Start of date range (inclusive).
            date_end: End of date range (inclusive).

        Returns:
            List of unified AdPerformance records.
        """
        ...

    @abstractmethod
    def test_connection(self, account_id: str) -> bool:
        """Validate credentials and connectivity for the given account."""
        ...

    def check_token_health(self) -> int | None:
        """Check token expiry. Returns days remaining, or None if not applicable."""
        return None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Safely convert a value to int."""
        try:
            return int(value or default)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Safely convert a value to float."""
        try:
            return float(value or default)
        except (ValueError, TypeError):
            return default
