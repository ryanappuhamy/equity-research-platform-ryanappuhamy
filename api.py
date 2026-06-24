"""
FastAPI wrapper for the equity research backend.
"""

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import ai_report
import alerts
import main
import portfolio
import portfolio_risk
from database import init_db

app = FastAPI(
    title="Equity Research API",
    description="Single-ticker research pipeline, portfolio tracking, and risk analysis",
    version="1.5",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


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


@app.get("/report/{ticker}")
def get_report(ticker: str, peers: Optional[str] = None):
    """Run the full single-ticker pipeline and return JSON."""
    manual_peers = [p.strip() for p in peers.split(",") if p.strip()] if peers else None
    try:
        result = main.run_pipeline(ticker, manual_peers=manual_peers, save_files=False)
        return result
    except Exception as e:
        print(f"[error] API /report/{ticker} failed: {e}")
        return {
            "ticker": ticker.upper(),
            "available": False,
            "note": str(e),
            "report": ai_report._template_report({"ticker": ticker.upper()}, reason=str(e)),
            "data": {},
        }


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
def portfolio_brief():
    """Generate AI weekly brief for all portfolio holdings."""
    try:
        holdings = portfolio.update_prices()
        if not holdings:
            raise HTTPException(status_code=404, detail="No portfolio saved")
        brief = ai_report.generate_portfolio_brief(holdings)
        return {"portfolio": holdings, "brief": brief}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[error] API GET /portfolio/brief failed: {e}")
        return {
            "portfolio": [],
            "brief": ai_report._portfolio_brief_template([], reason=str(e)),
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
