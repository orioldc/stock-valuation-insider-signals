#!/usr/bin/env python3
"""
CLI spot-checker: compare SEC Form 4 filings against the local DB for a company.

Usage:
    python validate_company.py --ticker AAPL
    python validate_company.py --cik 320193
    python validate_company.py --ticker AAPL --since 2025-01-01
"""

import sys
import os
import argparse
import logging
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_ingestion"))

from data_ingestion.edgar_client import fetch_form4_filings, parse_form4_xml
from data_ingestion.data_loader import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _norm_cik(value):
    """Normalize a CIK to a canonical no-leading-zeros string for matching.

    Returns "" (the null sentinel) for any input that is None, empty, or
    numerically zero — CIK 0 is never a valid SEC CIK. Non-zero numeric
    strings are returned as their decimal integer string (leading zeros
    stripped). Non-numeric strings are left-stripped of zeros as a fallback.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        n = int(s)
    except ValueError:
        return s.lstrip("0")
    return str(n) if n != 0 else ""


def _current_quarter_start():
    """Return first day of the current calendar quarter as 'YYYY-MM-DD'."""
    now = datetime.now()
    q_start_month = ((now.month - 1) // 3) * 3 + 1
    return f"{now.year}-{q_start_month:02d}-01"


def _resolve_company(args):
    """
    Look up company_id and cik from the DB.
    Returns (company_id, cik, ticker) or exits on error.
    """
    conn = get_db()
    try:
        if args.ticker:
            cur = conn.execute(
                "SELECT id, cik, ticker FROM companies WHERE UPPER(ticker) = UPPER(?)",
                (args.ticker,),
            )
            row = cur.fetchone()
            if row is None:
                print(f"ERROR: ticker '{args.ticker}' not found in companies table")
                sys.exit(1)
            company_id, cik, ticker = row
        else:
            try:
                cik_int = int(args.cik)
            except ValueError:
                print(f"ERROR: CIK '{args.cik}' must be a number")
                sys.exit(1)
            cur = conn.execute(
                "SELECT id, cik, ticker FROM companies WHERE cik = ?",
                (cik_int,),
            )
            row = cur.fetchone()
            if row is None:
                print(f"ERROR: CIK '{args.cik}' not found in companies table")
                sys.exit(1)
            company_id, cik, ticker = row
    finally:
        conn.close()

    if cik is None:
        print(f"ERROR: company '{ticker}' has no CIK stored - cannot fetch SEC filings")
        sys.exit(1)

    return company_id, cik, ticker


def _build_sec_keys(cik, since):
    """
    Fetch all Form 4 filings for cik since `since` and build a DISTINCT SET of
    transaction keys: (transaction_date, transaction_code, round(shares, 2),
    normalized_insider_cik).

    CIK-based matching avoids false misses from name formatting differences across
    data sources (e.g. "LIN CHIH-HSIANG (THOMPSON)" vs a differently-formatted name).

    Transactions whose normalized insider CIK is "" (None or unparseable) are excluded
    from the set and counted separately as null_cik_sec; they cannot be matched
    reliably.

    The set naturally dedups transactions that parse_form4_xml returns more than once
    for a single filing (a known parser quirk where the same transaction appears
    multiple times in the XML output).

    Also tracks the earliest transaction_date seen across all SEC transactions so the
    caller can widen the DB window to capture late-filed-but-early-dated transactions.

    Returns (filings_count, sec_keys:set, null_cik_sec:int, earliest_txn_date) where
    earliest_txn_date is a 'YYYY-MM-DD' string or None if no transactions were found.

    NOTE: fetch_form4_filings filters by filing_date (when the Form 4 was submitted
    to EDGAR), not by transaction_date. A filing submitted in April for a March
    transaction will be included here if `since` covers April, but its
    transaction_date is in March. The caller widens the DB window to the earliest
    transaction_date seen so late-filed transactions are not missed. The comparison
    is performed on distinct keys so parser-duplicated transactions do not inflate
    the SEC count and produce false "missing" results.
    """
    filings = fetch_form4_filings(cik, limit=None, since_date=since)
    sec_keys = set()
    null_cik_sec = 0
    earliest_txn_date = None
    for filing in filings:
        try:
            txns = parse_form4_xml(
                filing["cik"],
                filing["accession_number"],
                filing["primary_doc"],
            )
        except Exception as e:
            logger.warning(
                f"Failed to parse filing {filing.get('accession_number', '?')}: {e}"
            )
            continue
        for txn in txns:
            txn_date = txn.get("transaction_date", "")
            if txn_date and (earliest_txn_date is None or txn_date < earliest_txn_date):
                earliest_txn_date = txn_date
            norm = _norm_cik(txn.get("insider_cik"))
            if not norm:
                null_cik_sec += 1
                continue
            key = (
                txn_date,
                txn.get("transaction_code", ""),
                round(txn.get("shares") or 0, 2),
                norm,
            )
            sec_keys.add(key)
    return len(filings), sec_keys, null_cik_sec, earliest_txn_date


def _build_db_keys(company_id, window_start):
    """
    Load insider_transactions rows for company_id with transaction_date >= window_start
    and build a DISTINCT SET of keys: (transaction_date, transaction_type,
    round(shares_transacted, 2), normalized_reporting_cik).

    CIK-based matching avoids false misses from name formatting differences across
    data sources.

    Rows whose normalized reporting_cik is "" are excluded from the set and counted
    separately as null_cik_db.

    db_dup_count is the number of EXTRA rows sharing the same key within the window
    (i.e. sum(count - 1) over all keys appearing more than once). The DB's UNIQUE
    constraint on (company_id, transaction_date, reporting_cik, transaction_type,
    shares_transacted) should make this 0; a non-zero value indicates duplicate rows
    that slipped past the constraint.

    display_names maps each key to a sample reporting_name for human-readable output.

    Returns (db_keys:set, db_dup_count:int, null_cik_db:int, display_names:dict).
    """
    conn = get_db()
    try:
        cur = conn.execute(
            """
            SELECT transaction_date, transaction_type, shares_transacted, reporting_cik, reporting_name
            FROM insider_transactions
            WHERE company_id = ?
              AND transaction_date >= ?
            """,
            (company_id, window_start),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    key_counter = Counter()
    display_names = {}
    null_cik_db = 0

    for txn_date, txn_type, shares, cik, name in rows:
        norm = _norm_cik(cik)
        if not norm:
            null_cik_db += 1
            continue
        key = (
            txn_date or "",
            txn_type or "",
            round(shares or 0, 2),
            norm,
        )
        key_counter[key] += 1
        if key not in display_names and name:
            display_names[key] = name

    db_keys = set(key_counter.keys())
    db_dup_count = sum(count - 1 for count in key_counter.values() if count > 1)

    return db_keys, db_dup_count, null_cik_db, display_names


def main():
    parser = argparse.ArgumentParser(
        description="Spot-check a company's SEC Form 4 filings against the local DB"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", metavar="TICKER", help="Company ticker symbol")
    group.add_argument("--cik", metavar="CIK", help="Company CIK number")
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        default=None,
        help="Start date for SEC filings (default: first day of current quarter)",
    )
    args = parser.parse_args()

    since = args.since or _current_quarter_start()

    company_id, cik, ticker = _resolve_company(args)
    print(f"\nValidating: {ticker} | CIK: {cik} | company_id: {company_id} | since: {since}")
    print("-" * 60)

    logger.info(f"Fetching SEC filings for CIK {cik} since {since}...")
    filing_count, sec_keys, null_cik_sec, earliest_txn_date = _build_sec_keys(cik, since)

    # Widen the DB window to capture late-filed transactions: a Form 4 filed after
    # `since` may report a transaction dated before `since`. Using the earliest
    # transaction_date seen in SEC results as the window floor ensures those rows
    # are present in the DB query. The widened window may pull in DB rows from before
    # `since` that have no SEC counterpart in this fetch; those appear as DB-only and
    # are expected, not errors.
    window_start = min(since, earliest_txn_date) if earliest_txn_date else since

    logger.info(f"Loading DB transactions for company_id {company_id} (window_start={window_start})...")
    db_keys, db_dup_count, null_cik_db, display_names = _build_db_keys(company_id, window_start)

    matched = len(sec_keys & db_keys)
    missing = sec_keys - db_keys
    db_only = db_keys - sec_keys

    print(f"\nSummary:")
    print(f"  SEC filings fetched:               {filing_count}")
    print(f"  SEC distinct transactions:         {len(sec_keys)}")
    print(f"  DB distinct transactions in window:{len(db_keys)}")
    print(f"  Matched:                           {matched}")
    print(f"  Missing from DB:                   {len(missing)}")
    print(f"  DB-only (no SEC match):            {len(db_only)}")
    print(f"  In-DB duplicate rows:              {db_dup_count}")
    print(f"  SEC null-CIK skipped:              {null_cik_sec}")
    print(f"  DB null-CIK skipped:               {null_cik_db}")

    if missing:
        print(f"\nMissing from DB (up to 10 examples):")
        for key in list(missing)[:10]:
            txn_date, txn_code, shares, cik_key = key
            print(f"  date={txn_date} type={txn_code} shares={shares} cik={cik_key}")

    if db_only:
        print(f"\nDB-only (no SEC match, up to 10 examples):")
        for key in list(db_only)[:10]:
            txn_date, txn_code, shares, cik_key = key
            name = display_names.get(key, "")
            print(f"  date={txn_date} type={txn_code} shares={shares} cik={cik_key} name={name}")

    print()


if __name__ == "__main__":
    main()
