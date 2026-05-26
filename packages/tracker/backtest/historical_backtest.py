#!/usr/bin/env python3
"""
Historical backtest: detect insider buying clusters over 5 years and measure forward returns.
"""
import sqlite3
import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import yfinance as yf
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")


def get_db():
    return sqlite3.connect(DB_PATH)


def load_all_purchases():
    """Load all insider purchase transactions from DB."""
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT 
            it.id, c.ticker, c.sector, it.filing_date, it.transaction_date,
            it.reporting_name, it.reporting_cik, it.transaction_type,
            it.shares_transacted, it.price, it.shares_owned_after, it.raw_json
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type IN ('P', 'A')
          AND it.shares_transacted > 0
          AND it.price > 0
    """, conn)
    conn.close()
    df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
    df['filing_date'] = pd.to_datetime(df['filing_date'], errors='coerce')
    df = df.dropna(subset=['transaction_date'])
    df['total_value'] = df['shares_transacted'] * df['price']
    logger.info(f"Loaded {len(df)} purchase transactions across {df['ticker'].nunique()} tickers")
    return df


def detect_clusters(purchases_df, window_days=30, min_insiders=2):
    """
    Detect insider buying clusters: 2+ distinct insiders buying within window_days.
    Returns DataFrame of clusters with signal_date = last purchase date in cluster.
    """
    clusters = []
    
    for ticker, group in purchases_df.groupby('ticker'):
        group = group.sort_values('transaction_date')
        if len(group) < min_insiders:
            continue
        
        dates = group['transaction_date'].values
        
        # Sliding window approach
        i = 0
        used_dates = set()
        while i < len(group):
            window_end = group.iloc[i]['transaction_date'] + timedelta(days=window_days)
            window_mask = (group['transaction_date'] >= group.iloc[i]['transaction_date']) & \
                          (group['transaction_date'] <= window_end)
            window = group[window_mask]
            
            distinct_insiders = window['reporting_cik'].nunique()
            if distinct_insiders >= min_insiders:
                signal_date = window['transaction_date'].max()
                signal_date_str = str(signal_date.date()) if hasattr(signal_date, 'date') else str(signal_date)[:10]
                
                if signal_date_str not in used_dates:
                    used_dates.add(signal_date_str)
                    
                    total_value = window['total_value'].sum()
                    insider_names = [n for n in window['reporting_name'].unique().tolist() if n]
                    
                    # Check for officer/director from raw_json
                    has_officer = False
                    has_director = False
                    has_ceo = False
                    for _, row in window.iterrows():
                        try:
                            import json
                            raw = json.loads(row['raw_json']) if row['raw_json'] else {}
                            rel = raw.get('relationship', '') or ''
                            title = raw.get('title', '') or ''
                            combined = rel + ' ' + title
                            if 'Officer' in combined or title:
                                has_officer = True
                            if 'Director' in combined:
                                has_director = True
                            if any(t in combined.upper() for t in ['CEO', 'CHIEF EXECUTIVE']):
                                has_ceo = True
                        except:
                            pass
                    
                    clusters.append({
                        'ticker': ticker,
                        'sector': group.iloc[0]['sector'],
                        'signal_date': signal_date_str,
                        'num_insiders': distinct_insiders,
                        'num_transactions': len(window),
                        'total_value': total_value,
                        'insider_names': '; '.join(insider_names[:5]),
                        'has_officer': has_officer,
                        'has_director': has_director,
                        'has_ceo': has_ceo,
                    })
            
            # Move past this window
            i = window_mask.sum() if window_mask.sum() > i + 1 else i + 1
    
    df = pd.DataFrame(clusters)
    logger.info(f"Detected {len(df)} insider buying clusters")
    return df


def fetch_prices_for_returns(tickers, start_date="2019-12-01", end_date="2026-02-17"):
    """Fetch daily close prices for all tickers + SPY using yfinance."""
    all_tickers = list(set(tickers) | {"SPY"})
    logger.info(f"Fetching price data for {len(all_tickers)} tickers...")
    
    # Batch download
    prices = {}
    batch_size = 50
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i:i+batch_size]
        try:
            data = yf.download(batch, start=start_date, end=end_date, 
                             progress=False, auto_adjust=True, threads=True)
            if 'Close' in data.columns or (isinstance(data.columns, pd.MultiIndex) and 'Close' in data.columns.get_level_values(0)):
                if isinstance(data.columns, pd.MultiIndex):
                    close = data['Close']
                else:
                    close = data[['Close']]
                    close.columns = batch
                for col in close.columns:
                    s = close[col].dropna()
                    if len(s) > 0:
                        prices[col] = s
        except Exception as e:
            logger.warning(f"Failed to fetch batch {i}: {e}")
    
    logger.info(f"Got price data for {len(prices)} tickers")
    return prices


def compute_forward_returns(clusters_df, prices):
    """Compute forward returns for each cluster signal."""
    periods = {
        'ret_1m': 21,
        'ret_3m': 63,
        'ret_6m': 126,
        'ret_12m': 252,
    }
    
    spy_prices = prices.get('SPY')
    results = []
    
    for _, cluster in clusters_df.iterrows():
        ticker = cluster['ticker']
        signal_date = pd.Timestamp(cluster['signal_date'])
        
        ticker_prices = prices.get(ticker)
        if ticker_prices is None or spy_prices is None:
            continue
        
        # Find the next trading day on or after signal_date
        valid_dates = ticker_prices.index[ticker_prices.index >= signal_date]
        if len(valid_dates) == 0:
            continue
        entry_date = valid_dates[0]
        entry_price = ticker_prices[entry_date]
        
        # SPY entry
        spy_valid = spy_prices.index[spy_prices.index >= signal_date]
        if len(spy_valid) == 0:
            continue
        spy_entry = spy_prices[spy_valid[0]]
        
        row = cluster.to_dict()
        row['entry_date'] = str(entry_date.date())
        row['entry_price'] = entry_price
        
        for period_name, days in periods.items():
            target_date = entry_date + timedelta(days=int(days * 1.5))  # calendar days
            future = ticker_prices.index[(ticker_prices.index > entry_date)]
            spy_future = spy_prices.index[(spy_prices.index > entry_date)]
            
            if len(future) >= days and len(spy_future) >= days:
                exit_date = future[days - 1]
                exit_price = ticker_prices[exit_date]
                spy_exit = spy_prices[spy_future[days - 1]]
                
                stock_ret = (exit_price / entry_price - 1) * 100
                spy_ret = (spy_exit / spy_entry - 1) * 100
                excess = stock_ret - spy_ret
                
                row[period_name] = round(stock_ret, 2)
                row[f'spy_{period_name}'] = round(spy_ret, 2)
                row[f'excess_{period_name}'] = round(excess, 2)
            else:
                row[period_name] = None
                row[f'spy_{period_name}'] = None
                row[f'excess_{period_name}'] = None
        
        results.append(row)
    
    df = pd.DataFrame(results)
    logger.info(f"Computed forward returns for {len(df)} clusters")
    return df


def generate_summary(results_df):
    """Generate summary statistics."""
    lines = []
    lines.append("=" * 70)
    lines.append("INSIDER CLUSTER BACKTEST — 5-YEAR HISTORICAL ANALYSIS")
    lines.append("=" * 70)
    lines.append(f"\nTotal clusters detected: {len(results_df)}")
    lines.append(f"Unique tickers: {results_df['ticker'].nunique()}")
    lines.append(f"Date range: {results_df['signal_date'].min()} to {results_df['signal_date'].max()}")
    
    for period in ['ret_1m', 'ret_3m', 'ret_6m', 'ret_12m']:
        period_label = period.replace('ret_', '').upper()
        col = period
        excess_col = f'excess_{period}'
        
        valid = results_df.dropna(subset=[col, excess_col])
        if len(valid) == 0:
            continue
        
        lines.append(f"\n{'─' * 50}")
        lines.append(f"FORWARD {period_label} RETURNS (n={len(valid)})")
        lines.append(f"{'─' * 50}")
        lines.append(f"  Avg stock return:    {valid[col].mean():>7.2f}%")
        lines.append(f"  Median stock return: {valid[col].median():>7.2f}%")
        lines.append(f"  Avg SPY return:      {valid[f'spy_{col}'].mean():>7.2f}%")
        lines.append(f"  Avg excess return:   {valid[excess_col].mean():>7.2f}%")
        lines.append(f"  Median excess:       {valid[excess_col].median():>7.2f}%")
        lines.append(f"  Hit rate (>0%):      {(valid[col] > 0).mean()*100:>7.1f}%")
        lines.append(f"  Hit rate (>SPY):     {(valid[excess_col] > 0).mean()*100:>7.1f}%")
    
    # By cluster size
    lines.append(f"\n{'=' * 50}")
    lines.append("BY CLUSTER SIZE (3M EXCESS RETURN)")
    lines.append(f"{'=' * 50}")
    valid = results_df.dropna(subset=['excess_ret_3m'])
    if len(valid) > 0:
        for size in sorted(valid['num_insiders'].unique()):
            subset = valid[valid['num_insiders'] == size]
            lines.append(f"  {size} insiders: avg excess {subset['excess_ret_3m'].mean():>7.2f}% "
                        f"(n={len(subset)}, hit rate {(subset['excess_ret_3m']>0).mean()*100:.0f}%)")
    
    # By sector
    lines.append(f"\n{'=' * 50}")
    lines.append("BY SECTOR (3M EXCESS RETURN)")
    lines.append(f"{'=' * 50}")
    valid = results_df.dropna(subset=['excess_ret_3m', 'sector'])
    if len(valid) > 0:
        sector_stats = valid.groupby('sector')['excess_ret_3m'].agg(['mean', 'median', 'count'])
        sector_stats = sector_stats.sort_values('mean', ascending=False)
        for sector, row in sector_stats.iterrows():
            if row['count'] >= 3:
                lines.append(f"  {sector:<30} avg: {row['mean']:>7.2f}%  med: {row['median']:>7.2f}%  n={int(row['count'])}")
    
    # CEO/Officer clusters
    lines.append(f"\n{'=' * 50}")
    lines.append("BY INSIDER SENIORITY (3M EXCESS RETURN)")
    lines.append(f"{'=' * 50}")
    valid = results_df.dropna(subset=['excess_ret_3m'])
    if len(valid) > 0:
        for label, col in [('Has CEO', 'has_ceo'), ('Has Officer', 'has_officer'), ('Has Director', 'has_director')]:
            subset = valid[valid[col] == True]
            if len(subset) > 0:
                lines.append(f"  {label:<20} avg: {subset['excess_ret_3m'].mean():>7.2f}%  "
                           f"med: {subset['excess_ret_3m'].median():>7.2f}%  n={len(subset)}")
    
    return "\n".join(lines)


def run_backtest():
    """Main backtest entry point."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Step 1: Load purchases
    purchases = load_all_purchases()
    if len(purchases) == 0:
        print("No purchase transactions found. Run ingestion first.")
        return
    
    # Step 2: Detect clusters
    clusters = detect_clusters(purchases)
    if len(clusters) == 0:
        print("No clusters detected.")
        return
    
    # Step 3: Fetch prices
    tickers = clusters['ticker'].unique().tolist()
    prices = fetch_prices_for_returns(tickers)
    
    # Step 4: Compute forward returns
    results = compute_forward_returns(clusters, prices)
    
    # Step 5: Save results
    csv_path = os.path.join(OUTPUT_DIR, "historical_clusters.csv")
    results.to_csv(csv_path, index=False)
    print(f"\nSaved {len(results)} cluster results to {csv_path}")
    
    # Step 6: Summary
    summary = generate_summary(results)
    summary_path = os.path.join(OUTPUT_DIR, "backtest_summary.txt")
    with open(summary_path, 'w') as f:
        f.write(summary)
    print(f"Saved summary to {summary_path}")
    print(f"\n{summary}")


if __name__ == "__main__":
    run_backtest()
