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


def _build_sec_counter(cik, since):
    """
    Fetch all Form 4 filings for cik since `since` and build a Counter keyed by
    (transaction_date, transaction_code, round(shares, 2), insider_name.upper()).
    Returns (filings_count, counter).

    NOTE: fetch_form4_filings filters by filing_date (when the Form 4 was submitted
    to EDGAR), not by transaction_date. A filing submitted in April for a March
    transaction will be included here if `since` covers April, but its
    transaction_date is in March. See also _build_db_counter for the complementary
    date-window note.
    """
    filings = fetch_form4_filings(cik, limit=None, since_date=since)
    sec_counter = Counter()
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
            key = (
                txn.get("transaction_date", ""),
                txn.get("transaction_code", ""),
                round(txn.get("shares") or 0, 2),
                (txn.get("insider_name") or "").upper(),
            )
            sec_counter[key] += 1
    return len(filings), sec_counter


def _build_db_counter(company_id, since):
    """
    Load insider_transactions rows for company_id since `since` and build a Counter keyed by
    (transaction_date, transaction_type, round(shares_transacted, 2), reporting_name.upper()).
    Returns counter.

    NOTE: this counter filters by transaction_date >= since, not by filing_date.
    See _build_sec_counter for the complementary note on the date-window skew.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            """
            SELECT transaction_date, transaction_type, shares_transacted, reporting_name
            FROM insider_transactions
            WHERE company_id = ?
              AND transaction_date >= ?
            """,
            (company_id, since),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    db_counter = Counter()
    for txn_date, txn_type, shares, name in rows:
        key = (
            txn_date or "",
            txn_type or "",
            round(shares or 0, 2),
            (name or "").upper(),
        )
        db_counter[key] += 1
    return db_counter


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

    # NOTE: fetch_form4_filings filters by filing_date, while the DB filter uses
    # transaction_date. Transactions filed late (e.g. April Form 4 for a March
    # transaction) may appear in SEC results but not the DB window. Pass --since
    # one quarter earlier than the period you are validating to capture these.
    logger.info(f"Fetching SEC filings for CIK {cik} since {since}...")
    filing_count, sec_counter = _build_sec_counter(cik, since)

    logger.info(f"Loading DB transactions for company_id {company_id}...")
    db_counter = _build_db_counter(company_id, since)

    sec_total = sum(sec_counter.values())
    db_total = sum(db_counter.values())

    # Matched: keys present in both counters
    matched_keys = set(sec_counter.keys()) & set(db_counter.keys())
    matched_count = sum(min(sec_counter[k], db_counter[k]) for k in matched_keys)

    # Missing from DB: in SEC but not in DB (or fewer in DB than in SEC)
    missing = Counter()
    for key, count in sec_counter.items():
        db_count = db_counter.get(key, 0)
        if count > db_count:
            missing[key] = count - db_count

    # Over-represented in DB: more in DB than in SEC, but only for keys present in SEC
    over = Counter()
    for key in sec_counter:
        db_count = db_counter.get(key, 0)
        sec_count = sec_counter[key]
        if db_count > sec_count:
            over[key] = db_count - sec_count

    # DB-only rows: present in DB but have no matching key in SEC at all.
    db_only = Counter()
    for key in db_counter:
        if key not in sec_counter:
            db_only[key] += db_counter[key]

    print(f"\nSummary:")
    print(f"  SEC filings fetched:      {filing_count}")
    print(f"  SEC transactions (total): {sec_total}")
    print(f"  DB transactions (total):  {db_total}")
    print(f"  Matched:                  {matched_count}")
    print(f"  Missing from DB:          {sum(missing.values())}")
    print(f"  Over-represented in DB:   {sum(over.values())}")
    print(f"  DB-only (no SEC match):   {sum(db_only.values())}")

    if missing:
        print(f"\nMissing from DB (up to 10 examples):")
        for key, count in list(missing.most_common(10)):
            txn_date, txn_code, shares, name = key
            print(f"  date={txn_date} type={txn_code} shares={shares} name={name} missing_count={count}")

    if over:
        print(f"\nDuplicate/over-represented in DB (keys also present in SEC):")
        for key, count in list(over.most_common(10)):
            txn_date, txn_code, shares, name = key
            print(f"  date={txn_date} type={txn_code} shares={shares} name={name} extra={count}")

    if db_only:
        print(f"\nDB-only rows (no SEC match, up to 10 examples):")
        for key, count in list(db_only.most_common(10)):
            txn_date, txn_code, shares, name = key
            print(f"  date={txn_date} type={txn_code} shares={shares} name={name} count={count}")

    print()


if __name__ == "__main__":
    main()
