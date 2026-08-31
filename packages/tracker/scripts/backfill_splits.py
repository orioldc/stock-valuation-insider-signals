#!/usr/bin/env python3
"""
Split History Backfill — Fetch stock split events for all companies.

Fetches split history from yfinance and stores it in the split_events table.
Split data is used by compute_share_delta to discriminate reverse splits from
genuine buybacks and data errors.

Features:
  - Fetches full split history for all companies in the database
  - Persistent failure tracking to avoid retrying delisted/invalid tickers
  - Batched downloads for efficiency
  - Dry-run mode for testing
  - Resumable via checkpoint

Usage:
    python backfill_splits.py              # dry-run
    python backfill_splits.py --write      # populate database
    python backfill_splits.py --ticker AAPL --write  # single ticker (for testing)
"""

import sys
import os
import sqlite3
import time
import json
import logging
import argparse
from datetime import datetime
from collections import defaultdict
import yfinance as yf

# Add tracker to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "..", "db", "insider_signals.db")
CHECKPOINT_DIR = os.path.join(SCRIPT_DIR, "..", "checkpoints")

# Rate limiting for yfinance
BATCH_SIZE = 100  # Splits are lightweight, can batch larger
RATE_LIMIT_DELAY = 1.0  # seconds between batches


def get_db():
    """Get database connection with busy timeout."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def _ensure_table(conn):
    """Ensure split_events table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS split_events (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            ratio REAL NOT NULL,
            source TEXT DEFAULT 'yfinance',
            last_updated TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_split_ticker ON split_events(ticker)")
    conn.commit()


def _checkpoint_path():
    """Get checkpoint file path."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, "backfill_splits.json")


def _load_checkpoint():
    """Load checkpoint of processed tickers. Returns dict {ticker: status}."""
    path = _checkpoint_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        data = json.load(f)
    return data.get("processed", {})


def _save_checkpoint(processed_dict):
    """Save checkpoint of processed tickers."""
    path = _checkpoint_path()
    with open(path, "w") as f:
        json.dump({
            "processed": processed_dict,
            "updated": datetime.now().isoformat()
        }, f, indent=2)


def fetch_splits(ticker):
    """
    Fetch split history for a ticker from yfinance.

    Returns:
        - List of (date_str, ratio) tuples on success
        - None on failure (delisted, invalid, or error)
    """
    try:
        t = yf.Ticker(ticker)
        splits = t.splits

        if splits is None or len(splits) == 0:
            return []  # No splits is success (most tickers have none)

        # Convert pandas Series to list of (date, ratio) tuples
        # Split ratio format: 2.0 means 2-for-1 split (shares doubled)
        # 0.5 means 1-for-2 reverse split (shares halved)
        result = [(idx.strftime("%Y-%m-%d"), float(val)) for idx, val in splits.items()]
        return result

    except Exception as e:
        error_msg = str(e).lower()
        if "404" in error_msg or "not found" in error_msg:
            logger.debug(f"{ticker}: Not found (likely delisted)")
            return None  # Permanent failure
        elif "invalid" in error_msg or "malformed" in error_msg:
            logger.debug(f"{ticker}: Invalid ticker symbol")
            return None  # Permanent failure
        else:
            logger.warning(f"{ticker}: Error fetching splits: {e}")
            return None  # Transient failure (could retry)


def get_target_tickers(conn, single_ticker=None):
    """
    Get list of tickers to fetch splits for.

    Args:
        conn: Database connection
        single_ticker: If provided, only fetch this ticker (for testing)

    Returns:
        List of ticker strings
    """
    cur = conn.cursor()

    if single_ticker:
        # Check ticker exists in companies table
        cur.execute("SELECT ticker FROM companies WHERE ticker = ?", (single_ticker,))
        if cur.fetchone():
            return [single_ticker]
        else:
            logger.error(f"Ticker {single_ticker} not found in companies table")
            return []

    # Get all tickers that don't have split data yet
    cur.execute("""
        SELECT c.ticker
        FROM companies c
        LEFT JOIN split_events s ON c.ticker = s.ticker
        WHERE s.ticker IS NULL
        ORDER BY c.ticker
    """)

    return [row[0] for row in cur.fetchall()]


def backfill_splits(args):
    """Main backfill logic."""
    conn = get_db()
    _ensure_table(conn)

    # Get target tickers
    targets = get_target_tickers(conn, args.ticker)
    logger.info(f"Found {len(targets)} tickers to process")

    if not targets:
        logger.info("No tickers to process")
        conn.close()
        return

    # Load checkpoint
    checkpoint = _load_checkpoint()
    already_processed = set(checkpoint.keys())

    # Filter out already-processed tickers
    remaining = [t for t in targets if t not in already_processed]
    if len(remaining) < len(targets):
        logger.info(f"Skipping {len(targets) - len(remaining)} already-processed tickers from checkpoint")
    targets = remaining

    if not targets:
        logger.info("All tickers already processed (per checkpoint)")
        conn.close()
        return

    # Counters
    stats = defaultdict(int)
    new_splits = []

    # Process in batches
    for i in range(0, len(targets), BATCH_SIZE):
        batch = targets[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(targets) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"\nBatch {batch_num}/{total_batches}: Processing {len(batch)} tickers")

        for ticker in batch:
            splits = fetch_splits(ticker)

            if splits is None:
                # Permanent failure
                stats['failed'] += 1
                checkpoint[ticker] = 'failed'
            elif len(splits) == 0:
                # Success: no splits
                stats['no_splits'] += 1
                checkpoint[ticker] = 'no_splits'
            else:
                # Success: found splits
                stats['with_splits'] += 1
                stats['total_split_events'] += len(splits)
                checkpoint[ticker] = f'{len(splits)}_splits'

                for date_str, ratio in splits:
                    new_splits.append((ticker, date_str, ratio))

            # Progress
            if (stats['failed'] + stats['no_splits'] + stats['with_splits']) % 50 == 0:
                processed = stats['failed'] + stats['no_splits'] + stats['with_splits']
                logger.info(f"  Progress: {processed}/{len(targets)} tickers processed")

        # Save checkpoint after each batch
        _save_checkpoint(checkpoint)

        # Rate limit
        if i + BATCH_SIZE < len(targets):
            time.sleep(RATE_LIMIT_DELAY)

    # Write to database
    if args.write and new_splits:
        logger.info(f"\nWriting {len(new_splits)} split events to database...")
        cur = conn.cursor()
        cur.executemany("""
            INSERT OR REPLACE INTO split_events (ticker, date, ratio, source)
            VALUES (?, ?, ?, 'yfinance')
        """, new_splits)
        conn.commit()
        logger.info("✓ Splits written to database")
    elif not args.write:
        logger.info(f"\nDRY RUN: Would write {len(new_splits)} split events")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Tickers processed: {len(targets)}")
    logger.info(f"  With splits: {stats['with_splits']} tickers ({stats['total_split_events']} events)")
    logger.info(f"  No splits: {stats['no_splits']} tickers")
    logger.info(f"  Failed: {stats['failed']} tickers")

    if stats['with_splits'] > 0:
        logger.info(f"\nAverage splits per ticker (for tickers with splits): {stats['total_split_events'] / stats['with_splits']:.1f}")

    if not args.write:
        logger.info("\nRun with --write to populate database")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill split history from yfinance")
    parser.add_argument("--write", action="store_true", help="Write to database (default: dry-run)")
    parser.add_argument("--ticker", type=str, help="Process single ticker only (for testing)")
    args = parser.parse_args()

    dry_run_str = "DRY RUN" if not args.write else "WRITE MODE"
    logger.info("=" * 60)
    logger.info(f"SPLIT HISTORY BACKFILL — {dry_run_str}")
    logger.info("=" * 60)
    logger.info(f"Database: {DB_PATH}")
    logger.info("")

    backfill_splits(args)


if __name__ == "__main__":
    main()
