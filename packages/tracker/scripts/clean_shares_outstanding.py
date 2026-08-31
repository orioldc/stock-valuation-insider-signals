#!/usr/bin/env python3
"""
Clean corrupt shares_outstanding values from the database.

Applies validation rules to detect and remove impossible values:
1. Absolute bounds: shares must be between 100K and 100B
2. Relative outliers: QoQ jumps >50x or <1/50x indicate sentinels or unit errors

Deleting bad rows is preferable to correcting them — compute_share_delta handles
missing quarters gracefully, but corrupt values pollute the buyback signal.

Features:
  - Dry-run mode (default): reports what would be deleted without writing
  - Idempotent: re-running is a no-op after cleanup
  - Impact analysis: reports how many companies' 4Q deltas would change
  - Market cap impact: identifies companies whose market_cap would change

Usage:
    python scripts/clean_shares_outstanding.py              # dry-run (reports only)
    python scripts/clean_shares_outstanding.py --write      # apply to DB
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

# Validation bounds (match backfill_shares_outstanding.py)
MIN_PLAUSIBLE_SHARES = 100_000          # P1 of distribution is 1.4M
MAX_PLAUSIBLE_SHARES = 100_000_000_000  # 100B (P99.9 is 29B)
MAX_QOQ_RATIO = 50.0
MIN_QOQ_RATIO = 0.02  # 1/50


def get_db(db_path=None):
    """Get database connection with busy timeout for concurrent access."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def find_corrupt_rows(conn):
    """
    Find all corrupt shares_outstanding rows.

    Returns list of (row_id, company_id, ticker, date, shares, reason) tuples.
    """
    cur = conn.cursor()
    corrupt = []

    # Check 1: Absolute bounds violations
    logger.info("Checking absolute bounds...")
    cur.execute(f"""
        SELECT so.id, so.company_id, c.ticker, so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        WHERE so.shares < {MIN_PLAUSIBLE_SHARES}
           OR so.shares > {MAX_PLAUSIBLE_SHARES}
        ORDER BY c.ticker, so.date
    """)
    absolute_violations = cur.fetchall()

    for row_id, company_id, ticker, date, shares in absolute_violations:
        if shares < MIN_PLAUSIBLE_SHARES:
            reason = f"below_minimum ({shares:,.0f} < {MIN_PLAUSIBLE_SHARES:,})"
        else:
            reason = f"above_maximum ({shares:,.0f} > {MAX_PLAUSIBLE_SHARES:,})"
        corrupt.append((row_id, company_id, ticker, date, shares, reason))

    logger.info(f"  Found {len(absolute_violations)} absolute bound violations")

    # Check 2: Relative QoQ outliers (not already caught by absolute bounds)
    logger.info("Checking quarter-over-quarter outliers...")
    absolute_row_ids = {r[0] for r in corrupt}

    cur.execute("""
        WITH ordered_shares AS (
            SELECT
                so.id,
                so.company_id,
                c.ticker,
                so.date,
                so.shares,
                LAG(so.shares, 1) OVER (PARTITION BY so.company_id ORDER BY so.date) as prev_shares,
                LAG(so.date, 1) OVER (PARTITION BY so.company_id ORDER BY so.date) as prev_date
            FROM shares_outstanding so
            JOIN companies c ON so.company_id = c.id
        )
        SELECT id, company_id, ticker, date, shares, prev_shares, prev_date
        FROM ordered_shares
        WHERE prev_shares IS NOT NULL
          AND prev_shares > 0
          AND (
              CAST(shares AS REAL) / CAST(prev_shares AS REAL) > ?
              OR CAST(shares AS REAL) / CAST(prev_shares AS REAL) < ?
          )
    """, (MAX_QOQ_RATIO, MIN_QOQ_RATIO))

    qoq_violations = cur.fetchall()

    for row_id, company_id, ticker, date, shares, prev_shares, prev_date in qoq_violations:
        if row_id not in absolute_row_ids:
            ratio = shares / prev_shares if prev_shares > 0 else 0
            reason = f"qoq_outlier ({prev_shares:,.0f} -> {shares:,.0f} = {ratio:.2f}x on {prev_date})"
            corrupt.append((row_id, company_id, ticker, date, shares, reason))

    logger.info(f"  Found {len(qoq_violations)} QoQ outliers ({len([r for r in qoq_violations if r[0] not in absolute_row_ids])} new)")

    return corrupt


def compute_delta_impact(conn, corrupt_row_ids, dry_run=True):
    """
    Compute how many companies' trailing 4Q share deltas would change.

    Returns dict: {
        'companies_affected': int,
        'companies_with_delta_change': int,
        'sample_changes': [(ticker, old_delta, new_delta), ...]
    }
    """
    cur = conn.cursor()

    # Get unique company IDs with corrupt data
    company_ids = set(r[1] for r in corrupt_row_ids)
    logger.info(f"  Analyzing 4Q delta impact for {len(company_ids)} companies...")

    companies_with_delta_change = 0
    sample_changes = []

    # For each affected company, compute delta before and after cleanup
    for company_id in company_ids:
        # Get ticker
        cur.execute("SELECT ticker FROM companies WHERE id = ?", (company_id,))
        ticker = cur.fetchone()[0]

        # Get all shares data for this company (current state)
        cur.execute("""
            SELECT date, shares
            FROM shares_outstanding
            WHERE company_id = ?
            ORDER BY date
        """, (company_id,))
        all_rows = cur.fetchall()

        # Compute current 4Q delta
        current_delta = _compute_4q_delta(all_rows)

        # Simulate cleanup: remove corrupt rows
        corrupt_dates = {r[3] for r in corrupt_row_ids if r[1] == company_id}
        clean_rows = [(date, shares) for date, shares in all_rows if date not in corrupt_dates]

        # Compute post-cleanup 4Q delta
        clean_delta = _compute_4q_delta(clean_rows)

        # Check if delta changed
        if current_delta != clean_delta:
            companies_with_delta_change += 1
            if len(sample_changes) < 10:
                sample_changes.append((ticker, current_delta, clean_delta))

    return {
        'companies_affected': len(company_ids),
        'companies_with_delta_change': companies_with_delta_change,
        'sample_changes': sample_changes,
    }


def _compute_4q_delta(rows):
    """
    Compute trailing 4Q delta from [(date, shares), ...] rows.
    Mimics logic from signals/share_count_change.py.
    """
    if len(rows) < 2:
        return None

    # Deduplicate by quarter
    quarterly = {}
    for date_str, shares in rows:
        try:
            year = date_str[:4]
            month = int(date_str[5:7])
            q = (int(year), (month - 1) // 3 + 1)
            quarterly[q] = shares
        except (ValueError, IndexError):
            continue

    if len(quarterly) < 2:
        return None

    sorted_quarters = sorted(quarterly.keys())
    values = [quarterly[q] for q in sorted_quarters]

    # Trailing 4Q change
    if len(values) >= 5:
        delta_4q = (values[-1] - values[-5]) / values[-5] * 100 if values[-5] != 0 else 0
    else:
        delta_4q = (values[-1] - values[0]) / values[0] * 100 if values[0] != 0 else 0

    return round(delta_4q, 4)


def compute_market_cap_impact(conn, corrupt_rows):
    """
    Identify companies whose market_cap would change after cleanup.

    If the latest shares_outstanding value is corrupt, removing it means market_cap
    will be recomputed from the prior quarter's shares (or become NULL if no prior exists).

    Returns list of (ticker, current_mcap, would_change: bool, notes) tuples.
    """
    cur = conn.cursor()
    impact = []

    # Group corrupt rows by company
    by_company = defaultdict(list)
    for row_id, company_id, ticker, date, shares, reason in corrupt_rows:
        by_company[company_id].append((date, shares, reason))

    logger.info(f"  Analyzing market cap impact for {len(by_company)} companies...")

    for company_id, corrupt_records in by_company.items():
        # Get ticker and current market_cap
        cur.execute("SELECT ticker, market_cap FROM companies WHERE id = ?", (company_id,))
        row = cur.fetchone()
        if not row:
            continue
        ticker, current_mcap = row

        # Get latest shares date
        cur.execute("""
            SELECT date, shares FROM shares_outstanding
            WHERE company_id = ?
            ORDER BY date DESC
            LIMIT 1
        """, (company_id,))
        latest = cur.fetchone()

        if not latest:
            continue

        latest_date, latest_shares = latest

        # Check if latest shares value is corrupt
        corrupt_dates = {r[0] for r in corrupt_records}
        if latest_date in corrupt_dates:
            # Latest value is corrupt — market cap will change
            # Find what the new latest would be after cleanup
            cur.execute("""
                SELECT date, shares FROM shares_outstanding
                WHERE company_id = ? AND date NOT IN ({})
                ORDER BY date DESC
                LIMIT 1
            """.format(','.join(['?'] * len(corrupt_dates))),
            (company_id, *corrupt_dates))
            new_latest = cur.fetchone()

            if new_latest:
                new_date, new_shares = new_latest
                notes = f"latest corrupt ({latest_date}: {latest_shares:,.0f}), would use {new_date}: {new_shares:,.0f}"
            else:
                notes = f"latest corrupt ({latest_date}: {latest_shares:,.0f}), no clean data remains"

            impact.append((ticker, current_mcap, True, notes))
        else:
            # Latest value is clean — market cap won't change
            impact.append((ticker, current_mcap, False, "latest shares is clean"))

    return impact


def main():
    parser = argparse.ArgumentParser(
        description="Clean corrupt shares_outstanding values"
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
    logger.info("SHARES OUTSTANDING CLEANUP")
    logger.info("=" * 60)
    logger.info(f"Database: {db_path}")
    logger.info(f"Mode: {'DRY RUN (no changes)' if dry_run else 'WRITE MODE'}")
    logger.info("")
    logger.info(f"Validation bounds:")
    logger.info(f"  Absolute: {MIN_PLAUSIBLE_SHARES:,} - {MAX_PLAUSIBLE_SHARES:,} shares")
    logger.info(f"  Relative: {MIN_QOQ_RATIO}x - {MAX_QOQ_RATIO}x QoQ ratio")
    logger.info("")

    # Connect to database
    conn = get_db(db_path)

    # Find corrupt rows
    logger.info("=" * 60)
    logger.info("FINDING CORRUPT ROWS")
    logger.info("=" * 60)
    corrupt_rows = find_corrupt_rows(conn)

    logger.info("")
    logger.info(f"Total corrupt rows found: {len(corrupt_rows)}")
    logger.info(f"Unique companies affected: {len(set(r[1] for r in corrupt_rows))}")

    if not corrupt_rows:
        logger.info("")
        logger.info("✓ No corrupt rows found. Database is clean.")
        conn.close()
        return

    # Show samples by reason
    logger.info("")
    logger.info("Corrupt rows by reason:")
    by_reason = defaultdict(list)
    for row_id, company_id, ticker, date, shares, reason in corrupt_rows:
        category = reason.split('(')[0].strip()
        by_reason[category].append((ticker, date, shares, reason))

    for category, rows in by_reason.items():
        logger.info(f"  {category}: {len(rows)} rows")
        for ticker, date, shares, reason in rows[:5]:
            logger.info(f"    {ticker} {date}: {shares:,.0f} - {reason}")
        if len(rows) > 5:
            logger.info(f"    ... and {len(rows) - 5} more")

    # Compute 4Q delta impact
    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPUTING 4Q DELTA IMPACT")
    logger.info("=" * 60)
    delta_impact = compute_delta_impact(conn, corrupt_rows, dry_run=dry_run)

    logger.info(f"Companies with corrupt data: {delta_impact['companies_affected']}")
    logger.info(f"Companies whose 4Q delta would change: {delta_impact['companies_with_delta_change']}")

    if delta_impact['sample_changes']:
        logger.info("")
        logger.info("Sample 4Q delta changes (showing up to 10):")
        for ticker, old, new in delta_impact['sample_changes']:
            old_str = f"{old:+.2f}%" if old is not None else "None"
            new_str = f"{new:+.2f}%" if new is not None else "None"
            logger.info(f"  {ticker}: {old_str} → {new_str}")

    # Compute market cap impact
    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPUTING MARKET CAP IMPACT")
    logger.info("=" * 60)
    mcap_impact = compute_market_cap_impact(conn, corrupt_rows)

    would_change = [r for r in mcap_impact if r[2]]  # r[2] is would_change flag
    logger.info(f"Companies whose market_cap would change: {len(would_change)}")

    if would_change:
        logger.info("")
        logger.info("Companies with market cap impact (showing up to 20):")
        for ticker, current_mcap, _, notes in would_change[:20]:
            mcap_str = f"${current_mcap/1e9:.2f}B" if current_mcap else "None"
            logger.info(f"  {ticker} (mcap={mcap_str}): {notes}")
        if len(would_change) > 20:
            logger.info(f"  ... and {len(would_change) - 20} more")

    # Apply deletions (iteratively until convergence)
    if args.write:
        logger.info("")
        logger.info("=" * 60)
        logger.info("APPLYING DELETIONS")
        logger.info("=" * 60)

        total_deleted = 0
        iteration = 1
        remaining = corrupt_rows

        while remaining:
            logger.info(f"\nIteration {iteration}: Deleting {len(remaining)} corrupt rows...")

            cur = conn.cursor()
            for row_id, company_id, ticker, date, shares, reason in remaining:
                cur.execute("DELETE FROM shares_outstanding WHERE id = ?", (row_id,))

            conn.commit()
            total_deleted += len(remaining)

            # Check if more corrupt rows emerged after deletion
            remaining = find_corrupt_rows(conn)

            if not remaining:
                logger.info(f"  ✓ Converged! No corrupt rows remain after {iteration} iteration(s)")
                break

            if iteration >= 10:
                logger.warning(f"  ✗ Stopping after 10 iterations with {len(remaining)} rows still corrupt")
                logger.warning(f"    This may indicate legitimate corporate actions (reverse splits, etc.)")
                break

            iteration += 1

        logger.info(f"\nTotal deleted: {total_deleted} corrupt rows across {iteration} iteration(s)")

    else:
        logger.info("")
        logger.info("=" * 60)
        logger.info("DRY RUN SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Would delete: {len(corrupt_rows)} rows")
        logger.info(f"Affecting: {len(set(r[1] for r in corrupt_rows))} companies")
        logger.info(f"4Q delta impact: {delta_impact['companies_with_delta_change']} companies")
        logger.info(f"Market cap impact: {len(would_change)} companies")
        logger.info("")
        logger.info("Run with --write to apply changes")

    conn.close()


if __name__ == "__main__":
    main()
