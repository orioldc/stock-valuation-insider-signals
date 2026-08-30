#!/usr/bin/env python3
"""
Price Backfill Script — Extend price coverage back to 2019-01-01.

Fetches daily closes for the gap window (2019-01-01 → 2021-02-16) for tickers
with insider purchases in that period, plus SPY for benchmark continuity.

Features:
  - Self-healing: derives work list from DB state, not checkpoint files
  - Resumable: checkpoint tracking saves progress within a run
  - Persistent failure tracking: permanent failures (delisted/malformed) stored
    in DB to avoid retrying them across runs
  - Batched: downloads multiple tickers at once for efficiency
  - Tolerant: individual ticker failures don't stop the run
  - Dry-run mode: reports what would be fetched without writing
  - Coverage reporting: before/after row counts, date ranges, success/failure stats

On a DB with coverage already present, this is a no-op. On a DB missing coverage
(e.g., first CI run seeded from old release), it backfills once; coverage then
travels forward in the published DB artifact.
"""

import sys
import os
import sqlite3
import time
import json
import logging
import argparse
from datetime import datetime
import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "..", "db", "insider_signals.db")
CHECKPOINT_DIR = os.path.join(SCRIPT_DIR, "..", "checkpoints")

# Backfill window
START_DATE = "2019-01-01"
END_DATE = "2021-02-16"  # Exclusive end - existing coverage starts here
BATCH_SIZE = 50
RATE_LIMIT_DELAY = 2.0  # seconds between batches (increased from 1.0 to avoid rate limits)


def get_db():
    """Get database connection."""
    return sqlite3.connect(DB_PATH)


def _ensure_failures_table(conn):
    """
    Create price_backfill_failures table if it doesn't exist.

    This table persists permanent failures (delisted, malformed tickers) so we
    don't retry them every run. Created here rather than init_db.py because CI
    seeds from the previous release DB and only runs init_db.py on full rebuild.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_backfill_failures (
            ticker TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            last_attempt TEXT NOT NULL
        )
    """)
    conn.commit()


def _checkpoint_path():
    """Get checkpoint file path for backfill."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, "backfill_prices.json")


def _load_checkpoint():
    """
    Load checkpoint of resolved tickers.

    Returns dict mapping ticker -> reason.
    Handles old format (list of tickers) gracefully by checking actual DB state.
    """
    path = _checkpoint_path()
    if not os.path.exists(path):
        return {}

    with open(path, "r") as f:
        data = json.load(f)

    # New format: {"resolved": {ticker: reason}, "updated": "..."}
    if "resolved" in data:
        return data["resolved"]

    # Old format: {"completed": [ticker, ...], "updated": "..."}
    # Migrate by checking which ones actually have pre-2021 data
    if "completed" in data:
        logger.info("Migrating old checkpoint format...")
        conn = get_db()
        cur = conn.cursor()

        # Get tickers with actual pre-2021 data
        cur.execute("""
            SELECT DISTINCT ticker
            FROM prices
            WHERE date < '2021-02-16'
        """)
        tickers_with_data = {row[0] for row in cur.fetchall()}
        conn.close()

        resolved = {}
        for ticker in data["completed"]:
            if ticker in tickers_with_data:
                resolved[ticker] = "success (migrated)"
            # Else: don't migrate, let it retry

        logger.info(f"  Migrated {len(resolved)} successes, {len(data['completed']) - len(resolved)} will retry")
        return resolved

    return {}


def _save_checkpoint(resolved_dict):
    """
    Save checkpoint of resolved tickers.

    Args:
        resolved_dict: {ticker: reason} where reason is one of:
            - "success" (data written)
            - "delisted" (permanent: 404, symbol does not exist)
            - "malformed" (permanent: invalid ticker symbol)
            - "rate_limited" (transient: will retry)
            - "timeout" (transient: will retry)
            - etc.
    """
    path = _checkpoint_path()
    with open(path, "w") as f:
        json.dump({
            "resolved": resolved_dict,
            "updated": datetime.now().isoformat()
        }, f, indent=2)


def get_coverage_stats(conn):
    """Get current price coverage statistics."""
    cur = conn.cursor()

    # Overall stats
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM prices")
    row = cur.fetchone()

    stats = {
        'total_rows': row[0],
        'distinct_tickers': row[1],
        'min_date': row[2],
        'max_date': row[3]
    }

    # SPY coverage check
    cur.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM prices WHERE ticker = 'SPY'")
    spy_row = cur.fetchone()
    stats['spy_min'] = spy_row[0]
    stats['spy_max'] = spy_row[1]
    stats['spy_rows'] = spy_row[2]

    return stats


def get_target_tickers(conn):
    """
    Get list of tickers to backfill.

    Returns tickers with insider purchases in the gap window that lack price
    coverage for it, plus SPY if it lacks coverage. Excludes tickers listed in
    the failures table (permanent failures).

    On a DB with coverage already, this returns near-empty list (no-op).
    On a DB missing coverage (first CI run), it does the work once.
    Coverage then travels forward in the published DB artifact.
    """
    cur = conn.cursor()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Get tickers with known permanent failures
    cur.execute("SELECT ticker FROM price_backfill_failures")
    failed_tickers = {row[0] for row in cur.fetchall()}
    if failed_tickers:
        logger.info(f"Skipping {len(failed_tickers)} tickers with known permanent failures")

    # Get all tickers with purchases in gap window
    query = """
        SELECT DISTINCT c.ticker
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.shares_transacted > 0
          AND it.price > 0
          AND it.transaction_date >= ?
          AND it.transaction_date < ?
          AND c.ticker != 'NONE'
        ORDER BY c.ticker
    """
    cur.execute(query, (START_DATE, END_DATE))
    candidates = [row[0] for row in cur.fetchall()]

    # Get all tickers with existing coverage in gap window (bulk query)
    cur.execute("""
        SELECT DISTINCT ticker
        FROM prices
        WHERE date >= ?
          AND date < ?
    """, (START_DATE, END_DATE))
    tickers_with_coverage = {row[0] for row in cur.fetchall()}

    # Filter to tickers that need backfilling
    tickers_needing_backfill = [
        t for t in candidates
        if t not in failed_tickers and t not in tickers_with_coverage
    ]

    # Add benchmark ETFs unconditionally if they need backfilling
    # Benchmarks have no insider purchases, so they're skipped by the above query,
    # but they're required for size-matched backtesting - missing benchmarks
    # silently corrupt excess return calculations, so make them unconditional
    BENCHMARK_ETFS = ['SPY', 'IWM', 'MDY']
    for benchmark in BENCHMARK_ETFS:
        if benchmark not in failed_tickers and benchmark not in tickers_with_coverage:
            if benchmark not in tickers_needing_backfill:
                tickers_needing_backfill.append(benchmark)

    logger.info(f"Candidates with purchases in gap: {len(candidates)}")
    logger.info(f"Tickers needing backfill: {len(tickers_needing_backfill)}")

    return tickers_needing_backfill


def classify_failure(error_msg):
    """
    Classify a yfinance error as permanent or transient.

    Returns (is_permanent, reason_label).

    Permanent failures (checkpoint to avoid retry):
      - Delisted, 404, symbol not found
      - Malformed ticker symbols

    Transient failures (do NOT checkpoint, will retry):
      - Rate limits (429, "Too Many Requests")
      - Timeouts, connection errors
    """
    error_lower = str(error_msg).lower()

    # Transient: rate limits
    if "rate limit" in error_lower or "too many requests" in error_lower or "429" in error_lower:
        return (False, "rate_limited")

    # Transient: network/timeout
    if "timeout" in error_lower or "connection" in error_lower or "network" in error_lower:
        return (False, "timeout")

    # Permanent: delisted/404
    if "delisted" in error_lower or "404" in error_lower or "not found" in error_lower:
        return (True, "delisted")

    # Permanent: no timezone (yfinance signal for bad ticker)
    if "no timezone" in error_lower:
        return (True, "malformed")

    # Permanent: no price data for period
    if "no data" in error_lower or "no price data" in error_lower:
        return (True, "no_data")

    # Unknown: treat as transient to allow retry
    return (False, f"unknown_error")


def fetch_prices_batch(tickers, start_date, end_date):
    """
    Fetch prices for a batch of tickers using yfinance.

    Returns dict mapping ticker -> DataFrame with columns [date, close].
    Tolerates individual ticker failures.
    """
    results = {}

    try:
        # Download with auto_adjust=True to get split/dividend-adjusted closes
        data = yf.download(
            tickers,
            start=start_date,
            end=end_date,
            progress=False,
            auto_adjust=True,
            threads=True
        )

        if data.empty:
            return results

        # Handle single ticker case (returns Series) vs multi-ticker (MultiIndex columns)
        if len(tickers) == 1:
            ticker = tickers[0]
            if 'Close' in data.columns:
                df = pd.DataFrame({
                    'date': data.index,
                    'close': data['Close'].values
                })
                df = df.dropna(subset=['close'])
                if len(df) > 0:
                    results[ticker] = df
        else:
            # Multi-ticker: data.columns is MultiIndex with (metric, ticker)
            if 'Close' in data.columns.get_level_values(0):
                close_data = data['Close']
                for ticker in close_data.columns:
                    series = close_data[ticker].dropna()
                    if len(series) > 0:
                        df = pd.DataFrame({
                            'date': series.index,
                            'close': series.values
                        })
                        results[ticker] = df

    except Exception as e:
        logger.warning(f"Batch download failed: {e}")

    return results


def insert_prices(conn, ticker, prices_df, dry_run=False):
    """
    Insert prices for a ticker using INSERT OR IGNORE (additive only).

    Returns number of rows inserted (0 in dry-run mode).
    """
    if dry_run:
        return len(prices_df)

    cur = conn.cursor()
    rows = [
        (ticker, str(row['date'].date()), float(row['close']))
        for _, row in prices_df.iterrows()
    ]

    cur.executemany(
        "INSERT OR IGNORE INTO prices (ticker, date, close) VALUES (?, ?, ?)",
        rows
    )

    return cur.rowcount


def _record_permanent_failure(conn, ticker, reason, dry_run=False):
    """
    Record a permanent failure in the DB so it won't be retried next run.

    Uses INSERT OR REPLACE to update last_attempt timestamp if already present.
    """
    if dry_run:
        return

    timestamp = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO price_backfill_failures (ticker, reason, last_attempt)
        VALUES (?, ?, ?)
    """, (ticker, reason, timestamp))
    conn.commit()


def _migrate_checkpoint_to_db(conn):
    """
    One-time migration: read permanent failures from checkpoint file and
    populate the failures table.

    This migration allows existing deployments to transition from checkpoint-based
    to DB-based failure tracking without re-trying all known-bad tickers.
    """
    resolved = _load_checkpoint()
    if not resolved:
        return

    # Identify permanent failures in checkpoint (exclude "success" entries)
    permanent_reasons = {"delisted", "malformed", "no_data"}
    failures_to_migrate = [
        (ticker, reason)
        for ticker, reason in resolved.items()
        if reason in permanent_reasons
    ]

    if not failures_to_migrate:
        logger.info("Checkpoint migration: no permanent failures to migrate")
        return

    # Check how many are already in DB
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM price_backfill_failures")
    existing = {row[0] for row in cur.fetchall()}

    to_insert = [(t, r, datetime.now().isoformat())
                 for t, r in failures_to_migrate
                 if t not in existing]

    if to_insert:
        logger.info(f"Checkpoint migration: inserting {len(to_insert)} permanent failures into DB")
        conn.executemany("""
            INSERT OR IGNORE INTO price_backfill_failures (ticker, reason, last_attempt)
            VALUES (?, ?, ?)
        """, to_insert)
        conn.commit()
    else:
        logger.info(f"Checkpoint migration: all {len(failures_to_migrate)} failures already in DB")


def run_backfill(dry_run=False, max_tickers=None):
    """
    Main backfill entry point.

    Fetches prices for gap window, inserts into DB with checkpointing.
    """
    start_time = time.time()

    conn = get_db()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Migrate checkpoint to DB (one-time transition)
    _migrate_checkpoint_to_db(conn)

    # Report coverage before
    logger.info("=" * 60)
    logger.info("PRICE BACKFILL — Coverage Before")
    logger.info("=" * 60)
    before = get_coverage_stats(conn)
    logger.info(f"  Total rows:       {before['total_rows']:,}")
    logger.info(f"  Distinct tickers: {before['distinct_tickers']:,}")
    logger.info(f"  Date range:       {before['min_date']} → {before['max_date']}")
    logger.info(f"  SPY coverage:     {before['spy_min']} → {before['spy_max']} ({before['spy_rows']} rows)")

    # Get target tickers (now derives work list from DB state)
    tickers = get_target_tickers(conn)

    if max_tickers:
        tickers = tickers[:max_tickers]
        logger.info(f"Limited to {max_tickers} tickers for testing")

    # Load checkpoint
    resolved = _load_checkpoint()
    logger.info(f"Checkpoint: {len(resolved)} tickers already resolved")

    # Report checkpoint breakdown
    if resolved:
        successes = sum(1 for r in resolved.values() if r == "success" or "success" in r)
        permanent_failures = sum(1 for r in resolved.values() if r in ["delisted", "malformed", "no_data"])
        logger.info(f"  Successes: {successes}")
        logger.info(f"  Known-delisted/malformed: {permanent_failures}")

    # Filter to pending tickers
    pending = [t for t in tickers if t not in resolved]
    logger.info(f"Pending: {len(pending)} tickers")

    if dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN MODE — No data will be written")
        logger.info("=" * 60)

    # Batch processing
    total_rows_inserted = 0
    successful = []
    failed_permanent = []
    failed_transient = []

    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i:i+BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"Batch {batch_num}/{total_batches}: fetching {len(batch)} tickers...")

        # Fetch prices with error capture
        try:
            prices_by_ticker = fetch_prices_batch(batch, START_DATE, END_DATE)
        except Exception as e:
            # Entire batch failed (likely rate limit or network issue)
            is_permanent, reason = classify_failure(str(e))
            logger.warning(f"Batch {batch_num} failed: {e}")

            if not is_permanent:
                # Transient: do NOT checkpoint, will retry entire batch next run
                for ticker in batch:
                    failed_transient.append((ticker, reason))
                logger.info(f"  Batch marked for retry ({reason})")
                continue
            else:
                # Permanent batch failure (unlikely but handle it)
                prices_by_ticker = {}

        # Insert per ticker
        for ticker in batch:
            if ticker in prices_by_ticker:
                prices_df = prices_by_ticker[ticker]

                try:
                    rows_inserted = insert_prices(conn, ticker, prices_df, dry_run=dry_run)

                    if not dry_run:
                        # Commit after each ticker for incremental consistency
                        conn.commit()

                    total_rows_inserted += rows_inserted
                    successful.append(ticker)
                    resolved[ticker] = "success"

                    logger.debug(f"  {ticker}: {rows_inserted} rows")

                except Exception as e:
                    logger.warning(f"  {ticker}: insert failed - {e}")
                    is_permanent, reason = classify_failure(str(e))

                    if is_permanent:
                        failed_permanent.append((ticker, reason))
                        resolved[ticker] = reason
                        _record_permanent_failure(conn, ticker, reason, dry_run=dry_run)
                    else:
                        failed_transient.append((ticker, reason))
                        # Do NOT checkpoint transient failures
            else:
                # No data returned - check yfinance stderr to classify
                # yfinance logs errors to stderr, which we can't easily capture,
                # so we conservatively treat "no data" as permanent only if
                # it looks like a known-bad ticker
                if ticker.startswith('*') or ticker.startswith('(') or ticker in ['[NONE]', '[N/A]', '-']:
                    # Malformed ticker symbol
                    reason = "malformed"
                    failed_permanent.append((ticker, reason))
                    resolved[ticker] = reason
                    _record_permanent_failure(conn, ticker, reason, dry_run=dry_run)
                else:
                    # Could be delisted or rate-limited; treat as permanent
                    # (rate limits usually error, not return empty)
                    reason = "no_data"
                    failed_permanent.append((ticker, reason))
                    resolved[ticker] = reason
                    _record_permanent_failure(conn, ticker, reason, dry_run=dry_run)

                logger.debug(f"  {ticker}: {reason}")

        # Save checkpoint after each batch
        if not dry_run:
            _save_checkpoint(resolved)

        # Progress report
        if (i + BATCH_SIZE) < len(pending):
            elapsed = time.time() - start_time
            logger.info(f"  Progress: {len(resolved)}/{len(tickers)} tickers, "
                       f"{total_rows_inserted:,} rows, {elapsed:.0f}s elapsed")

        # Rate limiting
        if (i + BATCH_SIZE) < len(pending):
            time.sleep(RATE_LIMIT_DELAY)

    # Report coverage after
    logger.info("=" * 60)
    logger.info("PRICE BACKFILL — Coverage After")
    logger.info("=" * 60)

    if not dry_run:
        after = get_coverage_stats(conn)
        logger.info(f"  Total rows:       {after['total_rows']:,} (+{after['total_rows'] - before['total_rows']:,})")
        logger.info(f"  Distinct tickers: {after['distinct_tickers']:,} (+{after['distinct_tickers'] - before['distinct_tickers']:,})")
        logger.info(f"  Date range:       {after['min_date']} → {after['max_date']}")
        logger.info(f"  SPY coverage:     {after['spy_min']} → {after['spy_max']} ({after['spy_rows']} rows, +{after['spy_rows'] - before['spy_rows']})")

        # Verify SPY has full coverage
        spy_expected_days = (pd.to_datetime(after['max_date']) - pd.to_datetime(after['spy_min'])).days
        spy_coverage_pct = (after['spy_rows'] / spy_expected_days) * 100 if spy_expected_days > 0 else 0
        logger.info(f"  SPY coverage:     {spy_coverage_pct:.1f}% of calendar days")

        if after['spy_min'] <= START_DATE:
            logger.info(f"  ✓ SPY coverage extends to {START_DATE} (backtest-ready)")
        else:
            logger.warning(f"  ✗ SPY coverage only starts at {after['spy_min']} (expected {START_DATE})")
    else:
        logger.info(f"  Would insert:     ~{total_rows_inserted:,} rows")

    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Tickers requested:      {len(tickers)}")
    logger.info(f"  Successful:             {len(successful)}")
    logger.info(f"  Failed (permanent):     {len(failed_permanent)}")
    logger.info(f"  Failed (transient):     {len(failed_transient)}")
    logger.info(f"  Rows inserted:          {total_rows_inserted:,}")
    logger.info(f"  Runtime:                {time.time() - start_time:.0f}s")

    if failed_permanent:
        logger.info(f"\nPermanent failures: {len(failed_permanent)} (checkpointed, won't retry)")
        if len(failed_permanent) <= 30:
            for ticker, reason in failed_permanent[:30]:
                logger.info(f"  {ticker}: {reason}")
        else:
            # Show breakdown by reason
            from collections import Counter
            reason_counts = Counter(r for _, r in failed_permanent)
            logger.info("  Breakdown by reason:")
            for reason, count in reason_counts.most_common():
                logger.info(f"    {reason}: {count}")

    if failed_transient:
        logger.info(f"\nTransient failures: {len(failed_transient)} (NOT checkpointed, will retry next run)")
        if len(failed_transient) <= 30:
            for ticker, reason in failed_transient[:30]:
                logger.info(f"  {ticker}: {reason}")
        else:
            from collections import Counter
            reason_counts = Counter(r for _, r in failed_transient)
            logger.info("  Breakdown by reason:")
            for reason, count in reason_counts.most_common():
                logger.info(f"    {reason}: {count}")

    # Report actual pre-2021 coverage
    if not dry_run:
        logger.info("=" * 60)
        logger.info("PRE-2021 COVERAGE VERIFICATION")
        logger.info("=" * 60)

        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(DISTINCT ticker)
            FROM prices
            WHERE date < '2021-02-16'
        """)
        tickers_with_pre2021 = cur.fetchone()[0]
        logger.info(f"  Tickers with pre-2021 data: {tickers_with_pre2021:,}")
        logger.info(f"  Checkpoint resolved:        {len(resolved):,}")
        logger.info(f"  Checkpointed but no data:   {len(resolved) - tickers_with_pre2021:,}")

    conn.close()

    return {
        'total_rows': total_rows_inserted,
        'successful': len(successful),
        'failed_permanent': len(failed_permanent),
        'failed_transient': len(failed_transient)
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill price data from 2019-01-01 to 2021-02-16"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be fetched without writing to DB"
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        help="Limit to N tickers (testing)"
    )
    parser.add_argument(
        "--clear-checkpoint",
        action="store_true",
        help="Clear checkpoint and start fresh"
    )

    args = parser.parse_args()

    if args.clear_checkpoint:
        path = _checkpoint_path()
        if os.path.exists(path):
            os.remove(path)
            logger.info("Checkpoint cleared")

    run_backfill(dry_run=args.dry_run, max_tickers=args.max_tickers)
