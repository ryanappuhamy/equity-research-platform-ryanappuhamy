"""
User-defined alert rules — PostgreSQL (Supabase) or SQLite storage and condition checking.

Alerts fire when a metric crosses a threshold for tickers in the portfolio.
check_alerts runs the full research pipeline per ticker to collect current data.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.orm import Session

from database import Base, get_session, init_db
import main

VALID_METRICS = frozenset({"pe_ttm", "price", "revenue_growth_yoy", "insider_filings"})
VALID_OPERATORS = frozenset({"above", "below"})


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(16), nullable=False)
    metric = Column(String(32), nullable=False)
    operator = Column(String(16), nullable=False)
    threshold = Column(Float, nullable=False)
    triggered = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def _session() -> Session:
    return get_session()


def _alert_to_dict(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "ticker": alert.ticker,
        "metric": alert.metric,
        "operator": alert.operator,
        "threshold": alert.threshold,
        "triggered": alert.triggered,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "updated_at": alert.updated_at.isoformat() if alert.updated_at else None,
    }


def _validate_metric(metric: str) -> str:
    metric = metric.lower().strip()
    if metric not in VALID_METRICS:
        raise ValueError(f"Invalid metric '{metric}'. Must be one of: {', '.join(sorted(VALID_METRICS))}")
    return metric


def _validate_operator(operator: str) -> str:
    operator = operator.lower().strip()
    if operator not in VALID_OPERATORS:
        raise ValueError(f"Invalid operator '{operator}'. Must be one of: {', '.join(sorted(VALID_OPERATORS))}")
    return operator


def add_alert(ticker: str, metric: str, operator: str, threshold: float) -> dict:
    """Create a new alert rule, or return the existing one if duplicate."""
    try:
        ticker = ticker.upper().strip()
        metric = _validate_metric(metric)
        operator = _validate_operator(operator)
        threshold = float(threshold)

        with _session() as db:
            existing = (
                db.query(Alert)
                .filter_by(ticker=ticker, metric=metric, operator=operator, threshold=threshold)
                .first()
            )
            if existing:
                return _alert_to_dict(existing)

            alert = Alert(
                ticker=ticker,
                metric=metric,
                operator=operator,
                threshold=threshold,
                triggered=False,
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)
            return _alert_to_dict(alert)
    except ValueError as e:
        print(f"[error] Alerts: {e}")
        return {"available": False, "note": str(e)}
    except Exception as e:
        note = f"Failed to add alert for {ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}


def get_alerts() -> list[dict]:
    """Return all stored alert rules."""
    try:
        with _session() as db:
            rows = db.query(Alert).order_by(Alert.ticker, Alert.id).all()
            return [_alert_to_dict(a) for a in rows]
    except Exception as e:
        print(f"[error] Failed to load alerts: {e}")
        return []


def delete_alert(alert_id: int) -> dict:
    """Delete an alert rule by id."""
    try:
        with _session() as db:
            alert = db.query(Alert).filter_by(id=alert_id).first()
            if not alert:
                return {"deleted": False, "note": f"Alert {alert_id} not found"}
            db.delete(alert)
            db.commit()
            return {"deleted": True, "id": alert_id}
    except Exception as e:
        note = f"Failed to delete alert {alert_id}: {e}"
        print(f"[error] {note}")
        return {"deleted": False, "note": note}


def clear_alerts() -> dict:
    """Delete all alert rules from the database."""
    try:
        with _session() as db:
            count = db.query(Alert).delete()
            db.commit()
            return {"cleared": True, "count": count}
    except Exception as e:
        note = f"Failed to clear alerts: {e}"
        print(f"[error] {note}")
        return {"cleared": False, "note": note}


def _run_pipeline_for_ticker(ticker: str) -> dict:
    """Run the full single-ticker pipeline and return the data payload."""
    result = main.run_pipeline(ticker, save_files=False)
    return result.get("data") or {}


def _extract_metric(metric: str, data: dict) -> float | None:
    """Pull the current metric value from pipeline data."""
    try:
        if metric == "pe_ttm":
            fundamentals = data.get("fundamentals") or {}
            val = fundamentals.get("pe_ttm")
        elif metric == "revenue_growth_yoy":
            fundamentals = data.get("fundamentals") or {}
            val = fundamentals.get("revenue_growth_yoy")
        elif metric == "price":
            price_stats = data.get("price_stats") or {}
            if price_stats.get("available") is False:
                return None
            val = price_stats.get("last_price")
        elif metric == "insider_filings":
            insider = data.get("insider_activity") or {}
            if insider.get("available") is False:
                return None
            val = insider.get("form4_filings_last_6m")
        else:
            return None

        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError) as e:
        print(f"[error] Alerts: could not extract {metric}: {e}")
        return None


def _condition_met(operator: str, value: float, threshold: float) -> bool:
    if operator == "above":
        return value > threshold
    return value < threshold


def _build_explanation(ticker: str, metric: str, operator: str, value: float, threshold: float) -> str:
    op_word = "above" if operator == "above" else "below"
    return f"{ticker} {metric}={value:g} is {op_word} threshold {threshold:g}"


def _set_triggered(alert_id: int, triggered: bool) -> None:
    with _session() as db:
        alert = db.query(Alert).filter_by(id=alert_id).first()
        if alert:
            alert.triggered = triggered
            alert.updated_at = datetime.now(timezone.utc)
            db.commit()


def check_alerts(portfolio_tickers: list[str]) -> list[dict]:
    """
    Check all alert rules for portfolio tickers.

    Runs the full pipeline once per unique ticker, evaluates each rule,
    updates triggered state in the database, and returns fired alerts.
    """
    try:
        portfolio_set = {t.upper() for t in portfolio_tickers}
        rules = get_alerts()
        relevant = [r for r in rules if r["ticker"] in portfolio_set]

        if not relevant:
            return []

        pipeline_cache: dict[str, dict] = {}
        triggered: list[dict] = []

        for rule in relevant:
            ticker = rule["ticker"]
            if ticker not in pipeline_cache:
                print(f"Alerts: running pipeline for {ticker}")
                pipeline_cache[ticker] = _run_pipeline_for_ticker(ticker)

            value = _extract_metric(rule["metric"], pipeline_cache[ticker])
            if value is None:
                print(
                    f"[error] Alerts: no data for {ticker} metric {rule['metric']} — skipping rule {rule['id']}"
                )
                _set_triggered(rule["id"], False)
                continue

            is_triggered = _condition_met(rule["operator"], value, rule["threshold"])
            _set_triggered(rule["id"], is_triggered)

            if is_triggered:
                explanation = _build_explanation(
                    ticker, rule["metric"], rule["operator"], value, rule["threshold"]
                )
                triggered.append(
                    {
                        "alert_id": rule["id"],
                        "ticker": ticker,
                        "metric": rule["metric"],
                        "operator": rule["operator"],
                        "threshold": rule["threshold"],
                        "current_value": value,
                        "explanation": explanation,
                    }
                )

        return triggered
    except Exception as e:
        note = f"Alert check failed: {e}"
        print(f"[error] {note}")
        return []
