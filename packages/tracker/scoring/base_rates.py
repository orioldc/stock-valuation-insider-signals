"""Segment base-rate model for insider cluster conviction scoring.

Replaces fitted classifiers with walk-forward base rates by (sector, size_tier)
plus within-sector size adjustment on logmcap and logval only.

The real signal is size within sector: micro-caps beat peers 63.1%, large-caps 51.0%.
Most other features (buyback, technicals, cluster frequency) were sector proxies.
"""

import sqlite3
import os
import numpy as np
import json
from datetime import datetime
from scipy import stats

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))

MIN_SEGMENT_SIZE = 60  # Minimum samples to publish a segment base rate


def _ensure_base_rate_tables(conn):
    """Create base rate tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS segment_base_rates (
            sector TEXT NOT NULL,
            size_tier TEXT NOT NULL,
            year INTEGER NOT NULL,
            hit_rate REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            ci_lower REAL NOT NULL,
            ci_upper REAL NOT NULL,
            mean_peer_rank REAL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (sector, size_tier, year)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tier_base_rates (
            size_tier TEXT NOT NULL,
            year INTEGER NOT NULL,
            hit_rate REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            ci_lower REAL NOT NULL,
            ci_upper REAL NOT NULL,
            spy_beat_rate REAL,
            qqq_beat_rate REAL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (size_tier, year)
        )
    """)

    conn.commit()


def compute_wald_ci(successes, trials, alpha=0.05):
    """Compute Wald 95% confidence interval for a proportion.

    Args:
        successes: Number of successes
        trials: Number of trials
        alpha: Significance level (default 0.05 for 95% CI)

    Returns:
        (lower, upper) tuple
    """
    if trials == 0:
        return (0.0, 0.0)

    p = successes / trials
    z = stats.norm.ppf(1 - alpha / 2)
    se = np.sqrt(p * (1 - p) / trials)

    lower = max(0.0, p - z * se)
    upper = min(1.0, p + z * se)

    return (lower, upper)


def predict_size_adjustment(logmcap, logval, coef_logmcap, coef_logval, intercept):
    """Apply size adjustment to base rate.

    Args:
        logmcap: log10(market_cap)
        logval: log10(cluster_value)
        coef_logmcap: Coefficient for logmcap
        coef_logval: Coefficient for logval
        intercept: Intercept

    Returns:
        Adjustment factor (multiplicative, centered at 1.0)
    """
    # Logistic output
    z = coef_logmcap * logmcap + coef_logval * logval + intercept
    prob = 1.0 / (1.0 + np.exp(-z))

    # Convert to multiplicative adjustment centered at 1.0
    # prob=0.5 -> adj=1.0, prob=0.6 -> adj=1.2, prob=0.4 -> adj=0.8
    adjustment = prob / 0.5

    return adjustment


def save_segment_base_rates(db_path, rates_df):
    """Save segment base rates to database.

    Args:
        db_path: Database path
        rates_df: DataFrame with columns: sector, size_tier, year, hit_rate, n_samples,
                  ci_lower, ci_upper, mean_peer_rank
    """
    conn = sqlite3.connect(db_path)
    _ensure_base_rate_tables(conn)

    # Clear existing rates
    conn.execute("DELETE FROM segment_base_rates")

    for _, row in rates_df.iterrows():
        conn.execute("""
            INSERT INTO segment_base_rates
            (sector, size_tier, year, hit_rate, n_samples, ci_lower, ci_upper, mean_peer_rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row['sector'],
            row['size_tier'],
            row['year'],
            row['hit_rate'],
            row['n_samples'],
            row['ci_lower'],
            row['ci_upper'],
            row.get('mean_peer_rank'),
        ))

    conn.commit()
    conn.close()


def save_tier_base_rates(db_path, rates_df):
    """Save tier-level base rates to database.

    Args:
        db_path: Database path
        rates_df: DataFrame with columns: size_tier, year, hit_rate, n_samples,
                  ci_lower, ci_upper, spy_beat_rate, qqq_beat_rate
    """
    conn = sqlite3.connect(db_path)
    _ensure_base_rate_tables(conn)

    # Clear existing rates
    conn.execute("DELETE FROM tier_base_rates")

    for _, row in rates_df.iterrows():
        conn.execute("""
            INSERT INTO tier_base_rates
            (size_tier, year, hit_rate, n_samples, ci_lower, ci_upper, spy_beat_rate, qqq_beat_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row['size_tier'],
            row['year'],
            row['hit_rate'],
            row['n_samples'],
            row['ci_lower'],
            row['ci_upper'],
            row.get('spy_beat_rate'),
            row.get('qqq_beat_rate'),
        ))

    conn.commit()
    conn.close()


def get_segment_base_rate(db_path, sector, size_tier, as_of_year=None):
    """Get base rate for a segment, with fallback logic.

    Args:
        db_path: Database path
        sector: Sector name
        size_tier: Size tier name
        as_of_year: Optional year for walk-forward lookup (uses latest prior year)

    Returns:
        Dict with:
            - hit_rate: Base rate (or None if no data exists)
            - n_samples: Sample size
            - ci_lower, ci_upper: 95% CI bounds
            - level_used: 'segment', 'tier', or None
            - suppressed: True if not using requested segment level
            - reason: Explanation when hit_rate is None
    """
    conn = sqlite3.connect(db_path)

    # Try segment-level rate
    if as_of_year is not None:
        # Walk-forward: get latest year <= as_of_year
        query = """
            SELECT hit_rate, n_samples, ci_lower, ci_upper
            FROM segment_base_rates
            WHERE sector = ? AND size_tier = ? AND year <= ?
            ORDER BY year DESC
            LIMIT 1
        """
        row = conn.execute(query, (sector, size_tier, as_of_year)).fetchone()
    else:
        # Live scoring: get latest year
        query = """
            SELECT hit_rate, n_samples, ci_lower, ci_upper
            FROM segment_base_rates
            WHERE sector = ? AND size_tier = ?
            ORDER BY year DESC
            LIMIT 1
        """
        row = conn.execute(query, (sector, size_tier)).fetchone()

    if row and row[1] >= MIN_SEGMENT_SIZE:
        conn.close()
        return {
            'hit_rate': row[0],
            'n_samples': row[1],
            'ci_lower': row[2],
            'ci_upper': row[3],
            'level_used': 'segment',
            'suppressed': False,
            'reason': None,
        }

    # Fall back to tier-level rate
    if as_of_year is not None:
        tier_row = conn.execute("""
            SELECT hit_rate, n_samples, ci_lower, ci_upper
            FROM tier_base_rates
            WHERE size_tier = ? AND year <= ?
            ORDER BY year DESC
            LIMIT 1
        """, (size_tier, as_of_year)).fetchone()
    else:
        tier_row = conn.execute("""
            SELECT hit_rate, n_samples, ci_lower, ci_upper
            FROM tier_base_rates
            WHERE size_tier = ?
            ORDER BY year DESC
            LIMIT 1
        """, (size_tier,)).fetchone()

    if tier_row and tier_row[1] > 0:
        conn.close()
        return {
            'hit_rate': tier_row[0],
            'n_samples': tier_row[1],
            'ci_lower': tier_row[2],
            'ci_upper': tier_row[3],
            'level_used': 'tier',
            'suppressed': True,  # Not from requested segment
            'reason': f"segment n={row[1] if row else 0} < {MIN_SEGMENT_SIZE}" if row else "segment not found",
        }

    # No data available at any level
    conn.close()
    return {
        'hit_rate': None,
        'n_samples': 0,
        'ci_lower': None,
        'ci_upper': None,
        'level_used': None,
        'suppressed': True,
        'reason': f"no data for sector={sector}, tier={size_tier}",
    }


def get_tier_benchmark_rates(db_path, size_tier, as_of_year=None):
    """Get SPY/QQQ beat rates for a size tier.

    Args:
        db_path: Database path
        size_tier: Size tier name
        as_of_year: Optional year for walk-forward lookup

    Returns:
        Dict with spy_beat_rate, qqq_beat_rate, or None
    """
    conn = sqlite3.connect(db_path)

    if as_of_year is not None:
        row = conn.execute("""
            SELECT spy_beat_rate, qqq_beat_rate
            FROM tier_base_rates
            WHERE size_tier = ? AND year <= ?
            ORDER BY year DESC
            LIMIT 1
        """, (size_tier, as_of_year)).fetchone()
    else:
        row = conn.execute("""
            SELECT spy_beat_rate, qqq_beat_rate
            FROM tier_base_rates
            WHERE size_tier = ?
            ORDER BY year DESC
            LIMIT 1
        """, (size_tier,)).fetchone()

    conn.close()

    if not row:
        return None

    return {
        'spy_beat_rate': row[0],
        'qqq_beat_rate': row[1],
    }


def score_cluster(db_path, sector, size_tier, as_of_year=None):
    """Score a cluster using segment base rate.

    Args:
        db_path: Database path
        sector: Sector name
        size_tier: Size tier name
        as_of_year: Optional year for walk-forward scoring

    Returns:
        Dict with:
            - base_rate: Segment base rate (or None if no data)
            - ci_lower, ci_upper: 95% CI bounds (or None)
            - n_samples: Sample size for base rate
            - level_used: 'segment', 'tier', or None
            - suppressed: True if not from requested segment
            - reason: Explanation when base_rate is None
            - spy_beat_rate, qqq_beat_rate: Benchmark rates for tier (or None)
    """
    # Get segment base rate
    base_rate_info = get_segment_base_rate(db_path, sector, size_tier, as_of_year)

    # Get benchmark rates (tier-level only)
    if base_rate_info['level_used'] in ['segment', 'tier']:
        benchmark_rates = get_tier_benchmark_rates(db_path, size_tier, as_of_year)
    else:
        benchmark_rates = None

    result = {
        'base_rate': base_rate_info['hit_rate'],
        'ci_lower': base_rate_info['ci_lower'],
        'ci_upper': base_rate_info['ci_upper'],
        'n_samples': base_rate_info['n_samples'],
        'level_used': base_rate_info['level_used'],
        'suppressed': base_rate_info['suppressed'],
        'reason': base_rate_info['reason'],
    }

    if benchmark_rates:
        result['spy_beat_rate'] = benchmark_rates['spy_beat_rate']
        result['qqq_beat_rate'] = benchmark_rates['qqq_beat_rate']
    else:
        result['spy_beat_rate'] = None
        result['qqq_beat_rate'] = None

    return result
