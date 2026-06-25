"""
Cache fundamentals and price data in the shared DB (Supabase / SQLite).

Fundamentals are cached for 24 hours; price data uses a 30-minute TTL.
"""

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from database import Base, get_session

PRICE_CACHE_TTL_SECONDS = 30 * 60
FUNDAMENTALS_CACHE_TTL_SECONDS = 24 * 60 * 60


class MarketDataCache(Base):
    __tablename__ = "market_data_cache"
    __table_args__ = (
        UniqueConstraint("ticker", "data_type", "cache_key", name="uq_market_data_cache"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(16), nullable=False)
    data_type = Column(String(32), nullable=False)
    cache_key = Column(String(64), nullable=False, default="")
    payload = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False)


def _is_fresh(fetched_at: datetime, ttl_seconds: int) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - fetched_at
    return age < timedelta(seconds=ttl_seconds)


def _get_row(ticker: str, data_type: str, cache_key: str = "") -> MarketDataCache | None:
    try:
        with get_session() as db:
            return (
                db.query(MarketDataCache)
                .filter_by(
                    ticker=ticker.upper(),
                    data_type=data_type,
                    cache_key=cache_key,
                )
                .first()
            )
    except Exception as e:
        print(f"[warn] market cache read failed ({data_type}/{ticker}): {e}")
        return None


def _set_row(ticker: str, data_type: str, payload: str, cache_key: str = "") -> None:
    try:
        now = datetime.now(timezone.utc)
        ticker = ticker.upper()
        with get_session() as db:
            row = (
                db.query(MarketDataCache)
                .filter_by(ticker=ticker, data_type=data_type, cache_key=cache_key)
                .first()
            )
            if row:
                row.payload = payload
                row.fetched_at = now
            else:
                db.add(
                    MarketDataCache(
                        ticker=ticker,
                        data_type=data_type,
                        cache_key=cache_key,
                        payload=payload,
                        fetched_at=now,
                    )
                )
            db.commit()
    except Exception as e:
        print(f"[warn] market cache write failed ({data_type}/{ticker}): {e}")


def get_fundamentals(ticker: str) -> dict | None:
    row = _get_row(ticker, "fundamentals")
    if row and _is_fresh(row.fetched_at, FUNDAMENTALS_CACHE_TTL_SECONDS):
        try:
            return json.loads(row.payload)
        except json.JSONDecodeError as e:
            print(f"[warn] market cache corrupt fundamentals for {ticker}: {e}")
    return None


def set_fundamentals(ticker: str, data: dict) -> None:
    if not data.get("available"):
        return
    _set_row(ticker, "fundamentals", json.dumps(data))


def get_price_history(ticker: str, years: int) -> pd.DataFrame | None:
    cache_key = f"{years}y"
    row = _get_row(ticker, "price_history", cache_key)
    if row and _is_fresh(row.fetched_at, PRICE_CACHE_TTL_SECONDS):
        try:
            return pd.read_json(StringIO(row.payload), orient="split")
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[warn] market cache corrupt price history for {ticker}: {e}")
    return None


def set_price_history(ticker: str, years: int, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    cache_key = f"{years}y"
    _set_row(ticker, "price_history", df.to_json(orient="split", date_format="iso"), cache_key)


def get_live_prices(tickers: list[str]) -> dict[str, float] | None:
    """Cached snapshot for portfolio live-price fetches (5d window)."""
    if not tickers:
        return {}
    cache_key = ",".join(sorted(t.upper() for t in tickers))
    row = _get_row("_batch", "price_live", cache_key)
    if row and _is_fresh(row.fetched_at, PRICE_CACHE_TTL_SECONDS):
        try:
            raw = json.loads(row.payload)
            return {k: float(v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"[warn] market cache corrupt live prices: {e}")
    return None


def set_live_prices(tickers: list[str], prices: dict[str, float]) -> None:
    if not prices:
        return
    cache_key = ",".join(sorted(t.upper() for t in tickers))
    _set_row("_batch", "price_live", json.dumps(prices), cache_key)
