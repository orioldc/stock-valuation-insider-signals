"""Detect share count changes (buybacks) from shares outstanding data."""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")


def compute_share_delta(ticker):
    """
    Compute share count changes for a ticker.
    
    Returns dict with:
        delta_qoq: float (latest QoQ % change)
        delta_4q: float (trailing 4-quarter cumulative % change)
        trend: str ('buyback', 'dilution', 'stable')
        score: float (higher = more buyback)
        data_points: int
    """
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        WHERE c.ticker = ?
        ORDER BY so.date
    """, (ticker,)).fetchall()
    conn.close()
    
    if len(rows) < 2:
        return {"delta_qoq": 0, "delta_4q": 0, "trend": "insufficient_data", "score": 0, "data_points": len(rows)}
    
    # Deduplicate by keeping last value per approximate quarter
    # Group by year-quarter
    quarterly = {}
    for date_str, shares in rows:
        try:
            year = date_str[:4]
            month = int(date_str[5:7])
            q = (int(year), (month - 1) // 3 + 1)
            quarterly[q] = shares  # last value wins
        except (ValueError, IndexError):
            continue
    
    if len(quarterly) < 2:
        return {"delta_qoq": 0, "delta_4q": 0, "trend": "insufficient_data", "score": 0, "data_points": len(quarterly)}
    
    sorted_quarters = sorted(quarterly.keys())
    values = [quarterly[q] for q in sorted_quarters]
    
    # QoQ change (latest)
    delta_qoq = (values[-1] - values[-2]) / values[-2] * 100 if values[-2] != 0 else 0
    
    # Trailing 4-quarter change
    if len(values) >= 5:
        delta_4q = (values[-1] - values[-5]) / values[-5] * 100 if values[-5] != 0 else 0
    else:
        delta_4q = (values[-1] - values[0]) / values[0] * 100 if values[0] != 0 else 0
    
    # Trend
    if delta_qoq <= -1:
        trend = "buyback"
    elif delta_qoq >= 1:
        trend = "dilution"
    else:
        trend = "stable"
    
    # Score: normalize negative delta. More negative = higher score.
    # Use 4q delta, cap at [-20%, 0%] range, map to [0, 1]
    raw = min(0, delta_4q)  # only negative counts
    score = min(abs(raw) / 20.0, 1.0)  # -20% maps to 1.0
    
    return {
        "delta_qoq": round(delta_qoq, 4),
        "delta_4q": round(delta_4q, 4),
        "trend": trend,
        "score": round(score, 4),
        "data_points": len(quarterly),
    }
