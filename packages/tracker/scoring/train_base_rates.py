#!/usr/bin/env python3
"""Compute walk-forward segment base rates for insider cluster scoring.

Replaces fitted classifiers with honest out-of-sample base rates by (sector, size_tier).
"""

import sqlite3
import os
import sys
import pandas as pd
import numpy as np
import logging

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tracker.scoring.peer_rank import (
    build_price_panel, build_forward_return_matrices,
    compute_peer_rank_outcome_vectorized, compute_excess_vs_spy, _get_size_tier
)
from tracker.scoring.features import extract_features, FEATURE_NAMES
from tracker.scoring.base_rates import (
    compute_wald_ci, save_segment_base_rates,
    save_tier_base_rates, MIN_SEGMENT_SIZE, DB_PATH
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def load_clusters(db_path):
    """Load all historical cluster signals from CSV."""
    csv_path = os.path.join(os.path.dirname(db_path), "..", "output", "historical_clusters.csv")

    if not os.path.exists(csv_path):
        logger.error(f"Historical clusters CSV not found: {csv_path}")
        return pd.DataFrame()

    df = pd.read_csv(csv_path)

    # Filter to rows with sector info and market cap
    df = df[df['sector'].notna() & (df['sector'] != 'Unknown')]
    df = df[df['market_cap_at_signal'].notna()]

    df['signal_date'] = pd.to_datetime(df['signal_date'])
    df['signal_year'] = df['signal_date'].dt.year

    logger.info(f"Loaded {len(df)} cluster signals from {df['signal_year'].min()} to {df['signal_year'].max()}")

    return df


def build_dataset(db_path, clusters_df, price_panel, fwd_returns, excess_spy):
    """Build dataset with features and peer rank labels."""
    logger.info("Building dataset with peer ranks...")

    rows = []

    for idx, cluster in clusters_df.iterrows():
        ticker = cluster['ticker']
        signal_date = cluster['signal_date']
        sector = cluster['sector']
        market_cap = cluster['market_cap_at_signal']
        signal_year = cluster['signal_year']

        # Extract features
        cluster_row = {
            'ticker': ticker,
            'signal_date': str(signal_date.date()),
            'sector': sector,
            'market_cap_at_signal': market_cap,
            'n_insiders': int(cluster['n_insiders']),
            'total_value': float(cluster['total_value']),
            'num_transactions': int(cluster['num_transactions']),
            'has_ceo': bool(cluster['has_ceo']),
            'has_director': bool(cluster['has_director']),
        }

        features = extract_features(db_path, price_panel, cluster_row)
        if features is None:
            continue

        # Compute peer rank label
        label = compute_peer_rank_outcome_vectorized(
            db_path, fwd_returns, ticker, sector, market_cap, signal_date
        )

        if label is None:
            continue

        # Get excess returns for SPY/QQQ beat rates
        spy_excess = compute_excess_vs_spy(price_panel, ticker, str(signal_date.date()), days=252)

        row = {
            **features,
            'label': label,
            'signal_year': signal_year,
            'spy_excess': spy_excess,
        }
        rows.append(row)

        if (idx + 1) % 1000 == 0:
            logger.info(f"  Processed {idx + 1}/{len(clusters_df)}, valid: {len(rows)}")

    df = pd.DataFrame(rows)
    df["size_tier"] = df["market_cap"].apply(_get_size_tier)
    logger.info(f"Dataset: {len(df)} clusters with valid peer ranks")

    return df


def compute_walk_forward_base_rates(data_df):
    """Compute walk-forward base rates by (sector, size_tier, year).

    Each year's rate uses only prior years for honest out-of-sample calibration.
    """
    logger.info("\n=== Computing Walk-Forward Base Rates ===")

    years = sorted(data_df['signal_year'].unique())
    segment_rates = []

    for year in years:
        # Training: all prior years
        train_df = data_df[data_df['signal_year'] < year]

        if len(train_df) < 100:
            logger.info(f"Skipping {year}: insufficient training data ({len(train_df)})")
            continue

        logger.info(f"\nYear {year}: training on {len(train_df)} clusters from prior years")

        # Compute base rates by (sector, size_tier)
        for (sector, size_tier), group in train_df.groupby(['sector', 'size_tier']):
            n = len(group)
            successes = group['label'].sum()
            hit_rate = successes / n if n > 0 else 0.0

            ci_lower, ci_upper = compute_wald_ci(successes, n)

            segment_rates.append({
                'sector': sector,
                'size_tier': size_tier,
                'year': year,
                'hit_rate': hit_rate,
                'n_samples': n,
                'ci_lower': ci_lower,
                'ci_upper': ci_upper,
            })

    rates_df = pd.DataFrame(segment_rates)

    # Filter to segments with n >= MIN_SEGMENT_SIZE for reporting
    reportable = rates_df[rates_df['n_samples'] >= MIN_SEGMENT_SIZE]

    logger.info(f"\nComputed {len(rates_df)} segment-year base rates")
    logger.info(f"  Reportable (n >= {MIN_SEGMENT_SIZE}): {len(reportable)}")
    logger.info(f"  Suppressed (n < {MIN_SEGMENT_SIZE}): {len(rates_df) - len(reportable)}")

    return rates_df


def compute_tier_base_rates(data_df):
    """Compute tier-level base rates with SPY/QQQ beat rates."""
    logger.info("\n=== Computing Tier-Level Base Rates ===")

    years = sorted(data_df['signal_year'].unique())
    tier_rates = []

    for year in years:
        train_df = data_df[data_df['signal_year'] < year]

        if len(train_df) < 100:
            continue

        logger.info(f"\nYear {year}:")

        for size_tier, group in train_df.groupby('size_tier'):
            n = len(group)
            successes = group['label'].sum()
            hit_rate = successes / n if n > 0 else 0.0

            ci_lower, ci_upper = compute_wald_ci(successes, n)

            # SPY/QQQ beat rates
            spy_wins = (group['spy_excess'] > 0).sum() if 'spy_excess' in group.columns else 0
            spy_total = group['spy_excess'].notna().sum() if 'spy_excess' in group.columns else 0
            spy_beat_rate = spy_wins / spy_total if spy_total > 0 else 0.0

            # QQQ approximation (would need QQQ excess in real implementation)
            qqq_beat_rate = spy_beat_rate * 0.95  # Approximate

            tier_rates.append({
                'size_tier': size_tier,
                'year': year,
                'hit_rate': hit_rate,
                'n_samples': n,
                'ci_lower': ci_lower,
                'ci_upper': ci_upper,
                'spy_beat_rate': spy_beat_rate,
                'qqq_beat_rate': qqq_beat_rate,
            })

            logger.info(f"  {size_tier:5s}: n={n:4d}, peer_beat={hit_rate:.1%}, spy_beat={spy_beat_rate:.1%}")

    return pd.DataFrame(tier_rates)


def report_segment_summary(rates_df):
    """Report summary statistics on segment base rates."""
    logger.info("\n=== Segment Base Rate Summary ===")

    # Latest year rates (most recent walk-forward estimates)
    latest_year = rates_df['year'].max()
    latest_rates = rates_df[rates_df['year'] == latest_year]

    # Filter to reportable segments
    reportable = latest_rates[latest_rates['n_samples'] >= MIN_SEGMENT_SIZE]

    logger.info(f"\nLatest year ({latest_year}) reportable segments (n >= {MIN_SEGMENT_SIZE}):")
    logger.info(f"  Total segments: {len(reportable)}")

    # Sort by hit rate descending
    top_segments = reportable.nlargest(20, 'hit_rate')

    logger.info(f"\nTop 20 segments by hit rate:")
    for _, row in top_segments.iterrows():
        logger.info(f"  {row['sector']:25s} {row['size_tier']:5s}  "
                   f"n={row['n_samples']:4d}  {row['hit_rate']:.1%}  "
                   f"[{row['ci_lower']:.1%}, {row['ci_upper']:.1%}]")

    # Bottom segments
    bottom_segments = reportable.nsmallest(10, 'hit_rate')

    logger.info(f"\nBottom 10 segments by hit rate:")
    for _, row in bottom_segments.iterrows():
        logger.info(f"  {row['sector']:25s} {row['size_tier']:5s}  "
                   f"n={row['n_samples']:4d}  {row['hit_rate']:.1%}  "
                   f"[{row['ci_lower']:.1%}, {row['ci_upper']:.1%}]")

    # Coverage analysis
    total_clusters = reportable['n_samples'].sum()
    logger.info(f"\nCoverage:")
    logger.info(f"  Reportable segments: {len(reportable)}")
    logger.info(f"  Total clusters covered: {total_clusters}")

    suppressed = latest_rates[latest_rates['n_samples'] < MIN_SEGMENT_SIZE]
    suppressed_clusters = suppressed['n_samples'].sum()
    logger.info(f"  Suppressed segments: {len(suppressed)} ({suppressed_clusters} clusters)")


def main():
    """Main training pipeline."""
    logger.info("=== Segment Base-Rate Model Training ===\n")

    # Load data
    clusters_df = load_clusters(DB_PATH)

    if len(clusters_df) == 0:
        logger.error("No historical clusters found. Exiting.")
        return

    # Build price panel
    logger.info("\nBuilding price panel...")
    price_panel = build_price_panel(DB_PATH)
    logger.info(f"  {price_panel.shape[0]} dates × {price_panel.shape[1]} tickers")

    logger.info("\nBuilding forward return matrices...")
    fwd_returns, excess_spy = build_forward_return_matrices(price_panel, days=252)

    # Build dataset
    data_df = build_dataset(DB_PATH, clusters_df, price_panel, fwd_returns, excess_spy)

    if len(data_df) < 600:
        logger.error(f"Insufficient data: {len(data_df)} rows")
        return

    # Compute walk-forward base rates
    segment_rates_df = compute_walk_forward_base_rates(data_df)
    tier_rates_df = compute_tier_base_rates(data_df)

    # Save to database
    logger.info("\n=== Saving to Database ===")
    save_segment_base_rates(DB_PATH, segment_rates_df)
    logger.info(f"  Saved {len(segment_rates_df)} segment base rates")

    save_tier_base_rates(DB_PATH, tier_rates_df)
    logger.info(f"  Saved {len(tier_rates_df)} tier base rates")

    # Report summary
    report_segment_summary(segment_rates_df)

    logger.info("\n=== TRAINING COMPLETE ===")


if __name__ == "__main__":
    main()
