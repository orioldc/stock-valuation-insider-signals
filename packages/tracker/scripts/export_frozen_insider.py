#!/usr/bin/env python3
"""Export frozen insider signal data for use by valuation-agent.

Reads the latest_signals.csv and insider-tracker DB, enriches with
conviction scores and transaction details, and writes a gzipped JSON
file that can be copied to valuation-agent/data/insider_frozen.json.gz.

Usage:
    python scripts/export_frozen_insider.py

Output:
    output/insider_frozen.json.gz  (~200-500KB)
"""

import gzip
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "insider_signals.db")
SIGNALS_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "latest_signals.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "insider_frozen.json.gz")


def main():
    # Read CSV signals
    if not os.path.exists(SIGNALS_CSV):
        print(f"ERROR: {SIGNALS_CSV} not found. Run the pipeline first.")
        sys.exit(1)

    df = pd.read_csv(SIGNALS_CSV)
    print(f"Loaded {len(df)} tickers from latest_signals.csv")

    # Enrich with conviction scores
    try:
        from signals.conviction_scorer import score_dataframe
        # Add required columns for conviction scorer
        conn = sqlite3.connect(DB_PATH)
        company_rows = conn.execute("SELECT ticker, sector FROM companies").fetchall()
        conn.close()
        sector_map = {r[0]: r[1] for r in company_rows}
        df["sector"] = df["ticker"].map(sector_map)

        # Add placeholder columns expected by conviction_scorer
        df["has_ceo"] = False
        df["has_officer"] = False
        df["num_insiders"] = 0

        df = score_dataframe(df)
        print("Enriched with conviction scores")
    except Exception as e:
        print(f"WARNING: Conviction scoring failed ({e}), using raw data")
        df["conviction_score"] = None
        df["quality"] = None

    # Enrich with transaction details from DB
    conn = sqlite3.connect(DB_PATH)
    frozen = {}
    for _, row in df.iterrows():
        ticker = row["ticker"]

        # Get company_id
        company = conn.execute(
            "SELECT id FROM companies WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not company:
            continue
        company_id = company[0]

        # Get latest purchase transactions
        recent_txns = conn.execute("""
            SELECT reporting_name, transaction_date, shares_transacted, price
            FROM insider_transactions
            WHERE company_id = ? AND transaction_type = 'P'
            ORDER BY transaction_date DESC
            LIMIT 20
        """, (company_id,)).fetchall()

        n_insiders = 0
        total_value = 0.0
        latest_date = None
        insider_summary = None

        if recent_txns:
            # Filter to last 120 days
            cutoff = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
            recent = [t for t in recent_txns if t[1] >= cutoff] if recent_txns[0][1] >= cutoff else recent_txns

            if recent:
                names_set = set()
                total_val = 0.0
                for r in recent:
                    names_set.add(r[0])
                    total_val += (r[2] or 0) * (r[3] or 0)
                    if r[1] and (latest_date is None or r[1] > latest_date):
                        latest_date = r[1]
                n_insiders = len(names_set)
                total_value = total_val
                name_list = [n for n in list(names_set)[:3] if n]
                names = "; ".join(name_list) if name_list else "various"
                insider_summary = (
                    f"{n_insiders} insider(s) bought ${total_value:,.0f} in last 120 days ({names})"
                )

            if latest_date is None:
                latest_date = recent_txns[0][1]

        frozen[ticker] = {
            "in_universe": True,
            "conviction_score": int(row.get("conviction_score")) if pd.notna(row.get("conviction_score")) else None,
            "quality": str(row.get("quality", "")) if pd.notna(row.get("quality")) else None,
            "cluster_detected": bool(row.get("cluster_detected", False)),
            "n_insiders": n_insiders,
            "total_value": total_value,
            "share_delta_4q": float(row.get("share_delta_4q", 0) or 0),
            "share_delta_qoq": float(row.get("share_delta_qoq", 0) or 0),
            "share_trend": str(row.get("share_trend", "stable")),
            "latest_transaction_date": latest_date,
            "insider_summary": insider_summary,
        }

    conn.close()

    # Write gzipped JSON
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with gzip.open(OUTPUT_PATH, "wt", encoding="utf-8") as f:
        json.dump(frozen, f)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Exported {len(frozen)} tickers to {OUTPUT_PATH} ({size_kb:.0f} KB)")
    print(f"\nTo use in valuation-agent:")
    print(f"  cp {OUTPUT_PATH} ../valuation-agent/data/insider_frozen.json.gz")


if __name__ == "__main__":
    main()
