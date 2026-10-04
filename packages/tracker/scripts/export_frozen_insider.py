#!/usr/bin/env python3
"""Export the frozen insider snapshot that the valuation tool falls back on.

For every ticker in latest_signals.csv, writes the same figures the valuation
tool reads from the database (signals/ticker_summary.py) into a gzipped JSON
file. The valuation tool only uses this file when the database is missing.

Usage:
    python scripts/export_frozen_insider.py

Output:
    output/insider_frozen.json.gz
"""

import gzip
import json
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signals.ticker_summary import summarize_ticker  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "insider_signals.db")
SIGNALS_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "latest_signals.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "insider_frozen.json.gz")


def main():
    if not os.path.exists(SIGNALS_CSV):
        print(f"ERROR: {SIGNALS_CSV} not found. Run the pipeline first.")
        sys.exit(1)

    tickers = pd.read_csv(SIGNALS_CSV, keep_default_na=False, na_values=[''])["ticker"].dropna()
    print(f"Loaded {len(tickers)} tickers from latest_signals.csv")

    conn = sqlite3.connect(DB_PATH)
    try:
        frozen = {}
        for ticker in tickers:
            summary = summarize_ticker(conn, ticker)
            if summary is not None:
                frozen[summary["ticker"]] = summary
    finally:
        conn.close()

    if not frozen:
        print("ERROR: no tickers exported")
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with gzip.open(OUTPUT_PATH, "wt", encoding="utf-8") as f:
        json.dump(frozen, f)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Exported {len(frozen)} tickers to {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
