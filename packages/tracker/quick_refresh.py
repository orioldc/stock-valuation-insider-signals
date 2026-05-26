#!/usr/bin/env python3
"""
Quick Refresh — Skip SEC fetching, just re-run scoring on existing data.
For when SEC rate limiting makes full refresh impractical.
"""

import sys
import os
import time
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import load_universe, load_russell2000_additions, get_db
from signals.composite_scorer import score_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def run_quick_refresh():
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Get tickers that actually have data
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT c.ticker FROM companies c 
        JOIN insider_transactions t ON c.id = t.company_id
    """)
    tickers = [r[0] for r in cur.fetchall()]
    
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    total_txns = cur.fetchone()[0]
    conn.close()

    logger.info(f"Scoring {len(tickers)} tickers with {total_txns} total transactions")

    # Load previous signals for comparison
    import pandas as pd
    old_signals = None
    old_path = os.path.join(OUTPUT_DIR, "latest_signals.csv")
    if os.path.exists(old_path):
        try:
            old_signals = pd.read_csv(old_path)
        except:
            pass

    # Run scoring
    df = score_universe(tickers)
    
    # Save
    df.to_csv(os.path.join(OUTPUT_DIR, "latest_signals.csv"), index=False)
    
    elapsed = time.time() - start_time
    
    # Report
    clusters = df[df["cluster_detected"] == True] if "cluster_detected" in df.columns else pd.DataFrame()
    top20 = df.head(20)
    
    print(f"\n{'='*60}")
    print(f"INSIDER SIGNAL TRACKER — QUICK REFRESH REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"\nSUMMARY:")
    print(f"  Tickers scored:  {len(tickers)}")
    print(f"  Total transactions: {total_txns}")
    print(f"  Runtime: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    
    if not clusters.empty:
        print(f"\n{'='*60}")
        print(f"CLUSTERS DETECTED: {len(clusters)}")
        print(f"{'='*60}")
        for _, row in clusters.iterrows():
            print(f"  {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
                  f"Cluster Score: {row.get('cluster_score_raw', 0):.1f} | "
                  f"Share Δ4Q: {row.get('share_delta_4q', 0):.2f}%")
    
    if old_signals is not None and not old_signals.empty and not clusters.empty:
        old_cluster_tickers = set(old_signals[old_signals.get("cluster_detected", False) == True]["ticker"]) if "cluster_detected" in old_signals.columns else set()
        new_cluster_tickers = set(clusters["ticker"])
        newly_detected = new_cluster_tickers - old_cluster_tickers
        lost = old_cluster_tickers - new_cluster_tickers
        print(f"\nCLUSTER CHANGES vs. PREVIOUS:")
        print(f"  Newly detected:  {', '.join(sorted(newly_detected)) or '(none)'}")
        print(f"  No longer active: {', '.join(sorted(lost)) or '(none)'}")
    
    print(f"\n{'='*60}")
    print(f"TOP 20 SIGNALS BY CONVICTION")
    print(f"{'='*60}")
    for i, (_, row) in enumerate(top20.iterrows()):
        cluster_flag = "🔥" if row.get("cluster_detected", False) else "  "
        buyback_flag = "📉" if row.get("share_trend") == "buyback" else "  "
        print(f"  {i+1:>3}. {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
              f"Cluster: {row.get('cluster_norm', 0):.3f} {cluster_flag} | "
              f"Buyback: {row.get('share_norm', 0):.3f} {buyback_flag}")
    
    print(f"\nSaved to {OUTPUT_DIR}/latest_signals.csv")
    return df


if __name__ == "__main__":
    run_quick_refresh()
