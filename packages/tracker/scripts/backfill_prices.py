#!/usr/bin/env python3
"""
Price Backfill Script — Extend price coverage to all companies.

Fetches maximum available daily price history for all companies in the database,
resolving stale ticker symbols via SEC's authoritative company_tickers.json map.

Features:
  - Complete coverage: fetches prices for ALL companies, not just gap-window subset
  - Ticker resolution: maps stale tickers to current symbols via SEC CIK registry
  - Maximum history: fetches full available history, not just gap window
  - Self-healing: derives work list from DB state, not checkpoint files
  - Resumable: checkpoint tracking saves progress within a run
  - Persistent failure tracking: permanent failures (delisted/malformed) stored
    in DB to avoid retrying them across runs
  - Re-tests failures: gives failed tickers one more attempt with resolved symbols
  - Batched: downloads multiple tickers at once for efficiency
  - Tolerant: individual ticker failures don't stop the run
  - Dry-run mode: reports what would be fetched without writing
  - Coverage reporting: before/after row counts, date ranges, success/failure stats

Design:
  - Fetch symbol: Use stored ticker if valid for CIK, else SEC's current ticker
  - Storage: Always write under existing companies.ticker (join key unchanged)
  - Where stored != fetch, log the mismatch for separate rename decision

On a DB with coverage already present, this extends it. On a DB missing coverage
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
from collections import defaultdict
import urllib.request
import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "..", "db", "insider_signals.db")
CHECKPOINT_DIR = os.path.join(SCRIPT_DIR, "..", "checkpoints")

# Backfill window — now fetches maximum history, not just gap
START_DATE = "1970-01-01"  # yfinance maximum lookback
END_DATE = datetime.now().strftime("%Y-%m-%d")  # Today
BATCH_SIZE = 50
RATE_LIMIT_DELAY = 2.0  # seconds between batches (increased from 1.0 to avoid rate limits)

# SEC ticker map cache (global, fetched once per run)
_SEC_TICKER_MAP = None


def get_db():
    """Get database connection with busy timeout."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 60000")  # 60s timeout for concurrent writes
    return conn


def _fetch_sec_ticker_map():
    """
    Fetch SEC's authoritative company_tickers.json map.

    Returns dict mapping CIK (int) -> set of ticker strings.

    The SEC file is keyed by index, with each entry having cik_str, ticker, title.
    One CIK can have multiple listings (preferred shares, share classes), so we
    collect ALL tickers per CIK into a set. Treating it as a simple CIK->ticker
    dict by overwriting produces nonsense like BAC -> MER-PK (last entry wins).
    """
    global _SEC_TICKER_MAP
    if _SEC_TICKER_MAP is not None:
        return _SEC_TICKER_MAP

    url = "https://www.sec.gov/files/company_tickers.json"

    # SEC requires User-Agent format: "Company/App Contact@email.com"
    # See: https://www.sec.gov/os/accessing-edgar-data
    req = urllib.request.Request(url, headers={
        "User-Agent": "InsiderSignalTracker oriol.diaz@ozoneproject.com",
        "Accept": "application/json"
    })

    # Retry with exponential backoff
    for attempt in range(3):
        try:
            logger.info(f"Fetching SEC company_tickers.json (attempt {attempt + 1}/3)...")
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))

            # Build CIK -> set of tickers
            cik_to_tickers = defaultdict(set)
            for entry in data.values():
                cik = int(entry['cik_str'])
                ticker = entry['ticker'].upper()  # Normalize to uppercase
                cik_to_tickers[cik].add(ticker)

            _SEC_TICKER_MAP = dict(cik_to_tickers)
            logger.info(f"  Loaded {len(_SEC_TICKER_MAP):,} CIKs with ticker mappings")

            return _SEC_TICKER_MAP

        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt < 2:  # Don't sleep on last attempt
                sleep_time = 2 ** attempt  # 1s, 2s
                logger.info(f"  Retrying in {sleep_time}s...")
                time.sleep(sleep_time)

    logger.warning("Failed to fetch SEC ticker map after 3 attempts")
    logger.warning("Will fall back to stored tickers for all companies")
    logger.warning("(This means stale ticker symbols won't be resolved)")
    return {}


def resolve_ticker_symbol(stored_ticker, cik, sec_map):
    """
    Resolve the ticker symbol to use for fetching prices.

    Args:
        stored_ticker: Ticker from companies table (join key, never modified)
        cik: CIK from companies table
        sec_map: Dict mapping CIK -> set of valid tickers from SEC

    Returns:
        (fetch_symbol, is_resolved, reason)

        fetch_symbol: Symbol to use for yfinance download
        is_resolved: True if stored ticker is invalid and we resolved to a different one
        reason: Human-readable explanation

    Logic:
        1. If CIK is None or not in SEC map: use stored ticker (no better option)
        2. If stored ticker is in SEC's set for this CIK: use it (valid)
        3. Otherwise: pick SEC's "primary" ticker (first alphabetically) and flag it
    """
    if not cik or cik not in sec_map:
        return (stored_ticker, False, "no_sec_data")

    valid_tickers = sec_map[cik]

    if stored_ticker.upper() in valid_tickers:
        return (stored_ticker, False, "valid")

    # Stored ticker is stale/invalid - resolve to SEC's current ticker
    # Pick first alphabetically as "primary" (arbitrary but deterministic)
    resolved = sorted(valid_tickers)[0]

    return (resolved, True, f"resolved_{stored_ticker}_to_{resolved}")


def _log_ticker_mismatches(mismatches, output_file=None):
    """
    Log ticker mismatches (stored vs. fetch symbol).

    Args:
        mismatches: List of (stored_ticker, fetch_symbol, cik, name) tuples
        output_file: Optional path to write CSV
    """
    if not mismatches:
        logger.info("No ticker mismatches (all stored tickers are valid)")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"TICKER SYMBOL MISMATCHES: {len(mismatches)} companies")
    logger.info(f"{'='*60}")
    logger.info("Stored ticker is not current for CIK - fetching with resolved symbol")
    logger.info("(Storage still uses stored ticker as join key - no renames)")
    logger.info("")

    # Show top mismatches by transaction count if we have that data
    # For now, just show first 20 and save full list to file
    for i, (stored, fetched, cik, name) in enumerate(mismatches[:20]):
        logger.info(f"  {stored:6s} -> {fetched:6s}  CIK {cik:8d}  {name}")

    if len(mismatches) > 20:
        logger.info(f"  ... and {len(mismatches) - 20} more")

    # Write full list to file if requested
    if output_file:
        try:
            with open(output_file, 'w') as f:
                f.write("stored_ticker,fetch_symbol,cik,company_name\n")
                for stored, fetched, cik, name in mismatches:
                    # Escape commas in company name
                    name_escaped = name.replace('"', '""')
                    f.write(f'{stored},{fetched},{cik},"{name_escaped}"\n')
            logger.info(f"\nFull mismatch list saved to: {output_file}")
        except Exception as e:
            logger.warning(f"Failed to write mismatch file: {e}")


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


def get_target_tickers(conn, retest_failures=True):
    """
    Get list of (ticker, fetch_symbol, cik, name) tuples to backfill.

    Returns ALL companies lacking price coverage, plus companies from failures
    table for re-testing with resolved symbols. Resolves stale tickers via SEC map.

    Args:
        conn: Database connection
        retest_failures: If True, include companies from price_backfill_failures
                        for one more attempt with resolved ticker symbols

    Returns:
        List of (stored_ticker, fetch_symbol, cik, company_name) tuples
        where stored_ticker is the join key, fetch_symbol is what to download
    """
    cur = conn.cursor()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Fetch SEC ticker map once per run
    sec_map = _fetch_sec_ticker_map()

    # Get all companies
    cur.execute("""
        SELECT ticker, cik, name
        FROM companies
        WHERE ticker != 'NONE'
        ORDER BY ticker
    """)
    all_companies = cur.fetchall()

    logger.info(f"Total companies in DB: {len(all_companies):,}")

    # Get tickers with ANY existing price coverage
    cur.execute("SELECT DISTINCT ticker FROM prices")
    tickers_with_coverage = {row[0] for row in cur.fetchall()}
    logger.info(f"Companies with prices: {len(tickers_with_coverage):,}")

    # Get known failures
    cur.execute("SELECT ticker FROM price_backfill_failures")
    failed_tickers = {row[0] for row in cur.fetchall()}
    logger.info(f"Previously failed: {len(failed_tickers):,}")

    # Build work list with ticker resolution
    work_list = []
    ticker_mismatches = []

    for stored_ticker, cik, name in all_companies:
        # Resolve ticker symbol
        fetch_symbol, is_resolved, reason = resolve_ticker_symbol(stored_ticker, cik, sec_map)

        # Track mismatches for reporting
        if is_resolved:
            ticker_mismatches.append((stored_ticker, fetch_symbol, cik, name or ""))

        # Decide whether to include in work list
        if stored_ticker in tickers_with_coverage:
            # Already have prices for this ticker - skip (additive only)
            continue

        if stored_ticker in failed_tickers and not retest_failures:
            # Skip known failures unless retesting
            continue

        # Include in work list
        work_list.append((stored_ticker, fetch_symbol, cik, name or ""))

    # Add benchmark ETFs unconditionally (required for backtesting)
    BENCHMARK_ETFS = [
        ('SPY', None, 'SPDR S&P 500 ETF Trust'),
        ('IWM', None, 'iShares Russell 2000 ETF'),
        ('MDY', None, 'SPDR S&P MidCap 400 ETF Trust')
    ]
    for ticker, cik, name in BENCHMARK_ETFS:
        if ticker not in tickers_with_coverage:
            # ETFs don't have CIKs in our DB, fetch with stored ticker
            if not any(w[0] == ticker for w in work_list):
                work_list.append((ticker, ticker, cik, name))

    # Report ticker resolution stats
    if retest_failures and failed_tickers:
        retest_count = sum(1 for t, _, _, _ in work_list if t in failed_tickers)
        logger.info(f"Re-testing {retest_count} previously failed tickers with resolved symbols")

    logger.info(f"Companies needing prices: {len(work_list):,}")
    logger.info(f"  With ticker mismatches: {len(ticker_mismatches):,}")

    # Log ticker mismatches
    if ticker_mismatches:
        mismatch_file = os.path.join(CHECKPOINT_DIR, "ticker_mismatches.csv")
        _log_ticker_mismatches(ticker_mismatches, output_file=mismatch_file)

    return work_list


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


def run_backfill(dry_run=False, max_tickers=None, size_check=False):
    """
    Main backfill entry point.

    Fetches maximum available price history for all companies, inserts into DB
    with checkpointing. Resolves stale ticker symbols via SEC map.

    Args:
        dry_run: If True, report what would be done without writing
        max_tickers: Limit to N tickers for testing
        size_check: If True, estimate row count and DB growth, then exit
    """
    start_time = time.time()

    conn = get_db()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Migrate checkpoint to DB (one-time transition)
    _migrate_checkpoint_to_db(conn)

    # Purge stale failure records for tickers that now have prices
    # This prevents the bug where a ticker is in both prices and failures tables
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM price_backfill_failures
        WHERE ticker IN (
            SELECT DISTINCT ticker FROM prices
        )
    """)
    purged = cur.rowcount
    if purged > 0:
        logger.info(f"Purged {purged} stale failure records for tickers with current prices")
        conn.commit()

    # Get DB size before
    db_size_before = os.path.getsize(DB_PATH) / (1024 * 1024)  # MB

    # Report coverage before
    logger.info("=" * 60)
    logger.info("PRICE BACKFILL — Coverage Before")
    logger.info("=" * 60)
    before = get_coverage_stats(conn)
    logger.info(f"  Total rows:       {before['total_rows']:,}")
    logger.info(f"  Distinct tickers: {before['distinct_tickers']:,}")
    logger.info(f"  Date range:       {before['min_date']} → {before['max_date']}")
    logger.info(f"  SPY coverage:     {before['spy_min']} → {before['spy_max']} ({before['spy_rows']} rows)")
    logger.info(f"  DB size:          {db_size_before:.2f} MB")

    # Get companies in DB
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM companies WHERE ticker != 'NONE'")
    total_companies = cur.fetchone()[0]

    coverage_pct = (before['distinct_tickers'] / total_companies * 100) if total_companies > 0 else 0
    logger.info(f"  Coverage:         {before['distinct_tickers']:,} / {total_companies:,} ({coverage_pct:.1f}%)")

    # Get target tickers with resolution
    work_list = get_target_tickers(conn, retest_failures=True)

    if max_tickers:
        work_list = work_list[:max_tickers]
        logger.info(f"Limited to {max_tickers} tickers for testing")

    # Size estimation mode
    if size_check:
        logger.info("=" * 60)
        logger.info("SIZE ESTIMATION MODE")
        logger.info("=" * 60)

        # Estimate: ~252 trading days/year * years since 1970
        years_of_data = datetime.now().year - 1970
        estimated_rows_per_ticker = years_of_data * 252

        total_estimated_rows = len(work_list) * estimated_rows_per_ticker

        # Estimate DB growth: ~24 bytes per row (ticker, date, close, indexes)
        estimated_growth_mb = (total_estimated_rows * 24) / (1024 * 1024)
        estimated_final_size_mb = db_size_before + estimated_growth_mb

        logger.info(f"  Companies needing prices: {len(work_list):,}")
        logger.info(f"  Estimated rows/ticker:    ~{estimated_rows_per_ticker:,}")
        logger.info(f"  Total estimated rows:     ~{total_estimated_rows:,}")
        logger.info(f"  Current DB size:          {db_size_before:.2f} MB")
        logger.info(f"  Estimated growth:         ~{estimated_growth_mb:.2f} MB")
        logger.info(f"  Estimated final size:     ~{estimated_final_size_mb:.2f} MB")

        if estimated_final_size_mb > 4000:
            logger.warning(f"\n⚠️  WARNING: Estimated final size ({estimated_final_size_mb:.0f} MB) exceeds 4 GB")
            logger.warning("   This may cause issues with the release artifact")

        logger.info("\nPausing for review. Run without --size-check to proceed.")
        return {
            'total_rows': 0,
            'successful': 0,
            'failed_permanent': 0,
            'failed_transient': 0,
            'size_check': True,
            'estimated_final_size_mb': estimated_final_size_mb
        }

    # Load checkpoint
    resolved = _load_checkpoint()
    logger.info(f"Checkpoint: {len(resolved)} tickers already resolved")

    # Report checkpoint breakdown
    if resolved:
        successes = sum(1 for r in resolved.values() if r == "success" or "success" in r)
        permanent_failures = sum(1 for r in resolved.values() if r in ["delisted", "malformed", "no_data"])
        logger.info(f"  Successes: {successes}")
        logger.info(f"  Known-delisted/malformed: {permanent_failures}")

    # Filter to pending work items (check stored_ticker against checkpoint)
    pending = [item for item in work_list if item[0] not in resolved]
    logger.info(f"Pending: {len(pending)} companies")

    if dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN MODE — No data will be written")
        logger.info("=" * 60)

    # Batch processing
    total_rows_inserted = 0
    successful = []
    failed_permanent = []
    failed_transient = []
    rescued_from_failures = []

    # Track tickers that were in failures table before this run
    cur = conn.cursor()
    cur.execute("SELECT ticker FROM price_backfill_failures")
    previously_failed = {row[0] for row in cur.fetchall()}

    for i in range(0, len(pending), BATCH_SIZE):
        batch_items = pending[i:i+BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"Batch {batch_num}/{total_batches}: fetching {len(batch_items)} companies...")

        # Extract fetch symbols for yfinance (dedupe in case of collisions)
        fetch_symbols = list(set(item[1] for item in batch_items))

        # Fetch prices with error capture
        try:
            prices_by_ticker = fetch_prices_batch(fetch_symbols, START_DATE, END_DATE)
        except Exception as e:
            # Entire batch failed (likely rate limit or network issue)
            is_permanent, reason = classify_failure(str(e))
            logger.warning(f"Batch {batch_num} failed: {e}")

            if not is_permanent:
                # Transient: do NOT checkpoint, will retry entire batch next run
                for stored_ticker, fetch_symbol, cik, name in batch_items:
                    failed_transient.append((stored_ticker, reason))
                logger.info(f"  Batch marked for retry ({reason})")
                continue
            else:
                # Permanent batch failure (unlikely but handle it)
                prices_by_ticker = {}

        # Insert per company (stored_ticker is storage key, fetch_symbol is download key)
        for stored_ticker, fetch_symbol, cik, name in batch_items:
            if fetch_symbol in prices_by_ticker:
                prices_df = prices_by_ticker[fetch_symbol]

                try:
                    # CRITICAL: Store under stored_ticker, not fetch_symbol
                    # stored_ticker is the join key used throughout the system
                    rows_inserted = insert_prices(conn, stored_ticker, prices_df, dry_run=dry_run)

                    if not dry_run:
                        # Commit after each ticker for incremental consistency
                        conn.commit()

                    total_rows_inserted += rows_inserted
                    successful.append(stored_ticker)
                    resolved[stored_ticker] = "success"

                    # Write-through: success invalidates any existing failure record
                    # Always delete from failures table on success, not just if previously_failed
                    if not dry_run:
                        conn.execute("DELETE FROM price_backfill_failures WHERE ticker = ?",
                                   (stored_ticker,))
                        conn.commit()

                    # Track if this was a rescue from failures table
                    if stored_ticker in previously_failed:
                        rescued_from_failures.append((stored_ticker, fetch_symbol))

                    log_msg = f"  {stored_ticker}: {rows_inserted} rows"
                    if fetch_symbol != stored_ticker:
                        log_msg += f" (fetched as {fetch_symbol})"
                    logger.debug(log_msg)

                except Exception as e:
                    logger.warning(f"  {stored_ticker}: insert failed - {e}")
                    is_permanent, reason = classify_failure(str(e))

                    if is_permanent:
                        failed_permanent.append((stored_ticker, reason))
                        resolved[stored_ticker] = reason
                        _record_permanent_failure(conn, stored_ticker, reason, dry_run=dry_run)
                    else:
                        failed_transient.append((stored_ticker, reason))
                        # Do NOT checkpoint transient failures
            else:
                # No data returned for fetch_symbol
                # Classify based on ticker format
                if stored_ticker.startswith('*') or stored_ticker.startswith('(') or stored_ticker in ['[NONE]', '[N/A]', '-']:
                    # Malformed ticker symbol
                    reason = "malformed"
                    failed_permanent.append((stored_ticker, reason))
                    resolved[stored_ticker] = reason
                    _record_permanent_failure(conn, stored_ticker, reason, dry_run=dry_run)
                else:
                    # Could be delisted or no data available; treat as permanent
                    # (rate limits usually error, not return empty)
                    reason = "no_data"
                    failed_permanent.append((stored_ticker, reason))
                    resolved[stored_ticker] = reason
                    _record_permanent_failure(conn, stored_ticker, reason, dry_run=dry_run)

                log_msg = f"  {stored_ticker}: {reason}"
                if fetch_symbol != stored_ticker:
                    log_msg += f" (tried {fetch_symbol})"
                logger.debug(log_msg)

        # Save checkpoint after each batch
        if not dry_run:
            _save_checkpoint(resolved)

        # Progress report
        if (i + BATCH_SIZE) < len(pending):
            elapsed = time.time() - start_time
            completed = len(resolved)
            total = len(work_list)
            logger.info(f"  Progress: {completed}/{total} companies, "
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
        db_size_after = os.path.getsize(DB_PATH) / (1024 * 1024)  # MB

        logger.info(f"  Total rows:       {after['total_rows']:,} (+{after['total_rows'] - before['total_rows']:,})")
        logger.info(f"  Distinct tickers: {after['distinct_tickers']:,} (+{after['distinct_tickers'] - before['distinct_tickers']:,})")
        logger.info(f"  Date range:       {after['min_date']} → {after['max_date']}")
        logger.info(f"  SPY coverage:     {after['spy_min']} → {after['spy_max']} ({after['spy_rows']} rows, +{after['spy_rows'] - before['spy_rows']})")
        logger.info(f"  DB size:          {db_size_after:.2f} MB (+{db_size_after - db_size_before:.2f} MB)")

        coverage_pct = (after['distinct_tickers'] / total_companies * 100) if total_companies > 0 else 0
        logger.info(f"  Coverage:         {after['distinct_tickers']:,} / {total_companies:,} ({coverage_pct:.1f}%)")

        # Check for compressed release artifact size
        logger.info(f"\n  Estimated gzipped: ~{db_size_after * 0.07:.0f} MB (for release artifact)")

    else:
        logger.info(f"  Would insert:     ~{total_rows_inserted:,} rows")

    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Companies requested:    {len(work_list)}")
    logger.info(f"  Successful:             {len(successful)}")
    logger.info(f"  Rescued from failures:  {len(rescued_from_failures)}")
    logger.info(f"  Failed (permanent):     {len(failed_permanent)}")
    logger.info(f"  Failed (transient):     {len(failed_transient)}")
    logger.info(f"  Rows inserted:          {total_rows_inserted:,}")
    logger.info(f"  Runtime:                {time.time() - start_time:.0f}s")

    if rescued_from_failures:
        logger.info(f"\n{'='*60}")
        logger.info(f"RESCUED FROM FAILURES: {len(rescued_from_failures)} companies")
        logger.info(f"{'='*60}")
        logger.info("Previously failed, now succeeded with resolved ticker symbols:")
        for stored, fetched in rescued_from_failures[:20]:
            if stored != fetched:
                logger.info(f"  {stored:6s} (resolved to {fetched})")
            else:
                logger.info(f"  {stored:6s}")
        if len(rescued_from_failures) > 20:
            logger.info(f"  ... and {len(rescued_from_failures) - 20} more")

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

    conn.close()

    # Get final DB size (even in dry run, for comparison)
    db_size_final = os.path.getsize(DB_PATH) / (1024 * 1024) if not dry_run else db_size_before

    return {
        'total_rows': total_rows_inserted,
        'successful': len(successful),
        'rescued': len(rescued_from_failures),
        'failed_permanent': len(failed_permanent),
        'failed_transient': len(failed_transient),
        'db_size_mb': db_size_final
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill price data for all companies with ticker resolution"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be fetched without writing to DB"
    )
    parser.add_argument(
        "--size-check",
        action="store_true",
        help="Estimate row count and DB growth, then exit"
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

    run_backfill(dry_run=args.dry_run, max_tickers=args.max_tickers, size_check=args.size_check)
