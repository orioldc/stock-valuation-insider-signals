#!/usr/bin/env python3
"""Clean up malformed transaction_date values in existing insider_transactions.

Applies the same normalization logic used in the ingestion pipeline to rows
already in the database. Handles two known malformations:
1. Trailing timezone offset (e.g., '2024-06-27-05:00')
2. Two-digit year (e.g., '24-02-12')

Future dates beyond today are rejected. Already-valid ISO dates pass through
unchanged, even if transaction_date > filing_date.

Usage:
    python scripts/cleanup_transaction_dates.py              # dry-run (reports only)
    python scripts/cleanup_transaction_dates.py --write      # apply fixes to DB

Output:
    Counts of rows inspected, fixed by each rule, nulled, and left alone.
    Also reports data quality findings (e.g., transaction_date > filing_date).
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data_ingestion.bulk_edgar import normalize_transaction_date

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "insider_signals.db")


def main():
    parser = argparse.ArgumentParser(description="Clean up malformed transaction_date values")
    parser.add_argument("--write", action="store_true",
                        help="Apply fixes to database (default: dry-run only)")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"Database path (default: {DB_PATH})")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # Fetch all rows with transaction_date and filing_date
    print(f"{'DRY RUN' if not args.write else 'WRITE MODE'}: Processing {args.db}\n")
    cur.execute("""
        SELECT id, transaction_date, filing_date
        FROM insider_transactions
        WHERE transaction_date IS NOT NULL
    """)
    rows = cur.fetchall()
    print(f"Inspected: {len(rows)} rows with non-NULL transaction_date\n")

    # Categorize fixes
    stats = {
        "unchanged": 0,
        "timezone_offset": 0,
        "two_digit_year": 0,
        "nulled": 0,
    }
    updates = []
    txn_after_filing = []

    for row_id, txn_date, filing_date in rows:
        normalized = normalize_transaction_date(txn_date, filing_date)

        if normalized == txn_date:
            stats["unchanged"] += 1
            # Data quality check: transaction_date > filing_date (not fixed, just reported)
            if filing_date and txn_date and len(txn_date) == 10 and len(filing_date) == 10:
                if txn_date > filing_date:
                    txn_after_filing.append((row_id, txn_date, filing_date))
        elif normalized is None:
            stats["nulled"] += 1
            updates.append((row_id, None, txn_date, filing_date))
        else:
            # Categorize the fix type
            if len(txn_date) > 10 and txn_date[10] == '-':
                stats["timezone_offset"] += 1
            elif len(txn_date) == 8 and txn_date[2] == '-' and txn_date[5] == '-':
                stats["two_digit_year"] += 1
            updates.append((row_id, normalized, txn_date, filing_date))

    # Report
    print("Results:")
    print(f"  Unchanged (already valid):  {stats['unchanged']}")
    print(f"  Fixed (timezone offset):    {stats['timezone_offset']}")
    print(f"  Fixed (two-digit year):     {stats['two_digit_year']}")
    print(f"  Nulled (future dates):      {stats['nulled']}")
    print(f"  Total fixes:                {len(updates)}")

    # Data quality findings (not fixed, just reported)
    if txn_after_filing:
        print(f"\nData quality finding:")
        print(f"  Rows where transaction_date > filing_date: {len(txn_after_filing)}")
        print(f"    (Not fixed - cannot determine which field is wrong)")
        # Categorize by time gap
        one_day = sum(1 for _, t, f in txn_after_filing if (t[:10] > f[:10]) and abs(int(t[8:10]) - int(f[8:10])) <= 1)
        two_to_seven = sum(1 for _, t, f in txn_after_filing if abs(int(t[8:10]) - int(f[8:10])) in range(2, 8))
        print(f"    Sample breakdown: 1 day: {one_day}, examples: {txn_after_filing[:3]}")

    # Show sample fixes
    if updates:
        print(f"\nSample fixes (showing up to 10):")
        for row_id, new_val, old_val, filing in updates[:10]:
            print(f"  ID {row_id}: {old_val} → {new_val} (filing: {filing})")

    # Apply updates
    if args.write and updates:
        print(f"\nApplying {len(updates)} updates to database...")
        applied = 0
        skipped = 0
        for row_id, new_val, _, _ in updates:
            try:
                cur.execute(
                    "UPDATE insider_transactions SET transaction_date = ? WHERE id = ?",
                    (new_val, row_id)
                )
                applied += 1
            except sqlite3.IntegrityError as e:
                # Skip updates that would violate UNIQUE constraint
                # (normalized value already exists for this company/insider/type/shares)
                skipped += 1
        conn.commit()
        print(f"Done! Applied {applied} updates, skipped {skipped} duplicates.")

        # Verify
        cur.execute("SELECT MAX(transaction_date) FROM insider_transactions")
        max_date = cur.fetchone()[0]
        print(f"\nVerification: MAX(transaction_date) = {max_date}")
    elif not args.write and updates:
        print(f"\nDRY RUN: Would have updated {len(updates)} rows.")
        print("Run with --write to apply changes.")

    conn.close()


if __name__ == "__main__":
    main()
