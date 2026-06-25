"""
Cache fundamentals and price data in the shared DB (Supabase / SQLite).

Fundamentals are cached for 24 hours; price data uses a 30-minute TTL;
weekly portfolio briefs are cached for 7 days.
"""

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from database import Base, get_session

PRICE_CACHE_TTL_SECONDS = 30 * 60
FUNDAMENTALS_CACHE_TTL_SECONDS = 24 * 60 * 60
WEEKLY_BRIEF_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
WEEKLY_BRIEF_TICKER = "_portfolio"


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


def _portfolio_brief_cache_key(holdings: list[dict]) -> str:
    return ",".join(sorted(h["ticker"].upper() for h in holdings)) or "empty"


def get_weekly_brief(holdings: list[dict]) -> tuple[str | None, datetime | None]:
    """Return cached brief text and fetched_at, regardless of TTL."""
    row = _get_row(WEEKLY_BRIEF_TICKER, "weekly_brief", _portfolio_brief_cache_key(holdings))
    if not row:
        return None, None
    try:
        payload = json.loads(row.payload)
        brief = payload.get("brief")
    except json.JSONDecodeError as e:
        print(f"[warn] market cache corrupt weekly brief: {e}")
        return None, None
    fetched_at = row.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return brief, fetched_at


def weekly_brief_is_fresh(fetched_at: datetime | None) -> bool:
    if fetched_at is None:
        return False
    return _is_fresh(fetched_at, WEEKLY_BRIEF_CACHE_TTL_SECONDS)


def should_regenerate_weekly_brief(fetched_at: datetime | None, force_bypass: bool) -> bool:
    """
    Regenerate when force-bypass is authorized, cache is missing, cache is stale
    on a Friday, or there is no cached entry at all.
    """
    if force_bypass:
        return True
    if fetched_at is None:
        return True
    if weekly_brief_is_fresh(fetched_at):
        return False
    return datetime.now(timezone.utc).weekday() == 4


def set_weekly_brief(holdings: list[dict], brief: str) -> None:
    if not brief:
        return
    _set_row(
        WEEKLY_BRIEF_TICKER,
        "weekly_brief",
        json.dumps({"brief": brief}),
        _portfolio_brief_cache_key(holdings),
    )
