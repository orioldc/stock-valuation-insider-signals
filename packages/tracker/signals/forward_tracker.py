"""Forward Tracker — track active signal performance over time."""

import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS active_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    entry_price REAL,
    ret_1m REAL,
    ret_3m REAL,
    ret_6m REAL,
    ret_12m REAL,
    spy_ret_1m REAL,
    spy_ret_3m REAL,
    spy_ret_6m REAL,
    spy_ret_12m REAL,
    completed INTEGER DEFAULT 0,
    UNIQUE(ticker, signal_date)
)
"""


def _get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(CREATE_TABLE)
    conn.commit()
    return conn


def record_signal(ticker, signal_date, entry_price=None):
    """Record a new active signal. Fetches entry_price from yfinance if not provided."""
    if entry_price is None:
        try:
            hist = yf.Ticker(ticker).history(start=signal_date, period="5d")
            if not hist.empty:
                entry_price = float(hist["Close"].iloc[0])
        except Exception:
            pass
    
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO active_signals (ticker, signal_date, entry_price) VALUES (?, ?, ?)",
            (ticker, str(signal_date)[:10], entry_price)
        )
        conn.commit()
    finally:
        conn.close()


def _get_return(ticker, start_date, months):
    """Get return from start_date + months."""
    target_date = start_date + timedelta(days=months * 30)
    if target_date > datetime.now():
        return None
    try:
        hist = yf.Ticker(ticker).history(start=target_date - timedelta(days=5), end=target_date + timedelta(days=5))
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def update_forward_returns():
    """Update forward returns for all active signals."""
    conn = _get_conn()
    rows = pd.read_sql("SELECT * FROM active_signals WHERE completed = 0", conn)
    
    if rows.empty:
        conn.close()
        return
    
    horizons = [(1, "ret_1m", "spy_ret_1m"), (3, "ret_3m", "spy_ret_3m"),
                (6, "ret_6m", "spy_ret_6m"), (12, "ret_12m", "spy_ret_12m")]
    
    for _, row in rows.iterrows():
        if row["entry_price"] is None or pd.isna(row["entry_price"]):
            continue
        
        sig_date = datetime.strptime(str(row["signal_date"])[:10], "%Y-%m-%d")
        entry = row["entry_price"]
        updates = {}
        all_filled = True
        
        for months, col, spy_col in horizons:
            if row[col] is not None and not pd.isna(row[col]):
                continue
            
            price = _get_return(row["ticker"], sig_date, months)
            spy_price_start = _get_return("SPY", sig_date, 0) if row.get(spy_col) is None or pd.isna(row.get(spy_col)) else None
            spy_price_end = _get_return("SPY", sig_date, months)
            
            if price is not None:
                ret = (price - entry) / entry * 100
                updates[col] = round(ret, 2)
                if spy_price_start and spy_price_end:
                    spy_ret = (spy_price_end - spy_price_start) / spy_price_start * 100
                    updates[spy_col] = round(spy_ret, 2)
            else:
                all_filled = False
        
        if all_filled and all(row[h[1]] is not None and not pd.isna(row[h[1]]) for h in horizons):
            updates["completed"] = 1
        
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [row["id"]]
            conn.execute(f"UPDATE active_signals SET {set_clause} WHERE id = ?", vals)
    
    conn.commit()
    conn.close()


def get_active_signals():
    """Return all active signals as a DataFrame."""
    conn = _get_conn()
    df = pd.read_sql("SELECT * FROM active_signals ORDER BY signal_date DESC", conn)
    conn.close()
    return df
