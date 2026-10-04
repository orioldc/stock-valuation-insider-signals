"""
Daily closing prices from yfinance, stored in the prices table.

The backtests that used to live here read share counts the old way and were
no longer run; backtest/historical_backtest.py is the one the pipeline runs.
"""

import sqlite3
import os
import time
import warnings

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))

# ──────────────────────────────────────────────
# Price data management
# ──────────────────────────────────────────────

def _ensure_prices_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()


def fetch_prices(tickers, period="5y"):
    """Download/refresh daily prices for tickers + SPY into SQLite (incremental top-up).

    New tickers (no prior rows) fetch the full period; existing tickers fetch only
    from their last stored date forward. INSERT OR IGNORE dedupes overlap.
    """
    conn = sqlite3.connect(DB_PATH)
    _ensure_prices_table(conn)

    all_tickers = sorted(set(tickers) | {"SPY"})

    last_dates = {}
    for t, mx in conn.execute("SELECT ticker, MAX(date) FROM prices GROUP BY ticker"):
        last_dates[t] = mx

    new_tickers = [t for t in all_tickers if last_dates.get(t) is None]
    existing_tickers = [t for t in all_tickers if last_dates.get(t) is not None]
    print(f"  Refreshing prices: {len(existing_tickers)} existing (incremental), "
          f"{len(new_tickers)} new (full {period})...")

    batch_size = 20
    total_rows = 0

    def _store(batch, data):
        nonlocal total_rows
        rows = []
        for t in batch:
            try:
                closes = data["Close"].dropna() if len(batch) == 1 else data[t]["Close"].dropna()
                for dt, price in closes.items():
                    if pd.notna(price) and price > 0:
                        rows.append((t, dt.strftime("%Y-%m-%d"), float(price)))
            except Exception:
                print(f"    Warning: no data for {t}")
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO prices (ticker, date, close) VALUES (?, ?, ?)", rows
            )
            conn.commit()
            total_rows += len(rows)

    # New tickers: full history
    for i in range(0, len(new_tickers), batch_size):
        batch = new_tickers[i:i + batch_size]
        try:
            data = yf.download(" ".join(batch), period=period, interval="1d",
                               group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"    Error fetching new batch: {e}")
            continue
        _store(batch, data)
        time.sleep(0.5)

    # Existing tickers: incremental from the oldest last-stored date in each batch
    for i in range(0, len(existing_tickers), batch_size):
        batch = existing_tickers[i:i + batch_size]
        start = min(last_dates[t] for t in batch)
        try:
            data = yf.download(" ".join(batch), start=start, interval="1d",
                               group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"    Error fetching incremental batch: {e}")
            continue
        _store(batch, data)
        time.sleep(0.5)

    conn.close()
    print(f"  Price refresh complete: {total_rows} rows added.")
