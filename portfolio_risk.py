"""
Portfolio risk decomposition using 1 year of daily price history (yfinance).
"""

import numpy as np
import pandas as pd
import yfinance as yf


def _download_returns(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    """Daily simple returns for tickers, columns = tickers. Returns empty DataFrame on failure."""
    if not tickers:
        return pd.DataFrame()

    try:
        data = yf.download(
            tickers,
            period=period,
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
        if data.empty:
            print(f"[error] yfinance returned empty price history for {tickers}")
            return pd.DataFrame()

        closes = {}
        for t in tickers:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    closes[t] = data[t]["Close"]
                else:
                    closes[t] = data["Close"]
            except (KeyError, TypeError) as e:
                print(f"[error] yfinance: no history for {t}: {e}")
                continue

        if not closes:
            print(f"[error] yfinance: no valid closing prices for {tickers}")
            return pd.DataFrame()

        prices = pd.DataFrame(closes).dropna(how="all")
        if prices.empty:
            print(f"[error] yfinance: empty price frame after cleaning for {tickers}")
            return pd.DataFrame()

        return prices.pct_change().dropna()
    except Exception as e:
        print(f"[error] yfinance return download failed for {tickers}: {e}")
        return pd.DataFrame()


def _portfolio_weights(positions: list[dict]) -> pd.Series:
    """Weight by current market value."""
    weights = {}
    total = sum(p.get("market_value") or 0 for p in positions)
    if total <= 0:
        n = len(positions)
        return pd.Series({p["ticker"]: 1 / n for p in positions})
    for p in positions:
        weights[p["ticker"]] = (p.get("market_value") or 0) / total
    return pd.Series(weights)


def _annualized_vol(cov: pd.DataFrame, weights: pd.Series) -> float:
    """cov is annualized (daily cov × 252)."""
    w = weights.reindex(cov.index).fillna(0).values
    var = w @ cov.values @ w
    return float(np.sqrt(max(var, 0)))


def _risk_contributions(cov: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Each holding's contribution to portfolio variance (sums to total variance)."""
    w = weights.reindex(cov.index).fillna(0)
    port_var = w.values @ cov.values @ w.values
    if port_var <= 0:
        return pd.Series(0.0, index=cov.index)
    marginal = cov.values @ w.values
    contrib = w.values * marginal
    return pd.Series(contrib / port_var, index=cov.index)


def _download_benchmark_returns(benchmark: str, period: str = "1y") -> pd.Series:
    try:
        data = yf.download(benchmark, period=period, auto_adjust=True, progress=False)
        if data.empty or "Close" not in data.columns:
            print(f"[error] yfinance returned empty data for benchmark {benchmark}")
            return pd.Series(dtype=float)
        return data["Close"].pct_change().dropna()
    except Exception as e:
        print(f"[error] yfinance benchmark download failed for {benchmark}: {e}")
        return pd.Series(dtype=float)


def _betas(returns: pd.DataFrame, benchmark: str = "SPY") -> pd.Series:
    try:
        tickers = [c for c in returns.columns if c != benchmark]
        if benchmark not in returns.columns:
            bench_ret = _download_benchmark_returns(benchmark)
            if bench_ret.empty:
                print(f"[error] Cannot compute beta — no {benchmark} data")
                return pd.Series(1.0, index=tickers)
            returns = returns.copy()
            returns[benchmark] = bench_ret.reindex(returns.index).fillna(0)

        bench_var = returns[benchmark].var()
        if bench_var == 0:
            return pd.Series(1.0, index=tickers)
        return pd.Series(
            {t: returns[t].cov(returns[benchmark]) / bench_var for t in tickers}
        )
    except Exception as e:
        print(f"[error] Beta calculation failed: {e}")
        return pd.Series(1.0, index=[c for c in returns.columns if c != benchmark])


def _rate_sensitivity(returns: pd.DataFrame, rate_proxy: str = "TLT") -> pd.Series:
    """Sensitivity to rate moves via TLT (bond ETF) daily returns."""
    try:
        tickers = list(returns.columns)
        if rate_proxy not in returns.columns:
            tlt_ret = _download_benchmark_returns(rate_proxy)
            if tlt_ret.empty:
                print(f"[error] Cannot compute rate sensitivity — no {rate_proxy} data")
                return pd.Series(-0.5, index=tickers)
            returns = returns.copy()
            returns[rate_proxy] = tlt_ret.reindex(returns.index).fillna(0)

        tlt_var = returns[rate_proxy].var()
        if tlt_var == 0:
            return pd.Series(-0.5, index=tickers)
        return pd.Series(
            {t: returns[t].cov(returns[rate_proxy]) / tlt_var for t in tickers}
        )
    except Exception as e:
        print(f"[error] Rate sensitivity calculation failed: {e}")
        return pd.Series(-0.5, index=list(returns.columns))


def analyze_portfolio_risk(positions: list[dict]) -> dict:
    """
    Risk decomposition for a portfolio (from portfolio.get_portfolio output).

    Returns correlation matrix, portfolio vol, per-holding risk contribution,
    and simple scenario analysis.
    """
    if not positions:
        return {"available": False, "note": "Empty portfolio"}

    try:
        tickers = [p["ticker"] for p in positions]
        weights = _portfolio_weights(positions)

        returns = _download_returns(tickers)
        if returns.empty:
            note = "No price history returned for portfolio holdings"
            print(f"[error] {note}")
            return {"available": False, "note": note}

        valid = [t for t in tickers if t in returns.columns]
        if len(valid) < 1:
            note = "Insufficient price history for holdings"
            print(f"[error] {note}")
            return {"available": False, "note": note}

        returns = returns[valid]
        weights = weights.reindex(valid).fillna(0)
        if weights.sum() > 0:
            weights = weights / weights.sum()

        cov = returns.cov() * 252
        corr = returns.corr()
        port_vol = _annualized_vol(cov, weights)
        risk_pct = _risk_contributions(cov, weights)

        betas = _betas(returns)
        rate_sens = _rate_sensitivity(returns)

        market_shock = -0.20
        tlt_move_for_1pct_rate = -0.04
        scenario_market = float(sum(weights[t] * betas.get(t, 1.0) * market_shock for t in valid))
        scenario_rates = float(
            sum(weights[t] * rate_sens.get(t, -0.5) * tlt_move_for_1pct_rate for t in valid)
        )

        holdings_risk = []
        for t in valid:
            holdings_risk.append(
                {
                    "ticker": t,
                    "weight": round(float(weights[t]), 4),
                    "annualized_volatility": round(float(returns[t].std() * np.sqrt(252)), 4),
                    "beta_vs_spy": round(float(betas.get(t, 1.0)), 3),
                    "risk_contribution_pct": round(float(risk_pct.get(t, 0)), 4),
                    "rate_sensitivity": round(float(rate_sens.get(t, -0.5)), 3),
                }
            )

        return {
            "available": True,
            "lookback": "1y",
            "holdings_count": len(valid),
            "portfolio_annualized_volatility": round(port_vol, 4),
            "correlation_matrix": corr.round(4).to_dict(),
            "holdings_risk": holdings_risk,
            "scenarios": {
                "market_down_20pct": {
                    "estimated_portfolio_return": round(scenario_market, 4),
                    "description": "Weighted beta × -20% market move",
                },
                "rates_up_1pct": {
                    "estimated_portfolio_return": round(scenario_rates, 4),
                    "description": "Rate sensitivity via TLT covariance proxy",
                },
            },
        }
    except Exception as e:
        note = f"Portfolio risk analysis failed: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
