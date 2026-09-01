#!/usr/bin/env python3
"""
Fetch benchmark ETF prices for multi-benchmark backtesting.

Reuses the batched yfinance fetching from backfill_prices.py to download
benchmark indices over the full price coverage window:
- IWM (Russell 2000 small-cap)
- MDY (S&P MidCap 400)
- QQQ (Nasdaq-100)
- ^IXIC (Nasdaq Composite)
- URTH (iShares MSCI World)
- ACWI (MSCI All-Country World)

SPY (S&P 500) is already present. Together these enable measuring cluster
performance against size-matched, tech-focused, and global benchmarks.
"""

import sys
import os
import sqlite3
import logging
import pandas as pd
from datetime import datetime

# Add parent to path for shared functions
sys.path.insert(0, os.path.dirname(__file__))
from backfill_prices import (
    get_db,
    fetch_prices_batch,
    insert_prices,
    get_coverage_stats,
)

# Add tracker to path for provenance
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_DIR = os.path.join(SCRIPT_DIR, "..")
sys.path.insert(0, TRACKER_DIR)
from pipeline.provenance import record_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BENCHMARK_ETFS = ["IWM", "MDY", "QQQ", "^IXIC", "URTH", "ACWI"]  # SPY already exists

# Retry configuration
MAX_RETRIES = 4  # Initial attempt + 3 retries = 4 total
INITIAL_BACKOFF = 2  # seconds
MAX_BACKOFF = 60  # seconds


def fetch_single_benchmark_with_retry(ticker, start_date, end_date, max_retries=MAX_RETRIES):
    """
    Fetch a single benchmark ETF with exponential backoff retry.

    Returns tuple: (ticker, prices_df or None, error_reason or None)

    Retries on rate limits with exponential backoff. Other errors (404, malformed)
    are treated as permanent failures and return immediately.

    Fallback: if batch download fails (e.g., yfinance 1.7.0 pandas 3.x MultiIndex bug),
    falls back to per-ticker Ticker().history() which avoids the broken code path.
    """
    import yfinance as yf

    for attempt in range(max_retries):
        try:
            logger.info(f"{ticker}: fetching prices (attempt {attempt + 1}/{max_retries})...")

            # Try batch download first (shared function)
            prices_by_ticker = fetch_prices_batch([ticker], start_date, end_date)

            if ticker in prices_by_ticker:
                logger.info(f"{ticker}: success on attempt {attempt + 1}")
                return (ticker, prices_by_ticker[ticker], None)
            else:
                # Batch returned no data - try per-ticker fallback
                logger.warning(f"{ticker}: batch download returned no data, trying per-ticker fallback...")
                try:
                    stock = yf.Ticker(ticker)
                    hist = stock.history(start=start_date, end=end_date, auto_adjust=True)

                    if hist.empty or 'Close' not in hist.columns:
                        logger.warning(f"{ticker}: fallback also returned no data")
                        return (ticker, None, "no_data")

                    # Convert to same format as batch download
                    df = pd.DataFrame({
                        'date': hist.index,
                        'close': hist['Close'].values
                    })
                    df = df.dropna(subset=['close'])

                    if len(df) > 0:
                        logger.info(f"{ticker}: fallback succeeded with {len(df)} rows")
                        return (ticker, df, None)
                    else:
                        logger.warning(f"{ticker}: fallback returned no valid prices")
                        return (ticker, None, "no_data")

                except Exception as fallback_error:
                    logger.warning(f"{ticker}: fallback failed: {fallback_error}")
                    # Fall through to normal error handling
                    return (ticker, None, "no_data")

        except Exception as e:
            error_str = str(e)

            # Check if it's a rate limit error
            if "YFRateLimitError" in str(type(e).__name__) or "Too Many Requests" in error_str or "429" in error_str:
                if attempt < max_retries - 1:
                    # Exponential backoff: 2s, 4s, 8s, capped at MAX_BACKOFF
                    backoff = min(INITIAL_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                    logger.warning(f"{ticker}: rate limited, retrying in {backoff}s...")
                    import time
                    time.sleep(backoff)
                    continue
                else:
                    logger.error(f"{ticker}: rate limited after {max_retries} attempts")
                    return (ticker, None, "rate_limited")
            else:
                # Other errors (404, connection error, etc.) - don't retry
                logger.error(f"{ticker}: error: {e}")
                return (ticker, None, f"error: {type(e).__name__}")

    # Exhausted all retries
    logger.error(f"{ticker}: failed after {max_retries} attempts")
    return (ticker, None, "max_retries_exceeded")


def _ensure_failures_table(conn):
    """
    Create benchmark_backfill_failures table if it doesn't exist.

    Persists permanent failures (ticker doesn't exist, no data) so we don't
    retry them every run.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_backfill_failures (
            ticker TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            last_attempt TEXT NOT NULL
        )
    """)
    conn.commit()


def _record_permanent_failure(conn, ticker, reason, dry_run=False):
    """Record a permanent failure in the DB so it won't be retried next run."""
    if dry_run:
        return

    timestamp = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO benchmark_backfill_failures (ticker, reason, last_attempt)
        VALUES (?, ?, ?)
    """, (ticker, reason, timestamp))
    conn.commit()


def fetch_benchmarks(dry_run=False):
    """Fetch benchmark ETF prices over full coverage window."""
    conn = get_db()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Purge stale failure records for benchmarks that now have prices
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM benchmark_backfill_failures
        WHERE ticker IN (
            SELECT DISTINCT ticker FROM prices
        )
    """)
    purged = cur.rowcount
    if purged > 0:
        logger.info(f"Purged {purged} stale failure records for benchmarks with current prices")
        conn.commit()

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

    # Fetch prices individually with retry logic
    logger.info(f"Fetching {len(missing)} benchmark ETFs individually with retry...")
    total_inserted = 0
    successful = []
    failed = []

    for ticker in missing:
        # Fetch with retry
        _, prices_df, error_reason = fetch_single_benchmark_with_retry(ticker, min_date, max_date)

        if prices_df is None:
            logger.warning(f"{ticker}: failed to fetch ({error_reason})")
            # Record permanent failure for non-transient errors
            if error_reason not in ["rate_limited", "max_retries_exceeded"]:
                _record_permanent_failure(conn, ticker, error_reason, dry_run=dry_run)
            failed.append(ticker)
            continue

        # Insert prices
        rows_inserted = insert_prices(conn, ticker, prices_df, dry_run=dry_run)

        if rows_inserted > 0:
            # Write-through: success clears any existing failure record
            if not dry_run:
                conn.execute("DELETE FROM benchmark_backfill_failures WHERE ticker = ?", (ticker,))
                conn.commit()

            successful.append(ticker)
            total_inserted += rows_inserted
            logger.info(f"{ticker}: {rows_inserted} rows inserted")
        else:
            logger.warning(f"{ticker}: no rows inserted")
            failed.append(ticker)

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
    return {
        'total_rows': total_inserted,
        'successful': len(successful),
        'failed': len(failed),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch benchmark ETF prices (IWM, MDY, QQQ, ^IXIC, URTH, ACWI) for multi-benchmark backtesting"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be fetched"
    )

    args = parser.parse_args()

    if args.dry_run:
        # Dry-run: no provenance
        fetch_benchmarks(dry_run=True)
    else:
        # Write mode: use provenance tracking
        # Get DB path from get_db function
        conn = get_db()
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        conn.close()

        with record_run(db_path, 'benchmark_etfs') as run:
            stats = fetch_benchmarks(dry_run=False)
            run.rows_written = stats['total_rows']
            run.coverage(stats['successful'], len(BENCHMARK_ETFS))
            run.permanent_failures = stats['failed']

        logger.info("")
        logger.info(f"Provenance recorded: source='benchmark_etfs'")
