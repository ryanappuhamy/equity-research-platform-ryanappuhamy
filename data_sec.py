"""
SEC EDGAR — insider trading (Form 4 filings).

100% free, official, objective data. No API key needed.
SEC requires a User-Agent header identifying you (any email works).

We summarize recent insider buys vs sells — one of the few genuinely
objective "smart money" signals available for free.
"""

import requests
from datetime import datetime, timedelta
from requests.exceptions import RequestException, Timeout

SEC_HEADERS = {
    # SEC requires identification. Replace with your real contact.
    "User-Agent": "EquityResearchProject contact@example.com"
}


def _get_cik(ticker: str) -> str | None:
    """Map ticker -> CIK number using SEC's official mapping file."""
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        for entry in data.values():
            if entry["ticker"].upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10)
        print(f"[error] SEC EDGAR: CIK not found for ticker {ticker}")
        return None
    except Timeout:
        print(f"[error] SEC EDGAR: timeout fetching CIK mapping for {ticker}")
        return None
    except RequestException as e:
        print(f"[error] SEC EDGAR: request failed fetching CIK for {ticker}: {e}")
        return None
    except Exception as e:
        print(f"[error] SEC EDGAR: unexpected error fetching CIK for {ticker}: {e}")
        return None


def get_insider_activity(ticker: str, months_back: int = 6) -> dict:
    """
    Summary of recent Form 4 filings (insider transactions).
    Returns counts of filings — a coarse but objective signal.
    For full transaction parsing (buy vs sell amounts), extend with
    the form 4 XML parser; kept simple in v1.
    """
    try:
        cik = _get_cik(ticker)
        if cik is None:
            return {"available": False, "note": f"CIK not found for {ticker}"}

        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])

        cutoff = datetime.now() - timedelta(days=months_back * 30)
        form4_dates = [
            d for f, d in zip(forms, dates)
            if f == "4" and datetime.strptime(d, "%Y-%m-%d") >= cutoff
        ]

        return {
            "available": True,
            "form4_filings_last_6m": len(form4_dates),
            "most_recent_form4": form4_dates[0] if form4_dates else None,
            "note": (
                "Form 4 = insider transaction filing. High filing frequency alone "
                "is not directional; v2 should parse buy vs sell from the XML."
            ),
        }
    except Timeout:
        note = f"SEC EDGAR timeout fetching insider activity for {ticker}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
    except RequestException as e:
        note = f"SEC EDGAR request error for {ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
    except Exception as e:
        note = f"SEC EDGAR error for {ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
