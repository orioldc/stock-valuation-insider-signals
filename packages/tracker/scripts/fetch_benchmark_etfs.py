#!/usr/bin/env python3
"""
Fetch benchmark ETF prices (IWM, MDY) for size-matched backtesting.

Reuses the batched yfinance fetching from backfill_prices.py to download
IWM (Russell 2000) and MDY (S&P MidCap 400) over the full price coverage
window. SPY is already present, so this completes the benchmark suite.
"""

import sys
import os
import sqlite3
import logging
import pandas as pd

# Add parent to path for shared functions
sys.path.insert(0, os.path.dirname(__file__))
from backfill_prices import (
    get_db,
    fetch_prices_batch,
    insert_prices,
    get_coverage_stats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BENCHMARK_ETFS = ["IWM", "MDY"]  # SPY already exists


def fetch_benchmarks(dry_run=False):
    """Fetch benchmark ETF prices over full coverage window."""
    conn = get_db()

    # Get date range from existing prices
    cur = conn.cursor()
    cur.execute("SELECT MIN(date), MAX(date) FROM prices")
    min_date, max_date = cur.fetchone()

    if not min_date or not max_date:
        logger.error("No existing price data found. Run backfill_prices.py first.")
        conn.close()
        return

    logger.info(f"Fetching benchmark ETFs over {min_date} → {max_date}")

    # Check which benchmarks are missing
    cur.execute(
        f"SELECT ticker FROM prices WHERE ticker IN ({','.join('?' * len(BENCHMARK_ETFS))}) GROUP BY ticker",
        BENCHMARK_ETFS,
    )
    existing = {row[0] for row in cur.fetchall()}
    missing = [t for t in BENCHMARK_ETFS if t not in existing]

    if not missing:
        logger.info("All benchmark ETFs already present in DB")
        conn.close()
        return

    logger.info(f"Missing benchmarks: {missing}")

    # Fetch prices
    logger.info(f"Fetching {len(missing)} benchmark ETFs...")
    prices_by_ticker = fetch_prices_batch(missing, min_date, max_date)

    if not prices_by_ticker:
        logger.error("Failed to fetch any benchmark data")
        conn.close()
        return

    # Insert
    total_inserted = 0
    for ticker in missing:
        if ticker not in prices_by_ticker:
            logger.warning(f"{ticker}: no data returned")
            continue

        prices_df = prices_by_ticker[ticker]
        rows_inserted = insert_prices(conn, ticker, prices_df, dry_run=dry_run)

        if not dry_run:
            conn.commit()

        total_inserted += rows_inserted
        logger.info(f"{ticker}: {rows_inserted} rows inserted")

    # Report coverage
    logger.info("=" * 60)
    logger.info("BENCHMARK ETF COVERAGE")
    logger.info("=" * 60)

    for ticker in BENCHMARK_ETFS:
        cur.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM prices WHERE ticker = ?",
            (ticker,),
        )
        row = cur.fetchone()
        if row and row[2] > 0:
            logger.info(f"{ticker}: {row[0]} → {row[1]} ({row[2]} rows)")

            # Check for gaps
            expected_days = (pd.to_datetime(row[1]) - pd.to_datetime(row[0])).days
            coverage_pct = (row[2] / expected_days) * 100 if expected_days > 0 else 0
            if coverage_pct < 65:  # ~70% is typical for trading days
                logger.warning(f"  ⚠ Low coverage: {coverage_pct:.1f}% of calendar days")
            else:
                logger.info(f"  ✓ Coverage: {coverage_pct:.1f}% of calendar days")
        else:
            logger.warning(f"{ticker}: NO DATA")

    conn.close()

    logger.info(f"\nTotal rows inserted: {total_inserted:,}")
    return total_inserted


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch benchmark ETF prices (IWM, MDY) for size-matched backtesting"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be fetched"
    )

    args = parser.parse_args()
    fetch_benchmarks(dry_run=args.dry_run)
