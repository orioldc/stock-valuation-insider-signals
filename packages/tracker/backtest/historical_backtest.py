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
    Load daily close prices from DB for all tickers + benchmark ETFs.

    DB prices table has everything needed: 3.5M rows, 3K tickers, 2019-2026,
    including SPY/IWM/MDY for size-matched benchmark returns. No network access required.
    """
    all_tickers = list(set(tickers) | {"SPY", "IWM", "MDY"})
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

    logger.info(f"Loaded price data for {len(prices)} tickers (including benchmarks: SPY, IWM, MDY)")
    return prices


def compute_historical_market_cap(ticker, signal_date, prices):
    """
    Compute market cap at signal_date using historical shares_outstanding and prices.

    Returns (market_cap, data_source) where data_source indicates how it was computed.
    Falls back to companies.market_cap if historical data unavailable.
    """
    conn = get_db()
    cur = conn.cursor()

    # Get company_id
    cur.execute("SELECT id, market_cap FROM companies WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return (None, "missing")

    company_id, fallback_mcap = row

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
        return (fallback_mcap, "fallback")

    shares = shares_row[0]

    # Get price at signal_date
    ticker_prices = prices.get(ticker)
    if ticker_prices is None:
        return (fallback_mcap, "fallback")

    # Find the latest price on or before signal_date
    signal_ts = pd.Timestamp(signal_date)
    valid_dates = ticker_prices.index[ticker_prices.index <= signal_ts]
    if len(valid_dates) == 0:
        return (fallback_mcap, "fallback")

    price = ticker_prices[valid_dates[-1]]
    market_cap = shares * price

    return (market_cap, "historical")


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


def compute_forward_returns(clusters_df, prices):
    """
    Compute forward returns for each cluster signal.

    Calculates excess returns vs size-matched benchmarks (IWM/MDY/SPY) at 3m and 12m horizons,
    plus SPY-based excess returns for comparison. Uses historical market cap at signal_date
    to assign the appropriate benchmark.
    """
    from signals.size_adjustment import get_tier

    periods = {
        'ret_3m': 63,
        'ret_12m': 252,
    }

    # Verify all benchmarks present
    for benchmark in ['SPY', 'IWM', 'MDY']:
        if benchmark not in prices:
            logger.error(f"{benchmark} prices not found in DB - cannot compute size-matched returns")
            return pd.DataFrame()

    results = []
    tier_stats = {"historical": 0, "fallback": 0, "missing": 0}
    alignment_drops = 0

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
        market_cap, mcap_source = compute_historical_market_cap(ticker, str(signal_date.date()), prices)
        tier_stats[mcap_source] += 1

        # Assign tier and benchmark
        tier = get_tier(market_cap)
        benchmark_ticker = get_benchmark_for_tier(tier)

        # Get benchmark prices
        benchmark_prices = prices[benchmark_ticker]
        spy_prices = prices['SPY']

        # Benchmark entry - reject if too far from signal_date (max 5 trading days tolerance)
        # This prevents silently comparing non-overlapping windows when benchmark coverage has gaps
        MAX_ENTRY_LAG_DAYS = 7  # ~5 trading days tolerance for weekends/holidays

        benchmark_valid = benchmark_prices.index[benchmark_prices.index >= signal_date]
        spy_valid = spy_prices.index[spy_prices.index >= signal_date]

        if len(benchmark_valid) == 0 or len(spy_valid) == 0:
            continue

        # Check alignment - benchmark must be within tolerance
        benchmark_entry_date = benchmark_valid[0]
        spy_entry_date = spy_valid[0]

        if (benchmark_entry_date - signal_date).days > MAX_ENTRY_LAG_DAYS:
            # Benchmark too far in future - would misalign windows
            alignment_drops += 1
            continue

        if (spy_entry_date - signal_date).days > MAX_ENTRY_LAG_DAYS:
            # SPY too far in future - would misalign windows
            alignment_drops += 1
            continue

        benchmark_entry = benchmark_prices[benchmark_entry_date]
        spy_entry = spy_prices[spy_entry_date]

        row = cluster.to_dict()
        row['entry_date'] = str(entry_date.date())
        row['entry_price'] = entry_price
        row['market_cap_at_signal'] = market_cap
        row['tier'] = tier
        row['benchmark'] = benchmark_ticker

        for period_name, days in periods.items():
            future = ticker_prices.index[(ticker_prices.index > entry_date)]
            benchmark_future = benchmark_prices.index[(benchmark_prices.index > entry_date)]
            spy_future = spy_prices.index[(spy_prices.index > entry_date)]

            if len(future) >= days and len(benchmark_future) >= days and len(spy_future) >= days:
                exit_date = future[days - 1]
                exit_price = ticker_prices[exit_date]
                benchmark_exit = benchmark_prices[benchmark_future[days - 1]]
                spy_exit = spy_prices[spy_future[days - 1]]

                stock_ret = (exit_price / entry_price - 1) * 100
                benchmark_ret = (benchmark_exit / benchmark_entry - 1) * 100
                spy_ret = (spy_exit / spy_entry - 1) * 100

                # Size-matched excess return (primary metric)
                excess = stock_ret - benchmark_ret

                # SPY excess return (for comparison)
                excess_spy = stock_ret - spy_ret

                row[period_name] = round(stock_ret, 2)
                row[f'excess_{period_name}'] = round(excess, 2)
                row[f'excess_{period_name}_spy'] = round(excess_spy, 2)
            else:
                row[period_name] = None
                row[f'excess_{period_name}'] = None
                row[f'excess_{period_name}_spy'] = None

        results.append(row)

    df = pd.DataFrame(results)
    logger.info(f"Computed forward returns for {len(df)} clusters")
    logger.info(f"Market cap sources: {tier_stats['historical']} historical, "
               f"{tier_stats['fallback']} fallback, {tier_stats['missing']} missing")
    if alignment_drops > 0:
        logger.warning(f"Dropped {alignment_drops} clusters due to benchmark misalignment (>7 days lag)")

    return df


def generate_summary(results_df):
    """Generate summary statistics with size-matched benchmarking."""
    lines = []
    lines.append("=" * 70)
    lines.append("INSIDER CLUSTER BACKTEST — SIZE-MATCHED BENCHMARKS")
    lines.append("=" * 70)
    lines.append(f"\nTotal clusters detected: {len(results_df)}")
    lines.append(f"Unique tickers: {results_df['ticker'].nunique()}")
    lines.append(f"Date range: {results_df['signal_date'].min()} to {results_df['signal_date'].max()}")

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

    # Benchmark usage
    if 'benchmark' in results_df.columns:
        benchmark_counts = results_df['benchmark'].value_counts()
        lines.append(f"\nBenchmark usage:")
        for benchmark, count in benchmark_counts.items():
            lines.append(f"  {benchmark}: {count} ({count/len(results_df)*100:.1f}%)")

    for period in ['ret_3m', 'ret_12m']:
        period_label = period.replace('ret_', '').upper()
        col = period
        excess_col = f'excess_{period}'
        excess_spy_col = f'excess_{period}_spy'

        valid = results_df.dropna(subset=[col, excess_col])
        valid_spy = results_df.dropna(subset=[col, excess_spy_col])
        if len(valid) == 0:
            continue

        lines.append(f"\n{'─' * 70}")
        lines.append(f"FORWARD {period_label} RETURNS (n={len(valid)})")
        lines.append(f"{'─' * 70}")
        lines.append(f"  Avg stock return:          {valid[col].mean():>7.2f}%")
        lines.append(f"  Median stock return:       {valid[col].median():>7.2f}%")
        lines.append(f"  Hit rate (>0%):            {(valid[col] > 0).mean()*100:>7.1f}%")

        lines.append(f"\n  SIZE-MATCHED BENCHMARK:")
        lines.append(f"    Avg excess return:       {valid[excess_col].mean():>7.2f}%")
        lines.append(f"    Median excess:           {valid[excess_col].median():>7.2f}%")
        lines.append(f"    Hit rate (>benchmark):   {(valid[excess_col] > 0).mean()*100:>7.1f}%")

        if len(valid_spy) > 0:
            lines.append(f"\n  SPY BENCHMARK (for comparison):")
            lines.append(f"    Avg excess return:       {valid_spy[excess_spy_col].mean():>7.2f}%")
            lines.append(f"    Median excess:           {valid_spy[excess_spy_col].median():>7.2f}%")
            lines.append(f"    Hit rate (>SPY):         {(valid_spy[excess_spy_col] > 0).mean()*100:>7.1f}%")

    # By tier
    lines.append(f"\n{'=' * 70}")
    lines.append("BY MARKET CAP TIER (12M EXCESS RETURN)")
    lines.append(f"{'=' * 70}")
    valid = results_df.dropna(subset=['excess_ret_12m', 'tier'])
    if len(valid) > 0:
        from signals.size_adjustment import TIER_ORDER
        valid['signal_date_ts'] = pd.to_datetime(valid['signal_date'])
        for tier in TIER_ORDER + ['unknown']:
            subset = valid[valid['tier'] == tier]
            if len(subset) == 0:
                continue

            benchmark = subset.iloc[0]['benchmark'] if 'benchmark' in subset.columns else '?'
            avg_excess = subset['excess_ret_12m'].mean()
            med_excess = subset['excess_ret_12m'].median()
            hit_rate = (subset['excess_ret_12m'] > 0).mean() * 100

            # Signal date range and pre-2021 count
            min_signal = subset['signal_date_ts'].min().strftime('%Y-%m-%d')
            max_signal = subset['signal_date_ts'].max().strftime('%Y-%m-%d')
            pre_2021 = (subset['signal_date_ts'] < '2021-01-01').sum()

            # SPY comparison
            if f'excess_ret_12m_spy' in subset.columns:
                spy_subset = subset.dropna(subset=['excess_ret_12m_spy'])
                if len(spy_subset) > 0:
                    avg_excess_spy = spy_subset['excess_ret_12m_spy'].mean()
                    hit_rate_spy = (spy_subset['excess_ret_12m_spy'] > 0).mean() * 100
                    lines.append(
                        f"  {tier:<8} ({benchmark}): avg {avg_excess:>7.2f}%  med {med_excess:>7.2f}%  "
                        f"hit {hit_rate:>4.0f}%  n={len(subset):>4}  pre-2021={pre_2021:>3}"
                    )
                    lines.append(
                        f"            dates: {min_signal} → {max_signal}  "
                        f"(vs SPY: avg {avg_excess_spy:>7.2f}%, hit {hit_rate_spy:>4.0f}%)"
                    )
                else:
                    lines.append(
                        f"  {tier:<8} ({benchmark}): avg {avg_excess:>7.2f}%  med {med_excess:>7.2f}%  "
                        f"hit {hit_rate:>4.0f}%  n={len(subset):>4}  pre-2021={pre_2021:>3}"
                    )
                    lines.append(f"            dates: {min_signal} → {max_signal}")
            else:
                lines.append(
                    f"  {tier:<8} ({benchmark}): avg {avg_excess:>7.2f}%  med {med_excess:>7.2f}%  "
                    f"hit {hit_rate:>4.0f}%  n={len(subset):>4}  pre-2021={pre_2021:>3}"
                )
                lines.append(f"            dates: {min_signal} → {max_signal}")

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
