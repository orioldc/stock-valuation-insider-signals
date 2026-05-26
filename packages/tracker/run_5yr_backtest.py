#!/usr/bin/env python3
"""
Full 5-year re-ingestion + historical backtest.
Clears existing transactions and re-fetches everything with full history.
"""
import sys
import os
import sqlite3
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import load_universe, run_full_ingestion, get_db


def clear_transactions():
    """Clear existing insider transactions to re-ingest with full history."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    count = cur.fetchone()[0]
    print(f"Clearing {count} existing insider transactions...")
    cur.execute("DELETE FROM insider_transactions")
    conn.commit()
    conn.close()
    print("Done.")


def main():
    start = time.time()
    
    # Phase 1: Clear old data
    print("=" * 60)
    print("PHASE 1: CLEARING OLD TRANSACTIONS")
    print("=" * 60)
    clear_transactions()
    
    # Phase 2: Re-ingest with full 5-year history
    tickers = load_universe()
    # Deduplicate
    seen = set()
    tickers = [t for t in tickers if t not in seen and not seen.add(t)]
    print(f"\n{'=' * 60}")
    print(f"PHASE 2: INGESTING 5-YEAR HISTORY FOR {len(tickers)} TICKERS")
    print(f"{'=' * 60}")
    print(f"This will take a while due to SEC rate limiting...")
    run_full_ingestion(tickers, skip_existing=False)
    
    elapsed = time.time() - start
    print(f"\nIngestion took {elapsed/60:.1f} minutes")
    
    # Phase 3: Run backtest
    print(f"\n{'=' * 60}")
    print(f"PHASE 3: RUNNING HISTORICAL BACKTEST")
    print(f"{'=' * 60}")
    from backtest.historical_backtest import run_backtest
    run_backtest()
    
    total = time.time() - start
    print(f"\nTotal time: {total/60:.1f} minutes")


if __name__ == "__main__":
    main()
