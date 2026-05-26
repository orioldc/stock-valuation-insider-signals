#!/usr/bin/env python3
"""Run expanded pipeline: ingest ~500 tickers, score, report clusters."""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import load_universe, run_full_ingestion, get_db
from signals.composite_scorer import score_universe
from signals.insider_clusters import detect_clusters

def main():
    tickers = load_universe()
    # Deduplicate
    seen = set()
    unique = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    tickers = unique
    
    # Phase 1: Ingest
    print(f"\n{'='*60}")
    print(f"PHASE 1: INGESTING DATA FOR {len(tickers)} TICKERS")
    print(f"{'='*60}\n")
    run_full_ingestion(tickers, skip_existing=True)
    
    # Phase 2: Score
    print(f"\n{'='*60}")
    print(f"PHASE 2: SCORING UNIVERSE")
    print(f"{'='*60}\n")
    df = score_universe(tickers)
    
    # Phase 3: Results
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM companies")
    total_companies = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    total_txns = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM insider_transactions WHERE transaction_type = 'P'")
    total_buys = cur.fetchone()[0]
    conn.close()
    
    clusters_df = df[df["cluster_detected"] == True]
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total companies ingested: {total_companies}")
    print(f"Total insider transactions: {total_txns}")
    print(f"Total insider BUYS: {total_buys}")
    print(f"Insider buying clusters detected: {len(clusters_df)}")
    
    print(f"\n{'='*60}")
    print(f"TOP 30 RANKED COMPANIES BY COMPOSITE SCORE")
    print(f"{'='*60}\n")
    
    top30 = df.head(30)
    for i, row in top30.iterrows():
        cluster_flag = "🔥" if row["cluster_detected"] else "  "
        buyback_flag = "📉" if row["share_trend"] == "buyback" else "  "
        print(f"{i+1:>3}. {row['ticker']:<8} | Composite: {row['composite']:.4f} | "
              f"Cluster: {row['cluster_norm']:.3f} {cluster_flag} | "
              f"Buyback: {row['share_norm']:.3f} {buyback_flag} | "
              f"ΔQoQ: {row['share_delta_qoq']:>8.2f}% | Δ4Q: {row['share_delta_4q']:>8.2f}%")
    
    # Phase 4: All clusters with details
    print(f"\n{'='*60}")
    print(f"ALL INSIDER BUYING CLUSTERS ({len(clusters_df)} companies)")
    print(f"{'='*60}\n")
    
    for _, row in clusters_df.iterrows():
        ticker = row["ticker"]
        cluster = detect_clusters(ticker)
        print(f"\n--- {ticker} (cluster score: {cluster['score']:.2f}) ---")
        for trade in cluster["details"]:
            val_str = f"${trade['value']:,.0f}" if trade.get('value') else "N/A"
            print(f"  {trade['date']} | {trade['name']:<30} | {trade.get('relationship',''):<20} | "
                  f"{trade['shares']:>10,.0f} shares @ ${trade['price']:.2f} = {val_str}")
    
    # Save CSV
    os.makedirs("output", exist_ok=True)
    df.to_csv("output/latest_signals.csv", index=False)
    print(f"\nResults saved to output/latest_signals.csv")


if __name__ == "__main__":
    main()
