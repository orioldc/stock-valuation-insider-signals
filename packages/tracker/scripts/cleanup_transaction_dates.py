#!/usr/bin/env python3
"""Clean up malformed transaction_date values in existing insider_transactions.

Applies the same normalization logic used in the ingestion pipeline to rows
already in the database. Handles three known malformations:
1. Trailing timezone offset (e.g., '2024-06-27-05:00')
2. Two-digit year (e.g., '24-02-12')
3. Zero-padded century (e.g., '0022-10-12')

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
    parser.add_argument("--delete-duplicates", action="store_true",
                        help="Delete genuine duplicates (rows whose normalized value already exists)")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"Database path (default: {DB_PATH})")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout = 60000")  # 60s timeout for concurrent access
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
        "zero_padded_century": 0,
        "nulled": 0,
    }
    updates = []
    duplicates = []  # Genuine duplicates to delete
    txn_after_filing = []

    # First pass: categorize all rows
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
            # Check if normalized value already exists in a correctly-formatted row
            # Fetch the row details for BOTH unique constraints
            cur.execute("""
                SELECT company_id, reporting_cik, reporting_name,
                       transaction_type, shares_transacted, price
                FROM insider_transactions WHERE id = ?
            """, (row_id,))
            row = cur.fetchone()
            company_id, reporting_cik, reporting_name, txn_type, shares, price = row

            # Check BOTH unique constraints:
            # 1. Table constraint: (company_id, transaction_date, reporting_cik, transaction_type, shares_transacted)
            cur.execute("""
                SELECT id, transaction_date FROM insider_transactions
                WHERE company_id = ? AND transaction_date = ?
                  AND reporting_cik = ? AND transaction_type = ?
                  AND shares_transacted = ?
                  AND id != ?
            """, (company_id, normalized, reporting_cik, txn_type, shares, row_id))
            existing_cik = cur.fetchone()

            # 2. Unique index: (company_id, transaction_date, reporting_name, transaction_type, shares_transacted, price)
            cur.execute("""
                SELECT id, transaction_date FROM insider_transactions
                WHERE company_id = ? AND transaction_date = ?
                  AND reporting_name = ? AND transaction_type = ?
                  AND shares_transacted = ? AND price = ?
                  AND id != ?
            """, (company_id, normalized, reporting_name, txn_type, shares, price, row_id))
            existing_name = cur.fetchone()

            if existing_cik or existing_name:
                # Genuine duplicate: normalized value already exists
                existing = existing_cik or existing_name
                duplicates.append((row_id, txn_date, normalized, existing[0]))
            else:
                # Safe to update
                if len(txn_date) > 10 and txn_date[10] == '-':
                    stats["timezone_offset"] += 1
                elif len(txn_date) == 8 and txn_date[2] == '-' and txn_date[5] == '-':
                    stats["two_digit_year"] += 1
                elif len(txn_date) == 10 and txn_date[4] == '-' and txn_date[7] == '-' and txn_date[:2] == '00':
                    stats["zero_padded_century"] += 1
                updates.append((row_id, normalized, txn_date, filing_date))

    # Report
    print("Results:")
    print(f"  Unchanged (already valid):     {stats['unchanged']}")
    print(f"  Fixed (timezone offset):       {stats['timezone_offset']}")
    print(f"  Fixed (two-digit year):        {stats['two_digit_year']}")
    print(f"  Fixed (zero-padded century):   {stats['zero_padded_century']}")
    print(f"  Nulled (future dates):         {stats['nulled']}")
    print(f"  Genuine duplicates:            {len(duplicates)}")
    print(f"  Total updates:                 {len(updates)}")

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
        print(f"\nSample updates (showing up to 10):")
        for row_id, new_val, old_val, filing in updates[:10]:
            print(f"  ID {row_id}: {old_val} → {new_val} (filing: {filing})")

    if duplicates:
        print(f"\nSample duplicates (showing up to 10):")
        for row_id, old_val, norm_val, existing_id in duplicates[:10]:
            print(f"  ID {row_id}: {old_val} → would delete (duplicate of ID {existing_id} with {norm_val})")

    # Apply updates
    if args.write and updates:
        print(f"\nApplying {len(updates)} updates to database...")
        applied = 0
        failed = 0
        for row_id, new_val, old_val, filing in updates:
            try:
                cur.execute(
                    "UPDATE insider_transactions SET transaction_date = ? WHERE id = ?",
                    (new_val, row_id)
                )
                conn.commit()  # Commit each update immediately
                applied += 1
            except sqlite3.IntegrityError as e:
                # Should not happen since we pre-checked, but log it
                print(f"  WARNING: Failed to update ID {row_id} ({old_val} → {new_val}): {e}")
                failed += 1
                conn.rollback()
        print(f"Done! Applied {applied} updates, {failed} failed.")
    elif not args.write and updates:
        print(f"\nDRY RUN: Would update {len(updates)} rows.")
        print("Run with --write to apply changes.")

    # Delete duplicates
    if args.delete_duplicates and duplicates:
        print(f"\nDeleting {len(duplicates)} duplicate rows...")
        deleted = 0
        for row_id, old_val, norm_val, existing_id in duplicates:
            # Double-check the correctly-dated twin still exists
            cur.execute("SELECT id FROM insider_transactions WHERE id = ?", (existing_id,))
            if cur.fetchone():
                cur.execute("DELETE FROM insider_transactions WHERE id = ?", (row_id,))
                conn.commit()
                print(f"  Deleted ID {row_id} ({old_val}, duplicate of ID {existing_id} with {norm_val})")
                deleted += 1
            else:
                print(f"  WARNING: Skipped ID {row_id} - twin ID {existing_id} no longer exists")
        print(f"Done! Deleted {deleted} duplicates.")
    elif duplicates and not args.delete_duplicates:
        print(f"\nDRY RUN: Would delete {len(duplicates)} duplicate rows.")
        print("Run with --delete-duplicates to remove them.")

    # Final verification
    if args.write or args.delete_duplicates:
        print("\nFinal verification:")

        # Check MAX(transaction_date)
        cur.execute("SELECT MAX(transaction_date) FROM insider_transactions")
        max_date = cur.fetchone()[0]
        print(f"  MAX(transaction_date) = {max_date}")

        # Validate it's a proper ISO date
        max_is_valid = False
        if max_date:
            try:
                from datetime import datetime
                datetime.strptime(max_date, "%Y-%m-%d")
                max_is_valid = len(max_date) == 10
            except ValueError:
                pass

        if not max_is_valid:
            print(f"  ❌ FAILED: MAX(transaction_date) is not a valid ISO date")
        else:
            print(f"  ✓ MAX(transaction_date) is valid")

        # Check for remaining malformed rows
        cur.execute("""
            SELECT COUNT(*) FROM insider_transactions
            WHERE transaction_date IS NOT NULL
              AND transaction_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
        """)
        malformed_count = cur.fetchone()[0]
        print(f"  Remaining malformed rows: {malformed_count}")

        if malformed_count > 0:
            print(f"  ❌ FAILED: {malformed_count} malformed rows remain")
        else:
            print(f"  ✓ No malformed rows remain")

        # Check plausibility floor: no dates before 1990
        # (Form 4 electronic filing didn't exist before then, dataset is overwhelmingly 2020+)
        cur.execute("""
            SELECT COUNT(*) FROM insider_transactions
            WHERE transaction_date IS NOT NULL
              AND transaction_date < '1990-01-01'
        """)
        implausible_count = cur.fetchone()[0]
        print(f"  Dates before 1990 (implausible): {implausible_count}")

        if implausible_count > 0:
            # Show sample
            cur.execute("""
                SELECT id, transaction_date, filing_date
                FROM insider_transactions
                WHERE transaction_date IS NOT NULL
                  AND transaction_date < '1990-01-01'
                LIMIT 5
            """)
            sample = cur.fetchall()
            print(f"    Sample: {sample}")
            print(f"  ❌ FAILED: {implausible_count} implausible dates remain")
        else:
            print(f"  ✓ All dates are plausible (>= 1990)")

        # Overall status
        if max_is_valid and malformed_count == 0 and implausible_count == 0:
            print("\n✓ SUCCESS: All goals met")
        else:
            print("\n❌ INCOMPLETE: Some issues remain")

    conn.close()


if __name__ == "__main__":
    main()
