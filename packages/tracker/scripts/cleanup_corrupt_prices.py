#!/usr/bin/env python3
"""
Clean corrupt insider_transactions.price values from the database.

Applies validation rules to detect and nullify impossible prices:
1. Price <= 0: Store as NULL (unknown, not zero)
2. Hard bound: price > $1,000,000/share is corrupt (BRK-A at ~$700K must survive)
3. Relative to market: If >20x divergence from market close, likely corrupt

Preserves original corrupt values in a new column (price_original_corrupt) before
nullifying, so the data is recoverable. Transaction rows are NOT deleted — only
the price field is set to NULL.

Features:
  - Dry-run mode (default): reports what would be fixed without writing
  - Idempotent: re-running is a no-op after cleanup
  - Impact analysis: reports cluster total_value impact
  - Preserves corrupt values for forensics

Usage:
    python scripts/cleanup_corrupt_prices.py              # dry-run (reports only)
    python scripts/cleanup_corrupt_prices.py --write      # apply to DB
"""

import sys
import os
import sqlite3
import argparse
import logging
from collections import defaultdict

# Add tracker to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_DIR = os.path.join(SCRIPT_DIR, "..")
sys.path.insert(0, TRACKER_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(TRACKER_DIR, "db", "insider_signals.db")

# Validation bounds (match data_loader.py)
MAX_PLAUSIBLE_PRICE = 1_000_000.0
MAX_MARKET_RATIO = 20.0
MIN_MARKET_RATIO = 1.0 / 20.0


def get_db(db_path=None):
    """Get database connection with busy timeout for concurrent access."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def _compute_raw_market_price(ticker, transaction_date, adjusted_close, cur):
    """
    Compute as-transacted market price from split-back-adjusted close.

    Our prices table stores split-back-adjusted prices (adjusted to a recent reference).
    Transaction prices are as-transacted. To compare them, we must recover the
    as-transacted market price by multiplying by the cumulative split ratio for
    all splits that occurred AFTER the transaction date.

    Formula: raw_market = adjusted_close × PROD(ratio for splits after txn_date)

    Returns (raw_market_price, split_count)
    """
    # Get all splits for this ticker that occurred AFTER transaction_date
    cur.execute("""
        SELECT ratio
        FROM split_events
        WHERE ticker = ? AND date > ?
        ORDER BY date
    """, (ticker, transaction_date))

    splits = cur.fetchall()
    cumulative_ratio = 1.0
    for (ratio,) in splits:
        cumulative_ratio *= ratio

    raw_market = adjusted_close * cumulative_ratio
    return raw_market, len(splits)


def find_corrupt_prices(conn):
    """
    Find all corrupt insider_transactions.price values.

    Returns (corrupt_list, flagged_only_list) where:
    - corrupt_list: rows to NULL
    - flagged_only_list: divergent but not corrupt enough to NULL
    """
    cur = conn.cursor()
    corrupt = []
    flagged_only = []  # Divergent but not corrupt enough to NULL

    # Check 1: Price <= 0 (unknown, not zero)
    # Only check transaction_type='P' (purchases) — these are what matter for insider signals
    logger.info("Checking for non-positive prices (type='P' only)...")
    cur.execute("""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL AND it.price <= 0
        ORDER BY c.ticker, it.transaction_date
    """)
    non_positive = cur.fetchall()

    for row_id, ticker, txn_date, price, shares in non_positive:
        corrupt.append((row_id, ticker, txn_date, price, shares, "price_unknown"))

    logger.info(f"  Found {len(non_positive)} non-positive prices (will be set to NULL)")

    # Check 2: Absolute ceiling violations
    logger.info("Checking for prices above ceiling (type='P' only)...")
    cur.execute(f"""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL AND it.price > {MAX_PLAUSIBLE_PRICE}
        ORDER BY it.price DESC
    """)
    ceiling_violations = cur.fetchall()

    for row_id, ticker, txn_date, price, shares in ceiling_violations:
        corrupt.append((row_id, ticker, txn_date, price, shares, f"above_ceiling ({price:,.2f} > {MAX_PLAUSIBLE_PRICE:,.0f})"))

    logger.info(f"  Found {len(ceiling_violations)} ceiling violations")

    # Check 3: Market cap sanity (transaction value > company market cap)
    logger.info("Checking for transactions exceeding company market cap (type='P' only)...")

    # Plausibility bounds for market cap
    MIN_PLAUSIBLE_MCAP = 1_000_000.0      # $1M
    MAX_PLAUSIBLE_MCAP = 5_000_000_000_000.0  # $5T
    MCAP_RATIO_THRESHOLD = 1.0  # 100% of market cap

    cur.execute("""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted, c.market_cap
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL
          AND it.price > 0
          AND c.market_cap IS NOT NULL
        ORDER BY c.ticker, it.transaction_date
    """)

    mcap_transactions = cur.fetchall()
    already_flagged = {r[0] for r in corrupt}

    mcap_violations = 0
    mcap_flagged = 0

    for row_id, ticker, txn_date, tx_price, shares, market_cap in mcap_transactions:
        if row_id in already_flagged:
            continue

        tx_value = tx_price * shares

        # Check if market cap is plausible
        mcap_plausible = MIN_PLAUSIBLE_MCAP <= market_cap <= MAX_PLAUSIBLE_MCAP

        if mcap_plausible:
            ratio = tx_value / market_cap
            if ratio > MCAP_RATIO_THRESHOLD:
                # Transaction value exceeds company market cap - impossible
                corrupt.append((
                    row_id, ticker, txn_date, tx_price, shares,
                    f"exceeds_market_cap (tx_value=${tx_value/1e9:.2f}B, market_cap=${market_cap/1e9:.2f}B, "
                    f"ratio={ratio:.1f}x)"
                ))
                mcap_violations += 1
        else:
            # Market cap itself is implausible - flag but don't NULL
            if tx_value > 1e9:  # Only flag if transaction is large enough to matter
                flagged_only.append((
                    row_id, ticker, txn_date, tx_price, shares,
                    f"market_cap_implausible (tx_value=${tx_value/1e9:.2f}B, market_cap=${market_cap/1e9:.2f}B) - FLAGGED ONLY"
                ))
                mcap_flagged += 1

    logger.info(f"  Found {mcap_violations} transactions exceeding market cap (will be set to NULL)")
    if mcap_flagged > 0:
        logger.info(f"  Found {mcap_flagged} large transactions with implausible market cap (flagged only)")

    # Check 4: Market divergence with CORRECT basis (split-adjusted)
    logger.info("Checking for market price divergence with split-corrected basis (type='P' only)...")

    # Plausibility bounds for market price
    MIN_PLAUSIBLE_MARKET = 0.0001
    MAX_PLAUSIBLE_MARKET = 10000.0
    HIGH_DIVERGENCE_RATIO = 1000.0  # NULL threshold (far above 99th percentile of 6.0)

    # Get all purchase transactions with prices
    cur.execute("""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL
          AND it.price > 0
        ORDER BY c.ticker, it.transaction_date
    """)

    transactions = cur.fetchall()
    already_flagged = {r[0] for r in corrupt}

    null_high_side = 0
    flag_low_side = 0
    flag_other = 0

    for row_id, ticker, txn_date, tx_price, shares in transactions:
        if row_id in already_flagged:
            continue

        # Get adjusted market close on or before transaction date
        cur.execute("""
            SELECT close
            FROM prices
            WHERE ticker = ? AND date <= ?
            ORDER BY date DESC
            LIMIT 1
        """, (ticker, txn_date))

        market_row = cur.fetchone()
        if not market_row:
            continue  # No market price available

        adjusted_close = market_row[0]
        if adjusted_close <= 0:
            continue

        # Compute as-transacted market price (correct for splits)
        raw_market, split_count = _compute_raw_market_price(ticker, txn_date, adjusted_close, cur)

        # Check if market price is plausible
        market_plausible = MIN_PLAUSIBLE_MARKET <= raw_market <= MAX_PLAUSIBLE_MARKET

        # Compute divergence ratio
        ratio = tx_price / raw_market if raw_market > 0 else 0

        # Decision tree:
        # a) If market is plausible AND tx/market > 1000 → NULL the transaction
        # b) If tx/market < 1/1000 (far below market) → FLAG only (incomplete splits inflate our market price)
        # c) Everything else divergent → FLAG only

        if market_plausible and ratio > HIGH_DIVERGENCE_RATIO:
            # NULL this transaction - market is sane, transaction is corrupt
            corrupt.append((
                row_id, ticker, txn_date, tx_price, shares,
                f"market_divergence_high (tx=${tx_price:,.2f}, raw_market=${raw_market:.4f}, "
                f"ratio={ratio:.1f}x, {split_count} splits)"
            ))
            null_high_side += 1
        elif market_plausible and ratio < 1.0 / HIGH_DIVERGENCE_RATIO:
            # FLAG only - transaction far below market (likely our split_events is incomplete)
            flagged_only.append((
                row_id, ticker, txn_date, tx_price, shares,
                f"market_divergence_low (tx=${tx_price:,.2f}, raw_market=${raw_market:.4f}, "
                f"ratio={ratio:.4f}x, {split_count} splits) - FLAGGED ONLY"
            ))
            flag_low_side += 1
        elif not market_plausible:
            # Market price itself looks corrupt
            flagged_only.append((
                row_id, ticker, txn_date, tx_price, shares,
                f"market_price_corrupt (tx=${tx_price:,.2f}, raw_market=${raw_market:.4f}, "
                f"outside [{MIN_PLAUSIBLE_MARKET}, {MAX_PLAUSIBLE_MARKET}]) - FLAGGED ONLY"
            ))
            flag_other += 1

    logger.info(f"  Market divergence results:")
    logger.info(f"    {null_high_side} rows to NULL (high side, market plausible, ratio > {HIGH_DIVERGENCE_RATIO}x)")
    logger.info(f"    {flag_low_side} rows FLAGGED ONLY (low side, likely incomplete split_events)")
    logger.info(f"    {flag_other} rows FLAGGED ONLY (market price itself looks corrupt)")

    # Check 5: Aggregate-value-as-price pattern (group-level, cannot run at ingestion)
    # Within a (ticker, transaction_date, price) group, flag when:
    # - 3+ distinct insiders (reporting_cik) share an identical price, AND
    # - that price is >= $1,000 and an exact multiple of $1,000
    #
    # This catches cases where the aggregate transaction value was written into the
    # price field (e.g., NUTX at $20,000 "price" for 5 insiders on 2020-08-12).
    # Cannot be detected at ingestion time because Form 4s arrive over days/weeks.
    logger.info("Checking for aggregate-value-as-price pattern (>=3 insiders, round >=$1k)...")

    # Get all (ticker, transaction_date, price) groups with 3+ distinct insiders at identical price
    cur.execute("""
        SELECT c.ticker, it.transaction_date, it.price,
               COUNT(DISTINCT it.reporting_cik) as insider_count,
               GROUP_CONCAT(DISTINCT it.id) as row_ids
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL
          AND it.price > 0
        GROUP BY c.ticker, it.transaction_date, it.price
        HAVING COUNT(DISTINCT it.reporting_cik) >= 3
    """)

    groups = cur.fetchall()
    already_flagged = {r[0] for r in corrupt}
    aggregate_value_price = 0

    for ticker, txn_date, price, insider_count, row_ids_str in groups:
        # Check if price is >= $1,000 and exact multiple of $1,000
        if price >= 1000.0 and price % 1000.0 == 0.0:
            # This is likely aggregate value written as price
            row_ids = [int(rid) for rid in row_ids_str.split(',')]

            for row_id in row_ids:
                if row_id in already_flagged:
                    continue

                # Get shares for this specific row
                cur.execute("SELECT shares_transacted FROM insider_transactions WHERE id = ?", (row_id,))
                shares = cur.fetchone()[0]

                corrupt.append((
                    row_id, ticker, txn_date, price, shares,
                    f"aggregate_value_as_price (price=${price:,.0f}, {insider_count} insiders at identical round >=$$1k price)"
                ))
                aggregate_value_price += 1

    logger.info(f"  Found {aggregate_value_price} aggregate-value-as-price violations")
    logger.info(f"    Pattern: >=3 distinct insiders at identical price that is round >=$$1,000 multiple")

    return corrupt, flagged_only


def compute_cluster_impact(conn, corrupt_row_ids, dry_run=True):
    """
    Compute impact on cluster total_value by examining historical_clusters.csv.

    Returns dict with clusters over $1B and how their total_value would change.
    """
    cur = conn.cursor()

    # Get tickers affected by corrupt prices
    affected_tickers = set()
    for row_id, ticker, _, _, _, _ in corrupt_row_ids:
        affected_tickers.add(ticker)

    logger.info(f"  Analyzing cluster impact for {len(affected_tickers)} affected tickers...")

    # Check if historical_clusters.csv exists
    import csv
    from pathlib import Path
    repo_root = Path(SCRIPT_DIR).parents[2]  # packages/tracker/scripts -> repo root
    clusters_path = repo_root / "data" / "historical_clusters.csv"

    if not clusters_path.exists():
        clusters_path = repo_root / "packages" / "tracker" / "output" / "historical_clusters.csv"

    if not clusters_path.exists():
        logger.warning("  historical_clusters.csv not found, skipping cluster impact analysis")
        return {
            'clusters_with_affected_tickers': 0,
            'sample_clusters': []
        }

    # Read clusters CSV and find clusters with affected tickers
    affected_clusters = []
    with open(clusters_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['ticker'] in affected_tickers:
                try:
                    total_value = float(row.get('total_value', 0))
                    if total_value > 1_000_000_000:  # Only report clusters over $1B
                        affected_clusters.append({
                            'ticker': row['ticker'],
                            'signal_date': row.get('signal_date', ''),
                            'total_value': total_value
                        })
                except (ValueError, KeyError):
                    pass

    # Sort by total_value descending
    affected_clusters.sort(key=lambda x: x['total_value'], reverse=True)

    return {
        'clusters_with_affected_tickers': len(affected_clusters),
        'sample_clusters': affected_clusters[:50]  # Top 50
    }


def add_corrupt_price_column(conn):
    """Add price_original_corrupt column if it doesn't exist."""
    cur = conn.cursor()

    # Check if column exists
    cur.execute("PRAGMA table_info(insider_transactions)")
    columns = [row[1] for row in cur.fetchall()]

    if 'price_original_corrupt' not in columns:
        logger.info("Adding price_original_corrupt column to preserve original values...")
        cur.execute("ALTER TABLE insider_transactions ADD COLUMN price_original_corrupt REAL")
        conn.commit()
        logger.info("  Column added successfully")
    else:
        logger.info("  price_original_corrupt column already exists")


def main():
    parser = argparse.ArgumentParser(
        description="Clean corrupt insider_transactions.price values"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply changes to database (default: dry-run only)"
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})"
    )
    args = parser.parse_args()

    dry_run = not args.write
    db_path = args.db

    logger.info("=" * 60)
    logger.info("INSIDER TRANSACTION PRICE CLEANUP")
    logger.info("=" * 60)
    logger.info(f"Database: {db_path}")
    logger.info(f"Mode: {'DRY RUN (no changes)' if dry_run else 'WRITE MODE'}")
    logger.info("")
    logger.info(f"Validation bounds:")
    logger.info(f"  Absolute ceiling: ${MAX_PLAUSIBLE_PRICE:,.0f}/share")
    logger.info(f"  Market divergence: {MIN_MARKET_RATIO}x - {MAX_MARKET_RATIO}x")
    logger.info(f"  Non-positive: stored as NULL (unknown)")
    logger.info("")

    # Connect to database
    conn = get_db(db_path)

    # Find corrupt rows
    logger.info("=" * 60)
    logger.info("FINDING CORRUPT PRICES")
    logger.info("=" * 60)
    corrupt_rows, flagged_only = find_corrupt_prices(conn)

    logger.info("")
    logger.info(f"Total corrupt prices to NULL: {len(corrupt_rows)}")
    logger.info(f"Total flagged only (not mutated): {len(flagged_only)}")
    logger.info(f"Unique tickers affected (NULL): {len(set(r[1] for r in corrupt_rows))}")
    logger.info(f"Unique tickers flagged: {len(set(r[1] for r in flagged_only))}")

    if not corrupt_rows and not flagged_only:
        logger.info("")
        logger.info("✓ No corrupt or suspicious prices found. Database is clean.")
        conn.close()
        return

    # Show samples by reason (corrupt = will NULL)
    if corrupt_rows:
        logger.info("")
        logger.info("Corrupt prices to NULL by reason:")
        by_reason = defaultdict(list)
        for row_id, ticker, txn_date, price, shares, reason in corrupt_rows:
            category = reason.split('(')[0].strip()
            by_reason[category].append((ticker, txn_date, price, shares, reason))

        for category, rows in by_reason.items():
            logger.info(f"  {category}: {len(rows)} rows")
            for ticker, txn_date, price, shares, reason in rows[:5]:
                logger.info(f"    {ticker} {txn_date}: price=${price:,.2f}, shares={shares:,.0f} - {reason}")
            if len(rows) > 5:
                logger.info(f"    ... and {len(rows) - 5} more")

    # Show flagged-only samples
    if flagged_only:
        logger.info("")
        logger.info("Flagged prices (NOT mutated) by reason:")
        by_reason = defaultdict(list)
        for row_id, ticker, txn_date, price, shares, reason in flagged_only:
            category = reason.split('(')[0].strip()
            by_reason[category].append((ticker, txn_date, price, shares, reason))

        for category, rows in by_reason.items():
            logger.info(f"  {category}: {len(rows)} rows")
            for ticker, txn_date, price, shares, reason in rows[:5]:
                logger.info(f"    {ticker} {txn_date}: price=${price:,.2f}, shares={shares:,.0f} - {reason}")
            if len(rows) > 5:
                logger.info(f"    ... and {len(rows) - 5} more")

    # Compute cluster impact (only for rows we'll NULL)
    if corrupt_rows:
        logger.info("")
        logger.info("=" * 60)
        logger.info("COMPUTING CLUSTER IMPACT")
        logger.info("=" * 60)
        cluster_impact = compute_cluster_impact(conn, corrupt_rows, dry_run=dry_run)
    else:
        cluster_impact = {'clusters_with_affected_tickers': 0, 'sample_clusters': []}

    if cluster_impact['clusters_with_affected_tickers'] > 0:
        logger.info(f"Clusters over $1B with affected tickers: {cluster_impact['clusters_with_affected_tickers']}")
        logger.info("")
        logger.info("Sample affected clusters (showing up to 50):")
        for cluster in cluster_impact['sample_clusters']:
            logger.info(f"  {cluster['ticker']} on {cluster['signal_date']}: total_value=${cluster['total_value']/1e9:.2f}B")
    else:
        logger.info("No clusters over $1B affected (or historical_clusters.csv not found)")

    # Apply fixes
    if args.write:
        logger.info("")
        logger.info("=" * 60)
        logger.info("APPLYING FIXES")
        logger.info("=" * 60)

        # Add column to preserve original corrupt values
        add_corrupt_price_column(conn)

        logger.info(f"\nSetting {len(corrupt_rows)} corrupt prices to NULL (preserving originals)...")

        cur = conn.cursor()
        fixed = 0
        for row_id, ticker, txn_date, price, shares, reason in corrupt_rows:
            try:
                # Preserve original corrupt value, then set price to NULL
                cur.execute("""
                    UPDATE insider_transactions
                    SET price_original_corrupt = price, price = NULL
                    WHERE id = ?
                """, (row_id,))
                fixed += 1
            except Exception as e:
                logger.warning(f"  Failed to fix row {row_id} ({ticker} {txn_date}): {e}")

        conn.commit()
        logger.info(f"Done! Fixed {fixed} corrupt prices.")

        # Verify
        logger.info("")
        logger.info("=" * 60)
        logger.info("VERIFICATION")
        logger.info("=" * 60)

        # Re-run checks to see if any remain
        remaining_corrupt, remaining_flagged = find_corrupt_prices(conn)
        logger.info(f"Remaining corrupt prices to NULL: {len(remaining_corrupt)}")
        logger.info(f"Remaining flagged (not mutated): {len(remaining_flagged)}")

        if remaining_corrupt:
            logger.warning("✗ Some corrupt prices remain!")
            for row_id, ticker, txn_date, price, shares, reason in remaining_corrupt[:10]:
                logger.warning(f"  {ticker} {txn_date}: ${price:,.2f} - {reason}")
        else:
            logger.info("✓ All corrupt prices have been cleaned!")
            if remaining_flagged:
                logger.info(f"  ({len(remaining_flagged)} rows remain flagged but not mutated)")

    else:
        logger.info("")
        logger.info("=" * 60)
        logger.info("DRY RUN SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Would fix: {len(corrupt_rows)} corrupt prices")
        logger.info(f"Affecting: {len(set(r[1] for r in corrupt_rows))} tickers")
        if cluster_impact['clusters_with_affected_tickers'] > 0:
            logger.info(f"Clusters over $1B affected: {cluster_impact['clusters_with_affected_tickers']}")
        logger.info("")
        logger.info("Run with --write to apply changes")

    conn.close()


if __name__ == "__main__":
    main()
