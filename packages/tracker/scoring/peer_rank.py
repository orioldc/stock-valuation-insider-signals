"""Peer-relative ranking for forward returns.

Computes percentile rank of a ticker's forward return within its peer pool:
same sector AND same size tier.

IMPORTANT APPROXIMATION: Peer pool membership is determined by CURRENT market cap
from companies.market_cap, not historical market cap at signal_date. There is no
historical market-cap series in the DB (companies.market_cap_asof records when we
computed the value, not when it was true). This approximation is acceptable because
clusters and peers are graded on the same basis, and it matches the validated
approach that produced the reported performance numbers.
"""

import sqlite3
import os
import pandas as pd
import numpy as np

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))

# Size tiers (in millions)
SIZE_TIERS = [
    ("micro", 0, 300),
    ("small", 300, 2000),
    ("mid", 2000, 10000),
    ("large", 10000, 200000),
    ("mega", 200000, float('inf'))
]

MIN_PEER_POOL_SIZE = 20  # Minimum peers with valid returns to compute rank


def _get_size_tier(market_cap):
    """Map market cap to tier name."""
    if market_cap is None:
        return None
    mcap_millions = market_cap / 1e6
    for tier_name, lower, upper in SIZE_TIERS:
        if lower <= mcap_millions < upper:
            return tier_name
    return None


def build_price_panel(db_path=None):
    """Build a date x ticker price panel for efficient return calculation.

    Returns:
        DataFrame with dates as index, tickers as columns, prices as values
    """
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)

    # Load all prices
    df = pd.read_sql_query("""
        SELECT ticker, date, close
        FROM prices
        ORDER BY ticker, date
    """, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])

    # Pivot to date x ticker panel
    panel = df.pivot(index='date', columns='ticker', values='close')
    panel = panel.sort_index()

    return panel


def build_forward_return_matrices(price_panel, days=252):
    """Build forward return and excess return matrices (vectorized).

    This is the fast path for peer ranking across many clusters.

    Args:
        price_panel: DataFrame with dates as index, tickers as columns
        days: Forward return horizon (default 252 trading days)

    Returns:
        (fwd_returns, excess_spy) tuple where:
        - fwd_returns: DataFrame, same shape as price_panel, forward returns in percent
        - excess_spy: DataFrame, same shape, excess vs SPY in percent
    """
    # Forward returns: (W.shift(-252)/W - 1) * 100
    fwd = (price_panel.shift(-days) / price_panel - 1.0) * 100

    # Excess vs SPY
    if 'SPY' in fwd.columns:
        excess = fwd.sub(fwd['SPY'], axis=0)
    else:
        # No SPY data, use raw returns
        excess = fwd

    return fwd, excess


def get_peer_pool(db_path, sector, size_tier):
    """Get all tickers in the same sector and size tier.

    Uses CURRENT market cap from companies.market_cap (approximation documented
    in module docstring).

    Args:
        db_path: Database path
        sector: Sector name
        size_tier: Size tier name (micro, small, mid, large, mega)

    Returns:
        List of ticker symbols
    """
    if size_tier is None:
        return []

    tier_lower, tier_upper = next((lower, upper) for name, lower, upper in SIZE_TIERS if name == size_tier)

    conn = sqlite3.connect(db_path)

    query = """
        SELECT ticker
        FROM companies
        WHERE sector = ?
          AND market_cap IS NOT NULL
          AND market_cap >= ? * 1e6
          AND market_cap < ? * 1e6
    """

    df = pd.read_sql_query(query, conn, params=(sector, tier_lower, tier_upper))
    conn.close()

    return df['ticker'].tolist()


def compute_peer_rank_vectorized(db_path, fwd_returns, ticker, sector, market_cap, signal_date):
    """Compute percentile rank using vectorized forward returns (fast path).

    Args:
        db_path: Database path
        fwd_returns: Forward return matrix from build_forward_return_matrices
        ticker: Ticker to rank
        sector: Ticker's sector
        market_cap: Ticker's market cap (dollars)
        signal_date: Signal date (string YYYY-MM-DD or Timestamp)

    Returns:
        Percentile rank (0-100), or None if rank cannot be computed
    """
    size_tier = _get_size_tier(market_cap)
    if size_tier is None:
        return None

    # Get peer pool
    peers = get_peer_pool(db_path, sector, size_tier)
    if len(peers) < MIN_PEER_POOL_SIZE:
        return None

    # Find signal date in forward return matrix
    signal_ts = pd.Timestamp(signal_date)

    # Find first date >= signal_date
    valid_dates = fwd_returns.index[fwd_returns.index >= signal_ts]
    if len(valid_dates) == 0:
        return None

    entry_date = valid_dates[0]

    # Get peer columns that exist in matrix
    peer_cols = [p for p in peers if p in fwd_returns.columns]
    if len(peer_cols) < MIN_PEER_POOL_SIZE:
        return None

    # Get row of forward returns for this date
    row = fwd_returns.loc[entry_date, peer_cols].dropna()

    if len(row) < MIN_PEER_POOL_SIZE:
        return None

    # Check if target ticker has a valid return
    if ticker not in row.index:
        return None

    target_return = row[ticker]

    # Compute percentile rank
    rank = (row < target_return).sum() / len(row) * 100

    return rank


def compute_peer_rank_outcome_vectorized(db_path, fwd_returns, ticker, sector, market_cap, signal_date):
    """Compute binary outcome: 1 if above-median peer rank, 0 otherwise.

    Vectorized version for fast batch processing.

    Returns:
        1 if percentile rank > 50, 0 if <= 50, None if rank cannot be computed
    """
    rank = compute_peer_rank_vectorized(db_path, fwd_returns, ticker, sector, market_cap, signal_date)
    if rank is None:
        return None

    return 1 if rank > 50 else 0


# Legacy non-vectorized functions (kept for backward compatibility)

def compute_forward_return(price_panel, ticker, signal_date, days=252):
    """Compute forward return for a ticker from signal_date.

    DEPRECATED: Use build_forward_return_matrices + vectorized lookup instead.

    Args:
        price_panel: DataFrame with dates as index, tickers as columns
        ticker: Ticker symbol
        signal_date: Signal date (string YYYY-MM-DD or datetime)
        days: Trading days forward (default 252 = 1 year)

    Returns:
        Forward return as decimal (e.g., 0.15 for 15%), or None if insufficient data
    """
    if ticker not in price_panel.columns:
        return None

    signal_ts = pd.Timestamp(signal_date)
    ticker_prices = price_panel[ticker].dropna()

    # Find entry date (first trading day on or after signal)
    valid_dates = ticker_prices.index[ticker_prices.index >= signal_ts]
    if len(valid_dates) == 0:
        return None

    entry_date = valid_dates[0]
    entry_price = ticker_prices[entry_date]

    # Find exit date (exactly 'days' trading days later)
    future_dates = ticker_prices.index[ticker_prices.index > entry_date]
    if len(future_dates) < days:
        return None

    exit_date = future_dates[days - 1]
    exit_price = ticker_prices[exit_date]

    return (exit_price / entry_price) - 1.0


def compute_excess_vs_spy(price_panel, ticker, signal_date, days=252):
    """Compute forward return excess vs SPY.

    DEPRECATED: Use build_forward_return_matrices instead.

    Returns:
        Excess return as decimal, or None if insufficient data
    """
    stock_ret = compute_forward_return(price_panel, ticker, signal_date, days)
    spy_ret = compute_forward_return(price_panel, 'SPY', signal_date, days)

    if stock_ret is None or spy_ret is None:
        return None

    return stock_ret - spy_ret


def compute_peer_rank(db_path, price_panel, ticker, sector, market_cap, signal_date, days=252):
    """Compute percentile rank of ticker's forward return within peer pool.

    DEPRECATED: Use build_forward_return_matrices + compute_peer_rank_vectorized instead.

    This is the slow path that loops over peers. Use only for one-off scoring.

    Args:
        db_path: Database path
        price_panel: Pre-built price panel (date x ticker)
        ticker: Ticker to rank
        sector: Ticker's sector
        market_cap: Ticker's market cap (dollars)
        signal_date: Signal date (string YYYY-MM-DD)
        days: Forward return horizon (default 252 trading days)

    Returns:
        Percentile rank (0-100), or None if rank cannot be computed
    """
    # Build forward returns on the fly (slow for batch)
    fwd_returns, _ = build_forward_return_matrices(price_panel, days=days)

    return compute_peer_rank_vectorized(db_path, fwd_returns, ticker, sector, market_cap, signal_date)


def compute_peer_rank_outcome(db_path, price_panel, ticker, sector, market_cap, signal_date, days=252):
    """Compute binary outcome: 1 if above-median peer rank, 0 otherwise.

    DEPRECATED: Use build_forward_return_matrices + compute_peer_rank_outcome_vectorized instead.

    Returns:
        1 if percentile rank > 50, 0 if <= 50, None if rank cannot be computed
    """
    rank = compute_peer_rank(db_path, price_panel, ticker, sector, market_cap, signal_date, days)
    if rank is None:
        return None

    return 1 if rank > 50 else 0
