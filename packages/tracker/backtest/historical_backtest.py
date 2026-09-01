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


def load_split_events():
    """
    Load all split events from DB into a dict keyed by ticker.

    Returns dict mapping ticker -> list of (date, ratio) sorted by date.
    Each entry represents a split: forward k:1 has ratio=k, reverse 1:k has ratio=1/k.
    """
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT ticker, date, ratio
        FROM split_events
        ORDER BY ticker, date
    """, conn)
    conn.close()

    splits_by_ticker = {}
    for ticker, group in df.groupby('ticker'):
        splits_by_ticker[ticker] = list(group[['date', 'ratio']].itertuples(index=False, name=None))

    logger.info(f"Loaded {len(df)} split events for {len(splits_by_ticker)} tickers")
    return splits_by_ticker


def load_prices_from_db(tickers):
    """
    Load daily close prices from DB for all tickers + benchmark ETFs.

    DB prices table has everything needed: 3.5M rows, 3K tickers, 2019-2026,
    including all benchmarks (SPY/IWM/MDY/QQQ/^IXIC/URTH/ACWI). No network access required.
    """
    all_benchmarks = {"SPY", "IWM", "MDY", "QQQ", "^IXIC", "URTH", "ACWI"}
    all_tickers = list(set(tickers) | all_benchmarks)
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

    loaded_benchmarks = [b for b in all_benchmarks if b in prices]
    logger.info(f"Loaded price data for {len(prices)} tickers (including benchmarks: {', '.join(sorted(loaded_benchmarks))})")
    return prices


def compute_historical_market_cap(ticker, signal_date, prices, split_events):
    """
    Compute market cap at signal_date using historical shares_outstanding and split-adjusted prices.

    Correct formula: market_cap = adjusted_price * shares_as_filed * PROD(split_ratio for splits after signal_date)

    The prices table stores back-adjusted prices (adjusted to a recent reference date).
    shares_outstanding stores as-filed share counts (not adjusted for future splits).
    To get the true market cap at signal_date, we must undo the forward adjustments by
    multiplying by the cumulative split ratio for all splits that occurred AFTER signal_date.

    Example: stock at $3 today after 1:100 reverse split shows $300 adjusted price at signal_date.
    If shares_as_filed = 1M at signal_date, true market_cap = $300 * 1M * 0.01 = $3M, not $300M.

    Ratio guard: The reconstruction error is directional - missing reverse-split ratios and
    ordinary/ADS confusion both INFLATE the historical figure. So we anchor against current
    market cap: if historical/current > 50x, the reconstruction is untrustworthy.

    Threshold rationale: 50x allows for genuine 98% multi-year drawdowns (1/0.02 = 50x)
    while rejecting penny-stock reverse-split contamination (which shows ratios in the
    hundreds or thousands). A tighter threshold (e.g. 20x) would discard real crashes.

    Returns (market_cap, data_source, current_market_cap) where data_source indicates how it was computed.
    Falls back to companies.market_cap if historical data unavailable.
    Returns (None, "unknown", current_mcap) if computed market cap is implausible or ratio guard triggers.
    """
    # Plausibility bounds
    MIN_PLAUSIBLE = 1e6   # $1M
    MAX_PLAUSIBLE = 5e12  # $5T (matches scripts/recompute_market_caps.py convention)
    MAX_RATIO = 50.0      # historical/current ratio threshold

    conn = get_db()
    cur = conn.cursor()

    # Get company_id and current market cap
    cur.execute("SELECT id, market_cap FROM companies WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return (None, "missing", None)

    company_id, current_mcap = row

    # Get latest shares_outstanding as of signal_date
    cur.execute(
        """
        SELECT shares
        FROM shares_outstanding
        WHERE company_id = ? AND date <= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (company_id, signal_date),
    )
    shares_row = cur.fetchone()

    conn.close()

    if not shares_row:
        # Fall back to companies.market_cap
        return (current_mcap, "fallback", current_mcap)

    shares = shares_row[0]

    # Get price at signal_date
    ticker_prices = prices.get(ticker)
    if ticker_prices is None:
        return (current_mcap, "fallback", current_mcap)

    # Find the latest price on or before signal_date
    signal_ts = pd.Timestamp(signal_date)
    valid_dates = ticker_prices.index[ticker_prices.index <= signal_ts]
    if len(valid_dates) == 0:
        return (current_mcap, "fallback", current_mcap)

    price = ticker_prices[valid_dates[-1]]

    # Compute cumulative split ratio for all splits AFTER signal_date
    # Forward k:1 split has ratio=k; reverse 1:k split has ratio=1/k
    # Cumulative ratio is the product of all ratios
    cumulative_ratio = 1.0
    ticker_splits = split_events.get(ticker, [])
    for split_date, ratio in ticker_splits:
        if split_date > signal_date:
            cumulative_ratio *= ratio

    # Apply correction: market_cap = adjusted_price * shares_as_filed * cumulative_split_ratio
    market_cap = shares * price * cumulative_ratio

    # Ratio guard: if historical/current exceeds threshold, reconstruction is untrustworthy
    # This catches incomplete split_events and ordinary/ADS basis switches
    if current_mcap and current_mcap > 0:
        ratio = market_cap / current_mcap
        if ratio > MAX_RATIO:
            return (None, "unknown", current_mcap)

    # Plausibility guard: reject nonsensical absolute values
    if market_cap < MIN_PLAUSIBLE or market_cap > MAX_PLAUSIBLE:
        return (None, "unknown", current_mcap)

    return (market_cap, "historical", current_mcap)


def get_benchmark_for_tier(tier):
    """Map market cap tier to benchmark ETF."""
    if tier in ["micro", "small"]:
        return "IWM"
    elif tier == "mid":
        return "MDY"
    elif tier in ["large", "mega"]:
        return "SPY"
    else:  # unknown
        return "SPY"


def compute_forward_returns(clusters_df, prices, split_events):
    """
    Compute forward returns for each cluster signal against multiple benchmarks.

    Calculates excess returns vs:
    - Size-matched benchmarks (IWM/MDY/SPY) - stored as excess_ret_3m/12m
    - SPY - stored as excess_ret_3m/12m_spy
    - QQQ - stored as excess_ret_3m/12m_qqq
    - ^IXIC - stored as excess_ret_3m/12m_ixic
    - URTH - stored as excess_ret_3m/12m_urth
    - ACWI - stored as excess_ret_3m/12m_acwi

    Uses historical market cap at signal_date to assign size-matched benchmark.
    Market cap computation now accounts for split adjustments to avoid directional bias.
    Requires both stock and benchmark to have same 252 or 63 trading days.
    Refuses to compute when benchmark has no price near signal_date (>7 days lag).
    """
    from signals.size_adjustment import get_tier

    periods = {
        'ret_3m': 63,
        'ret_12m': 252,
    }

    # Split benchmarks into required and optional
    # Required: SPY (excess return basis), IWM/MDY (size-matched benchmarks)
    # Optional: QQQ, ^IXIC, URTH, ACWI (comparison-only)
    required_benchmarks = ['SPY', 'IWM', 'MDY']
    optional_benchmarks = ['QQQ', '^IXIC', 'URTH', 'ACWI']

    benchmark_suffixes = {
        'SPY': 'spy',
        'QQQ': 'qqq',
        '^IXIC': 'ixic',
        'URTH': 'urth',
        'ACWI': 'acwi',
    }

    # Verify required benchmarks present
    missing_required = [b for b in required_benchmarks if b not in prices]
    if missing_required:
        logger.error(f"Missing required benchmark prices: {missing_required}")
        logger.error(f"Required benchmarks (SPY, IWM, MDY) are needed for size-matched excess returns")
        return pd.DataFrame()

    # Warn about missing optional benchmarks
    missing_optional = [b for b in optional_benchmarks if b not in prices]
    if missing_optional:
        logger.warning(f"Missing optional benchmark prices: {missing_optional}")
        logger.warning(f"Excess return columns for {missing_optional} will be omitted")

    # Available benchmarks for iteration
    available_benchmarks = [b for b in (required_benchmarks + optional_benchmarks) if b in prices]

    results = []
    tier_stats = {"historical": 0, "fallback": 0, "missing": 0, "unknown": 0}
    alignment_drops = 0
    benchmark_alignment_drops = {b: 0 for b in available_benchmarks}

    # Max lag tolerance for benchmark entry vs signal_date
    MAX_ENTRY_LAG_DAYS = 7  # ~5 trading days tolerance for weekends/holidays

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

        # Compute historical market cap at signal_date
        market_cap, mcap_source, current_mcap = compute_historical_market_cap(ticker, str(signal_date.date()), prices, split_events)
        tier_stats[mcap_source] += 1

        # Assign tier and size-matched benchmark
        tier = get_tier(market_cap)
        benchmark_ticker = get_benchmark_for_tier(tier)

        # Get all available benchmark prices and check alignment
        benchmark_data = {}
        skip_cluster = False

        for bench in available_benchmarks:
            bench_prices = prices[bench]
            bench_valid = bench_prices.index[bench_prices.index >= signal_date]

            if len(bench_valid) == 0:
                # For required benchmarks, this is an error (skip cluster)
                if bench in required_benchmarks:
                    skip_cluster = True
                    break
                # For optional benchmarks, just skip this one
                continue

            bench_entry_date = bench_valid[0]
            if (bench_entry_date - signal_date).days > MAX_ENTRY_LAG_DAYS:
                benchmark_alignment_drops[bench] += 1
                # For required benchmarks, this is an error (skip cluster)
                if bench in required_benchmarks:
                    skip_cluster = True
                    break
                # For optional benchmarks, just skip this one
                continue

            benchmark_data[bench] = {
                'prices': bench_prices,
                'entry_date': bench_entry_date,
                'entry_price': bench_prices[bench_entry_date],
            }

        if skip_cluster:
            alignment_drops += 1
            continue

        row = cluster.to_dict()
        row['entry_date'] = str(entry_date.date())
        row['entry_price'] = entry_price
        row['market_cap_at_signal'] = market_cap
        row['current_market_cap'] = current_mcap
        row['market_cap_source'] = mcap_source
        row['tier'] = tier
        row['benchmark'] = benchmark_ticker

        for period_name, days in periods.items():
            future = ticker_prices.index[(ticker_prices.index > entry_date)]

            if len(future) < days:
                # Stock doesn't have enough future data
                row[period_name] = None
                row[f'excess_{period_name}'] = None
                for bench, suffix in benchmark_suffixes.items():
                    row[f'excess_{period_name}_{suffix}'] = None
                continue

            exit_date = future[days - 1]
            exit_price = ticker_prices[exit_date]
            stock_ret = (exit_price / entry_price - 1) * 100

            row[period_name] = round(stock_ret, 2)

            # Compute excess returns for each benchmark (only if available)
            for bench, suffix in benchmark_suffixes.items():
                if bench not in benchmark_data:
                    # Benchmark missing (optional benchmark not fetched)
                    row[f'excess_{period_name}_{suffix}'] = None
                    continue

                bench_info = benchmark_data[bench]
                bench_future = bench_info['prices'].index[
                    bench_info['prices'].index > bench_info['entry_date']
                ]

                if len(bench_future) >= days:
                    bench_exit_date = bench_future[days - 1]
                    bench_exit_price = bench_info['prices'][bench_exit_date]
                    bench_ret = (bench_exit_price / bench_info['entry_price'] - 1) * 100
                    excess = stock_ret - bench_ret
                    row[f'excess_{period_name}_{suffix}'] = round(excess, 2)
                else:
                    row[f'excess_{period_name}_{suffix}'] = None

            # Size-matched excess return (primary metric, kept as excess_ret_3m/12m for backward compat)
            size_matched_bench = benchmark_ticker
            if size_matched_bench in benchmark_data:
                bench_info = benchmark_data[size_matched_bench]
                bench_future = bench_info['prices'].index[
                    bench_info['prices'].index > bench_info['entry_date']
                ]
                if len(bench_future) >= days:
                    bench_exit_date = bench_future[days - 1]
                    bench_exit_price = bench_info['prices'][bench_exit_date]
                    bench_ret = (bench_exit_price / bench_info['entry_price'] - 1) * 100
                    excess = stock_ret - bench_ret
                    row[f'excess_{period_name}'] = round(excess, 2)
                else:
                    row[f'excess_{period_name}'] = None
            else:
                row[f'excess_{period_name}'] = None

        results.append(row)

    df = pd.DataFrame(results)
    logger.info(f"Computed forward returns for {len(df)} clusters")
    logger.info(f"Market cap sources: {tier_stats['historical']} historical, "
               f"{tier_stats['fallback']} fallback (today's value for historical signal), "
               f"{tier_stats['unknown']} unknown (implausible), {tier_stats['missing']} missing")
    if alignment_drops > 0:
        logger.warning(f"Dropped {alignment_drops} clusters due to benchmark misalignment (>7 days lag)")
        for bench, count in benchmark_alignment_drops.items():
            if count > 0:
                logger.warning(f"  {bench}: {count} clusters dropped")

    return df


def generate_summary(results_df):
    """Generate summary statistics with multi-benchmark comparison."""
    lines = []
    lines.append("=" * 80)
    lines.append("INSIDER CLUSTER BACKTEST — MULTI-BENCHMARK COMPARISON")
    lines.append("=" * 80)
    lines.append(f"\nTotal clusters detected: {len(results_df)}")
    lines.append(f"Unique tickers: {results_df['ticker'].nunique()}")
    lines.append(f"Date range: {results_df['signal_date'].min()} to {results_df['signal_date'].max()}")

    # Market cap provenance
    if 'market_cap_source' in results_df.columns:
        lines.append(f"\nMarket cap provenance:")
        provenance_counts = results_df['market_cap_source'].value_counts()
        for source in ['historical', 'fallback', 'unknown', 'missing']:
            count = provenance_counts.get(source, 0)
            pct = count / len(results_df) * 100 if len(results_df) > 0 else 0
            lines.append(f"  {source:12s}: {count:4d} ({pct:5.1f}%)")
        lines.append(f"\nNOTE: 'fallback' uses today's market cap for signals up to 6 years old (look-ahead error).")
        lines.append(f"      'unknown' = computed value implausible (outside $1M-$5T range).")

    # Market cap distribution
    valid_mcap = results_df.dropna(subset=['market_cap_at_signal'])
    if len(valid_mcap) > 0:
        lines.append(f"\nMarket cap at signal (n={len(valid_mcap)}):")
        lines.append(f"  Median: ${valid_mcap['market_cap_at_signal'].median() / 1e9:.2f}B")
        lines.append(f"  Mean: ${valid_mcap['market_cap_at_signal'].mean() / 1e9:.2f}B")
        under_2b = (valid_mcap['market_cap_at_signal'] < 2e9).mean() * 100
        over_10b = (valid_mcap['market_cap_at_signal'] >= 10e9).mean() * 100
        lines.append(f"  Share under $2B: {under_2b:.1f}%")
        lines.append(f"  Share over $10B: {over_10b:.1f}%")

    # Benchmark usage (size-matched)
    if 'benchmark' in results_df.columns:
        benchmark_counts = results_df['benchmark'].value_counts()
        lines.append(f"\nSize-matched benchmark usage:")
        for benchmark, count in benchmark_counts.items():
            lines.append(f"  {benchmark}: {count} ({count/len(results_df)*100:.1f}%)")

    # Show mega and large tier membership lists for eyeballing
    if 'tier' in results_df.columns:
        mega = results_df[results_df['tier'] == 'mega']
        if len(mega) > 0:
            mega_tickers = sorted(mega['ticker'].unique())
            lines.append(f"\nMEGA tier members (n={len(mega_tickers)}):")
            for ticker in mega_tickers:
                ticker_rows = mega[mega['ticker'] == ticker]
                hist_mcap = ticker_rows.iloc[0]['market_cap_at_signal']
                curr_mcap = ticker_rows.iloc[0].get('current_market_cap')
                if hist_mcap:
                    hist_str = f"hist ${hist_mcap/1e9:.1f}B"
                else:
                    hist_str = "hist unknown"
                if curr_mcap:
                    curr_str = f"curr ${curr_mcap/1e9:.1f}B"
                else:
                    curr_str = "curr unknown"
                lines.append(f"  {ticker:6s}: {hist_str:20s} {curr_str}")

        large = results_df[results_df['tier'] == 'large']
        if len(large) > 0:
            large_tickers = sorted(large['ticker'].unique())
            lines.append(f"\nLARGE tier members (n={len(large_tickers)}):")
            for ticker in large_tickers:
                ticker_rows = large[large['ticker'] == ticker]
                hist_mcap = ticker_rows.iloc[0]['market_cap_at_signal']
                curr_mcap = ticker_rows.iloc[0].get('current_market_cap')
                if hist_mcap:
                    hist_str = f"hist ${hist_mcap/1e9:.1f}B"
                else:
                    hist_str = "hist unknown"
                if curr_mcap:
                    curr_str = f"curr ${curr_mcap/1e9:.1f}B"
                else:
                    curr_str = "curr unknown"
                lines.append(f"  {ticker:6s}: {hist_str:20s} {curr_str}")

    # Multi-benchmark comparison for each period
    benchmarks_to_report = [
        ('spy', 'SPY (S&P 500)'),
        ('qqq', 'QQQ (Nasdaq-100)'),
        ('ixic', '^IXIC (Nasdaq Composite)'),
        ('urth', 'URTH (MSCI World)'),
        ('acwi', 'ACWI (MSCI All-Country World)'),
        (None, 'Size-matched (IWM/MDY/SPY)')  # None = excess_ret_3m/12m
    ]

    for period in ['ret_3m', 'ret_12m']:
        period_label = period.replace('ret_', '').upper()
        col = period

        # Stock returns summary
        valid_stock = results_df.dropna(subset=[col])
        if len(valid_stock) == 0:
            continue

        lines.append(f"\n{'=' * 80}")
        lines.append(f"FORWARD {period_label} RETURNS")
        lines.append(f"{'=' * 80}")
        lines.append(f"  Stock returns (n={len(valid_stock)}):")
        lines.append(f"    Median:     {valid_stock[col].median():>7.2f}%")
        lines.append(f"    Mean:       {valid_stock[col].mean():>7.2f}%")
        lines.append(f"    Hit rate:   {(valid_stock[col] > 0).mean()*100:>7.1f}%")

        # Benchmark comparisons
        lines.append(f"\n  Excess returns vs benchmarks:")
        for suffix, label in benchmarks_to_report:
            if suffix is None:
                # Size-matched benchmark
                excess_col = f'excess_{period}'
            else:
                excess_col = f'excess_{period}_{suffix}'

            valid = results_df.dropna(subset=[excess_col])
            if len(valid) == 0:
                lines.append(f"\n    {label}: NO DATA")
                continue

            # Compute statistics
            median_excess = valid[excess_col].median()
            mean_excess = valid[excess_col].mean()
            win_rate = (valid[excess_col] > 0).mean() * 100

            # Exclude outliers (|excess| > 1000%) for cleaner mean
            valid_no_outliers = valid[valid[excess_col].abs() <= 1000]
            mean_no_outliers = valid_no_outliers[excess_col].mean()
            n_outliers = len(valid) - len(valid_no_outliers)

            lines.append(f"\n    {label}:")
            lines.append(f"      Median excess:      {median_excess:>7.2f}%")
            lines.append(f"      Win rate:           {win_rate:>7.1f}%")
            lines.append(f"      Mean excess:        {mean_excess:>7.2f}%")
            if n_outliers > 0:
                lines.append(f"      Mean (no outliers): {mean_no_outliers:>7.2f}%  ({n_outliers} outliers excluded)")
            lines.append(f"      n={len(valid)}")

    # By tier - report each benchmark
    lines.append(f"\n{'=' * 80}")
    lines.append("BY MARKET CAP TIER (12M EXCESS RETURN VS EACH BENCHMARK)")
    lines.append(f"{'=' * 80}")
    valid = results_df.dropna(subset=['tier'])
    if len(valid) > 0:
        from signals.size_adjustment import TIER_ORDER
        valid['signal_date_ts'] = pd.to_datetime(valid['signal_date'])

        benchmarks_for_tier = [
            (None, 'Size-matched'),
            ('spy', 'SPY'),
            ('qqq', 'QQQ'),
            ('ixic', '^IXIC'),
            ('urth', 'URTH'),
            ('acwi', 'ACWI'),
        ]

        for tier in TIER_ORDER + ['unknown']:
            subset = valid[valid['tier'] == tier]
            if len(subset) == 0:
                continue

            # Tier summary line with provenance breakdown
            size_matched_bench = subset.iloc[0]['benchmark'] if 'benchmark' in subset.columns else '?'
            min_signal = subset['signal_date_ts'].min().strftime('%Y-%m-%d')
            max_signal = subset['signal_date_ts'].max().strftime('%Y-%m-%d')
            pre_2021 = (subset['signal_date_ts'] < '2021-01-01').sum()

            # Provenance breakdown within tier
            if 'market_cap_source' in subset.columns:
                hist_count = (subset['market_cap_source'] == 'historical').sum()
                fallback_count = (subset['market_cap_source'] == 'fallback').sum()
                unknown_count = (subset['market_cap_source'] == 'unknown').sum()
                provenance_str = f"hist={hist_count}, fallback={fallback_count}, unknown={unknown_count}"
            else:
                provenance_str = "provenance unknown"

            lines.append(f"\n  {tier.upper()} (n={len(subset)}, {provenance_str}, pre-2021={pre_2021}, size-matched={size_matched_bench})")
            lines.append(f"    Signal dates: {min_signal} → {max_signal}")

            # Report stats for each benchmark
            for suffix, label in benchmarks_for_tier:
                if suffix is None:
                    excess_col = 'excess_ret_12m'
                else:
                    excess_col = f'excess_ret_12m_{suffix}'

                bench_subset = subset.dropna(subset=[excess_col])
                if len(bench_subset) == 0:
                    continue

                med_excess = bench_subset[excess_col].median()
                win_rate = (bench_subset[excess_col] > 0).mean() * 100
                mean_excess = bench_subset[excess_col].mean()

                lines.append(
                    f"      {label:12s}: med {med_excess:>7.2f}%  win {win_rate:>4.0f}%  "
                    f"mean {mean_excess:>7.2f}%"
                )

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


def verify_price_series_consistency(split_events, prices):
    """
    Verify that price series are internally consistent (back-adjusted to single reference).

    Checks whether prices exhibit discontinuities at split dates that would indicate
    the series was built from chunks fetched at different times (split between fetches).

    A consistent back-adjusted series should NOT show price changes proportional to
    the split ratio at split dates — the entire history is already adjusted.

    Returns dict with findings for each ticker checked.
    """
    test_tickers = ['XXII', 'WINT', 'ASTI']
    logger.info("\n" + "=" * 70)
    logger.info("PRICE SERIES CONSISTENCY CHECK")
    logger.info("=" * 70)
    logger.info("Checking for discontinuities at split dates (would indicate")
    logger.info("price series built from chunks fetched at different times).")
    logger.info("")

    findings = {}

    for ticker in test_tickers:
        ticker_splits = split_events.get(ticker, [])
        ticker_prices = prices.get(ticker)

        if not ticker_splits or ticker_prices is None:
            logger.info(f"{ticker}: No splits or no price data, skipping")
            findings[ticker] = "no_data"
            continue

        discontinuities = []

        for split_date, ratio in ticker_splits:
            # Find price before and after split
            split_ts = pd.Timestamp(split_date)

            before_dates = ticker_prices.index[ticker_prices.index < split_ts]
            after_dates = ticker_prices.index[ticker_prices.index >= split_ts]

            if len(before_dates) == 0 or len(after_dates) == 0:
                continue

            price_before = ticker_prices[before_dates[-1]]
            price_after = ticker_prices[after_dates[0]]
            date_before = before_dates[-1]
            date_after = after_dates[0]

            # Price ratio at split (should NOT match split ratio if back-adjusted consistently)
            price_ratio = price_after / price_before if price_before != 0 else 0
            ratio_error = abs(price_ratio - ratio)

            # Discontinuity detected if price_ratio ~= ratio (within 10% tolerance)
            # A properly back-adjusted series has price_ratio unrelated to split ratio
            is_discontinuous = ratio_error < abs(ratio) * 0.1

            if is_discontinuous:
                discontinuities.append({
                    'split_date': split_date,
                    'split_ratio': ratio,
                    'price_before': price_before,
                    'price_after': price_after,
                    'price_ratio': price_ratio,
                    'date_before': str(date_before.date()),
                    'date_after': str(date_after.date()),
                })

        if discontinuities:
            findings[ticker] = "DISCONTINUITY_DETECTED"
            logger.warning(f"\n{ticker}: DISCONTINUITY DETECTED at {len(discontinuities)} split(s)!")
            for d in discontinuities[:3]:  # Show first 3
                logger.warning(f"  {d['split_date']}: price {d['price_before']:.2f} → {d['price_after']:.2f} "
                             f"(ratio {d['price_ratio']:.4f} ~= split ratio {d['split_ratio']:.4f})")
        else:
            findings[ticker] = "consistent"
            logger.info(f"{ticker}: CONSISTENT — {len(ticker_splits)} split(s) checked, "
                       f"no discontinuities (price series properly back-adjusted)")

    logger.info("")
    return findings


def investigate_negative_skew(results_df):
    """
    Compare size-matched vs SPY benchmarking to determine if underperformance was a size artifact.

    Reports distributions and winner/loser breakdowns for both benchmark approaches.
    """
    logger.info("\n" + "=" * 70)
    logger.info("SIZE-MATCHED vs SPY BENCHMARKING COMPARISON")
    logger.info("=" * 70)

    valid_12m = results_df.dropna(subset=['ret_12m', 'excess_ret_12m']).copy()
    if len(valid_12m) == 0:
        logger.warning("No valid 12m returns to investigate")
        return

    # 1. Check raw distributions
    logger.info(f"\nRaw 12m stock returns: mean={valid_12m['ret_12m'].mean():.2f}%, "
               f"median={valid_12m['ret_12m'].median():.2f}%")

    # Size-matched benchmark
    valid_12m['benchmark_ret_12m'] = valid_12m['ret_12m'] - valid_12m['excess_ret_12m']
    logger.info(f"Size-matched benchmark 12m: mean={valid_12m['benchmark_ret_12m'].mean():.2f}%, "
               f"median={valid_12m['benchmark_ret_12m'].median():.2f}%")
    logger.info(f"Excess vs size-matched: mean={valid_12m['excess_ret_12m'].mean():.2f}%, "
               f"median={valid_12m['excess_ret_12m'].median():.2f}%")

    # SPY comparison
    if 'excess_ret_12m_spy' in valid_12m.columns:
        valid_spy = valid_12m.dropna(subset=['excess_ret_12m_spy'])
        if len(valid_spy) > 0:
            valid_spy['spy_ret_12m'] = valid_spy['ret_12m'] - valid_spy['excess_ret_12m_spy']
            logger.info(f"\nSPY 12m returns: mean={valid_spy['spy_ret_12m'].mean():.2f}%, "
                       f"median={valid_spy['spy_ret_12m'].median():.2f}%")
            logger.info(f"Excess vs SPY: mean={valid_spy['excess_ret_12m_spy'].mean():.2f}%, "
                       f"median={valid_spy['excess_ret_12m_spy'].median():.2f}%")

    # 2. Distribution by quintile
    logger.info(f"\n12m excess return distribution (size-matched):")
    logger.info(f"  10th percentile: {valid_12m['excess_ret_12m'].quantile(0.1):.2f}%")
    logger.info(f"  25th percentile: {valid_12m['excess_ret_12m'].quantile(0.25):.2f}%")
    logger.info(f"  50th percentile: {valid_12m['excess_ret_12m'].quantile(0.50):.2f}%")
    logger.info(f"  75th percentile: {valid_12m['excess_ret_12m'].quantile(0.75):.2f}%")
    logger.info(f"  90th percentile: {valid_12m['excess_ret_12m'].quantile(0.90):.2f}%")

    # 3. Winners vs losers (size-matched)
    winners = valid_12m[valid_12m['excess_ret_12m'] > 0]
    losers = valid_12m[valid_12m['excess_ret_12m'] <= 0]
    logger.info(f"\nWinners (>size-matched benchmark): {len(winners)} ({len(winners)/len(valid_12m)*100:.1f}%), "
               f"avg excess: {winners['excess_ret_12m'].mean():.2f}%")
    logger.info(f"Losers (<=size-matched benchmark): {len(losers)} ({len(losers)/len(valid_12m)*100:.1f}%), "
               f"avg excess: {losers['excess_ret_12m'].mean():.2f}%")

    # 4. SPY comparison
    if 'excess_ret_12m_spy' in valid_12m.columns:
        valid_spy = valid_12m.dropna(subset=['excess_ret_12m_spy'])
        if len(valid_spy) > 0:
            winners_spy = valid_spy[valid_spy['excess_ret_12m_spy'] > 0]
            losers_spy = valid_spy[valid_spy['excess_ret_12m_spy'] <= 0]
            logger.info(f"\nWinners (>SPY): {len(winners_spy)} ({len(winners_spy)/len(valid_spy)*100:.1f}%), "
                       f"avg excess: {winners_spy['excess_ret_12m_spy'].mean():.2f}%")
            logger.info(f"Losers (<=SPY): {len(losers_spy)} ({len(losers_spy)/len(valid_spy)*100:.1f}%), "
                       f"avg excess: {losers_spy['excess_ret_12m_spy'].mean():.2f}%")

    # 5. Outlier check (exclude >1000% for mean comparison)
    valid_no_outliers = valid_12m[valid_12m['excess_ret_12m'].abs() <= 1000]
    logger.info(f"\nExcluding outliers (|excess| > 1000%):")
    logger.info(f"  Excluded: {len(valid_12m) - len(valid_no_outliers)} clusters")
    logger.info(f"  Mean excess (size-matched): {valid_no_outliers['excess_ret_12m'].mean():.2f}%")

    if 'excess_ret_12m_spy' in valid_no_outliers.columns:
        valid_spy_no_outliers = valid_no_outliers.dropna(subset=['excess_ret_12m_spy'])
        valid_spy_no_outliers = valid_spy_no_outliers[valid_spy_no_outliers['excess_ret_12m_spy'].abs() <= 1000]
        if len(valid_spy_no_outliers) > 0:
            logger.info(f"  Mean excess (SPY): {valid_spy_no_outliers['excess_ret_12m_spy'].mean():.2f}%")


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

    # Step 3: Load split events (once, for all tickers)
    split_events = load_split_events()

    # Step 4: Load prices from DB (offline, no network)
    tickers = clusters['ticker'].unique().tolist()
    prices = load_prices_from_db(tickers)

    # Step 5: Verify price series consistency (check for discontinuities at splits)
    verify_price_series_consistency(split_events, prices)

    # Step 6: Compute forward returns
    results = compute_forward_returns(clusters, prices, split_events)

    if len(results) == 0:
        logger.warning("No results with valid forward returns.")
        return

    # Step 7: Investigate negative skew
    investigate_negative_skew(results)

    # Step 8: Save results with required columns for historical_hit_rate.py
    csv_path = os.path.join(OUTPUT_DIR, "historical_clusters.csv")
    results.to_csv(csv_path, index=False)
    logger.info(f"\nSaved {len(results)} cluster results to {csv_path}")

    # Step 9: Summary
    summary = generate_summary(results)
    summary_path = os.path.join(OUTPUT_DIR, "backtest_summary.txt")
    with open(summary_path, 'w') as f:
        f.write(summary)
    logger.info(f"Saved summary to {summary_path}")
    print(f"\n{summary}")


if __name__ == "__main__":
    run_backtest()
