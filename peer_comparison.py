"""
Peer comparison (comps) — relative valuation, objective.

Builds a comps table across the target and its peers, computes the peer
median for each multiple, and flags where the target trades at a
premium/discount. No opinions: just where the numbers sit.
"""

import pandas as pd

import data_fundamentals as df_mod

COMPARISON_METRICS = [
    "pe_ttm",
    "ev_ebitda",
    "ev_revenue",
    "price_to_book",
    "gross_margin",
    "operating_margin",
    "roe",
    "revenue_growth_yoy",
]


def build_comps_table(target_ticker: str, peer_tickers: list[str]) -> pd.DataFrame:
    """Comps table: target + peers, one row per company."""
    try:
        rows = []
        all_tickers = [target_ticker] + [p for p in peer_tickers if p.upper() != target_ticker.upper()]

        for tk in all_tickers:
            try:
                f = df_mod.get_fundamentals(tk)
                if not f.get("available", True) and f.get("note"):
                    print(f"[error] comps skipped {tk}: {f.get('note')}")
                    continue
                row = {"ticker": tk.upper(), "company": f.get("company_name")}
                for m in COMPARISON_METRICS:
                    row[m] = f.get(m)
                rows.append(row)
            except Exception as e:
                print(f"[error] comps failed for {tk}: {e}")

        if not rows:
            print(f"[error] peer comps table empty for {target_ticker}")
            return pd.DataFrame()

        return pd.DataFrame(rows)
    except Exception as e:
        print(f"[error] build_comps_table failed for {target_ticker}: {e}")
        return pd.DataFrame()


def relative_valuation(comps: pd.DataFrame, target_ticker: str) -> dict:
    """
    For each multiple: target value, peer median, premium/discount %.
    Positive premium on P/E, EV/EBITDA etc means the target is MORE expensive.
    """
    try:
        if comps is None or comps.empty:
            print(f"[error] relative_valuation: empty comps table for {target_ticker}")
            return {"available": False, "note": "No peer comparison data available"}

        target_ticker = target_ticker.upper()
        target_row = comps[comps["ticker"] == target_ticker]
        peer_rows = comps[comps["ticker"] != target_ticker]

        if target_row.empty or peer_rows.empty:
            note = f"Insufficient comps rows for relative valuation on {target_ticker}"
            print(f"[error] {note}")
            return {"available": False, "note": note}

        out = {"available": True}
        for m in COMPARISON_METRICS:
            t_val = target_row[m].iloc[0]
            peer_median = peer_rows[m].median(skipna=True)
            if pd.notna(t_val) and pd.notna(peer_median) and peer_median != 0:
                out[m] = {
                    "target": round(float(t_val), 4),
                    "peer_median": round(float(peer_median), 4),
                    "premium_vs_peers": round(float(t_val / peer_median - 1), 4),
                }
        return out
    except Exception as e:
        note = f"Relative valuation failed for {target_ticker}: {e}"
        print(f"[error] {note}")
        return {"available": False, "note": note}
