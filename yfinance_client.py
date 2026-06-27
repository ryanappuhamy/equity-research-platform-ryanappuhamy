"""
Shared yfinance access with retry and exponential backoff.

Rate limits on popular tickers are common; every yfinance call in this
project should go through these helpers (3 attempts, 2s / 4s / 8s delays).
"""

import time
from typing import Callable, TypeVar

import pandas as pd
import yfinance as yf

RETRY_DELAYS_SEC = (2, 4, 8)
MAX_ATTEMPTS = 3

T = TypeVar("T")


def yf_call(fn: Callable[[], T]) -> T:
    """Run a yfinance callable with up to MAX_ATTEMPTS and backoff between failures."""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS_SEC[attempt]
                print(
                    f"[warn] yfinance attempt {attempt + 1}/{MAX_ATTEMPTS} failed: {exc}; "
                    f"retrying in {delay}s"
                )
                time.sleep(delay)
    assert last_error is not None
    raise last_error


def yf_download(*args, **kwargs) -> pd.DataFrame:
    return yf_call(lambda: yf.download(*args, **kwargs))


def yf_ticker_info(ticker: str) -> dict:
    return yf_call(lambda: yf.Ticker(ticker).info or {})


def yf_analyst_price_targets(ticker: str) -> dict | None:
    return yf_call(lambda: yf.Ticker(ticker.upper()).analyst_price_targets)


def yf_last_price(ticker: str) -> float:
    return float(yf_call(lambda: yf.Ticker(ticker.upper()).fast_info.last_price))


def yf_ticker_sector(ticker: str) -> str | None:
    """Sector from yfinance fast_info, falling back to info."""

    def _fetch() -> str | None:
        t = yf.Ticker(ticker.upper())
        sector = None
        try:
            fast = t.fast_info
            if hasattr(fast, "get"):
                sector = fast.get("sector")
            else:
                sector = getattr(fast, "sector", None)
        except Exception:
            pass
        if not sector:
            sector = (t.info or {}).get("sector")
        return sector if sector else None

    return yf_call(_fetch)
