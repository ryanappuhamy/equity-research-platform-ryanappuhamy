"""
FastAPI wrapper for the equity research backend.
"""

import logging
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import ai_report
import alerts
import main
import portfolio
import portfolio_performance
import portfolio_risk
import market_cache
from database import init_db
from yfinance_client import yf_last_price

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Equity Research API",
    description="Single-ticker research pipeline, portfolio tracking, and risk analysis",
    version="1.5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://equity-research-frontend-lilac.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio_performance.router)


@app.on_event("startup")
def _startup() -> None:
    try:
        init_db()
    except Exception:
        logger.exception("Database initialization failed; starting without database")


class PositionInput(BaseModel):
    ticker: str
    shares: float = Field(gt=0)
    avg_cost_price: float = Field(gt=0)


class PortfolioInput(BaseModel):
    positions: list[PositionInput]


class AlertInput(BaseModel):
    ticker: str
    metric: str
    operator: str
    threshold: float


def _inject_live_price(ticker: str, report: dict) -> dict:
    """Refresh last_price on a cached report without re-running the pipeline."""
    try:
        live = round(float(yf_last_price(ticker)), 2)
        data = report.setdefault("data", {})
        price_stats = dict(data.get("price_stats") or {})
        price_stats["last_price"] = live
        price_stats["available"] = True
        data["price_stats"] = price_stats
    except Exception as e:
        print(f"[warn] live price injection failed for {ticker}: {e}")
    return report


@app.get("/report/{ticker}")
def get_report(ticker: str, peers: Optional[str] = None):
    """Run the full single-ticker pipeline and return JSON (cached 24h)."""
    ticker = ticker.upper()
    manual_peers = [p.strip().upper() for p in peers.split(",") if p.strip()] if peers else None
    try:
        cached = market_cache.get_report(ticker, manual_peers)
        if cached is not None:
            return _inject_live_price(ticker, cached)

        result = main.run_pipeline(ticker, manual_peers=manual_peers, save_files=False)
        market_cache.set_report(ticker, result, manual_peers)
        return result
    except Exception as e:
        print(f"[error] API /report/{ticker} failed: {e}")
        return {
            "ticker": ticker,
            "available": False,
            "note": str(e),
            "report": ai_report._template_report({"ticker": ticker}, reason=str(e)),
            "data": {},
        }


@app.delete("/report/{ticker}/cache")
def delete_report_cache_endpoint(
    ticker: str,
    x_force_password: Optional[str] = Header(default=None, alias="X-Force-Password"),
):
    """Delete cached report JSON for a ticker from Supabase."""
    if x_force_password != "ExtraPls":
        raise HTTPException(status_code=401, detail="Unauthorized")
    ticker = ticker.upper()
    count = market_cache.delete_report_cache(ticker)
    return {"deleted": True, "ticker": ticker, "count": count}


@app.post("/portfolio")
def save_portfolio(body: PortfolioInput):
    """Save a portfolio to the database (replaces existing default portfolio)."""
    try:
        positions = [p.model_dump() for p in body.positions]
        saved = portfolio.replace_portfolio(positions)
        if not saved and positions:
            return {"available": False, "note": "Failed to save portfolio", "positions": [], "count": 0}
        return {"positions": saved, "count": len(saved)}
    except Exception as e:
        print(f"[error] API POST /portfolio failed: {e}")
        return {"available": False, "note": str(e), "positions": [], "count": 0}


@app.get("/portfolio")
def get_portfolio():
    """Return saved portfolio positions with live prices, P&L, and weights."""
    try:
        holdings = portfolio.update_prices()
        return {"positions": holdings, "count": len(holdings)}
    except Exception as e:
        print(f"[error] API GET /portfolio failed: {e}")
        return {"available": False, "note": str(e), "positions": [], "count": 0}


@app.get("/portfolio/analysis")
def portfolio_analysis():
    """Return risk decomposition for the saved portfolio."""
    try:
        holdings = portfolio.update_prices()
        if not holdings:
            raise HTTPException(status_code=404, detail="No portfolio saved")
        risk = portfolio_risk.analyze_portfolio_risk(holdings)
        return {"portfolio": holdings, "risk": risk}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[error] API GET /portfolio/analysis failed: {e}")
        return {
            "portfolio": [],
            "risk": {"available": False, "note": str(e)},
        }


@app.get("/portfolio/brief")
def portfolio_brief(
    force: bool = False,
    x_force_password: Optional[str] = Header(default=None, alias="X-Force-Password"),
):
    """Return AI weekly brief for portfolio holdings (cached up to 7 days)."""
    try:
        holdings = portfolio.update_prices()
        if not holdings:
            raise HTTPException(status_code=404, detail="No portfolio saved")

        force_bypass = force and x_force_password == "ExtraPls"
        cached_brief, fetched_at = market_cache.get_weekly_brief(holdings)

        if not market_cache.should_regenerate_weekly_brief(fetched_at, force_bypass):
            return {
                "portfolio": holdings,
                "brief": cached_brief,
                "from_cache": True,
                "cached_at": fetched_at.isoformat() if fetched_at else None,
            }

        brief = ai_report.generate_portfolio_brief(holdings)
        market_cache.set_weekly_brief(holdings, brief)
        return {
            "portfolio": holdings,
            "brief": brief,
            "from_cache": False,
            "cached_at": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[error] API GET /portfolio/brief failed: {e}")
        return {
            "portfolio": [],
            "brief": ai_report._portfolio_brief_template([], reason=str(e)),
            "from_cache": False,
        }


@app.post("/alerts")
def create_alert(body: AlertInput):
    """Add a new alert rule."""
    try:
        result = alerts.add_alert(
            ticker=body.ticker,
            metric=body.metric,
            operator=body.operator,
            threshold=body.threshold,
        )
        if result.get("available") is False:
            return result
        return {"alert": result}
    except Exception as e:
        print(f"[error] API POST /alerts failed: {e}")
        return {"available": False, "note": str(e)}


@app.get("/alerts")
def list_alerts():
    """Return all saved alert rules."""
    try:
        rules = alerts.get_alerts()
        return {"alerts": rules, "count": len(rules)}
    except Exception as e:
        print(f"[error] API GET /alerts failed: {e}")
        return {"available": False, "note": str(e), "alerts": [], "count": 0}


@app.delete("/alerts/{alert_id}")
def remove_alert(alert_id: int):
    """Delete an alert rule by ID."""
    try:
        result = alerts.delete_alert(alert_id)
        if not result.get("deleted"):
            return result
        return {"deleted": True, "id": alert_id}
    except Exception as e:
        print(f"[error] API DELETE /alerts/{alert_id} failed: {e}")
        return {"deleted": False, "note": str(e)}


@app.get("/alerts/check")
def check_alerts_endpoint():
    """Check all alert rules against current portfolio data."""
    try:
        holdings = portfolio.update_prices()
        tickers = [h["ticker"] for h in holdings]
        if not tickers:
            return {"triggered": [], "count": 0, "note": "No portfolio saved"}
        triggered = alerts.check_alerts(tickers)
        return {"triggered": triggered, "count": len(triggered)}
    except Exception as e:
        print(f"[error] API GET /alerts/check failed: {e}")
        return {"triggered": [], "count": 0, "note": str(e)}
