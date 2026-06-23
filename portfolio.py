"""
Portfolio tracker — SQLite storage for positions and live P&L.
"""

from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config

DEFAULT_PORTFOLIO_NAME = "default"


class Base(DeclarativeBase):
    pass


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_name = Column(String(64), nullable=False, default=DEFAULT_PORTFOLIO_NAME)
    ticker = Column(String(16), nullable=False)
    shares = Column(Float, nullable=False)
    avg_cost_price = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


engine = create_engine(f"sqlite:///{config.PORTFOLIO_DB}", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(engine)


def _session() -> Session:
    return SessionLocal()


def add_position(
    ticker: str,
    shares: float,
    avg_cost_price: float,
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
) -> dict:
    """Add or update a position (same ticker aggregates into one row)."""
    try:
        ticker = ticker.upper()
        with _session() as db:
            pos = (
                db.query(Position)
                .filter_by(portfolio_name=portfolio_name, ticker=ticker)
                .first()
            )
            if pos:
                total_cost = pos.shares * pos.avg_cost_price + shares * avg_cost_price
                pos.shares += shares
                pos.avg_cost_price = total_cost / pos.shares if pos.shares else avg_cost_price
                pos.updated_at = datetime.now(timezone.utc)
            else:
                pos = Position(
                    portfolio_name=portfolio_name,
                    ticker=ticker,
                    shares=shares,
                    avg_cost_price=avg_cost_price,
                )
                db.add(pos)
            db.commit()
            db.refresh(pos)
            return _position_to_dict(pos)
    except Exception as e:
        note = f"Failed to add position {ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}


def remove_position(
    ticker: str,
    shares: float | None = None,
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
) -> dict:
    """Remove shares from a position; delete row if shares go to zero."""
    try:
        ticker = ticker.upper()
        with _session() as db:
            pos = (
                db.query(Position)
                .filter_by(portfolio_name=portfolio_name, ticker=ticker)
                .first()
            )
            if not pos:
                return {"removed": False, "note": f"No position for {ticker}"}

            if shares is None or shares >= pos.shares:
                removed = pos.shares
                db.delete(pos)
                db.commit()
                return {"removed": True, "ticker": ticker, "shares_removed": removed}

            pos.shares -= shares
            pos.updated_at = datetime.now(timezone.utc)
            db.commit()
            return {"removed": True, "ticker": ticker, "shares_removed": shares, "shares_remaining": pos.shares}
    except Exception as e:
        note = f"Failed to remove position {ticker}: {e}"
        print(f"[error] {note}")
        return {"removed": False, "note": note}


def replace_portfolio(
    positions: list[dict],
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME,
) -> list[dict]:
    """Replace the entire portfolio with the given positions."""
    try:
        with _session() as db:
            db.query(Position).filter_by(portfolio_name=portfolio_name).delete()
            for p in positions:
                db.add(
                    Position(
                        portfolio_name=portfolio_name,
                        ticker=p["ticker"].upper(),
                        shares=float(p["shares"]),
                        avg_cost_price=float(p["avg_cost_price"]),
                    )
                )
            db.commit()

        return get_portfolio(portfolio_name=portfolio_name)
    except Exception as e:
        note = f"Failed to replace portfolio: {e}"
        print(f"[error] {note}")
        return []


def _fetch_prices(tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    try:
        data = yf.download(
            tickers,
            period="5d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
        if data.empty:
            print(f"[error] yfinance returned empty price data for {tickers}")
            return {}

        prices = {}
        for ticker in tickers:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    close = data[ticker]["Close"].dropna()
                else:
                    close = data["Close"].dropna()
                if not close.empty:
                    prices[ticker] = float(close.iloc[-1])
            except (KeyError, TypeError) as e:
                print(f"[error] yfinance: no price data for {ticker}: {e}")
                continue
        return prices
    except Exception as e:
        print(f"[error] yfinance price fetch failed for {tickers}: {e}")
        return {}


def get_portfolio(portfolio_name: str = DEFAULT_PORTFOLIO_NAME) -> list[dict]:
    """Return all positions with current prices, value, P&L, and weight."""
    try:
        with _session() as db:
            rows = db.query(Position).filter_by(portfolio_name=portfolio_name).all()
            if not rows:
                return []

            tickers = [r.ticker for r in rows]
            prices = _fetch_prices(tickers)

            positions = []
            for row in rows:
                pos = _position_to_dict(row)
                pos["current_price"] = prices.get(row.ticker)
                if pos["current_price"] is None:
                    print(f"[error] yfinance: missing current price for {row.ticker}")
                positions.append(pos)

            total_value = sum(
                (p["current_price"] or 0) * p["shares"] for p in positions
            )
            for p in positions:
                price = p["current_price"] or 0
                cost_basis = p["shares"] * p["avg_cost_price"]
                market_value = p["shares"] * price
                p["market_value"] = round(market_value, 2)
                p["cost_basis"] = round(cost_basis, 2)
                p["pnl"] = round(market_value - cost_basis, 2)
                p["pnl_pct"] = round((market_value / cost_basis - 1), 4) if cost_basis else None
                p["weight"] = round(market_value / total_value, 4) if total_value else 0

            return positions
    except Exception as e:
        print(f"[error] Failed to load portfolio: {e}")
        return []


def update_prices(portfolio_name: str = DEFAULT_PORTFOLIO_NAME) -> list[dict]:
    """Refresh live prices and return enriched portfolio."""
    try:
        return get_portfolio(portfolio_name=portfolio_name)
    except Exception as e:
        print(f"[error] Failed to update portfolio prices: {e}")
        return []


def _position_to_dict(pos: Position) -> dict:
    return {
        "ticker": pos.ticker,
        "shares": pos.shares,
        "avg_cost_price": pos.avg_cost_price,
        "updated_at": pos.updated_at.isoformat() if pos.updated_at else None,
    }
