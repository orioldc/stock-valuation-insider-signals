"""One ticker's insider buying and share count change, read from the cleaned database.

The valuation tool (packages/valuation/data/insider_signals.py) and the frozen
snapshot (scripts/export_frozen_insider.py) both use this, so they show the
same figures as the scanner. The database has already been cleaned (amendments
applied, duplicates removed, joint filings merged; see form4_rules.py), so
insiders are counted by their SEC CIK, never by name.

Trades filed without a price count as insiders buying but are left out of the
dollar totals; how many were left out is reported alongside the totals.
"""

import json
from datetime import date, timedelta

from signals.insider_clusters import _find_best_cluster, _get_seniority_weight
from signals.share_count_change import compute_share_delta
from signals.sweet_spot_filter import classify_cluster

# Insiders and dollars bought are counted over this many days.
COUNT_WINDOW_DAYS = 120
# A cluster is 2+ insiders buying within CLUSTER_WINDOW_DAYS, looked for in the
# last CLUSTER_LOOKBACK_DAYS (the same as the scanner, insider_clusters.py).
CLUSTER_LOOKBACK_DAYS = 90
CLUSTER_WINDOW_DAYS = 30


def _is_ceo(relationship):
    rel = relationship.upper()
    return "CEO" in rel or "CHIEF EXECUTIVE" in rel


def _is_officer(relationship):
    rel = relationship.upper()
    return "OFFICER" in rel or "VP" in rel or _is_ceo(relationship)


def summarize_ticker(conn, ticker, as_of=None):
    """The insider buying and share count change for one ticker, as of a day
    (default today). Returns None when the ticker is not in the database.
    """
    as_of = as_of or date.today().isoformat()
    ticker = ticker.upper()
    company = conn.execute(
        "SELECT id, sector FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    if company is None:
        return None
    company_id, sector = company

    count_from = (date.fromisoformat(as_of) - timedelta(days=COUNT_WINDOW_DAYS)).isoformat()
    rows = conn.execute("""
        SELECT transaction_date, reporting_name, reporting_cik,
               shares_transacted, price, raw_json
        FROM insider_transactions
        WHERE company_id = ? AND transaction_type = 'P'
          AND transaction_date >= ? AND transaction_date <= ?
        ORDER BY transaction_date
    """, (company_id, count_from, as_of)).fetchall()
    latest_purchase = conn.execute("""
        SELECT MAX(transaction_date) FROM insider_transactions
        WHERE company_id = ? AND transaction_type = 'P' AND transaction_date <= ?
    """, (company_id, as_of)).fetchone()[0]

    trades = []
    for when, name, cik, shares, price, raw_json in rows:
        relationship = (json.loads(raw_json) if raw_json else {}).get("relationship", "") or ""
        priced = price is not None and price > 0
        trades.append({
            "date": when, "name": name, "cik": cik,
            "shares": shares or 0, "price": price if priced else None,
            # 0 only so the cluster score can add it up; the totals below skip it.
            "value": (shares or 0) * price if priced else 0,
            "priced": priced,
            "relationship": relationship,
            "seniority_weight": _get_seniority_weight(relationship),
        })

    def totals(group):
        ciks = {t["cik"] for t in group}
        value = sum(t["value"] for t in group if t["priced"])
        unpriced = sum(1 for t in group if not t["priced"])
        return ciks, value, unpriced

    ciks, total_value, n_unpriced = totals(trades)
    insider_summary = None
    if trades:
        names = []
        for t in reversed(trades):  # newest first
            if t["name"] and t["name"] not in names:
                names.append(t["name"])
        insider_summary = (f"{len(ciks)} insider(s) bought ${total_value:,.0f} in the "
                           f"{COUNT_WINDOW_DAYS} days to {as_of}")
        if n_unpriced:
            insider_summary += f" (plus {n_unpriced} trade(s) filed without a price)"
        if names:
            insider_summary += f" ({'; '.join(names[:3])})"

    cluster_from = (date.fromisoformat(as_of) - timedelta(days=CLUSTER_LOOKBACK_DAYS)).isoformat()
    cluster, score = _find_best_cluster(
        [t for t in trades if t["date"] >= cluster_from], CLUSTER_WINDOW_DAYS)
    cluster_detected = score > 0
    cluster_ciks, cluster_value, cluster_unpriced = totals(cluster if cluster_detected else [])
    has_ceo = any(_is_ceo(t["relationship"]) for t in cluster) if cluster_detected else False
    has_officer = any(_is_officer(t["relationship"]) for t in cluster) if cluster_detected else False
    quality = None
    if cluster_detected:
        quality, _, _ = classify_cluster({
            "num_insiders": len(cluster_ciks), "total_value": cluster_value,
            "sector": sector, "has_ceo": has_ceo, "has_officer": has_officer,
        })

    share = compute_share_delta(ticker, as_of=as_of, conn=conn)
    data_through = conn.execute("SELECT MAX(filing_date) FROM insider_transactions").fetchone()[0]

    return {
        "ticker": ticker,
        "in_universe": True,
        "quality": quality,
        "cluster_detected": cluster_detected,
        "cluster_n_insiders": len(cluster_ciks),
        "cluster_total_value": cluster_value,
        "cluster_trades_unpriced": cluster_unpriced,
        "has_ceo": has_ceo,
        "has_officer": has_officer,
        "n_insiders": len(ciks),
        "total_value": total_value,
        "trades_unpriced": n_unpriced,
        "share_delta_4q": share["delta_4q"],
        "share_delta_qoq": share["delta_qoq"],
        "share_trend": share["trend"],
        "share_period_end": share["period_end"],
        "latest_transaction_date": latest_purchase,
        "insider_summary": insider_summary,
        "as_of": as_of,
        # The newest filing in the database: nothing filed after this is included.
        "data_through": data_through,
        "cluster_window_days": CLUSTER_LOOKBACK_DAYS,
        "count_window_days": COUNT_WINDOW_DAYS,
    }
