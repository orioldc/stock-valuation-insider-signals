"""Detect insider buying clusters - multiple insiders purchasing within a rolling window."""

import sqlite3
import json
import math
import os
from datetime import datetime, timedelta

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))


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


def _find_best_cluster(trades, window_days=30):
    """
    Find the best-scoring cluster from a list of trades using a sliding window.

    Pure function for live detection (single best cluster at current time).
    Trades must be dicts with keys: date, cik, value, seniority_weight.

    Returns (best_cluster, best_score) where cluster is a list of trade dicts.

    Performance: Optimized to O(n) using two-pointer sliding window on sorted trades.
    Dates are parsed once upfront instead of twice per inner iteration.
    """
    if not trades:
        return [], 0.0

    # Parse dates once upfront — original version parsed twice per inner iteration
    parsed_trades = []
    for t in trades:
        try:
            date_str = t["date"][:10] if len(t["date"]) >= 10 else t["date"]
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
            parsed_trades.append((parsed_date, t))
        except (ValueError, TypeError):
            continue  # Skip unparseable dates

    if not parsed_trades:
        return [], 0.0

    best_cluster = []
    best_score = 0.0

    # Two-pointer sliding window: for each anchor i, find window bounds [left, right)
    # where all trades j satisfy t[i].date <= t[j].date <= t[i].date + window
    left = 0
    right = 0

    for i, (t_date, t) in enumerate(parsed_trades):
        window_end = t_date + timedelta(days=window_days)

        # Move left pointer: skip trades with date < t_date
        while left < len(parsed_trades) and parsed_trades[left][0] < t_date:
            left += 1

        # Move right pointer: extend to include all trades <= window_end
        while right < len(parsed_trades) and parsed_trades[right][0] <= window_end:
            right += 1

        # Collect cluster: all trades in [left, right) except i itself
        cluster = [t]  # Start with anchor trade
        for j in range(left, right):
            if j != i:
                cluster.append(parsed_trades[j][1])

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

    return best_cluster, best_score


def _enumerate_all_clusters(trades, window_days=30, min_insiders=2):
    """
    Enumerate all non-overlapping cluster events from a ticker's trade history.

    For backtesting: walks trades chronologically and emits a cluster event whenever
    a window contains >=min_insiders distinct CIKs. Once a cluster is emitted, skips
    past the window to avoid counting the same buying episode repeatedly.

    Returns list of (cluster, score) tuples where cluster is a list of trade dicts.
    Each cluster's signal_date is the last trade date within that cluster.

    WHY: The live path returns the single best cluster (for "is there a signal now?").
    The backtest needs the full timeline of cluster events to build a hit rate history.
    """
    if not trades:
        return []

    # Parse dates once upfront
    parsed_trades = []
    for t in trades:
        try:
            date_str = t["date"][:10] if len(t["date"]) >= 10 else t["date"]
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
            parsed_trades.append((parsed_date, t))
        except (ValueError, TypeError):
            continue

    if not parsed_trades:
        return []

    # Sort by date (should already be sorted from DB, but defensive)
    parsed_trades.sort(key=lambda x: x[0])

    clusters = []
    i = 0

    while i < len(parsed_trades):
        anchor_date, anchor_trade = parsed_trades[i]
        window_end = anchor_date + timedelta(days=window_days)

        # Collect all trades in [anchor_date, anchor_date + window_days]
        cluster = [anchor_trade]
        j = i + 1
        while j < len(parsed_trades) and parsed_trades[j][0] <= window_end:
            cluster.append(parsed_trades[j][1])
            j += 1

        # Check if this window qualifies as a cluster
        distinct_insiders = set(tr["cik"] for tr in cluster)
        if len(distinct_insiders) >= min_insiders:
            # Compute score
            cluster_size = len(distinct_insiders)
            total_value = sum(tr["value"] for tr in cluster)
            avg_seniority = sum(tr["seniority_weight"] for tr in cluster) / len(cluster)
            log_value = math.log(max(total_value, 1))
            score = cluster_size * log_value * avg_seniority

            clusters.append((cluster, score))

            # Skip past this window to avoid overlapping clusters
            # Advance to first trade after window_end
            i = j
        else:
            # No cluster at this anchor, try next trade
            i += 1

    return clusters


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
    trades = []
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

    # Use canonical cluster finder (optimized O(n) sliding window)
    best_cluster, best_score = _find_best_cluster(trades, window_days)

    return {
        "cluster_detected": best_score > 0,
        "score": round(best_score, 4),
        "details": best_cluster,
    }
