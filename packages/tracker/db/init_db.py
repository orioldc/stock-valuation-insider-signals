"""Initialize the insider_signals.db SQLite database."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "insider_signals.db"

# One row per trade line of one SEC filing. accession_number + line_number is
# the identity: two same-size trades on the same day are two rows, and the
# same filing loaded twice is one. Amendments are resolved after loading
# (form4_rules.apply_amendments), not by this constraint.
INSIDER_TRANSACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS insider_transactions (
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
    price_original_corrupt REAL,
    accession_number TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    document_type TEXT,
    date_of_orig_sub TEXT,
    UNIQUE(accession_number, line_number)
);
CREATE INDEX IF NOT EXISTS idx_txn_company ON insider_transactions(company_id);
CREATE INDEX IF NOT EXISTS idx_txn_date ON insider_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_txn_owner ON insider_transactions(company_id, reporting_cik, transaction_date);

-- Every (issuer CIK, ticker) pair seen in filings, with the issuer's latest name
-- and the dates it used that ticker. Tickers get reused, so this is what
-- company_identity uses to decide which company a ticker belongs to today.
CREATE TABLE IF NOT EXISTS issuer_tickers (
    cik INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    first_filing TEXT,
    last_filing TEXT,
    PRIMARY KEY (cik, ticker)
);

-- Old CIKs of companies that re-registered, pointing at the company they became.
CREATE TABLE IF NOT EXISTS company_ciks (
    cik INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT UNIQUE NOT NULL,
    name TEXT,
    cik INTEGER,
    sector TEXT,
    industry TEXT,
    market_cap REAL,
    market_cap_asof TEXT
);
""" + INSIDER_TRANSACTIONS_DDL + """

CREATE TABLE IF NOT EXISTS shares_outstanding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    shares REAL NOT NULL,
    source TEXT,
    UNIQUE(company_id, date)
);

-- Year-on-year share count change reported in one 10-Q/10-K (both figures
-- from the same filing, so on the same basis). Rebuilt with shares_outstanding
-- by data_ingestion/share_counts.py; filed is when the market could know it.
CREATE TABLE IF NOT EXISTS share_count_changes (
    company_id INTEGER NOT NULL,
    accession_number TEXT NOT NULL,
    filed TEXT NOT NULL,
    period_end TEXT NOT NULL,
    basis TEXT NOT NULL,
    shares REAL NOT NULL,
    shares_year_ago REAL NOT NULL,
    repurchases INTEGER NOT NULL,  -- the filing reports buying back stock in the period
    shares_prev_quarter REAL,      -- previous quarter's average, when on the same basis
    PRIMARY KEY (company_id, accession_number)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    signal_date TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strength REAL,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
"""


def reset_insider_transactions(conn):
    """Drop insider_transactions (and the issuer tables derived alongside it)
    and recreate them empty with the current schema.

    The table is rebuilt from SEC on every release rather than patched, so
    no row, column or index from an older release can survive into a new one.
    """
    conn.execute("DROP TABLE IF EXISTS insider_transactions")
    conn.execute("DROP TABLE IF EXISTS issuer_tickers")
    conn.execute("DROP TABLE IF EXISTS company_ciks")
    conn.executescript(INSIDER_TRANSACTIONS_DDL)
    conn.commit()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
