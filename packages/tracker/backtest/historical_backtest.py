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
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")

# Import canonical cluster enumeration from live signal code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from signals.insider_clusters import _enumerate_all_clusters, _get_seniority_weight


def get_db():
    return sqlite3.connect(DB_PATH)


def load_all_purchases():
    """
    Load all insider purchase transactions from DB.

    Filters to 'P' (Purchase) only, excluding 'A' (Award/Grant) which are
    not open-market purchases and don't match what the live signal detects.
    Also filters out malformed dates and junk tickers like 'NONE'.
    """
    conn = get_db()

    # Get valid date range from prices table
    date_range = pd.read_sql_query("SELECT MIN(date) as min_date, MAX(date) as max_date FROM prices", conn)
    min_price_date = date_range['min_date'].iloc[0]
    max_price_date = date_range['max_date'].iloc[0]
    logger.info(f"Price data available from {min_price_date} to {max_price_date}")

    df = pd.read_sql_query("""
        SELECT
            it.id, c.ticker, c.sector, it.filing_date, it.transaction_date,
            it.reporting_name, it.reporting_cik, it.transaction_type,
            it.shares_transacted, it.price, it.shares_owned_after, it.raw_json
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.shares_transacted > 0
          AND it.price > 0
          AND c.ticker != 'NONE'
    """, conn)
    conn.close()

    initial_count = len(df)

    # Parse transaction_date and filter malformed/impossible dates
    df['transaction_date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
    df['filing_date'] = pd.to_datetime(df['filing_date'], errors='coerce')

    # Count various drop reasons
    null_dates = df['transaction_date'].isnull().sum()
    df = df.dropna(subset=['transaction_date'])

    # Filter to dates within price range (valid ISO dates only)
    before_filter = len(df)
    df = df[
        (df['transaction_date'] >= min_price_date) &
        (df['transaction_date'] <= max_price_date)
    ]
    out_of_range = before_filter - len(df)

    df['total_value'] = df['shares_transacted'] * df['price']

    # Log what was dropped
    total_dropped = initial_count - len(df)
    logger.info(f"Loaded {len(df)} purchase transactions across {df['ticker'].nunique()} tickers")
    logger.info(f"Dropped {total_dropped} rows: {null_dates} malformed dates, {out_of_range} out of price range")

    return df


def detect_clusters(purchases_df, window_days=30, min_insiders=2):
    """
    Enumerate all historical cluster events using canonical enumeration logic.

    WHY: The live signal needs the single best cluster ("is there a signal now?").
    The backtest needs the full timeline of non-overlapping cluster events to build
    a hit rate track record. Enumeration walks trades chronologically and emits each
    qualifying cluster, advancing past its window to avoid counting the same buying
    episode repeatedly.

    Returns DataFrame with one row per cluster event, signal_date = last trade in cluster.
    """
    clusters = []

    for ticker, group in purchases_df.groupby('ticker'):
        group = group.sort_values('transaction_date')
        if len(group) < min_insiders:
            continue

        # Convert to the trade dict format expected by canonical enumeration
        trades = []
        for _, row in group.iterrows():
            raw = json.loads(row['raw_json']) if row['raw_json'] else {}
            relationship = raw.get('relationship', '')
            trades.append({
                'date': str(row['transaction_date'].date()),
                'name': row['reporting_name'],
                'cik': row['reporting_cik'],
                'shares': row['shares_transacted'],
                'price': row['price'],
                'value': row['total_value'],
                'relationship': relationship,
                'seniority_weight': _get_seniority_weight(relationship),
            })

        # Enumerate all non-overlapping cluster events for this ticker
        ticker_clusters = _enumerate_all_clusters(trades, window_days, min_insiders)

        for cluster, score in ticker_clusters:
            # Extract cluster metadata
            signal_date = max(t['date'] for t in cluster)
            distinct_insiders = set(t['cik'] for t in cluster)
            total_value = sum(t['value'] for t in cluster)
            insider_names = [str(t['name']) for t in cluster if t['name'] and not pd.isna(t['name'])]
            insider_names = list(set(insider_names))  # Deduplicate

            # Check for officer/director
            has_officer = any('OFFICER' in t['relationship'].upper() or 'VP' in t['relationship'].upper()
                            for t in cluster)
            has_director = any('DIRECTOR' in t['relationship'].upper() for t in cluster)
            has_ceo = any('CEO' in t['relationship'].upper() or 'CHIEF EXECUTIVE' in t['relationship'].upper()
                         for t in cluster)

            clusters.append({
                'ticker': ticker,
                'sector': group.iloc[0]['sector'],
                'signal_date': signal_date,
                'n_insiders': len(distinct_insiders),
                'num_transactions': len(cluster),
                'total_value': total_value,
                'insider_names': '; '.join(insider_names[:5]),
                'has_officer': has_officer,
                'has_director': has_director,
                'has_ceo': has_ceo,
            })

    df = pd.DataFrame(clusters)
    logger.info(f"Detected {len(df)} insider buying clusters across {df['ticker'].nunique()} tickers")

    # Log clusters-per-ticker distribution for verification
    if len(df) > 0:
        cpt = df.groupby('ticker').size()
        logger.info(f"Clusters per ticker: min={cpt.min()}, median={cpt.median():.0f}, max={cpt.max()}, "
                   f"tickers with >=3: {(cpt >= 3).sum()}")

    return df


def load_prices_from_db(tickers):
    """
    Load daily close prices from DB for all tickers + SPY.

    DB prices table has everything needed: 3.5M rows, 3K tickers, 2021-2026,
    including SPY for benchmark returns. No network access required.
    """
    all_tickers = list(set(tickers) | {"SPY"})
    logger.info(f"Loading price data for {len(all_tickers)} tickers from DB...")

    conn = get_db()

    # Load prices for all relevant tickers
    placeholders = ','.join('?' * len(all_tickers))
    query = f"""
        SELECT ticker, date, close
        FROM prices
        WHERE ticker IN ({placeholders})
        ORDER BY ticker, date
    """

    df = pd.read_sql_query(query, conn, params=all_tickers)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])

    # Convert to dict of Series indexed by date
    prices = {}
    for ticker, group in df.groupby('ticker'):
        prices[ticker] = group.set_index('date')['close'].sort_index()

    logger.info(f"Loaded price data for {len(prices)} tickers (including SPY)")
    return prices


def compute_forward_returns(clusters_df, prices):
    """
    Compute forward returns for each cluster signal.

    Calculates excess returns vs SPY benchmark at 3m and 12m horizons,
    which are the key inputs for historical hit rate analysis.
    """
    periods = {
        'ret_3m': 63,
        'ret_12m': 252,
    }

    spy_prices = prices.get('SPY')
    if spy_prices is None:
        logger.error("SPY prices not found in DB - cannot compute excess returns")
        return pd.DataFrame()

    results = []

    for _, cluster in clusters_df.iterrows():
        ticker = cluster['ticker']
        signal_date = pd.Timestamp(cluster['signal_date'])

        ticker_prices = prices.get(ticker)
        if ticker_prices is None:
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
                row[f'excess_{period_name}'] = round(excess, 2)
            else:
                row[period_name] = None
                row[f'excess_{period_name}'] = None

        results.append(row)

    df = pd.DataFrame(results)
    logger.info(f"Computed forward returns for {len(df)} clusters")
    return df


def generate_summary(results_df):
    """Generate summary statistics."""
    lines = []
    lines.append("=" * 70)
    lines.append("INSIDER CLUSTER BACKTEST — HISTORICAL ANALYSIS")
    lines.append("=" * 70)
    lines.append(f"\nTotal clusters detected: {len(results_df)}")
    lines.append(f"Unique tickers: {results_df['ticker'].nunique()}")
    lines.append(f"Date range: {results_df['signal_date'].min()} to {results_df['signal_date'].max()}")

    for period in ['ret_3m', 'ret_12m']:
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
        for size in sorted(valid['n_insiders'].unique()):
            subset = valid[valid['n_insiders'] == size]
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


def investigate_negative_skew(results_df):
    """
    Investigate the negative median excess return to determine if it's real or a bug.

    Checks:
    1. Are stock and SPY returns measured over same calendar span?
    2. What's the raw ret_12m distribution vs SPY distribution?
    3. Are winners being silently dropped (missing prices after run-ups)?
    """
    logger.info("\n" + "=" * 70)
    logger.info("INVESTIGATING NEGATIVE MEDIAN EXCESS RETURN")
    logger.info("=" * 70)

    valid_12m = results_df.dropna(subset=['ret_12m', 'excess_ret_12m']).copy()
    if len(valid_12m) == 0:
        logger.warning("No valid 12m returns to investigate")
        return

    # 1. Check raw distributions
    logger.info(f"\nRaw 12m stock returns: mean={valid_12m['ret_12m'].mean():.2f}%, "
               f"median={valid_12m['ret_12m'].median():.2f}%")

    # Reconstruct SPY returns from excess
    valid_12m['spy_ret_12m'] = valid_12m['ret_12m'] - valid_12m['excess_ret_12m']
    logger.info(f"SPY 12m returns: mean={valid_12m['spy_ret_12m'].mean():.2f}%, "
               f"median={valid_12m['spy_ret_12m'].median():.2f}%")
    logger.info(f"Excess 12m: mean={valid_12m['excess_ret_12m'].mean():.2f}%, "
               f"median={valid_12m['excess_ret_12m'].median():.2f}%")

    # 2. Check for missing winners (tickers that might have delisted after big runs)
    all_signals = results_df['ticker'].unique()
    signals_with_12m = valid_12m['ticker'].unique()
    missing_12m = set(all_signals) - set(signals_with_12m)
    logger.info(f"\nSignals missing 12m data: {len(missing_12m)} tickers")
    if len(missing_12m) > 0 and len(missing_12m) < 20:
        logger.info(f"  Sample: {sorted(list(missing_12m))[:10]}")

    # 3. Distribution by quintile
    logger.info(f"\n12m excess return distribution:")
    logger.info(f"  10th percentile: {valid_12m['excess_ret_12m'].quantile(0.1):.2f}%")
    logger.info(f"  25th percentile: {valid_12m['excess_ret_12m'].quantile(0.25):.2f}%")
    logger.info(f"  50th percentile: {valid_12m['excess_ret_12m'].quantile(0.50):.2f}%")
    logger.info(f"  75th percentile: {valid_12m['excess_ret_12m'].quantile(0.75):.2f}%")
    logger.info(f"  90th percentile: {valid_12m['excess_ret_12m'].quantile(0.90):.2f}%")

    # 4. Winners vs losers
    winners = valid_12m[valid_12m['excess_ret_12m'] > 0]
    losers = valid_12m[valid_12m['excess_ret_12m'] <= 0]
    logger.info(f"\nWinners (>SPY): {len(winners)} ({len(winners)/len(valid_12m)*100:.1f}%), "
               f"avg excess: {winners['excess_ret_12m'].mean():.2f}%")
    logger.info(f"Losers (<=SPY): {len(losers)} ({len(losers)/len(valid_12m)*100:.1f}%), "
               f"avg excess: {losers['excess_ret_12m'].mean():.2f}%")


def run_backtest():
    """
    Main backtest entry point.

    Runs fully offline using only DB data - no network access required.
    Generates historical_clusters.csv with ticker, sector, signal_date, n_insiders,
    and excess returns vs SPY at 3m/12m horizons.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Step 1: Load purchases
    purchases = load_all_purchases()
    if len(purchases) == 0:
        logger.error("No purchase transactions found. Run ingestion first.")
        return

    # Step 2: Detect clusters using canonical enumeration
    clusters = detect_clusters(purchases)
    if len(clusters) == 0:
        logger.warning("No clusters detected.")
        return

    # Step 3: Load prices from DB (offline, no network)
    tickers = clusters['ticker'].unique().tolist()
    prices = load_prices_from_db(tickers)

    # Step 4: Compute forward returns
    results = compute_forward_returns(clusters, prices)

    if len(results) == 0:
        logger.warning("No results with valid forward returns.")
        return

    # Step 5: Investigate negative skew
    investigate_negative_skew(results)

    # Step 6: Save results with required columns for historical_hit_rate.py
    csv_path = os.path.join(OUTPUT_DIR, "historical_clusters.csv")
    results.to_csv(csv_path, index=False)
    logger.info(f"\nSaved {len(results)} cluster results to {csv_path}")

    # Step 7: Summary
    summary = generate_summary(results)
    summary_path = os.path.join(OUTPUT_DIR, "backtest_summary.txt")
    with open(summary_path, 'w') as f:
        f.write(summary)
    logger.info(f"Saved summary to {summary_path}")
    print(f"\n{summary}")


if __name__ == "__main__":
    run_backtest()
