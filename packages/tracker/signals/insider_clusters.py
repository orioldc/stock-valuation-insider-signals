"""Detect insider buying clusters - multiple insiders purchasing within a rolling window."""

import sqlite3
import json
import math
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")


def _get_seniority_weight(relationship: str) -> float:
    """Parse relationship string for seniority weight."""
    r = relationship.upper()
    # Top tier: CEO, CFO, COO, Chief, President
    if any(k in r for k in ["CEO", "CFO", "COO", "CHIEF", "PRESIDENT"]):
        return 3.0
    # Officer tier
    if any(k in r for k in ["VP", "SVP", "EVP", "OFFICER"]):
        return 2.0
    # Director tier
    if "DIRECTOR" in r:
        return 1.5
    # 10% owner
    if "10%" in r or "OWNER" in r:
        return 1.0
    return 1.0


def detect_clusters(ticker, lookback_days=90, window_days=30):
    """
    Detect insider buying clusters for a ticker.

    Returns dict with:
        cluster_detected: bool
        score: float
        details: list of trade dicts
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT it.transaction_date, it.reporting_name, it.reporting_cik,
               it.shares_transacted, it.price, it.raw_json
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE c.ticker = ?
          AND it.transaction_type = 'P'
          AND it.transaction_date >= ?
        ORDER BY it.transaction_date
    """, (ticker, cutoff)).fetchall()
    conn.close()

    if not rows:
        return {"cluster_detected": False, "score": 0.0, "details": []}

    # Parse trades; skip any row whose date cannot be parsed as YYYY-MM-DD so
    # that a single malformed legacy row does not crash the entire monthly job.
    # trade_dates is a parallel list of datetime objects aligned by index with
    # trades; never stored inside the trade dict to keep details JSON-safe.
    trades = []
    trade_dates = []
    for r in rows:
        try:
            dt = datetime.strptime(r["transaction_date"][:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        raw = json.loads(r["raw_json"]) if r["raw_json"] else {}
        relationship = raw.get("relationship", "")
        price = r["price"] or 0
        shares = r["shares_transacted"] or 0
        value = price * shares
        trades.append({
            "date": r["transaction_date"],
            "name": r["reporting_name"],
            "cik": r["reporting_cik"],
            "shares": shares,
            "price": price,
            "value": value,
            "relationship": relationship,
            "seniority_weight": _get_seniority_weight(relationship),
        })
        trade_dates.append(dt)

    # Rolling window: for each trade, find all trades within window_days
    best_cluster = []
    best_score = 0.0

    for i, t in enumerate(trades):
        t_date = trade_dates[i]
        window_end = t_date + timedelta(days=window_days)

        cluster = [t]
        for j, t2 in enumerate(trades):
            if i == j:
                continue
            if t_date <= trade_dates[j] <= window_end:
                cluster.append(t2)

        # Distinct insiders
        distinct_insiders = set(tr["cik"] for tr in cluster)
        if len(distinct_insiders) < 2:
            continue

        cluster_size = len(distinct_insiders)
        total_value = sum(tr["value"] for tr in cluster)
        avg_seniority = sum(tr["seniority_weight"] for tr in cluster) / len(cluster)

        log_value = math.log(max(total_value, 1))
        score = cluster_size * log_value * avg_seniority

        if score > best_score:
            best_score = score
            best_cluster = cluster

    return {
        "cluster_detected": best_score > 0,
        "score": round(best_score, 4),
        "details": best_cluster,
    }
