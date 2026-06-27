"""
Cache fundamentals and price data in the shared DB (Supabase / SQLite).

Fundamentals, reports, SEC insider activity, Finnhub consensus, and FRED macro
are cached for 24 hours; earnings transcripts for 7 days; price data for 30 minutes;
weekly portfolio briefs for 7 days; sector lookups for 30 days; portfolio performance for 7 days.
"""

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from database import Base, get_session

PRICE_CACHE_TTL_SECONDS = 30 * 60
FUNDAMENTALS_CACHE_TTL_SECONDS = 24 * 60 * 60
REPORT_CACHE_TTL_SECONDS = 24 * 60 * 60
WEEKLY_BRIEF_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
INSIDER_ACTIVITY_CACHE_TTL_SECONDS = 24 * 60 * 60
ANALYST_CONSENSUS_CACHE_TTL_SECONDS = 24 * 60 * 60
EARNINGS_TRANSCRIPT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MACRO_DATA_CACHE_TTL_SECONDS = 24 * 60 * 60
SECTOR_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
PORTFOLIO_PERFORMANCE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
WEEKLY_BRIEF_TICKER = "_portfolio"
GLOBAL_CACHE_TICKER = "_global"


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


def _normalize_sector(sector: str | None) -> str | None:
    if not sector or not isinstance(sector, str):
        return None
    sector = sector.strip()
    return sector.title() if sector else None


def get_fundamentals_sectors(tickers: list[str]) -> dict[str, str | None]:
    """Return sector from cached fundamentals payloads (ignores TTL). Missing cache → None."""
    if not tickers:
        return {}
    tickers = [t.upper() for t in tickers]
    sectors: dict[str, str | None] = {t: None for t in tickers}
    try:
        with get_session() as db:
            rows = (
                db.query(MarketDataCache)
                .filter(
                    MarketDataCache.ticker.in_(tickers),
                    MarketDataCache.data_type == "fundamentals",
                    MarketDataCache.cache_key == "",
                )
                .all()
            )
            for row in rows:
                try:
                    payload = json.loads(row.payload)
                    sectors[row.ticker] = _normalize_sector(payload.get("sector"))
                except json.JSONDecodeError as e:
                    print(f"[warn] market cache corrupt fundamentals for {row.ticker}: {e}")
    except Exception as e:
        print(f"[warn] market cache sector lookup failed: {e}")
    return sectors


def get_sectors(tickers: list[str]) -> dict[str, str | None]:
    """Return sector from dedicated sector cache rows (30-day TTL). Missing/stale → None."""
    if not tickers:
        return {}
    tickers = [t.upper() for t in tickers]
    sectors: dict[str, str | None] = {t: None for t in tickers}
    try:
        with get_session() as db:
            rows = (
                db.query(MarketDataCache)
                .filter(
                    MarketDataCache.ticker.in_(tickers),
                    MarketDataCache.data_type == "sector",
                    MarketDataCache.cache_key == "",
                )
                .all()
            )
            for row in rows:
                if not _is_fresh(row.fetched_at, SECTOR_CACHE_TTL_SECONDS):
                    continue
                try:
                    payload = json.loads(row.payload)
                    sectors[row.ticker] = _normalize_sector(payload.get("sector"))
                except json.JSONDecodeError as e:
                    print(f"[warn] market cache corrupt sector for {row.ticker}: {e}")
    except Exception as e:
        print(f"[warn] market cache sector read failed: {e}")
    return sectors


def set_sector(ticker: str, sector: str) -> str | None:
    normalized = _normalize_sector(sector)
    if not normalized:
        return None
    _set_row(ticker, "sector", json.dumps({"sector": normalized}))
    return normalized


def _get_json_cache(
    ticker: str,
    data_type: str,
    ttl_seconds: int,
    cache_key: str = "",
) -> dict | None:
    row = _get_row(ticker, data_type, cache_key)
    if row and _is_fresh(row.fetched_at, ttl_seconds):
        try:
            return json.loads(row.payload)
        except json.JSONDecodeError as e:
            print(f"[warn] market cache corrupt {data_type} for {ticker}: {e}")
    return None


def _set_json_cache(ticker: str, data_type: str, data: dict, cache_key: str = "") -> None:
    _set_row(ticker, data_type, json.dumps(data), cache_key)


def get_insider_activity(ticker: str, cache_key: str = "") -> dict | None:
    return _get_json_cache(ticker, "insider_activity", INSIDER_ACTIVITY_CACHE_TTL_SECONDS, cache_key)


def set_insider_activity(ticker: str, data: dict, cache_key: str = "") -> None:
    _set_json_cache(ticker, "insider_activity", data, cache_key)


def get_analyst_consensus(ticker: str) -> dict | None:
    return _get_json_cache(ticker, "analyst_consensus", ANALYST_CONSENSUS_CACHE_TTL_SECONDS)


def set_analyst_consensus(ticker: str, data: dict) -> None:
    _set_json_cache(ticker, "analyst_consensus", data)


def get_earnings_transcript(ticker: str, cache_key: str = "") -> dict | None:
    return _get_json_cache(
        ticker, "earnings_transcript", EARNINGS_TRANSCRIPT_CACHE_TTL_SECONDS, cache_key
    )


def set_earnings_transcript(ticker: str, data: dict, cache_key: str = "") -> None:
    _set_json_cache(ticker, "earnings_transcript", data, cache_key)


def get_macro_data() -> dict | None:
    return _get_json_cache(GLOBAL_CACHE_TICKER, "macro_data", MACRO_DATA_CACHE_TTL_SECONDS)


def set_macro_data(data: dict) -> None:
    _set_json_cache(GLOBAL_CACHE_TICKER, "macro_data", data)


def _report_cache_key(peers: list[str] | None) -> str:
    if not peers:
        return ""
    return ",".join(sorted(p.upper() for p in peers))


def get_report(ticker: str, peers: list[str] | None = None) -> dict | None:
    row = _get_row(ticker, "report", _report_cache_key(peers))
    if row and _is_fresh(row.fetched_at, REPORT_CACHE_TTL_SECONDS):
        try:
            return json.loads(row.payload)
        except json.JSONDecodeError as e:
            print(f"[warn] market cache corrupt report for {ticker}: {e}")
    return None


def set_report(ticker: str, result: dict, peers: list[str] | None = None) -> None:
    _set_row(ticker, "report", json.dumps(result), _report_cache_key(peers))


def delete_report_cache(ticker: str) -> int:
    """Delete all cached report rows for a ticker (any peers cache_key)."""
    try:
        ticker = ticker.upper()
        with get_session() as db:
            count = (
                db.query(MarketDataCache)
                .filter_by(ticker=ticker, data_type="report")
                .delete()
            )
            db.commit()
            return count
    except Exception as e:
        print(f"[warn] report cache delete failed for {ticker}: {e}")
        return 0


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


def get_price_history_stale(ticker: str, years: int) -> pd.DataFrame | None:
    """Return cached price history regardless of TTL (fallback when yfinance is rate-limited)."""
    ticker = ticker.upper()
    cache_key = f"{years}y"
    row = _get_row(ticker, "price_history", cache_key)
    if row is None:
        try:
            with get_session() as db:
                row = (
                    db.query(MarketDataCache)
                    .filter_by(ticker=ticker, data_type="price_history")
                    .order_by(MarketDataCache.fetched_at.desc())
                    .first()
                )
        except Exception as e:
            print(f"[warn] market cache stale price history read failed for {ticker}: {e}")
            return None
    if not row:
        return None
    try:
        return pd.read_json(StringIO(row.payload), orient="split")
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[warn] market cache corrupt price history for {ticker}: {e}")
        return None


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


def _portfolio_holdings_cache_key(holdings: list[dict]) -> str:
    parts = []
    for h in sorted(holdings, key=lambda x: x["ticker"].upper()):
        shares = h.get("shares", 0)
        parts.append(f"{h['ticker'].upper()}:{shares}")
    return ",".join(parts) or "empty"


def get_portfolio_performance(holdings: list[dict], benchmark: str = "SPY") -> dict | None:
    row = _get_row(
        WEEKLY_BRIEF_TICKER,
        "portfolio_performance",
        _portfolio_performance_cache_key(holdings, benchmark),
    )
    if row and _is_fresh(row.fetched_at, PORTFOLIO_PERFORMANCE_CACHE_TTL_SECONDS):
        try:
            return json.loads(row.payload)
        except json.JSONDecodeError as e:
            print(f"[warn] market cache corrupt portfolio performance: {e}")
    return None


def set_portfolio_performance(
    holdings: list[dict],
    data: dict,
    benchmark: str = "SPY",
) -> None:
    if not data.get("available"):
        return
    _set_row(
        WEEKLY_BRIEF_TICKER,
        "portfolio_performance",
        json.dumps(data),
        _portfolio_performance_cache_key(holdings, benchmark),
    )


def _portfolio_performance_cache_key(holdings: list[dict], benchmark: str) -> str:
    return f"{_portfolio_holdings_cache_key(holdings)}|{benchmark.upper()}"
