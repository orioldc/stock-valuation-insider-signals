"""Composite scoring: combine insider cluster + share change signals.

The composite is SIZE-ADJUSTED: raw cluster and share scores are bucket-
percentile-ranked within market-cap tier and weighted by tier (mid-cap = 1.0
sweet spot, micro-cap down-weighted to suppress noise). This replaces the
prior max-normalization which mechanically favored micro-caps.
"""

import sqlite3
import json
import os
import pandas as pd
from datetime import datetime

from signals.insider_clusters import detect_clusters
from signals.share_count_change import compute_share_delta
from signals.size_adjustment import (
    get_tier, TIER_WEIGHTS, TIER_ORDER, size_adjusted_score, bucket_percentile_rank,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")


def _load_market_caps(tickers):
    """Fetch market_cap for tickers from companies table. Returns {ticker: mcap or None}."""
    if not tickers:
        return {}
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, market_cap FROM companies WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: (float(r[1]) if r[1] else None) for r in rows}


def score_universe(tickers, date=None):
    """Score all tickers and return ranked DataFrame.

    Weights: 0.6 × size-adjusted insider cluster + 0.4 × size-adjusted buyback.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    mcaps = _load_market_caps(tickers)

    results = []
    for ticker in tickers:
        cluster = detect_clusters(ticker)
        share = compute_share_delta(ticker)
        mcap = mcaps.get(ticker)
        results.append({
            "ticker": ticker,
            "market_cap": mcap,
            "cap_tier": get_tier(mcap),
            "cluster_detected": cluster["cluster_detected"],
            "cluster_score_raw": cluster["score"],
            "cluster_details": len(cluster["details"]),
            "share_delta_qoq": share["delta_qoq"],
            "share_delta_4q": share["delta_4q"],
            "share_trend": share["trend"],
            "share_score_raw": share["score"],
        })

    df = pd.DataFrame(results)

    # Build per-tier raw-score distributions for percentile ranking
    by_tier_cluster = {t: df.loc[df["cap_tier"] == t, "cluster_score_raw"].tolist() for t in TIER_ORDER}
    by_tier_share = {t: df.loc[df["cap_tier"] == t, "share_score_raw"].tolist() for t in TIER_ORDER}

    def cluster_pct(row):
        return bucket_percentile_rank(by_tier_cluster, row["cap_tier"], row["cluster_score_raw"])

    def share_pct(row):
        return bucket_percentile_rank(by_tier_share, row["cap_tier"], row["share_score_raw"])

    df["cluster_pct"] = df.apply(cluster_pct, axis=1)
    df["share_pct"] = df.apply(share_pct, axis=1)
    df["tier_weight"] = df["cap_tier"].map(TIER_WEIGHTS).fillna(0.0)

    df["cluster_adj"] = df.apply(
        lambda r: size_adjusted_score(r["cluster_score_raw"], r["cap_tier"], r["cluster_pct"]),
        axis=1,
    )
    df["share_adj"] = df.apply(
        lambda r: size_adjusted_score(r["share_score_raw"], r["cap_tier"], r["share_pct"]),
        axis=1,
    )

    # Legacy max-norm columns are kept for backward-compat with downstream readers,
    # but composite is now driven by the size-adjusted scores.
    max_cluster = df["cluster_score_raw"].max()
    df["cluster_norm"] = df["cluster_score_raw"] / max_cluster if max_cluster > 0 else 0.0
    max_share = df["share_score_raw"].max()
    df["share_norm"] = df["share_score_raw"] / max_share if max_share > 0 else 0.0

    df["composite"] = 0.6 * df["cluster_adj"] + 0.4 * df["share_adj"]
    df = df.sort_values("composite", ascending=False).reset_index(drop=True)

    _store_signals(df, date)
    return df


def _store_signals(df, date):
    """Store composite scores in the signals table."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    for _, row in df.iterrows():
        cur.execute("SELECT id FROM companies WHERE ticker = ?", (row["ticker"],))
        company = cur.fetchone()
        if not company:
            continue
        
        details = json.dumps({
            "cluster_score": row["cluster_score_raw"],
            "cluster_norm": row["cluster_norm"],
            "cluster_adj": row.get("cluster_adj"),
            "cluster_pct": row.get("cluster_pct"),
            "share_score": row["share_score_raw"],
            "share_norm": row["share_norm"],
            "share_adj": row.get("share_adj"),
            "share_pct": row.get("share_pct"),
            "share_delta_qoq": row["share_delta_qoq"],
            "share_delta_4q": row["share_delta_4q"],
            "cluster_detected": bool(row["cluster_detected"]),
            "cap_tier": row.get("cap_tier"),
            "tier_weight": row.get("tier_weight"),
            "market_cap": row.get("market_cap"),
        })
        
        # Delete old signal for same date/type
        cur.execute("DELETE FROM signals WHERE company_id = ? AND signal_date = ? AND signal_type = 'composite'",
                     (company[0], date))
        cur.execute("""
            INSERT INTO signals (company_id, signal_date, signal_type, strength, details)
            VALUES (?, ?, 'composite', ?, ?)
        """, (company[0], date, row["composite"], details))
    
    conn.commit()
    conn.close()
