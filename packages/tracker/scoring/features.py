"""Feature extraction for conviction scoring.

All features are strictly as-of the signal date (no look-ahead).
Includes split-adjusted buyback computation.
"""

import sqlite3
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))


def _load_split_events(conn, ticker):
    """Load split events for ticker as dict {date_str: ratio}."""
    rows = conn.execute("""
        SELECT date, ratio
        FROM split_events
        WHERE ticker = ?
        ORDER BY date
    """, (ticker,)).fetchall()
    return {date_str: ratio for date_str, ratio in rows}


def _compute_split_adjusted_buyback(conn, ticker, signal_date):
    """Compute split-adjusted share count change over trailing 365 days.

    Returns:
        (buyback_pct, buyback_missing) tuple where:
        - buyback_pct is negative for buybacks, positive for dilution, None if unusable
        - buyback_missing is 1 if unusable, 0 otherwise

    Rejects as unusable:
    - Delta < -25% (likely reporting-basis switch)
    - Delta > 200% (extreme dilution, likely reporting issue)
    - Share data staler than 400 days
    - Observations less than 200 days apart
    """
    signal_ts = pd.Timestamp(signal_date)
    lookback = signal_ts - timedelta(days=365)

    # Get shares as of signal_date and 365 days prior
    rows = conn.execute("""
        SELECT so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        WHERE c.ticker = ?
          AND so.date <= ?
        ORDER BY so.date DESC
        LIMIT 1
    """, (ticker, signal_date)).fetchall()

    if not rows:
        return (None, 1)

    current_date, current_shares = rows[0]

    rows_past = conn.execute("""
        SELECT so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        WHERE c.ticker = ?
          AND so.date <= ?
        ORDER BY so.date DESC
        LIMIT 1
    """, (ticker, str(lookback.date()))).fetchall()

    if not rows_past:
        return (None, 1)

    past_date, past_shares = rows_past[0]

    # Check recency
    current_ts = pd.Timestamp(current_date)
    past_ts = pd.Timestamp(past_date)

    days_to_signal = (signal_ts - current_ts).days
    if days_to_signal > 400:
        return (None, 1)

    days_between = (current_ts - past_ts).days
    if days_between < 200:
        return (None, 1)

    # Load splits and adjust past shares for any splits between past_date and current_date
    splits = _load_split_events(conn, ticker)
    cumulative_ratio = 1.0
    for split_date, ratio in splits.items():
        if past_date < split_date <= current_date:
            cumulative_ratio *= ratio

    adjusted_past_shares = past_shares * cumulative_ratio

    # Compute delta
    if adjusted_past_shares == 0:
        return (None, 1)

    delta_pct = (current_shares / adjusted_past_shares - 1.0) * 100

    # Reject implausible values
    if delta_pct < -25 or delta_pct > 200:
        return (None, 1)

    # Return negated so positive means shares retired (buyback)
    return (-delta_pct, 0)


def _compute_technical_features(price_panel, ticker, signal_date):
    """Compute technical features from price panel.

    Returns dict with:
        vol60: 60-day realized volatility (annualized)
        d52: Distance from 252-day high (percent)
        sma: SMA50 / SMA200 - 1
        mom3: 3-month momentum (total return)
        mom12: 12-month momentum (total return)
    """
    if ticker not in price_panel.columns:
        return {
            'vol60': None,
            'd52': None,
            'sma': None,
            'mom3': None,
            'mom12': None,
        }

    signal_ts = pd.Timestamp(signal_date)
    prices = price_panel[ticker].dropna()

    # Get prices up to signal_date
    historical = prices[prices.index <= signal_ts]
    if len(historical) < 252:
        return {
            'vol60': None,
            'd52': None,
            'sma': None,
            'mom3': None,
            'mom12': None,
        }

    latest_price = historical.iloc[-1]

    # Volatility (60 days, annualized)
    if len(historical) >= 60:
        returns_60 = historical.iloc[-60:].pct_change().dropna()
        vol60 = returns_60.std() * np.sqrt(252)
    else:
        vol60 = None

    # Distance from 52-week high
    high_252 = historical.iloc[-252:].max()
    d52 = (latest_price / high_252 - 1.0) * 100

    # SMA ratio
    if len(historical) >= 200:
        sma50 = historical.iloc[-50:].mean()
        sma200 = historical.iloc[-200:].mean()
        sma = sma50 / sma200 - 1.0
    else:
        sma = None

    # Momentum
    if len(historical) >= 63:
        mom3 = (latest_price / historical.iloc[-63] - 1.0)
    else:
        mom3 = None

    if len(historical) >= 252:
        mom12 = (latest_price / historical.iloc[-252] - 1.0)
    else:
        mom12 = None

    return {
        'vol60': vol60,
        'd52': d52,
        'sma': sma,
        'mom3': mom3,
        'mom12': mom12,
    }


def _compute_historical_cluster_count(conn, ticker, signal_date):
    """Count prior resolved clusters for this ticker.

    Only counts clusters that had already resolved (signal + 365d <= this signal date).
    This is strictly causal - no look-ahead.

    Returns:
        log1p(count)
    """
    signal_ts = pd.Timestamp(signal_date)
    cutoff = (signal_ts - timedelta(days=365)).strftime('%Y-%m-%d')

    count = conn.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE company_id = (SELECT id FROM companies WHERE ticker = ?)
          AND signal_type = 'composite'
          AND signal_date <= ?
    """, (ticker, cutoff)).fetchone()[0]

    return np.log1p(count)


def extract_features(db_path, price_panel, cluster_row):
    """Extract features for a cluster signal.

    Args:
        db_path: Database path
        price_panel: Pre-built price panel (date x ticker)
        cluster_row: Dict or Series with keys:
            - ticker
            - signal_date
            - n_insiders
            - total_value
            - num_transactions
            - has_ceo
            - has_director
            - market_cap_at_signal (optional, will fetch if missing)
            - sector (optional, will fetch if missing)

    Returns:
        Dict of features, or None if required data is missing
    """
    ticker = cluster_row['ticker']
    signal_date = cluster_row['signal_date']

    conn = sqlite3.connect(db_path)

    # Get sector and market_cap if not provided
    sector = cluster_row.get('sector')
    market_cap = cluster_row.get('market_cap_at_signal')

    if sector is None or market_cap is None:
        row = conn.execute("""
            SELECT sector, market_cap
            FROM companies
            WHERE ticker = ?
        """, (ticker,)).fetchone()

        if not row:
            conn.close()
            return None

        if sector is None:
            sector = row[0]
        if market_cap is None:
            market_cap = row[1]

    if market_cap is None or sector is None:
        conn.close()
        return None

    # Cluster features
    ni = min(cluster_row['n_insiders'], 8)  # Cap at 8
    logval = np.log10(max(cluster_row['total_value'], 1.0))
    ntx = min(cluster_row['num_transactions'], 20)  # Cap at 20
    ceo = 1 if cluster_row.get('has_ceo', False) else 0
    dirf = 1 if cluster_row.get('has_director', False) else 0

    # Market cap
    logmcap = np.log10(market_cap)

    # Buyback
    buyback, bb_missing = _compute_split_adjusted_buyback(conn, ticker, signal_date)

    # Historical cluster count
    hist_n = _compute_historical_cluster_count(conn, ticker, signal_date)

    conn.close()

    # Technical features
    tech = _compute_technical_features(price_panel, ticker, signal_date)

    features = {
        'ticker': ticker,
        'signal_date': signal_date,
        'sector': sector,
        'market_cap': market_cap,
        'logmcap': logmcap,
        'ni': ni,
        'logval': logval,
        'ntx': ntx,
        'ceo': ceo,
        'dirf': dirf,
        'buyback': buyback if buyback is not None else 0.0,
        'bb_missing': bb_missing,
        'hist_n': hist_n,
        'vol60': tech['vol60'] if tech['vol60'] is not None else 0.0,
        'd52': tech['d52'] if tech['d52'] is not None else 0.0,
        'sma': tech['sma'] if tech['sma'] is not None else 0.0,
        'mom3': tech['mom3'] if tech['mom3'] is not None else 0.0,
        'mom12': tech['mom12'] if tech['mom12'] is not None else 0.0,
    }

    # Mark missing technicals
    features['tech_missing'] = 1 if tech['vol60'] is None else 0

    return features


def feature_vector(features, feature_names):
    """Convert feature dict to numpy array in standard order.

    Args:
        features: Dict of features
        feature_names: List of feature names in order

    Returns:
        Numpy array of shape (n_features,)
    """
    return np.array([features.get(name, 0.0) for name in feature_names])


# Standard feature set for model
FEATURE_NAMES = [
    'logmcap', 'vol60', 'd52', 'sma', 'mom3', 'mom12',
    'ni', 'logval', 'ntx', 'ceo', 'dirf',
    'buyback', 'bb_missing', 'hist_n', 'tech_missing'
]
