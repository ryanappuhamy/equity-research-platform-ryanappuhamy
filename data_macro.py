"""
FRED — macro context (free API key from fred.stlouisfed.org).

Provides the macro backdrop for the report: rates, inflation, unemployment.
Objective official data.
"""

import requests
from requests.exceptions import RequestException, Timeout

import config

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fred_latest(series_id: str) -> float | None:
    """Latest observation of a FRED series."""
    try:
        params = {
            "series_id": series_id,
            "api_key": config.FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 13,
        }
        r = requests.get(FRED_BASE, params=params, timeout=20)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if not obs:
            print(f"[error] FRED: no observations for series {series_id}")
            return None
        return float(obs[0]["value"])
    except Timeout:
        print(f"[error] FRED: timeout fetching latest value for {series_id}")
        return None
    except RequestException as e:
        print(f"[error] FRED: request failed for {series_id}: {e}")
        return None
    except Exception as e:
        print(f"[error] FRED: failed to fetch latest value for {series_id}: {e}")
        return None


def _fred_yoy(series_id: str) -> float | None:
    """Year-over-year % change of a monthly FRED series (e.g. CPI)."""
    try:
        params = {
            "series_id": series_id,
            "api_key": config.FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 13,
        }
        r = requests.get(FRED_BASE, params=params, timeout=20)
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
        if len(obs) < 13:
            print(f"[error] FRED: insufficient observations for YoY on {series_id} ({len(obs)}/13)")
            return None
        obs.sort(key=lambda o: o["date"])
        year_ago = float(obs[-13]["value"])
        latest = float(obs[-1]["value"])
        yoy = latest / year_ago - 1
        if yoy > 0.15 or yoy < -0.05:
            print(f"[error] FRED: {series_id} YoY {yoy:.2%} outside expected range, returning None")
            return None
        return yoy
    except Timeout:
        print(f"[error] FRED: timeout fetching YoY for {series_id}")
        return None
    except RequestException as e:
        print(f"[error] FRED: request failed for YoY on {series_id}: {e}")
        return None
    except Exception as e:
        print(f"[error] FRED: failed to compute YoY for {series_id}: {e}")
        return None


def get_macro_context() -> dict:
    """Macro snapshot for the report. Empty dict if no FRED key set."""
    if not config.FRED_API_KEY:
        return {"available": False, "note": "Set FRED_API_KEY for macro context (free)"}

    try:
        cpi = _fred_yoy(config.FRED_SERIES["cpi_yoy"])
        result = {
            "available": True,
            "fed_funds_rate": _fred_latest(config.FRED_SERIES["fed_funds"]),
            "ten_year_yield": _fred_latest(config.FRED_SERIES["ten_year"]),
            "cpi_yoy": round(cpi, 4) if cpi is not None else None,
            "unemployment_rate": _fred_latest(config.FRED_SERIES["unemployment"]),
        }
        if all(result[k] is None for k in ("fed_funds_rate", "ten_year_yield", "cpi_yoy", "unemployment_rate")):
            note = "FRED returned no usable macro data"
            print(f"[error] {note}")
            return {"available": False, "note": note}
        return result
    except Exception as e:
        note = f"FRED macro context failed: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
