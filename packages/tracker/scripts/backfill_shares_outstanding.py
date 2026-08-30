#!/usr/bin/env python3
"""
Shares Outstanding Backfill Script — Fill coverage gaps from SEC XBRL companyfacts.

Fetches quarterly shares outstanding data for companies lacking coverage, using the
same SEC API endpoint as the valuation module. Also cleans corrupt future dates.

Features:
  - Self-healing: derives work list from DB state (companies lacking >=5 quarters)
  - Resumable: permanent failures stored in DB to avoid retrying across runs
  - Batched: respects SEC rate limits with checkpointing
  - Tolerant: individual CIK failures don't stop the run
  - Dry-run mode: reports what would be fetched without writing
  - Coverage reporting: before/after stats, date validation

On a DB with coverage already present, this is a no-op. On a DB missing coverage
(e.g., first CI run seeded from old release), it backfills once; coverage then
travels forward in the published DB artifact.

Usage:
    python scripts/backfill_shares_outstanding.py              # dry-run (reports only)
    python scripts/backfill_shares_outstanding.py --write      # apply to DB
    python scripts/backfill_shares_outstanding.py --write --max-companies 25  # test run
"""

import sys
import os
import sqlite3
import time
import logging
import argparse
from datetime import datetime

# Add valuation module to path for SEC client
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VALUATION_DIR = os.path.join(SCRIPT_DIR, "..", "..", "valuation")
sys.path.insert(0, VALUATION_DIR)

from data.edgar_client import _get

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(SCRIPT_DIR, "..", "db", "insider_signals.db")

# SEC rate limit: documented at 10 req/s, stay comfortably under
REQUEST_DELAY = 0.15  # ~6.6 req/s
BATCH_COMMIT_SIZE = 100  # Commit after this many successful fetches


def get_db():
    """Get database connection with busy timeout for concurrent access."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def _ensure_failures_table(conn):
    """
    Create shares_backfill_failures table if it doesn't exist.

    Persists permanent failures (CIK 404, no data) so we don't retry them every run.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shares_backfill_failures (
            cik INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            reason TEXT NOT NULL,
            last_attempt TEXT NOT NULL
        )
    """)
    conn.commit()


def get_coverage_stats(conn):
    """Get current shares_outstanding coverage statistics."""
    cur = conn.cursor()

    # Overall stats
    cur.execute("""
        SELECT
            COUNT(*) as total_rows,
            COUNT(DISTINCT company_id) as companies_with_data,
            MIN(date) as min_date,
            MAX(date) as max_date
        FROM shares_outstanding
    """)
    row = cur.fetchone()
    stats = {
        'total_rows': row[0],
        'companies_with_data': row[1],
        'min_date': row[2],
        'max_date': row[3],
    }

    # Companies with sufficient data for buyback signal (>=5 quarters)
    cur.execute("""
        SELECT COUNT(DISTINCT company_id)
        FROM (
            SELECT company_id, COUNT(*) as quarters
            FROM shares_outstanding
            GROUP BY company_id
            HAVING quarters >= 5
        )
    """)
    stats['companies_5plus_quarters'] = cur.fetchone()[0]

    # Total companies in universe
    cur.execute("SELECT COUNT(*) FROM companies")
    stats['total_companies'] = cur.fetchone()[0]

    # Corrupt dates (materially in the future)
    cur.execute("""
        SELECT COUNT(*) FROM shares_outstanding
        WHERE date > '2026-12-31'
    """)
    stats['corrupt_dates'] = cur.fetchone()[0]

    return stats


def get_target_companies(conn):
    """
    Get list of companies to backfill.

    Returns companies with a valid CIK that have <5 quarters of data, excluding
    companies with known permanent failures.
    """
    cur = conn.cursor()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Get CIKs with known permanent failures
    cur.execute("SELECT cik FROM shares_backfill_failures")
    failed_ciks = {row[0] for row in cur.fetchall()}
    if failed_ciks:
        logger.info(f"Skipping {len(failed_ciks)} CIKs with known permanent failures")

    # Get companies with <5 quarters of data
    query = """
        SELECT c.id, c.ticker, c.cik
        FROM companies c
        LEFT JOIN (
            SELECT company_id, COUNT(*) as quarters
            FROM shares_outstanding
            GROUP BY company_id
        ) so ON c.id = so.company_id
        WHERE c.cik IS NOT NULL
          AND c.cik != ''
          AND (so.quarters IS NULL OR so.quarters < 5)
        ORDER BY c.ticker
    """
    cur.execute(query)
    candidates = [(row[0], row[1], row[2]) for row in cur.fetchall()]

    # Filter out known failures
    candidates_filtered = [
        (company_id, ticker, cik)
        for company_id, ticker, cik in candidates
        if cik not in failed_ciks
    ]

    logger.info(f"Companies with <5 quarters: {len(candidates)}")
    logger.info(f"After filtering known failures: {len(candidates_filtered)}")

    return candidates_filtered


def _fetch_shares_outstanding(cik: int):
    """
    Fetch shares outstanding time series from SEC Company Facts API.

    Returns (success: bool, data: list[dict], error_type: str)
    - success=True, data=[{date, shares}, ...]: fetch succeeded
    - success=False, error_type='permanent': CIK doesn't exist or has no data (don't retry)
    - success=False, error_type='transient': rate limit or timeout (retry later)
    """
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"

    try:
        resp = _get(url)

        # Check for 404 (CIK doesn't exist)
        if resp.status_code == 404:
            return False, [], 'permanent'

        data = resp.json()
    except Exception as e:
        error_str = str(e).lower()

        # Classify error
        if '404' in error_str or 'not found' in error_str:
            return False, [], 'permanent'
        elif '429' in error_str or 'rate limit' in error_str:
            logger.warning(f"Rate limited on CIK {cik}")
            return False, [], 'transient'
        elif 'timeout' in error_str or 'connection' in error_str:
            logger.warning(f"Connection issue for CIK {cik}: {e}")
            return False, [], 'transient'
        else:
            logger.warning(f"Failed to fetch company facts for CIK {cik}: {e}")
            return False, [], 'transient'

    # Parse shares outstanding facts
    facts = data.get("facts", {})

    # Try both common labels for shares outstanding
    shares_units = None
    for taxonomy in ("dei", "us-gaap"):
        if taxonomy not in facts:
            continue
        for label in ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", "CommonStockSharesIssued"):
            if label in facts[taxonomy]:
                shares_units = facts[taxonomy][label].get("units", {})
                break
        if shares_units:
            break

    if not shares_units:
        # No shares outstanding data for this CIK (permanent)
        return False, [], 'permanent'

    # Prefer "shares" unit
    entries = shares_units.get("shares", []) or []
    if not entries:
        # Try first available unit
        for unit_name, unit_entries in shares_units.items():
            entries = unit_entries
            break

    if not entries:
        return False, [], 'permanent'

    results = []
    for entry in entries:
        # Prefer filed date with fiscal period, fall back to just end date
        date = entry.get("fp", "") and entry.get("end", "")
        if not date:
            date = entry.get("end", "")

        if date and entry.get("val"):
            results.append({
                "date": date,
                "shares": entry.get("val", 0),
            })

    return True, results, None


def _record_permanent_failure(conn, cik, ticker, reason, dry_run=False):
    """Record a permanent failure in the DB so it won't be retried next run."""
    if dry_run:
        return

    timestamp = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO shares_backfill_failures (cik, ticker, reason, last_attempt)
        VALUES (?, ?, ?, ?)
    """, (cik, ticker, reason, timestamp))
    conn.commit()


def insert_shares_data(conn, company_id, shares_data, dry_run=False):
    """
    Insert shares outstanding data using INSERT OR IGNORE (additive only).

    Returns number of rows inserted (estimate in dry-run mode).
    """
    if dry_run:
        return len(shares_data)

    cur = conn.cursor()
    rows = [
        (company_id, record['date'], record['shares'], 'sec_xbrl')
        for record in shares_data
    ]

    inserted = 0
    for row in rows:
        try:
            cur.execute(
                "INSERT OR IGNORE INTO shares_outstanding (company_id, date, shares, source) VALUES (?, ?, ?, ?)",
                row
            )
            inserted += cur.rowcount
        except sqlite3.IntegrityError:
            # Already exists, skip
            continue

    return inserted


def clean_corrupt_dates(conn, dry_run=False):
    """
    Clean corrupt dates (years 2027+) by deleting those rows.

    The date field has a NOT NULL constraint, so we delete rather than null.
    Returns list of (company_id, date) tuples that were cleaned.
    """
    cur = conn.cursor()

    # Find corrupt dates
    cur.execute("""
        SELECT id, company_id, date, shares
        FROM shares_outstanding
        WHERE date > '2026-12-31'
        ORDER BY date
    """)
    corrupt_rows = cur.fetchall()

    if not corrupt_rows:
        return []

    logger.info(f"\nFound {len(corrupt_rows)} rows with corrupt dates:")
    for row_id, company_id, date, shares in corrupt_rows:
        # Get ticker for reporting
        cur.execute("SELECT ticker FROM companies WHERE id = ?", (company_id,))
        ticker = cur.fetchone()[0]
        logger.info(f"  ID {row_id}: {ticker} (company_id={company_id}), date={date}, shares={shares}")

    if not dry_run:
        logger.info(f"Deleting {len(corrupt_rows)} rows with corrupt dates...")
        for row_id, company_id, date, shares in corrupt_rows:
            cur.execute("DELETE FROM shares_outstanding WHERE id = ?", (row_id,))
        conn.commit()
        logger.info("Done!")
    else:
        logger.info(f"DRY RUN: Would delete {len(corrupt_rows)} rows with corrupt dates")

    return [(company_id, date) for _, company_id, date, _ in corrupt_rows]


def run_backfill(dry_run=True, max_companies=None):
    """
    Main backfill entry point.

    Fetches shares outstanding for companies lacking coverage, inserts into DB
    with checkpointing and failure tracking.
    """
    start_time = time.time()

    conn = get_db()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Report coverage before
    logger.info("=" * 60)
    logger.info("SHARES OUTSTANDING BACKFILL — Coverage Before")
    logger.info("=" * 60)
    before = get_coverage_stats(conn)
    logger.info(f"  Total companies:            {before['total_companies']:,}")
    logger.info(f"  Companies with ANY data:    {before['companies_with_data']:,} ({before['companies_with_data']/before['total_companies']*100:.1f}%)")
    logger.info(f"  Companies with >=5 quarters: {before['companies_5plus_quarters']:,} ({before['companies_5plus_quarters']/before['total_companies']*100:.1f}%)")
    logger.info(f"  Total rows:                 {before['total_rows']:,}")
    logger.info(f"  Date range:                 {before['min_date']} → {before['max_date']}")
    logger.info(f"  Corrupt dates (2027+):      {before['corrupt_dates']}")

    # Clean corrupt dates first
    logger.info("\n" + "=" * 60)
    logger.info("CLEANING CORRUPT DATES")
    logger.info("=" * 60)
    cleaned = clean_corrupt_dates(conn, dry_run=dry_run)

    # Get target companies
    logger.info("\n" + "=" * 60)
    logger.info("IDENTIFYING WORK LIST")
    logger.info("=" * 60)
    companies = get_target_companies(conn)

    # Check how many have CIK
    total_needing_data = len(companies)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM companies WHERE cik IS NULL OR cik = ''")
    companies_without_cik = cur.fetchone()[0]

    logger.info(f"Companies needing data:     {total_needing_data}")
    logger.info(f"Companies without CIK:      {companies_without_cik} (cannot fetch)")

    if max_companies:
        companies = companies[:max_companies]
        logger.info(f"Limited to {max_companies} companies for testing")

    if dry_run:
        logger.info("\n" + "=" * 60)
        logger.info("DRY RUN MODE — No data will be written")
        logger.info("=" * 60)

    # Process companies
    total_rows_inserted = 0
    successful = []
    failed_permanent = []
    failed_transient = []
    batch_count = 0

    logger.info("\n" + "=" * 60)
    logger.info("FETCHING SHARES OUTSTANDING DATA")
    logger.info("=" * 60)

    for i, (company_id, ticker, cik) in enumerate(companies):
        if i > 0 and i % 50 == 0:
            logger.info(f"Progress: {i}/{len(companies)} companies processed, {total_rows_inserted:,} rows inserted")

        # Fetch data
        time.sleep(REQUEST_DELAY)  # Rate limiting
        success, shares_data, error_type = _fetch_shares_outstanding(cik)

        if success:
            # Insert data
            try:
                rows_inserted = insert_shares_data(conn, company_id, shares_data, dry_run=dry_run)

                if rows_inserted > 0:
                    total_rows_inserted += rows_inserted
                    successful.append((ticker, cik, len(shares_data)))
                    batch_count += 1

                    logger.debug(f"  {ticker} (CIK {cik}): {rows_inserted} rows inserted from {len(shares_data)} datapoints")

                    # Commit in batches
                    if not dry_run and batch_count >= BATCH_COMMIT_SIZE:
                        conn.commit()
                        batch_count = 0
                else:
                    # No new rows (already had data)
                    logger.debug(f"  {ticker} (CIK {cik}): no new rows (data already present)")

            except Exception as e:
                logger.warning(f"  {ticker} (CIK {cik}): insert failed - {e}")
                failed_transient.append((ticker, cik, 'insert_error'))
        else:
            # Fetch failed
            if error_type == 'permanent':
                failed_permanent.append((ticker, cik, 'no_data'))
                _record_permanent_failure(conn, cik, ticker, 'no_data', dry_run=dry_run)
                logger.debug(f"  {ticker} (CIK {cik}): no data available (permanent)")
            else:  # transient
                failed_transient.append((ticker, cik, error_type))
                logger.debug(f"  {ticker} (CIK {cik}): {error_type} (will retry)")

    # Final commit
    if not dry_run and batch_count > 0:
        conn.commit()

    # Report coverage after
    logger.info("\n" + "=" * 60)
    logger.info("SHARES OUTSTANDING BACKFILL — Coverage After")
    logger.info("=" * 60)

    if not dry_run:
        after = get_coverage_stats(conn)
        logger.info(f"  Total companies:            {after['total_companies']:,}")
        logger.info(f"  Companies with ANY data:    {after['companies_with_data']:,} ({after['companies_with_data']/after['total_companies']*100:.1f}%) [+{after['companies_with_data'] - before['companies_with_data']}]")
        logger.info(f"  Companies with >=5 quarters: {after['companies_5plus_quarters']:,} ({after['companies_5plus_quarters']/after['total_companies']*100:.1f}%) [+{after['companies_5plus_quarters'] - before['companies_5plus_quarters']}]")
        logger.info(f"  Total rows:                 {after['total_rows']:,} [+{after['total_rows'] - before['total_rows']:,}]")
        logger.info(f"  Date range:                 {after['min_date']} → {after['max_date']}")
        logger.info(f"  Corrupt dates (2027+):      {after['corrupt_dates']}")

        if after['corrupt_dates'] == 0:
            logger.info("  ✓ All corrupt dates cleaned")
        else:
            logger.warning(f"  ✗ {after['corrupt_dates']} corrupt dates remain")
    else:
        logger.info(f"  Would insert:               ~{total_rows_inserted:,} rows")
        logger.info(f"  Would add coverage for:     ~{len(successful)} companies")

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Companies processed:        {len(companies)}")
    logger.info(f"  Successful:                 {len(successful)}")
    logger.info(f"  Failed (permanent):         {len(failed_permanent)}")
    logger.info(f"  Failed (transient):         {len(failed_transient)}")
    logger.info(f"  Rows inserted:              {total_rows_inserted:,}")
    logger.info(f"  Corrupt dates cleaned:      {len(cleaned)}")
    logger.info(f"  Runtime:                    {time.time() - start_time:.0f}s")

    if failed_permanent:
        logger.info(f"\nPermanent failures: {len(failed_permanent)} (won't retry)")
        if len(failed_permanent) <= 20:
            for ticker, cik, reason in failed_permanent[:20]:
                logger.info(f"  {ticker} (CIK {cik}): {reason}")
        else:
            from collections import Counter
            reason_counts = Counter(r for _, _, r in failed_permanent)
            logger.info("  Breakdown by reason:")
            for reason, count in reason_counts.most_common():
                logger.info(f"    {reason}: {count}")

    if failed_transient:
        logger.info(f"\nTransient failures: {len(failed_transient)} (will retry next run)")
        if len(failed_transient) <= 20:
            for ticker, cik, reason in failed_transient[:20]:
                logger.info(f"  {ticker} (CIK {cik}): {reason}")

    # Show some successful samples
    if successful and not dry_run:
        logger.info(f"\nSample successful fetches (showing up to 5):")
        for ticker, cik, datapoints in successful[:5]:
            cur.execute("""
                SELECT date, shares
                FROM shares_outstanding so
                JOIN companies c ON so.company_id = c.id
                WHERE c.ticker = ?
                ORDER BY date DESC
                LIMIT 2
            """, (ticker,))
            recent = cur.fetchall()
            if recent:
                logger.info(f"  {ticker} (CIK {cik}): {datapoints} datapoints, latest: {recent[0][0]} ({recent[0][1]:,.0f} shares)")

    conn.close()

    return {
        'total_rows': total_rows_inserted,
        'successful': len(successful),
        'failed_permanent': len(failed_permanent),
        'failed_transient': len(failed_transient),
        'corrupt_dates_cleaned': len(cleaned),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill shares outstanding data from SEC XBRL companyfacts"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply changes to database (default: dry-run only)"
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        help="Limit to N companies (testing)"
    )

    args = parser.parse_args()

    run_backfill(dry_run=not args.write, max_companies=args.max_companies)
