
import os
import sys
import time
import logging
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import load_universe
from signals.composite_scorer import score_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "insider_signals.db")

def get_new_transactions():
    """Count transactions inserted in last 24h."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Check if 'created_at' column exists first (just in case schema differs slightly)
    cur.execute("PRAGMA table_info(insider_transactions)")
    cols = [c[1] for c in cur.fetchall()]
    
    start_time = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    
    if 'created_at' in cols:
        cur.execute("SELECT COUNT(*) FROM insider_transactions WHERE created_at >= ?", (start_time,))
        total = cur.fetchone()[0]
        
        cur.execute("""
            SELECT c.ticker, COUNT(*) 
            FROM insider_transactions t
            JOIN companies c ON t.company_id = c.id
            WHERE t.created_at >= ?
            GROUP BY c.ticker
            ORDER BY COUNT(*) DESC
        """, (start_time,))
        tickers_with_new = cur.fetchall()
    else:
        # Fallback to filing_date if created_at missing
        cur.execute("SELECT COUNT(*) FROM insider_transactions WHERE filing_date >= ?", (datetime.now().strftime("%Y-%m-%d"),))
        total = cur.fetchone()[0]
        tickers_with_new = []
        
    conn.close()
    return total, tickers_with_new

def _load_old_signals():
    try:
        path = os.path.join(OUTPUT_DIR, "latest_signals.csv")
        if os.path.exists(path):
            return pd.read_csv(path)
    except Exception:
        pass
    return None

def _generate_report(df, old_signals, tickers, new_txn_total, tickers_with_new, errors):
    """Generate the weekly summary report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clusters = df[df["cluster_detected"] == True]
    top20 = df.head(20)

    lines = [
        f"{'=' * 60}",
        f"INSIDER SIGNAL TRACKER — WEEKLY REFRESH REPORT",
        f"Generated: {now}",
        f"{'=' * 60}",
        f"",
        f"SUMMARY:",
        f"  Universe size:              {len(tickers)}",
        f"  New transactions ingested:  {new_txn_total} (approx. last 24h)",
        f"  Tickers with new data:      {len(tickers_with_new)}",
        f"  Errors:                     {len(errors)}",
        f"",
        f"{'=' * 60}",
        f"CLUSTERS DETECTED: {len(clusters)}",
        f"{'=' * 60}",
    ]

    if not clusters.empty:
        for _, row in clusters.iterrows():
            lines.append(f"  {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
                        f"Cluster Score: {row['cluster_score_raw']:.1f} | "
                        f"Share Δ4Q: {row.get('share_delta_4q', 0):.2f}%")
    else:
        lines.append("  (none)")

    # New/changed clusters
    if old_signals is not None and not old_signals.empty:
        old_cluster_tickers = set(old_signals[old_signals["cluster_detected"] == True]["ticker"])
        new_cluster_tickers = set(clusters["ticker"]) if not clusters.empty else set()

        newly_detected = new_cluster_tickers - old_cluster_tickers
        lost_clusters = old_cluster_tickers - new_cluster_tickers

        lines.extend([
            f"",
            f"CLUSTER CHANGES vs. PREVIOUS:",
            f"  Newly detected:  {', '.join(sorted(newly_detected)) or '(none)'}",
            f"  No longer active: {', '.join(sorted(lost_clusters)) or '(none)'}",
        ])

    lines.extend([
        f"",
        f"{'=' * 60}",
        f"TOP 20 SIGNALS",
        f"{'=' * 60}",
    ])

    for i, row in top20.iterrows():
        cluster_flag = "🔥" if row["cluster_detected"] else "  "
        buyback_flag = "📉" if row.get("share_trend") == "buyback" else "  "
        lines.append(f"  {i+1:>3}. {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
                    f"Cluster: {row.get('cluster_norm', 0):.3f} {cluster_flag} | "
                    f"Buyback: {row.get('share_norm', 0):.3f} {buyback_flag}")

    if tickers_with_new:
        lines.extend([
            f"",
            f"{'=' * 60}",
            f"TICKERS WITH NEW DATA (top 30)",
            f"{'=' * 60}",
        ])
        for ticker, count in sorted(tickers_with_new, key=lambda x: -x[1])[:30]:
            lines.append(f"  {ticker:<6}: {count} new transactions")

    lines.append("")
    return "\n".join(lines)

def main():
    logger.info("Starting finish_refresh.py...")
    
    # 1. Get transaction stats
    new_txn_total, tickers_with_new = get_new_transactions()
    logger.info(f"Found {new_txn_total} new transactions in last 24h")
    
    # 2. Score universe
    tickers = load_universe()
    logger.info(f"Scoring {len(tickers)} tickers...")
    df = score_universe(tickers)
    
    # 3. Save CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_path = os.path.join(OUTPUT_DIR, "latest_signals.csv")
    
    # Load old signals before overwriting
    old_signals = _load_old_signals()
    
    df.to_csv(csv_path, index=False)
    logger.info(f"Saved latest_signals.csv to {csv_path}")
    
    # 4. Generate report
    report = _generate_report(df, old_signals, tickers, new_txn_total, tickers_with_new, [])
    
    report_path = os.path.join(OUTPUT_DIR, "weekly_report.txt")
    with open(report_path, "w") as f:
        f.write(report)
    logger.info(f"Report saved to {report_path}")
    
    # Print report to stdout so I can read it
    print(report)

if __name__ == "__main__":
    main()
