#!/usr/bin/env python3
"""Run full ingestion + scoring pipeline."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import load_universe, run_full_ingestion
from signals.composite_scorer import score_universe

def main():
    tickers = load_universe()
    
    # Step 1: Full ingestion
    print(f"\n{'='*60}")
    print(f"PHASE 1: INGESTING DATA FOR {len(tickers)} TICKERS")
    print(f"{'='*60}\n")
    run_full_ingestion(tickers)
    
    # Step 2: Score universe
    print(f"\n{'='*60}")
    print(f"PHASE 2: SCORING UNIVERSE")
    print(f"{'='*60}\n")
    df = score_universe(tickers)
    
    # Step 3: Print top 20
    print(f"\n{'='*60}")
    print(f"TOP 20 RANKED COMPANIES")
    print(f"{'='*60}\n")
    
    top20 = df.head(20)
    for i, row in top20.iterrows():
        cluster_flag = "🔥" if row["cluster_detected"] else "  "
        buyback_flag = "📉" if row["share_trend"] == "buyback" else "  "
        print(f"{i+1:>3}. {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
              f"Cluster: {row['cluster_norm']:.3f} {cluster_flag} | "
              f"Buyback: {row['share_norm']:.3f} {buyback_flag} | "
              f"ΔQoQ: {row['share_delta_qoq']:>7.2f}% | Δ4Q: {row['share_delta_4q']:>7.2f}%")
    
    # Step 4: Save CSV
    os.makedirs("output", exist_ok=True)
    df.to_csv("output/latest_signals.csv", index=False)
    print(f"\nResults saved to output/latest_signals.csv")

if __name__ == "__main__":
    main()
