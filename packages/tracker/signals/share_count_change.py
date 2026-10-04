"""Whether a company is buying back its own stock or diluting it.

Reads the year-on-year share count change from the latest 10-Q or 10-K
(share_count_changes, built by data_ingestion/share_counts.py). Both figures
come from the same filing, so stock splits and reporting mistakes cancel out
and no split history or plausibility filter is needed.

The quarter-on-quarter change (delta_qoq) compares with the previous quarter's
average from the filing before. It is shown only when both filings are on the
same basis (see add_previous_quarter); the score uses the year-on-year change.

A fall of more than 25% in a year is only counted as a buyback when the filing
also reports buying back stock; without that it is usually a restructuring or
a share exchange, so it is reported as an unexplained decline and not scored.
"""

import os
import sqlite3
from datetime import date

from data_ingestion.share_counts import classify_change, share_change_as_of

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))

# A change for a period that ended more than a year ago says nothing about now.
MAX_STALENESS_DAYS = 365


def fmt_pct(value) -> str:
    """'-3.21%', or 'n/a' when there is no figure (None or NaN)."""
    return "n/a" if value is None or value != value else f"{value:+.2f}%"


def compute_share_delta(ticker, as_of=None, conn=None):
    """
    The latest year-on-year share count change for a ticker, as known on as_of
    (default today). Pass conn to read from an open database instead of DB_PATH.

    Returns dict with:
        delta_qoq: float or None (% change against the previous quarter, when the two
                   filings provably count shares the same way; see share_counts.py)
        delta_4q: float or None (% change in shares over the year; negative = fewer shares)
        trend: str ('buyback', 'dilution', 'stable', 'unexplained_decline', 'stale',
               'insufficient_data')
        score: float (0-1, higher = bigger buyback; 0 unless trend is 'buyback')
        period_end, filed: str or None (the period the change covers, the day it was filed)
        basis: str or None ('average_3m', 'average_12m' or 'year_end')
        repurchases: bool (the filing reports buying back stock in the period)
        data_quality: str ('clean', 'stale', 'insufficient_data')
    """
    as_of = as_of or date.today().isoformat()
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT id FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        change = share_change_as_of(conn, row[0], as_of) if row else None
    finally:
        if own_conn:
            conn.close()

    if change is None:
        return {
            "delta_qoq": None, "delta_4q": None, "trend": "insufficient_data", "score": 0,
            "period_end": None, "filed": None, "basis": None, "repurchases": False,
            "data_quality": "insufficient_data",
        }

    qoq = change["change_qoq_pct"]
    result = {
        "delta_qoq": round(qoq, 4) if qoq is not None else None,
        "delta_4q": round(change["change_pct"], 4),
        "period_end": change["period_end"],
        "filed": change["filed"],
        "basis": change["basis"],
        "repurchases": change["repurchases"],
    }
    days_old = (date.fromisoformat(as_of) - date.fromisoformat(change["period_end"])).days
    if days_old > MAX_STALENESS_DAYS:
        return {**result, "trend": "stale", "score": 0,
                "data_quality": f"stale (period ended {change['period_end']})"}

    trend = classify_change(change)
    # -20% a year or more scores 1.0.
    score = min(abs(change["change_pct"]) / 20.0, 1.0) if trend == "buyback" else 0
    return {**result, "trend": trend, "score": round(score, 4), "data_quality": "clean"}
