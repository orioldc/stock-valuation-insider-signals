#!/usr/bin/env python3
"""
Backfill current-quarter Form 4 filings using SEC's quarterly index.

Instead of probing every company's CIK, this fetches SEC's form.idx which lists
ALL filings for the quarter in a single request, filters to Form 4/4/A, matches
to tracked companies, and fetches only those filings.

Substantially faster than the per-company probe approach for sparse quarters.

Usage:
    python backfill_quarter_index.py [--year YYYY] [--quarter N]
"""

import sys
import os
import json
import logging
import argparse
import re
import time
import sqlite3
from datetime import datetime
from typing import Set, List, Dict, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_ingestion"))

from data_ingestion.data_loader import get_db, ensure_company, _store_filings
from data_ingestion.edgar_client import _get as sec_get, get_rate_stats, fetch_form4_filings, fetch_sec_company_list
from data_ingestion.form4_rules import TRANSACTION_FORM_TYPES

# Add pipeline to path for provenance
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from pipeline.provenance import record_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "insider_signals.db")
CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")

def _ensure_failures_table(conn):
    """
    Create quarter_index_failures table if it doesn't exist.

    Persists permanent failures (CIK not found, parse errors) so we don't
    retry them every quarter.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quarter_index_failures (
            cik TEXT NOT NULL,
            ticker TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            reason TEXT NOT NULL,
            last_attempt TEXT NOT NULL,
            PRIMARY KEY (cik, year, quarter)
        )
    """)
    conn.commit()


def _record_permanent_failure(conn, cik, ticker, year, quarter, reason):
    """Record a permanent failure in the DB so it won't be retried next run."""
    timestamp = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO quarter_index_failures (cik, ticker, year, quarter, reason, last_attempt)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (cik, ticker, year, quarter, reason, timestamp))
    conn.commit()


def _checkpoint_path(year, quarter):
    """Get checkpoint file path for this quarter's backfill."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"backfill_idx_{year}_Q{quarter}.json")


def _load_checkpoint(year, quarter) -> Set[str]:
    """Load set of completed accession numbers for this quarter."""
    path = _checkpoint_path(year, quarter)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        return set(data.get("completed", []))
    return set()


def _save_checkpoint(year, quarter, completed_set: Set[str]):
    """Save completed accession numbers for this quarter."""
    path = _checkpoint_path(year, quarter)
    with open(path, "w") as f:
        json.dump(
            {"completed": list(completed_set), "updated": datetime.now().isoformat()},
            f,
        )


def _clear_checkpoint(year, quarter):
    """Remove checkpoint after successful completion."""
    path = _checkpoint_path(year, quarter)
    if os.path.exists(path):
        os.remove(path)


def fetch_form_index(year: int, quarter: int) -> str:
    """Fetch SEC's form.idx for the given year and quarter."""
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    logger.info(f"Fetching form index: {url}")
    # Go through edgar_client like every other SEC call: requests brings its
    # own CA certificates, so this works on Pythons where urllib cannot verify
    # SEC's certificate (it failed that way on the monthly runner).
    try:
        content = sec_get(url).content.decode('latin-1')  # SEC uses latin-1 encoding
        logger.info(f"Fetched form index: {len(content)} bytes")
        return content
    except Exception as e:
        logger.error(f"Failed to fetch form index: {e}")
        raise


def parse_form_index(index_content: str) -> List[Dict[str, str]]:
    """
    Parse form.idx content to extract Form 4, 4/A, 5 and 5/A filings.

    Each filing is listed once per CIK on it (the issuer and every reporting
    owner), so 'cik' here is not necessarily the issuer; parse_form4_xml drops
    filings whose issuer is a different company.

    Format (after header lines):
    Form Type    Company Name                     CIK        Date Filed  File Name
    4            AARON'S COMPANY, INC. (THE)      0001807966 2026-07-01  edgar/data/1807966/0001209191-26-046401.txt
    """
    filings = []
    lines = index_content.split('\n')

    # Skip header lines (format description and column headers)
    # Header ends with a dashed line
    in_data = False
    for line in lines:
        if line.startswith('---'):
            in_data = True
            continue
        if not in_data or not line.strip():
            continue

        # Columns are nominally fixed-width, but long company names push the
        # later columns right, so read CIK, date and file name from the end.
        parts = line.split()
        if len(parts) < 5 or not parts[-1].endswith('.txt'):
            continue
        form_type = line[0:12].strip()
        cik_str, date_filed, file_name = parts[-3], parts[-2], parts[-1]
        company_name = line[12:].rsplit(cik_str, 1)[0].strip()

        if form_type not in TRANSACTION_FORM_TYPES:
            continue

        # Extract CIK (remove leading zeros but keep as string)
        try:
            cik = str(int(cik_str))  # Remove leading zeros
        except ValueError:
            logger.warning(f"Invalid CIK: {cik_str}")
            continue

        # Extract accession number from file name
        # Format: edgar/data/{CIK}/{ACCESSION}.txt
        match = re.search(r'/([0-9-]+)\.txt$', file_name)
        if not match:
            logger.warning(f"Could not extract accession number from: {file_name}")
            continue
        accession_number = match.group(1)

        # Extract the directory CIK from file_name (edgar/data/{DIR_CIK}/{ACCESSION}.txt)
        parts = file_name.split('/')
        dir_cik = parts[2] if len(parts) > 2 else cik

        filings.append({
            'form_type': form_type,
            'company_name': company_name,
            'cik': cik,  # Issuer CIK
            'dir_cik': dir_cik,  # Directory CIK (for file paths)
            'filing_date': date_filed,
            'file_name': file_name,
            'accession_number': accession_number,
        })

    return filings


def get_tracked_ciks(conn) -> Dict[str, Tuple[int, str]]:
    """Get map of CIK -> (company_id, ticker) for companies we track."""
    cur = conn.cursor()
    cur.execute("SELECT id, ticker, cik FROM companies WHERE cik IS NOT NULL")
    rows = cur.fetchall()

    # CIK as key, (company_id, ticker) as value
    cik_map = {}
    for company_id, ticker, cik in rows:
        # Normalize CIK (remove leading zeros)
        normalized_cik = str(int(cik)) if cik else None
        if normalized_cik:
            cik_map[normalized_cik] = (company_id, ticker)

    return cik_map


def run_backfill(year: int, quarter: int):
    """Backfill Form 4 filings for the given quarter using SEC's index."""

    # Get DB connection early for failures table
    conn = get_db()

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Purge stale failure records for this quarter where we now have data
    # (Success in one run should clear the failure from a previous run)
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM quarter_index_failures
        WHERE year = ? AND quarter = ?
          AND cik IN (
              SELECT DISTINCT CAST(c.cik AS TEXT)
              FROM companies c
              JOIN insider_transactions it ON c.id = it.company_id
              WHERE it.filing_date >= ?
          )
    """, (year, quarter, f"{year}-{((quarter - 1) * 3 + 1):02d}-01"))
    purged = cur.rowcount
    if purged > 0:
        logger.info(f"Purged {purged} stale failure records for this quarter with new transactions")
        conn.commit()

    conn.close()

    # Load checkpoint
    completed = _load_checkpoint(year, quarter)
    logger.info(f"Checkpoint: {len(completed)} companies already completed")

    # Fetch and parse the index
    index_content = fetch_form_index(year, quarter)
    all_filings = parse_form_index(index_content)
    logger.info(f"Index contains {len(all_filings)} Form 4/5 filings")

    # Get tracked CIKs
    conn = get_db()
    tracked_ciks = get_tracked_ciks(conn)
    conn.close()
    logger.info(f"Tracking {len(tracked_ciks)} companies with CIKs")

    # Listed companies with no row yet (an IPO, or a new holding company that
    # took over an existing ticker) would otherwise be missed until the next
    # bulk file. Their row is created when they are processed.
    sec_map, sec_titles = fetch_sec_company_list()
    ticker_by_cik = {}
    for t, c in sec_map.items():
        ticker_by_cik.setdefault(str(c), t)
    new_listed = {f["cik"] for f in all_filings} & set(ticker_by_cik) - set(tracked_ciks)
    for cik in new_listed:
        tracked_ciks[cik] = (None, ticker_by_cik[cik])
    logger.info(f"Adding {len(new_listed)} listed companies not yet tracked")

    # Find unique issuer CIKs in the index that we track
    ciks_with_filings = set()
    untracked_count = 0
    for filing in all_filings:
        if filing['cik'] in tracked_ciks:
            ciks_with_filings.add(filing['cik'])
        else:
            untracked_count += 1

    logger.info(
        f"Found {len(ciks_with_filings)} tracked companies with filings "
        f"(vs {len(tracked_ciks)} total tracked companies, "
        f"{untracked_count} untracked filings)"
    )

    # Filter to companies not yet completed
    ciks_to_process = [cik for cik in ciks_with_filings if cik not in completed]

    if not ciks_to_process:
        logger.info("No new companies to process")
        return {
            "total_filings": len(all_filings),
            "tracked_companies": len(ciks_with_filings),
            "processed": 0,
            "inserted": 0,
            "errors": 0,
        }

    logger.info(f"Processing {len(ciks_to_process)} companies ({len(completed)} already completed)")

    # Quarter date range
    quarter_start = f"{year}-{((quarter - 1) * 3 + 1):02d}-01"

    # Process companies (using edgar_client's fetch_form4_filings)
    conn = get_db()
    conn.execute("PRAGMA busy_timeout = 60000")

    total_inserted = 0
    errors = 0
    processed = 0

    try:
        for idx, cik in enumerate(ciks_to_process):
            company_id, ticker = tracked_ciks[cik]

            try:
                if company_id is None:
                    company_id = ensure_company(conn, ticker, int(cik), sec_titles.get(int(cik)))
                # Fetch filings for this company (edgar_client handles finding primary docs)
                filings = fetch_form4_filings(cik, limit=None, since_date=quarter_start)

                # Parse failures are logged inside parse_form4_xml and yield no rows.
                company_inserted = _store_filings(conn, company_id, ticker, filings)
                parse_failures = 0

                conn.commit()
                total_inserted += company_inserted

                # Checkpoint on success or partial success
                if company_inserted > 0 or parse_failures < len(filings):
                    completed.add(cik)
                    processed += 1

                    # Write-through: success clears any existing failure record for this quarter
                    conn.execute("""
                        DELETE FROM quarter_index_failures
                        WHERE cik = ? AND year = ? AND quarter = ?
                    """, (cik, year, quarter))
                    conn.commit()
                else:
                    logger.warning(f"{ticker} ({cik}): all {len(filings)} filings failed to parse")

                # Progress log every 200 companies
                if (idx + 1) % 200 == 0:
                    stats = get_rate_stats()
                    logger.info(
                        f"Progress: {idx + 1}/{len(ciks_to_process)} companies | "
                        f"{total_inserted} new txns | "
                        f"errors={errors} | "
                        f"delay={stats.get('current_delay', 0):.2f}s"
                    )
                    _save_checkpoint(year, quarter, completed)

            except Exception as e:
                err_str = str(e)
                logger.warning(f"{ticker} ({cik}): error - {e}")
                # Treat 503/429 as transient
                if "503" not in err_str and "429" not in err_str and "Failed after" not in err_str:
                    # Permanent error - record failure
                    _record_permanent_failure(conn, cik, ticker, year, quarter, str(e)[:200])
                    errors += 1
                    completed.add(cik)
                    processed += 1

        # Final checkpoint
        _save_checkpoint(year, quarter, completed)

        all_done = len(completed) == len(ciks_with_filings)

        print(
            f"\n{'=' * 60}\n"
            f"BACKFILL COMPLETE\n"
            f"{'=' * 60}\n"
            f"Total filings in index:       {len(all_filings)}\n"
            f"Tracked companies:            {len(ciks_with_filings)}\n"
            f"Companies processed:          {processed}\n"
            f"Total new transactions:       {total_inserted}\n"
            f"Errors (permanent):           {errors}\n"
            f"All companies completed:      {all_done}\n"
        )

        if all_done:
            _clear_checkpoint(year, quarter)
            logger.info("All companies completed - checkpoint cleared")
        else:
            remaining = len(ciks_to_process) - processed
            logger.info(f"{remaining} companies remain - checkpoint preserved for retry")

    finally:
        conn.close()

    return {
        "total_filings": len(all_filings),
        "tracked_companies": len(ciks_with_filings),
        "processed": processed,
        "inserted": total_inserted,
        "errors": errors,
    }


def _current_quarter() -> Tuple[int, int]:
    """Return (year, quarter) for the current calendar quarter."""
    now = datetime.now()
    quarter = ((now.month - 1) // 3) + 1
    return (now.year, quarter)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill current-quarter Form 4 filings using SEC's quarterly index"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Year (default: current year)",
    )
    parser.add_argument(
        "--quarter",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help="Quarter 1-4 (default: current quarter)",
    )
    args = parser.parse_args()

    year, quarter = args.year, args.quarter
    if year is None or quarter is None:
        default_year, default_quarter = _current_quarter()
        year = year or default_year
        quarter = quarter or default_quarter

    logger.info(f"Starting backfill for {year} Q{quarter}")

    # Use provenance tracking
    with record_run(DB_PATH, 'insider_transactions') as run:
        stats = run_backfill(year, quarter)
        run.rows_written = stats['inserted']
        run.coverage(stats['processed'], stats['tracked_companies'])
        run.permanent_failures = stats['errors']
        # Also record quarter metadata for tracking which quarters are complete
        run.detail = {'year': year, 'quarter': quarter}

    logger.info("")
    logger.info(f"Provenance recorded: source='insider_transactions' (quarter={year}Q{quarter})")
