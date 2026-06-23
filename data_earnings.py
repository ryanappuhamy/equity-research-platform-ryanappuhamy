"""
SEC EDGAR — earnings call transcript from recent 8-K filings.

Free, no API key. Scans recent Form 8-K filings for EX-99 exhibits that
contain an earnings call transcript (or the closest earnings-related exhibit).
"""

import re
from html import unescape

import requests
from requests.exceptions import RequestException, Timeout

from data_sec import _get_cik, SEC_HEADERS

ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


def _parse_index_exhibits(html: str) -> list[dict]:
    """Extract EX-99 exhibit rows from an SEC filing index page."""
    try:
        rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.S | re.I)
        exhibits = []
        for row in rows:
            if "EX-99" not in row.upper():
                continue
            texts = [t.strip() for t in re.findall(r">([^<]+)<", row) if t.strip()]
            if len(texts) < 3:
                continue
            exhibits.append(
                {
                    "type": texts[1],
                    "filename": texts[2],
                    "description": texts[3] if len(texts) > 3 else "",
                }
            )
        return exhibits
    except Exception as e:
        print(f"[error] SEC EDGAR: failed to parse filing index: {e}")
        return []


def _exhibit_score(exhibit: dict) -> int:
    """Higher score = better match for earnings call transcript."""
    blob = f"{exhibit['filename']} {exhibit['description']} {exhibit['type']}".lower()
    if "transcript" in blob or "earnings call" in blob:
        return 100
    if "conference call" in blob or "call transcript" in blob:
        return 90
    if "commentary" in blob or "prepared remarks" in blob:
        return 50
    if "press release" in blob or "results" in blob:
        return 30
    if exhibit["type"].upper().startswith("EX-99.1"):
        return 20
    return 10


def _clean_html(html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    try:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
        text = text.replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        print(f"[error] SEC EDGAR: failed to clean HTML text: {e}")
        return ""


def _fetch_filing_index(cik_int: int, accession: str) -> str | None:
    try:
        acc_nodash = accession.replace("-", "")
        url = f"{ARCHIVES_BASE}/{cik_int}/{acc_nodash}/{accession}-index.htm"
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        return r.text
    except Timeout:
        print(f"[error] SEC EDGAR: timeout fetching filing index for {accession}")
        return None
    except RequestException as e:
        print(f"[error] SEC EDGAR: request failed fetching filing index for {accession}: {e}")
        return None
    except Exception as e:
        print(f"[error] SEC EDGAR: unexpected error fetching filing index for {accession}: {e}")
        return None


def _download_exhibit(cik_int: int, accession: str, filename: str) -> str | None:
    try:
        acc_nodash = accession.replace("-", "")
        url = f"{ARCHIVES_BASE}/{cik_int}/{acc_nodash}/{filename}"
        r = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"[error] SEC EDGAR: exhibit download returned HTTP {r.status_code} for {filename}")
            return None
        return r.text
    except Timeout:
        print(f"[error] SEC EDGAR: timeout downloading exhibit {filename}")
        return None
    except RequestException as e:
        print(f"[error] SEC EDGAR: request failed downloading exhibit {filename}: {e}")
        return None
    except Exception as e:
        print(f"[error] SEC EDGAR: unexpected error downloading exhibit {filename}: {e}")
        return None


def get_earnings_transcript(ticker: str, max_filings: int = 40) -> dict:
    """
    Fetch the most recent 8-K earnings call transcript (or best EX-99 exhibit).

    Returns dict with cleaned text and filing metadata, or available=False.
    """
    try:
        cik = _get_cik(ticker)
        if cik is None:
            note = f"CIK not found for {ticker}"
            print(f"[error] SEC EDGAR transcript: {note}")
            return {"available": False, "note": note}

        cik_int = int(cik)
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=20)
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        checked = 0
        candidates: list[tuple[int, str, str, dict]] = []
        for form, filing_date, accession in zip(forms, dates, accessions):
            if form != "8-K":
                continue
            checked += 1
            if checked > max_filings:
                break

            index_html = _fetch_filing_index(cik_int, accession)
            if not index_html:
                continue

            exhibits = _parse_index_exhibits(index_html)
            if not exhibits:
                continue

            for exhibit in exhibits:
                score = _exhibit_score(exhibit)
                candidates.append((score, filing_date, accession, exhibit))

        if not candidates:
            note = f"No EX-99 exhibits found in last {max_filings} 8-K filings for {ticker}"
            print(f"[error] SEC EDGAR transcript: {note}")
            return {"available": False, "note": note}

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        score, filing_date, accession, best = candidates[0]

        raw_html = _download_exhibit(cik_int, accession, best["filename"])
        if not raw_html:
            note = f"Failed to download earnings exhibit for {ticker}"
            print(f"[error] SEC EDGAR transcript: {note}")
            return {"available": False, "note": note}

        cleaned = _clean_html(raw_html)
        if len(cleaned) < 500:
            note = f"Earnings exhibit text too short for {ticker} ({len(cleaned)} chars)"
            print(f"[error] SEC EDGAR transcript: {note}")
            return {"available": False, "note": note}

        is_transcript = score >= 90
        return {
            "available": True,
            "ticker": ticker.upper(),
            "filing_date": filing_date,
            "accession": accession,
            "exhibit": best["filename"],
            "exhibit_type": best["type"],
            "exhibit_description": best["description"],
            "is_transcript": is_transcript,
            "text": cleaned,
            "char_count": len(cleaned),
            "note": (
                "Full earnings call transcript from 8-K EX-99 exhibit."
                if is_transcript
                else "No explicit transcript exhibit found; using closest earnings-related EX-99."
            ),
        }
    except Timeout:
        note = f"SEC EDGAR timeout fetching transcript for {ticker}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
    except RequestException as e:
        note = f"SEC EDGAR request error fetching transcript for {ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
    except Exception as e:
        note = f"SEC EDGAR transcript error for {ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
