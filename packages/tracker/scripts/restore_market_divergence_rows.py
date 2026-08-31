#!/usr/bin/env python3
"""
Restore rows incorrectly nulled by the market-divergence rule.

The market-divergence rule compared as-transacted prices against split-back-adjusted
market closes — different bases. This is a basis mismatch that destroyed 4,065+ good
rows (e.g., AMZN 2020 purchases at $1,900 flagged as corrupt because our adjusted
close shows $95 after the 2022 20:1 split).

This script:
1. Restores ALL 6,552 rows nulled by market divergence from price_original_corrupt
2. Keeps the 54 hard-bound violations (price > $1M) as NULL
3. Keeps the 1,117 price<=0 rows as NULL

Usage:
    python scripts/restore_market_divergence_rows.py              # dry-run
    python scripts/restore_market_divergence_rows.py --write      # apply
"""

import sys
import os
import sqlite3
import argparse
import logging

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_DIR = os.path.join(SCRIPT_DIR, "..")
sys.path.insert(0, TRACKER_DIR)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(TRACKER_DIR, "db", "insider_signals.db")

MAX_PLAUSIBLE_PRICE = 1_000_000.0


def get_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def find_rows_to_restore(conn):
    """
    Find rows to restore: those with price_original_corrupt that are NOT hard-bound
    or price<=0 violations.

    Returns list of (row_id, ticker, txn_date, original_price, reason) tuples.
    """
    cur = conn.cursor()

    # Find all rows with price_original_corrupt (these were nulled)
    cur.execute("""
        SELECT it.id, c.ticker, it.transaction_date, it.price_original_corrupt
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price_original_corrupt IS NOT NULL
        ORDER BY c.ticker, it.transaction_date
    """)

    to_restore = []
    keep_nulled = []

    for row_id, ticker, txn_date, original_price in cur.fetchall():
        # Keep nulled if hard-bound violation (> $1M)
        if original_price > MAX_PLAUSIBLE_PRICE:
            keep_nulled.append((row_id, ticker, txn_date, original_price, "above_ceiling"))
        # Keep nulled if price <= 0
        elif original_price <= 0:
            keep_nulled.append((row_id, ticker, txn_date, original_price, "price_unknown"))
        # Otherwise, restore (was incorrectly flagged by market divergence)
        else:
            to_restore.append((row_id, ticker, txn_date, original_price, "market_divergence_basis_error"))

    return to_restore, keep_nulled


def main():
    parser = argparse.ArgumentParser(
        description="Restore rows incorrectly nulled by market-divergence rule"
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
    logger.info("RESTORE MARKET-DIVERGENCE ROWS")
    logger.info("=" * 60)
    logger.info(f"Database: {db_path}")
    logger.info(f"Mode: {'DRY RUN (no changes)' if dry_run else 'WRITE MODE'}")
    logger.info("")

    conn = get_db(db_path)

    logger.info("=" * 60)
    logger.info("ANALYZING NULLED ROWS")
    logger.info("=" * 60)

    to_restore, keep_nulled = find_rows_to_restore(conn)

    logger.info(f"Total rows with price_original_corrupt: {len(to_restore) + len(keep_nulled)}")
    logger.info(f"  To restore (market divergence basis error): {len(to_restore)}")
    logger.info(f"  Keep nulled (hard-bound or price<=0): {len(keep_nulled)}")
    logger.info("")

    # Show breakdown of keep_nulled
    from collections import defaultdict
    keep_by_reason = defaultdict(list)
    for row_id, ticker, txn_date, price, reason in keep_nulled:
        keep_by_reason[reason].append((ticker, txn_date, price))

    logger.info("Rows that will remain NULL (correctly nulled):")
    for reason, rows in keep_by_reason.items():
        logger.info(f"  {reason}: {len(rows)} rows")
        for ticker, txn_date, price in rows[:3]:
            logger.info(f"    {ticker} {txn_date}: ${price:,.2f}")
        if len(rows) > 3:
            logger.info(f"    ... and {len(rows) - 3} more")
    logger.info("")

    # Show sample of rows to restore
    logger.info("Sample rows to restore (showing up to 20):")
    for row_id, ticker, txn_date, original_price, reason in to_restore[:20]:
        logger.info(f"  {ticker} {txn_date}: ${original_price:,.2f}")
    if len(to_restore) > 20:
        logger.info(f"  ... and {len(to_restore) - 20} more")
    logger.info("")

    # Check for specific AMZN 2020 rows
    amzn_2020 = [r for r in to_restore if r[1] == 'AMZN' and r[2].startswith('2020-')]
    if amzn_2020:
        logger.info(f"AMZN 2020 purchases to restore: {len(amzn_2020)}")
        for row_id, ticker, txn_date, original_price, reason in amzn_2020[:5]:
            logger.info(f"  {ticker} {txn_date}: ${original_price:,.2f}")
        logger.info("")

    # Apply restoration
    if args.write and to_restore:
        logger.info("=" * 60)
        logger.info("RESTORING PRICES")
        logger.info("=" * 60)
        logger.info(f"Restoring {len(to_restore)} rows from price_original_corrupt...")

        cur = conn.cursor()
        restored = 0
        for row_id, ticker, txn_date, original_price, reason in to_restore:
            try:
                cur.execute("""
                    UPDATE insider_transactions
                    SET price = price_original_corrupt,
                        price_original_corrupt = NULL
                    WHERE id = ?
                """, (row_id,))
                restored += 1
            except Exception as e:
                logger.warning(f"  Failed to restore row {row_id} ({ticker} {txn_date}): {e}")

        conn.commit()
        logger.info(f"Done! Restored {restored} rows.")

        # Verify
        logger.info("")
        logger.info("=" * 60)
        logger.info("VERIFICATION")
        logger.info("=" * 60)

        # Check that price_original_corrupt is only set for rows that should remain NULL
        cur.execute("""
            SELECT COUNT(*)
            FROM insider_transactions
            WHERE transaction_type = 'P'
              AND price_original_corrupt IS NOT NULL
        """)
        remaining_corrupt = cur.fetchone()[0]

        expected_remaining = len(keep_nulled)
        logger.info(f"Rows with price_original_corrupt still set: {remaining_corrupt}")
        logger.info(f"Expected (hard-bound + price<=0): {expected_remaining}")

        if remaining_corrupt == expected_remaining:
            logger.info("✓ Correct number of rows remain nulled")
        else:
            logger.warning(f"✗ Mismatch! Expected {expected_remaining}, got {remaining_corrupt}")

        # Check AMZN 2020 specifically
        cur.execute("""
            SELECT COUNT(*), AVG(price)
            FROM insider_transactions it
            JOIN companies c ON it.company_id = c.id
            WHERE c.ticker = 'AMZN'
              AND it.transaction_type = 'P'
              AND it.transaction_date >= '2020-01-01'
              AND it.transaction_date < '2021-01-01'
              AND it.price IS NOT NULL
        """)
        amzn_count, amzn_avg = cur.fetchone()

        if amzn_count:
            logger.info(f"\nAMZN 2020 purchases: {amzn_count} rows, avg price ${amzn_avg:,.2f}")
            if amzn_avg > 1000:
                logger.info("✓ AMZN 2020 prices restored to ~$1,900 range")
            else:
                logger.warning(f"✗ AMZN 2020 avg price too low: ${amzn_avg:,.2f}")

        # Summary of NULL prices by category
        logger.info("")
        logger.info("Final NULL price summary:")

        cur.execute("""
            SELECT COUNT(*)
            FROM insider_transactions it
            WHERE it.transaction_type = 'P'
              AND it.price IS NULL
              AND it.price_original_corrupt IS NOT NULL
              AND it.price_original_corrupt > ?
        """, (MAX_PLAUSIBLE_PRICE,))
        ceiling_null = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*)
            FROM insider_transactions it
            WHERE it.transaction_type = 'P'
              AND it.price IS NULL
              AND it.price_original_corrupt IS NOT NULL
              AND it.price_original_corrupt <= 0
        """)
        zero_null = cur.fetchone()[0]

        logger.info(f"  Hard-bound violations (>$1M): {ceiling_null} rows")
        logger.info(f"  Unknown prices (<=0): {zero_null} rows")
        logger.info(f"  Total NULL prices: {ceiling_null + zero_null} rows")

    else:
        logger.info("=" * 60)
        logger.info("DRY RUN SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Would restore: {len(to_restore)} rows")
        logger.info(f"Would keep nulled: {len(keep_nulled)} rows (hard-bound + price<=0)")
        if amzn_2020:
            logger.info(f"  Including {len(amzn_2020)} AMZN 2020 purchases")
        logger.info("")
        logger.info("Run with --write to apply changes")

    conn.close()


if __name__ == "__main__":
    main()
