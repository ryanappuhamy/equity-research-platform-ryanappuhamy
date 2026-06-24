"""
Central configuration.
API keys are read from environment variables — never hardcode them.

Required:
    ANTHROPIC_API_KEY   -> Claude API (AI report generation)
Optional:
    FMP_API_KEY         -> Financial Modeling Prep (clean fundamentals, $14/mo plan)
    FRED_API_KEY        -> FRED macro data (free, register at fred.stlouisfed.org)
    FINNHUB_API_KEY     -> Finnhub analyst consensus, price targets, EPS estimates (free tier)
    DATABASE_URL        -> PostgreSQL connection string (Supabase/Render); falls back to SQLite

If FMP_API_KEY is missing, the system falls back to yfinance for fundamentals.
"""

import os

# --- API keys (from environment) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

# --- Claude model (with fallbacks, newest first) ---
CLAUDE_MODELS = [
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
]

# --- Analysis settings ---
PRICE_LOOKBACK_YEARS = 5
PEER_COUNT = 5                  # number of peers in the comps table
RISK_FREE_RATE = 0.04           # used for Sharpe ratio

# --- FRED series used as macro context ---
FRED_SERIES = {
    "fed_funds": "FEDFUNDS",          # Fed funds rate
    "cpi_yoy": "CPIAUCSL",            # CPI (we compute YoY)
    "ten_year": "DGS10",              # 10Y Treasury yield
    "unemployment": "UNRATE",         # Unemployment rate
}

# --- Output ---
OUTPUT_DIR = "output"
PORTFOLIO_DB = os.environ.get("PORTFOLIO_DB", "portfolio.db")


def _normalize_database_url(url: str) -> str:
    """Ensure SQLAlchemy-compatible PostgreSQL driver URL."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def get_database_url() -> str:
    """PostgreSQL via DATABASE_URL, or local SQLite fallback."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return _normalize_database_url(url)
    return f"sqlite:///{PORTFOLIO_DB}"


DATABASE_URL = get_database_url()
