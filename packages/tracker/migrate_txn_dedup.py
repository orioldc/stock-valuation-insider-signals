#!/usr/bin/env python3
"""
Idempotent migration: fix the insider_transactions UNIQUE constraint to use
transaction_type without price, preventing NULL-driven duplicate rows.

Safe to run multiple times: a second run detects the constraint is already
present and exits without touching the DB.
"""

import sys
import os
import re
import sqlite3
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "insider_signals.db")

CREATE_NEW_TABLE = """
CREATE TABLE insider_transactions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    filing_date TEXT,
    transaction_date TEXT,
    reporting_name TEXT,
    reporting_cik TEXT,
    transaction_type TEXT,
    shares_transacted REAL,
    price REAL,
    shares_owned_after REAL,
    source TEXT DEFAULT 'FMP',
    raw_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(company_id, transaction_date, reporting_cik, transaction_type, shares_transacted)
)
"""


def _get_table_sql(conn):
    """Return the CREATE TABLE SQL for insider_transactions from sqlite_master."""
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='insider_transactions'"
    )
    row = cur.fetchone()
    return row[0] if row else None


def _get_column_names(conn):
    """Return ordered list of column names for insider_transactions via PRAGMA."""
    cur = conn.execute("PRAGMA table_info(insider_transactions)")
    return [row[1] for row in cur.fetchall()]


def run_migration():
    if not os.path.exists(DB_PATH):
        logger.error(f"DB not found at {DB_PATH} - nothing to migrate")
        sys.exit(1)

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        table_sql = _get_table_sql(conn)
        if table_sql is None:
            logger.error("insider_transactions table does not exist")
            sys.exit(1)

        m = re.search(r'UNIQUE\s*\(', table_sql, re.IGNORECASE)
        if m is None:
            logger.warning("No UNIQUE clause found in existing schema - proceeding with migration")
        else:
            # balanced-paren scan starting from m.start()
            unique_start = m.start()
            depth = 0
            unique_end = -1
            for i, ch in enumerate(table_sql[unique_start:], start=unique_start):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        unique_end = i
                        break
            if unique_end == -1:
                logger.warning("Could not parse UNIQUE clause from sqlite_master SQL - proceeding with migration as precaution")
            else:
                unique_clause = table_sql[unique_start:unique_end + 1]
                if "transaction_type" in unique_clause and "price" not in unique_clause:
                    logger.info("already migrated, no-op")
                    return

        all_cols = _get_column_names(conn)
        # Exclude the primary key - let AUTOINCREMENT assign fresh ids in the rebuilt table.
        cols = [c for c in all_cols if c != "id"]
        cols_csv = ", ".join(cols)

        logger.info(f"Columns to copy: {cols_csv}")

        cur = conn.execute("SELECT COUNT(*) FROM insider_transactions")
        before_count = cur.fetchone()[0]
        logger.info(f"Row count before migration: {before_count}")

        # Capture explicit (user-defined) index definitions before the table is
        # dropped.  sql IS NOT NULL skips auto-indexes created by UNIQUE/PRIMARY
        # KEY constraints; the rebuilt table's own constraint recreates those.
        idx_rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='insider_transactions' AND sql IS NOT NULL"
        ).fetchall()
        index_ddls = [r[0] for r in idx_rows]
        logger.info(f"Captured {len(index_ddls)} explicit index definitions to recreate")

        with conn:
            conn.execute(CREATE_NEW_TABLE)
            conn.execute(
                f"INSERT OR IGNORE INTO insider_transactions_new ({cols_csv}) "
                f"SELECT {cols_csv} FROM insider_transactions"
            )
            conn.execute("DROP TABLE insider_transactions")
            conn.execute(
                "ALTER TABLE insider_transactions_new RENAME TO insider_transactions"
            )
            for ddl in index_ddls:
                conn.execute(ddl)

        cur = conn.execute("SELECT COUNT(*) FROM insider_transactions")
        after_count = cur.fetchone()[0]
        logger.info(f"Row count after migration: {after_count}")

        if before_count != after_count:
            logger.warning(
                f"UNEXPECTED: row count changed {before_count} -> {after_count} during migration; "
                f"widening a UNIQUE constraint should never drop rows - investigate before publishing"
            )
        else:
            logger.info("Row counts match - migration complete, no duplicates dropped")

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    run_migration()
