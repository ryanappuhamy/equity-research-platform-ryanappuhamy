"""
Portfolio historical performance — daily NAV, benchmark comparison, and summary metrics.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

import config
import market_cache
from portfolio import DEFAULT_PORTFOLIO_NAME, get_position_rows
from yfinance_client import yf_download

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

DEFAULT_BENCHMARK = "SPY"
ALLOWED_BENCHMARKS = frozenset({"SPY", "QQQ", "SOXX", "VTI"})
MAX_LOOKBACK_DAYS = config.PRICE_LOOKBACK_YEARS * 365


def _to_utc_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _start_date(positions: list[dict]) -> date:
    today = datetime.now(timezone.utc).date()
    max_lookback = today - timedelta(days=MAX_LOOKBACK_DAYS)
    timestamps = [_to_utc_dt(p.get("updated_at")) for p in positions]
    timestamps = [ts for ts in timestamps if ts is not None]
    if not timestamps:
        return max_lookback
    earliest = min(timestamps).date()
    return max(max_lookback, earliest)


def _extract_single_close(data: pd.DataFrame, ticker: str) -> pd.Series | None:
    try:
        if isinstance(data.columns, pd.MultiIndex):
            close = data[ticker]["Close"]
        else:
            close = data["Close"]
        close = close.dropna()
        return close if not close.empty else None
    except (KeyError, TypeError) as e:
        print(f"[warn] yfinance: no close prices for {ticker}: {e}")
        return None


def _trim_closes(series: pd.Series, start: date, end: date) -> pd.Series | None:
    if series.empty:
        return None
    idx = pd.to_datetime(series.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    series = series.copy()
    series.index = idx.normalize()
    trimmed = series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
    trimmed = trimmed.dropna()
    return trimmed if not trimmed.empty else None


def _closes_from_cache(ticker: str, start: date, end: date) -> pd.Series | None:
    df = market_cache.get_price_history_stale(ticker, config.PRICE_LOOKBACK_YEARS)
    if df is None or df.empty or "Close" not in df.columns:
        return None
    return _trim_closes(df["Close"], start, end)


def _download_ticker_closes(ticker: str, start: date, end: date) -> pd.Series | None:
    try:
        data = yf_download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
        )
        if data.empty:
            print(f"[warn] yfinance returned empty price history for {ticker}")
            return None
        closes = _extract_single_close(data, ticker)
        return _trim_closes(closes, start, end) if closes is not None else None
    except Exception as e:
        print(f"[warn] yfinance price history failed for {ticker}: {e}")
        return None


def _get_ticker_closes(ticker: str, start: date, end: date) -> pd.Series | None:
    closes = _download_ticker_closes(ticker, start, end)
    if closes is not None:
        return closes

    cached = _closes_from_cache(ticker, start, end)
    if cached is not None:
        print(f"[info] using cached price history for {ticker} after yfinance failure")
        return cached

    print(f"[warn] no live or cached price history for {ticker}, skipping")
    return None


def _download_closes(
    tickers: list[str], start: date, end: date
) -> tuple[pd.DataFrame, list[str]]:
    """Download each ticker independently; fall back to cache; skip remaining failures."""
    if not tickers:
        return pd.DataFrame(), []

    closes: dict[str, pd.Series] = {}
    missing: list[str] = []
    for ticker in tickers:
        series = _get_ticker_closes(ticker, start, end)
        if series is None:
            missing.append(ticker)
        else:
            closes[ticker] = series

    if not closes:
        return pd.DataFrame(), missing

    return pd.DataFrame(closes).sort_index(), missing


def _merge_series(nav: pd.Series, benchmark: pd.Series | None) -> list[dict]:
    if benchmark is None:
        return [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "nav": round(float(val), 2),
            }
            for idx, val in nav.items()
            if pd.notna(val)
        ]

    merged = pd.DataFrame({"nav": nav, "benchmark": benchmark}).dropna()
    return [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "nav": round(float(row.nav), 2),
            "benchmark": round(float(row.benchmark), 2),
        }
        for idx, row in merged.iterrows()
    ]


def _compute_metrics(nav: pd.Series) -> dict:
    if len(nav) < 2:
        return {
            "sharpe_ratio": None,
            "max_drawdown": None,
            "total_return_pct": None,
        }

    total_return_pct = round((float(nav.iloc[-1]) / float(nav.iloc[0]) - 1) * 100, 2)

    running_max = nav.cummax()
    drawdown = (nav - running_max) / running_max
    max_drawdown = round(float(drawdown.min()), 4)

    daily_returns = nav.pct_change().dropna()
    if daily_returns.empty or daily_returns.std() == 0:
        sharpe_ratio = None
    else:
        ann_return = float(daily_returns.mean() * 252)
        ann_vol = float(daily_returns.std() * np.sqrt(252))
        sharpe_ratio = round((ann_return - config.RISK_FREE_RATE) / ann_vol, 3)

    return {
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_drawdown,
        "total_return_pct": total_return_pct,
    }


def compute_portfolio_performance(
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
    benchmark: str = DEFAULT_BENCHMARK,
) -> dict:
    """Daily NAV vs a benchmark ETF with Sharpe, max drawdown, and total return."""
    benchmark = benchmark.strip().upper()
    if benchmark not in ALLOWED_BENCHMARKS:
        return {
            "available": False,
            "note": f"benchmark must be one of {sorted(ALLOWED_BENCHMARKS)}",
        }

    positions = get_position_rows(portfolio_name)
    if not positions:
        return {"available": False, "note": "No portfolio saved"}

    cached = market_cache.get_portfolio_performance(positions, benchmark)
    if cached is not None:
        result = dict(cached)
        result["from_cache"] = True
        return result

    shares_map = {p["ticker"].upper(): float(p["shares"]) for p in positions}
    tickers = list(shares_map.keys())
    start = _start_date(positions)
    end = datetime.now(timezone.utc).date()

    prices, missing_holdings = _download_closes(tickers, start, end)
    valid = [t for t in tickers if t in prices.columns]
    if not valid:
        note = "No price history for portfolio tickers"
        if missing_holdings:
            note = f"{note}: {', '.join(sorted(missing_holdings))}"
        return {"available": False, "note": note, "missing_tickers": sorted(missing_holdings)}

    holding_prices = prices[valid].ffill()
    shares_series = pd.Series({t: shares_map[t] for t in valid})
    nav = (holding_prices * shares_series).sum(axis=1).dropna()
    if len(nav) < 2:
        return {"available": False, "note": "Insufficient NAV history"}

    benchmark_closes = _get_ticker_closes(benchmark, start, end)
    missing_tickers = list(missing_holdings)
    benchmark_normalized = None
    benchmark_note = None

    if benchmark_closes is None:
        missing_tickers.append(benchmark)
        benchmark_note = f"{benchmark} benchmark unavailable"
    else:
        benchmark_prices = benchmark_closes.reindex(nav.index).ffill()
        if benchmark_prices.isna().any():
            missing_tickers.append(benchmark)
            benchmark_note = f"Incomplete {benchmark} benchmark data"
        else:
            benchmark_normalized = (
                benchmark_prices / float(benchmark_prices.iloc[0]) * float(nav.iloc[0])
            )

    metrics = _compute_metrics(nav)

    notes: list[str] = []
    if missing_holdings:
        notes.append(
            f"Partial data — missing price history for: {', '.join(sorted(missing_holdings))}"
        )
    if benchmark_note:
        notes.append(benchmark_note)

    result = {
        "available": True,
        "from_cache": False,
        "start_date": nav.index[0].strftime("%Y-%m-%d"),
        "end_date": nav.index[-1].strftime("%Y-%m-%d"),
        "benchmark_ticker": benchmark,
        "benchmark": benchmark,
        "benchmark_available": benchmark_normalized is not None,
        "series": _merge_series(nav, benchmark_normalized),
        "metrics": metrics,
        "partial": bool(notes),
        "missing_tickers": sorted(set(missing_tickers)),
        **metrics,
    }
    if notes:
        result["note"] = "; ".join(notes)

    cache_payload = {k: v for k, v in result.items() if k != "from_cache"}
    market_cache.set_portfolio_performance(positions, cache_payload, benchmark)
    return result


@router.get("/performance")
def get_portfolio_performance(
    benchmark: str = Query(default=DEFAULT_BENCHMARK),
):
    """Return daily portfolio NAV vs benchmark with performance metrics (cached 7 days per benchmark)."""
    try:
        result = compute_portfolio_performance(benchmark=benchmark)
        if not result.get("available"):
            note = result.get("note", "")
            if note.startswith("benchmark must be"):
                raise HTTPException(status_code=400, detail=note)
        return result
    except HTTPException:
        raise
    except Exception as e:
        print(f"[error] API GET /portfolio/performance failed: {e}")
        return {"available": False, "note": str(e), "series": []}
