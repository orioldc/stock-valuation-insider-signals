"""Initialize the insider_signals.db SQLite database."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "insider_signals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT UNIQUE NOT NULL,
    name TEXT,
    cik INTEGER,
    sector TEXT,
    industry TEXT
);

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
    UNIQUE(company_id, transaction_date, reporting_cik, shares_transacted)
);

CREATE TABLE IF NOT EXISTS shares_outstanding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    shares REAL NOT NULL,
    source TEXT,
    UNIQUE(company_id, date)
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

CREATE INDEX IF NOT EXISTS idx_txn_company ON insider_transactions(company_id);CREATE INDEX IF NOT EXISTS idx_txn_date ON insider_transactions(transaction_date);CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
"""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
