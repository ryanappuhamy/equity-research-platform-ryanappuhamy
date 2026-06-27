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


def _extract_closes(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    closes: dict[str, pd.Series] = {}
    for ticker in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                closes[ticker] = data[ticker]["Close"]
            else:
                closes[ticker] = data["Close"]
        except (KeyError, TypeError) as e:
            print(f"[warn] yfinance: no close prices for {ticker}: {e}")
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).sort_index()


def _download_closes(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    try:
        data = yf_download(
            tickers,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
        if data.empty:
            print(f"[error] yfinance returned empty price history for {tickers}")
            return pd.DataFrame()
        return _extract_closes(data, tickers)
    except Exception as e:
        print(f"[error] yfinance price history failed for {tickers}: {e}")
        return pd.DataFrame()


def _merge_series(nav: pd.Series, benchmark: pd.Series) -> list[dict]:
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

    download_tickers = list(dict.fromkeys(tickers + [benchmark]))
    prices = _download_closes(download_tickers, start, end)
    if prices.empty:
        return {"available": False, "note": "No price history returned for portfolio holdings"}

    valid = [t for t in tickers if t in prices.columns]
    if not valid:
        return {"available": False, "note": "No price history for portfolio tickers"}

    holding_prices = prices[valid].ffill()
    shares_series = pd.Series({t: shares_map[t] for t in valid})
    nav = (holding_prices * shares_series).sum(axis=1).dropna()
    if len(nav) < 2:
        return {"available": False, "note": "Insufficient NAV history"}

    if benchmark not in prices.columns:
        return {"available": False, "note": f"No {benchmark} benchmark data"}

    benchmark_prices = prices[benchmark].reindex(nav.index).ffill()
    if benchmark_prices.isna().any():
        return {"available": False, "note": f"Incomplete {benchmark} benchmark data"}

    benchmark_normalized = benchmark_prices / float(benchmark_prices.iloc[0]) * float(nav.iloc[0])
    metrics = _compute_metrics(nav)

    result = {
        "available": True,
        "from_cache": False,
        "start_date": nav.index[0].strftime("%Y-%m-%d"),
        "end_date": nav.index[-1].strftime("%Y-%m-%d"),
        "benchmark_ticker": benchmark,
        "benchmark": benchmark,
        "series": _merge_series(nav, benchmark_normalized),
        "metrics": metrics,
        **metrics,
    }

    cache_payload = {k: v for k, v in result.items() if k != "from_cache"}
    market_cache.set_portfolio_performance(positions, cache_payload, benchmark)
    return result


@router.get("/performance")
def get_portfolio_performance(
    benchmark: str = Query(default=DEFAULT_BENCHMARK),
):
    """Return daily portfolio NAV vs benchmark with performance metrics (cached 24h per benchmark)."""
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
