#!/usr/bin/env python3
"""
Data quality contract — invariants that define a valid release artifact.

Each check has:
  - id: Stable identifier for selecting/referencing the check
  - description: Human-readable explanation
  - severity: CRITICAL (blocks release) or WARN (advisory only)
  - check_fn: Function that returns a structured result dict

Check function signature:
    def check_xyz(conn) -> dict
        Returns: {
            'passed': bool,
            'measured': dict,  # Actual values measured
            'expected': dict,  # Thresholds/criteria
            'details': str     # Human-readable explanation (optional)
        }

Adding a new check:
    1. Define check function following the signature above
    2. Add entry to CHECKS list with id, description, severity, and check_fn
    3. That's it — validate.py will automatically discover and run it
"""

import sqlite3
import logging
import os
import urllib.request
import json
import csv
import gzip
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


# ── Severity levels ──

CRITICAL = "CRITICAL"  # Blocks release
WARN = "WARN"          # Advisory only


# ── Runtime path resolution (matches actual usage patterns) ──

def _resolve_insider_frozen_path():
    """Resolve insider_frozen.json.gz path at call time.

    Precedence: env override → data/ (runtime) → packages/valuation/data/ (committed fallback)
    Matches packages/valuation/data/insider_signals.py resolution logic.
    """
    if "INSIDER_FROZEN_DATA" in os.environ:
        return Path(os.environ["INSIDER_FROZEN_DATA"])

    # Determine repo root from DB path (traverse up from any location)
    # This is fragile but matches the runtime pattern
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[2]  # packages/tracker/pipeline/ → repo root

    data_path = repo_root / "data" / "insider_frozen.json.gz"
    if data_path.exists():
        return data_path

    # Committed fallback
    return repo_root / "packages" / "valuation" / "data" / "insider_frozen.json.gz"


def _resolve_historical_clusters_path():
    """Resolve historical_clusters.csv path at call time.

    Precedence: env override → data/ (runtime) → packages/tracker/output/ (local pipeline)
    Matches packages/tracker/signals/historical_hit_rate.py resolution logic.
    """
    if "HISTORICAL_CSV_PATH" in os.environ:
        return Path(os.environ["HISTORICAL_CSV_PATH"])

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[2]

    data_path = repo_root / "data" / "historical_clusters.csv"
    if data_path.exists():
        return data_path

    return repo_root / "packages" / "tracker" / "output" / "historical_clusters.csv"


def _resolve_latest_signals_path():
    """Resolve latest_signals.csv path at call time.

    Precedence: data/ (runtime) → packages/tracker/output/ (local pipeline)
    Matches packages/tracker/refresh.py OUTPUT_DIR pattern.
    """
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[2]

    data_path = repo_root / "data" / "latest_signals.csv"
    if data_path.exists():
        return data_path

    return repo_root / "packages" / "tracker" / "output" / "latest_signals.csv"


# ── Check implementations ──

def check_insider_transactions_freshness(conn):
    """Newest insider transaction is within 7 days of today.

    Threshold type: Target (currently passing, guards against ingestion staleness)
    """
    cur = conn.cursor()

    # Get the newest valid transaction date (exclude malformed dates)
    cur.execute("""
        SELECT MAX(transaction_date)
        FROM insider_transactions
        WHERE transaction_date LIKE '2___-__-__'
          AND transaction_date <= date('now', '+2 days')
    """)
    newest = cur.fetchone()[0]

    if not newest:
        return {
            'passed': False,
            'measured': {'newest_transaction_date': None},
            'expected': {'max_age_days': 7},
            'details': 'No valid transaction dates found'
        }

    newest_date = datetime.strptime(newest, '%Y-%m-%d')
    age_days = (datetime.now() - newest_date).days

    return {
        'passed': age_days <= 7,
        'measured': {
            'newest_transaction_date': newest,
            'age_days': age_days
        },
        'expected': {'max_age_days': 7, 'threshold_type': 'target'}
    }


def check_insider_transactions_monthly_volume(conn):
    """
    Most recent COMPLETE month's transaction volume is at least 70% of the
    median for that calendar month across the prior 3 years.

    Skips the current partial month since Form 4s have a 2-day filing lag.

    Threshold type: Target (currently passing, guards against ingestion degradation)
    """
    cur = conn.cursor()

    # Get the most recent complete month
    now = datetime.now()
    if now.day < 28:
        last_complete_month = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    else:
        last_complete_month = (now.replace(day=1) - timedelta(days=1)).replace(day=1)

    target_month = last_complete_month.strftime('%Y-%m')
    calendar_month = last_complete_month.month

    # Get transaction count for the target month (only valid dates)
    cur.execute("""
        SELECT COUNT(*)
        FROM insider_transactions
        WHERE strftime('%Y-%m', transaction_date) = ?
          AND transaction_date LIKE '2___-__-__'
    """, (target_month,))
    target_count = cur.fetchone()[0]

    # Get counts for the same calendar month in the prior 3 years
    historical_counts = []
    for year_offset in range(1, 4):
        prior_year = last_complete_month.year - year_offset
        prior_month = f"{prior_year}-{calendar_month:02d}"
        cur.execute("""
            SELECT COUNT(*)
            FROM insider_transactions
            WHERE strftime('%Y-%m', transaction_date) = ?
              AND transaction_date LIKE '2___-__-__'
        """, (prior_month,))
        count = cur.fetchone()[0]
        if count > 0:
            historical_counts.append(count)

    if not historical_counts:
        return {
            'passed': False,
            'measured': {
                'target_month': target_month,
                'target_count': target_count,
                'historical_median': None
            },
            'expected': {'min_pct_of_median': 70, 'threshold_type': 'target'},
            'details': 'No historical data for this calendar month'
        }

    # Calculate median
    historical_counts.sort()
    n = len(historical_counts)
    if n % 2 == 0:
        median = (historical_counts[n//2 - 1] + historical_counts[n//2]) / 2.0
    else:
        median = float(historical_counts[n//2])

    min_expected = median * 0.70
    passed = target_count >= min_expected

    return {
        'passed': passed,
        'measured': {
            'target_month': target_month,
            'target_count': target_count,
            'historical_median': int(median),
            'pct_of_median': round((target_count / median * 100) if median > 0 else 0, 1)
        },
        'expected': {
            'min_pct_of_median': 70,
            'min_count': int(min_expected),
            'threshold_type': 'target'
        }
    }


def check_share_buyback_coverage(conn):
    """
    Companies with >=5 quarters of shares_outstanding, as a fraction of
    companies not in shares_backfill_failures.

    Threshold: 85% (current actual 91.7%)
    Threshold type: Regression guard (set 5% below current to catch degradation)
    """
    cur = conn.cursor()

    # Total eligible companies (excluding known failures)
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE ticker != 'NONE'
          AND (cik IS NULL OR cik NOT IN (SELECT cik FROM shares_backfill_failures))
    """)
    total_eligible = cur.fetchone()[0]

    # Companies with >=5 quarters of data
    cur.execute("""
        SELECT COUNT(DISTINCT company_id)
        FROM (
            SELECT company_id, COUNT(DISTINCT strftime('%Y-%m', date)) as quarters
            FROM shares_outstanding
            WHERE company_id IN (
                SELECT id FROM companies
                WHERE ticker != 'NONE'
                  AND (cik IS NULL OR cik NOT IN (SELECT cik FROM shares_backfill_failures))
            )
            GROUP BY company_id
            HAVING quarters >= 5
        )
    """)
    with_coverage = cur.fetchone()[0]

    # Get failure count for context
    cur.execute("SELECT COUNT(*) FROM shares_backfill_failures")
    failures = cur.fetchone()[0]

    coverage_pct = (with_coverage / total_eligible * 100) if total_eligible > 0 else 0
    min_pct = 85.0

    return {
        'passed': coverage_pct >= min_pct,
        'measured': {
            'companies_with_5q': with_coverage,
            'companies_eligible': total_eligible,
            'known_failures': failures,
            'coverage_pct': round(coverage_pct, 1)
        },
        'expected': {
            'min_coverage_pct': min_pct,
            'threshold_type': 'regression_guard',
            'current_actual': 91.7
        }
    }


def check_price_coverage(conn):
    """
    Companies with a price within the last 5 trading days, as a fraction of
    those not in price_backfill_failures AND not inactive.

    Three states partition the universe:
      - Covered: has price within 5 trading days
      - Known-failed: in price_backfill_failures (no data at all)
      - Inactive: has price history but none within 3 months (delisted, complete history)

    Inactive companies are excluded from the denominator — they have complete historical
    data and cannot have a current price (delisted). Counting them as "missing coverage"
    makes the check unpassable no matter how healthy the pipeline is.

    Inactive threshold: 3 months (>90 calendar days). The 1-3 month bucket is ambiguous
    (recently delisted vs thin trading) and reported separately as a WARN.

    Threshold: 95%
    Threshold type: Target (correct denominator, not softened threshold)
    """
    cur = conn.cursor()

    # Get the most recent price date in the DB
    cur.execute("SELECT MAX(date) FROM prices")
    max_date = cur.fetchone()[0]

    if not max_date:
        return {
            'passed': False,
            'measured': {},
            'expected': {'min_coverage_pct': 95, 'max_age_trading_days': 5, 'threshold_type': 'target'},
            'details': 'No price data in database'
        }

    # Calculate cutoffs
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d')
    recent_cutoff = (max_date_dt - timedelta(days=7)).strftime('%Y-%m-%d')  # 5 trading days ≈ 7 calendar
    inactive_cutoff = (max_date_dt - timedelta(days=90)).strftime('%Y-%m-%d')  # 3 months
    ambiguous_start = (max_date_dt - timedelta(days=30)).strftime('%Y-%m-%d')  # 1 month

    # Total universe (excluding 'NONE')
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE ticker != 'NONE'
    """)
    total_universe = cur.fetchone()[0]

    # Known failures
    cur.execute("""
        SELECT COUNT(*)
        FROM price_backfill_failures
        WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
    """)
    known_failures = cur.fetchone()[0]

    # Inactive: has price history but last price >3 months ago
    # Derive on-the-fly from MAX(date) per ticker
    cur.execute("""
        SELECT COUNT(DISTINCT ticker)
        FROM (
            SELECT ticker, MAX(date) as last_date
            FROM prices
            WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
            GROUP BY ticker
        )
        WHERE last_date < ?
    """, (inactive_cutoff,))
    inactive = cur.fetchone()[0]

    # Ambiguous: last price between 1-3 months ago (recently delisted vs thin trading)
    cur.execute("""
        SELECT COUNT(DISTINCT ticker)
        FROM (
            SELECT ticker, MAX(date) as last_date
            FROM prices
            WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
            GROUP BY ticker
        )
        WHERE last_date >= ? AND last_date < ?
    """, (inactive_cutoff, ambiguous_start))
    ambiguous = cur.fetchone()[0]

    # Eligible = universe - known_failures - inactive
    eligible = total_universe - known_failures - inactive

    # Covered: has price within 5 trading days
    cur.execute("""
        SELECT COUNT(DISTINCT ticker)
        FROM prices
        WHERE date >= ?
          AND ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
    """, (recent_cutoff,))
    covered = cur.fetchone()[0]

    # Coverage percentage
    coverage_pct = (covered / eligible * 100) if eligible > 0 else 0
    min_pct = 95.0

    # Get inactive count from provenance for growth tracking
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from provenance import get_pipeline_meta
        meta = get_pipeline_meta(db_path)
        previous_inactive = int(meta.get('inactive_count', '0')) if meta else 0
    except (ImportError, ValueError):
        previous_inactive = None

    return {
        'passed': coverage_pct >= min_pct,
        'measured': {
            'total_universe': total_universe,
            'covered': covered,
            'known_failures': known_failures,
            'inactive': inactive,
            'ambiguous_1_3mo': ambiguous,
            'eligible': eligible,
            'coverage_pct': round(coverage_pct, 1),
            'cutoff_recent': recent_cutoff,
            'cutoff_inactive': inactive_cutoff,
            'most_recent_price_date': max_date,
            'previous_inactive': previous_inactive,
            'inactive_growth': inactive - previous_inactive if previous_inactive is not None else None
        },
        'expected': {
            'min_coverage_pct': min_pct,
            'max_age_trading_days': 5,
            'inactive_threshold_days': 90,
            'threshold_type': 'target',
            'note': 'Inactive (>90d) excluded from denominator; has complete history but delisted'
        }
    }


def check_ambiguous_price_age(conn):
    """
    Companies with last price between 1-3 months ago (ambiguous state).

    This population is genuinely ambiguous: recently delisted (inactive) or merely
    thin trading (should still be attempted). Reporting it separately as WARN is
    the honest treatment.

    A sudden jump signals fetching broke for live tickers and is being misfiled
    as delisting — precisely the failure this contract exists to catch.

    Threshold: ≤300 (informational, no hard limit)
    Threshold type: Informational (monitors for spikes, not absolute threshold)
    """
    cur = conn.cursor()

    # Get the most recent price date in the DB
    cur.execute("SELECT MAX(date) FROM prices")
    max_date = cur.fetchone()[0]

    if not max_date:
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'informational'},
            'details': 'No price data in database'
        }

    # Ambiguous bucket: 1-3 months ago
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d')
    ambiguous_start = (max_date_dt - timedelta(days=30)).strftime('%Y-%m-%d')
    ambiguous_end = (max_date_dt - timedelta(days=90)).strftime('%Y-%m-%d')

    cur.execute("""
        SELECT ticker, last_date, CAST((JULIANDAY(?) - JULIANDAY(last_date)) AS INTEGER) as age_days
        FROM (
            SELECT ticker, MAX(date) as last_date
            FROM prices
            WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
            GROUP BY ticker
        )
        WHERE last_date >= ? AND last_date < ?
        ORDER BY age_days DESC
    """, (max_date, ambiguous_end, ambiguous_start))
    ambiguous_tickers = cur.fetchall()

    count = len(ambiguous_tickers)

    # Get previous count from provenance
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from provenance import get_pipeline_meta
        meta = get_pipeline_meta(db_path)
        previous_ambiguous = int(meta.get('ambiguous_count', '0')) if meta else 0
    except (ImportError, ValueError):
        previous_ambiguous = None

    growth = count - previous_ambiguous if previous_ambiguous is not None else None

    return {
        'passed': True,  # Informational only, always passes
        'measured': {
            'ambiguous_count': count,
            'previous_count': previous_ambiguous,
            'growth': growth,
            'window': f"{ambiguous_end} to {ambiguous_start}",
            'examples': [f"{t} (last {d}, {a}d ago)" for t, d, a in ambiguous_tickers[:10]]
        },
        'expected': {
            'threshold_type': 'informational',
            'note': 'Recently delisted vs thin trading — ambiguous. Monitor for spikes.'
        }
    }


def check_inactive_price_growth(conn):
    """
    Inactive population (>3 months stale) grows materially between runs.

    A healthy universe delists a trickle; a spike means fetching broke for live
    securities and is being misfiled as delisting. This is precisely the failure
    this contract exists to catch, and it would otherwise look like passing.

    Threshold: ≤50 growth per run (WARN if exceeded)
    Threshold type: Regression guard (detects pipeline breakage)
    """
    cur = conn.cursor()

    # Get the most recent price date in the DB
    cur.execute("SELECT MAX(date) FROM prices")
    max_date = cur.fetchone()[0]

    if not max_date:
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'regression_guard'},
            'details': 'No price data in database'
        }

    # Inactive: >3 months
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d')
    inactive_cutoff = (max_date_dt - timedelta(days=90)).strftime('%Y-%m-%d')

    cur.execute("""
        SELECT COUNT(DISTINCT ticker)
        FROM (
            SELECT ticker, MAX(date) as last_date
            FROM prices
            WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
            GROUP BY ticker
        )
        WHERE last_date < ?
    """, (inactive_cutoff,))
    current_inactive = cur.fetchone()[0]

    # Get previous count from provenance
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from provenance import get_pipeline_meta, set_pipeline_meta
        meta = get_pipeline_meta(db_path)
        previous_inactive = int(meta.get('inactive_count', '0')) if meta else 0

        # Update current count for next run (read-only mode prevents this, but try anyway)
        try:
            set_pipeline_meta(db_path, 'inactive_count', str(current_inactive))
        except:
            pass  # Read-only mode, expected
    except (ImportError, ValueError):
        previous_inactive = None

    if previous_inactive is None:
        return {
            'passed': True,
            'measured': {
                'current_inactive': current_inactive,
                'previous_inactive': None,
                'growth': None
            },
            'expected': {
                'max_growth': 50,
                'threshold_type': 'regression_guard',
                'note': 'First run; recording baseline'
            }
        }

    growth = current_inactive - previous_inactive
    max_growth = 50

    return {
        'passed': growth <= max_growth,
        'measured': {
            'current_inactive': current_inactive,
            'previous_inactive': previous_inactive,
            'growth': growth
        },
        'expected': {
            'max_growth': max_growth,
            'threshold_type': 'regression_guard',
            'note': 'Spike in inactive count signals fetching broke for live tickers'
        }
    }


def check_benchmark_etf_coverage(conn):
    """
    Required benchmark ETFs (SPY, IWM, MDY) are present and continuous from the
    earliest signal_date to present, with no interior gaps in consecutive trading days.
    Optional benchmarks (QQQ, ^IXIC, URTH, ACWI) are checked but reported as warnings.

    A benchmark with an interior gap corrupts every excess-return calculation. We scope
    this to the backtest window (earliest signal onward) rather than the full price history
    because different benchmarks launched at different times (SPY 1993, MDY 1995, IWM 2000,
    QQQ 1999, ^IXIC 1971, ACWI 2008, URTH 2012).

    Trailing-edge tolerance: ETFs are fetched by different code paths at different moments,
    so their last-trading-day may differ by a few days (fetch timing, not data loss). We
    allow up to 5 trading days (~7 calendar days) of trailing lag, but zero tolerance for
    interior gaps, which is the case that actually corrupts excess returns.

    Threshold type: Target (zero tolerance for interior gaps, tolerance for trailing lag)
    """
    cur = conn.cursor()

    # Get the backtest window: earliest signal_date to most recent price date
    cur.execute("SELECT MIN(signal_date) FROM signals WHERE signal_date IS NOT NULL")
    earliest_signal = cur.fetchone()[0]

    if not earliest_signal:
        # No signals in DB yet — can't validate benchmark coverage
        return {
            'passed': True,
            'measured': {'benchmarks': {}, 'backtest_window': None},
            'expected': {'threshold_type': 'target'},
            'details': 'No signals in database yet; benchmark check deferred'
        }

    cur.execute("SELECT MAX(date) FROM prices")
    latest_price = cur.fetchone()[0]

    if not latest_price:
        return {
            'passed': False,
            'measured': {'benchmarks': {}},
            'expected': {'threshold_type': 'target'},
            'details': 'No price data in database'
        }

    # Backtest window
    window_start = earliest_signal
    window_end = latest_price

    # Trailing edge tolerance: 7 calendar days (~5 trading days)
    trailing_tolerance_days = 7

    # Required benchmarks: SPY, IWM, MDY (used in historical_backtest.py)
    # Optional benchmarks: QQQ, ^IXIC, URTH, ACWI (nice-to-have comparisons)
    required_benchmarks = ['SPY', 'IWM', 'MDY']
    optional_benchmarks = ['QQQ', '^IXIC', 'URTH', 'ACWI']
    all_benchmarks = required_benchmarks + optional_benchmarks

    results = {}
    all_passed = True
    failure_reasons = []
    optional_warnings = []

    for ticker in all_benchmarks:
        is_required = ticker in required_benchmarks
        # Get all dates for this benchmark within the window
        cur.execute("""
            SELECT date
            FROM prices
            WHERE ticker = ?
              AND date >= ?
              AND date <= ?
            ORDER BY date
        """, (ticker, window_start, window_end))
        dates = [row[0] for row in cur.fetchall()]

        if not dates:
            results[ticker] = {
                'present': False,
                'required': is_required,
                'min_date': None,
                'max_date': None,
                'row_count': 0,
                'interior_gaps': [],
                'trailing_lag_days': None
            }
            if is_required:
                all_passed = False
                failure_reasons.append(f"{ticker}: NOT PRESENT in backtest window")
            else:
                optional_warnings.append(f"{ticker}: NOT PRESENT in backtest window")
            continue

        # Check for interior gaps: missing consecutive trading days
        # A gap is when two consecutive dates differ by more than 4 calendar days
        # (accounts for long weekends, but not week+ outages)
        interior_gaps = []
        for i in range(1, len(dates)):
            prev_date = datetime.strptime(dates[i-1], '%Y-%m-%d')
            curr_date = datetime.strptime(dates[i], '%Y-%m-%d')
            days_diff = (curr_date - prev_date).days

            if days_diff > 4:
                interior_gaps.append({
                    'from': dates[i-1],
                    'to': dates[i],
                    'days': days_diff
                })

        # Check trailing edge: how far behind window_end is this ETF's last date?
        window_end_dt = datetime.strptime(window_end, '%Y-%m-%d')
        etf_last_dt = datetime.strptime(dates[-1], '%Y-%m-%d')
        trailing_lag_days = (window_end_dt - etf_last_dt).days

        # Check leading edge: does it cover the window start?
        covers_start = dates[0] <= window_start

        results[ticker] = {
            'present': True,
            'required': is_required,
            'min_date': dates[0],
            'max_date': dates[-1],
            'row_count': len(dates),
            'covers_window_start': covers_start,
            'trailing_lag_days': trailing_lag_days,
            'interior_gaps': interior_gaps[:5] if interior_gaps else []  # Show first 5
        }

        # Fail conditions (only fail CRITICAL for required benchmarks)
        if not covers_start:
            msg = f"{ticker}: MISSING LEADING EDGE (starts {dates[0]}, window starts {window_start})"
            if is_required:
                all_passed = False
                failure_reasons.append(msg)
            else:
                optional_warnings.append(msg)

        if interior_gaps:
            gap_summary = f"{len(interior_gaps)} gap(s), first: {interior_gaps[0]['from']} → {interior_gaps[0]['to']} ({interior_gaps[0]['days']} days)"
            msg = f"{ticker}: INTERIOR GAPS — {gap_summary}"
            if is_required:
                all_passed = False
                failure_reasons.append(msg)
            else:
                optional_warnings.append(msg)

        if trailing_lag_days > trailing_tolerance_days:
            msg = f"{ticker}: TRAILING LAG EXCESSIVE (ends {dates[-1]}, {trailing_lag_days} days behind window end {window_end}, tolerance {trailing_tolerance_days} days)"
            if is_required:
                all_passed = False
                failure_reasons.append(msg)
            else:
                optional_warnings.append(msg)

    return {
        'passed': all_passed,
        'measured': {
            'benchmarks': results,
            'backtest_window': f"{window_start} to {window_end}",
            'window_derived_from': f"earliest signal ({window_start}) to latest price ({window_end})",
            'failure_reasons': failure_reasons if failure_reasons else [],
            'optional_warnings': optional_warnings if optional_warnings else []
        },
        'expected': {
            'required_etfs': required_benchmarks,
            'optional_etfs': optional_benchmarks,
            'must_cover_window_start': True,
            'must_be_continuous_interior': True,
            'max_interior_gap_days': 4,
            'max_trailing_lag_days': trailing_tolerance_days,
            'threshold_type': 'target',
            'note': 'CRITICAL failures only for required benchmarks (SPY, IWM, MDY); optional benchmarks (QQQ, ^IXIC, URTH, ACWI) reported as warnings'
        }
    }


def check_date_format_integrity(conn):
    """
    Zero malformed or impossible dates in insider_transactions, shares_outstanding, prices.

    Malformed: not matching YYYY-MM-DD pattern with valid century (19xx or 20xx)
    Impossible: materially in the future (>2 days, accounting for SEC filing edge cases)

    Catches dates like "0022-10-12" (two-digit year zero-padded instead of century-prefixed).
    These are recoverable from filing_date but must be cleaned before release.

    Threshold type: Target (zero tolerance)
    """
    cur = conn.cursor()
    issues = {}
    total_issues = 0

    # insider_transactions.transaction_date
    # Check for: wrong pattern, wrong century, or future dates
    cur.execute("""
        SELECT COUNT(*)
        FROM insider_transactions
        WHERE transaction_date NOT LIKE '____-__-__'
           OR (transaction_date NOT LIKE '19__-__-__' AND transaction_date NOT LIKE '20__-__-__')
           OR transaction_date > date('now', '+2 days')
    """)
    bad_txn_dates = cur.fetchone()[0]
    if bad_txn_dates > 0:
        # Get examples
        cur.execute("""
            SELECT transaction_date, COUNT(*) as cnt
            FROM insider_transactions
            WHERE transaction_date NOT LIKE '____-__-__'
               OR (transaction_date NOT LIKE '19__-__-__' AND transaction_date NOT LIKE '20__-__-__')
               OR transaction_date > date('now', '+2 days')
            GROUP BY transaction_date
            ORDER BY cnt DESC
            LIMIT 5
        """)
        examples = [f"{row[0]} ({row[1]} rows)" for row in cur.fetchall()]
        issues['insider_transactions.transaction_date'] = {
            'count': bad_txn_dates,
            'examples': examples
        }
        total_issues += bad_txn_dates

    # shares_outstanding.date
    cur.execute("""
        SELECT COUNT(*)
        FROM shares_outstanding
        WHERE date NOT LIKE '____-__-__'
           OR (date NOT LIKE '19__-__-__' AND date NOT LIKE '20__-__-__')
           OR date > date('now', '+2 days')
    """)
    bad_shares_dates = cur.fetchone()[0]
    if bad_shares_dates > 0:
        cur.execute("""
            SELECT date, COUNT(*) as cnt
            FROM shares_outstanding
            WHERE date NOT LIKE '____-__-__'
               OR (date NOT LIKE '19__-__-__' AND date NOT LIKE '20__-__-__')
               OR date > date('now', '+2 days')
            GROUP BY date
            ORDER BY cnt DESC
            LIMIT 5
        """)
        examples = [f"{row[0]} ({row[1]} rows)" for row in cur.fetchall()]
        issues['shares_outstanding.date'] = {
            'count': bad_shares_dates,
            'examples': examples
        }
        total_issues += bad_shares_dates

    # prices.date
    cur.execute("""
        SELECT COUNT(*)
        FROM prices
        WHERE date NOT LIKE '____-__-__'
           OR (date NOT LIKE '19__-__-__' AND date NOT LIKE '20__-__-__')
           OR date > date('now', '+2 days')
    """)
    bad_price_dates = cur.fetchone()[0]
    if bad_price_dates > 0:
        cur.execute("""
            SELECT date, COUNT(*) as cnt
            FROM prices
            WHERE date NOT LIKE '____-__-__'
               OR (date NOT LIKE '19__-__-__' AND date NOT LIKE '20__-__-__')
               OR date > date('now', '+2 days')
            GROUP BY date
            ORDER BY cnt DESC
            LIMIT 5
        """)
        examples = [f"{row[0]} ({row[1]} rows)" for row in cur.fetchall()]
        issues['prices.date'] = {
            'count': bad_price_dates,
            'examples': examples
        }
        total_issues += bad_price_dates

    return {
        'passed': total_issues == 0,
        'measured': {
            'total_malformed_dates': total_issues,
            'by_table': issues if issues else 'none'
        },
        'expected': {
            'malformed_dates': 0,
            'future_dates_allowed_days': 2,
            'valid_centuries': ['19xx', '20xx'],
            'threshold_type': 'target'
        }
    }


def check_price_values(conn):
    """
    Zero rows with prices.close <= 0.

    Non-positive prices are invalid, full stop. This is non-negotiable — a price
    must be strictly positive.

    Deliberately does NOT flag high close values (>$10k, >$100k) on their own.
    Most are legitimate split-adjusted history for reverse-split microcaps — a
    stock at $3 today after cumulative 1:100,000 reverse splits genuinely shows
    enormous adjusted prices in the past. They are only wrong when treated as a
    current price, which the market_cap_plausibility check catches.

    Threshold type: Target (zero tolerance for non-positive prices)
    """
    cur = conn.cursor()

    # Count non-positive prices
    cur.execute("""
        SELECT COUNT(*)
        FROM prices
        WHERE close <= 0
    """)
    non_positive = cur.fetchone()[0]

    if non_positive > 0:
        # Get examples
        cur.execute("""
            SELECT ticker, date, close
            FROM prices
            WHERE close <= 0
            ORDER BY close, ticker
            LIMIT 20
        """)
        examples = [f"{row[0]} on {row[1]}: ${row[2]}" for row in cur.fetchall()]
    else:
        examples = []

    return {
        'passed': non_positive == 0,
        'measured': {
            'non_positive_price_count': non_positive,
            'examples': examples
        },
        'expected': {
            'non_positive_prices': 0,
            'note': 'Price must be strictly positive; zero tolerance',
            'threshold_type': 'target'
        }
    }


def check_extreme_price_values(conn):
    """
    Prices.close values above $100k (informational, flags for review).

    Most extreme prices are legitimate split-adjusted history for reverse-split
    microcaps. A stock at $3 today after cumulative 1:100,000 reverse splits
    genuinely shows $300k adjusted prices in the past. However, values above
    $100k are worth flagging for review to catch potential data corruption.

    This check is informational (WARN) because these values are legitimate
    back-adjusted artifacts, not corruption. They only become wrong when
    treated as current prices, which market_cap_plausibility catches.

    Threshold: $100,000 per share
    Threshold type: Informational (flags for review, not corruption)
    """
    cur = conn.cursor()

    # Threshold for extreme prices
    extreme_threshold = 100000.0

    # Count prices above threshold
    cur.execute("""
        SELECT COUNT(*)
        FROM prices
        WHERE close > ?
    """, (extreme_threshold,))
    extreme_count = cur.fetchone()[0]

    if extreme_count > 0:
        # Get examples (highest values, with context)
        cur.execute("""
            SELECT ticker, date, close,
                   (SELECT COUNT(*) FROM prices p2 WHERE p2.ticker = prices.ticker) as total_rows
            FROM prices
            WHERE close > ?
            ORDER BY close DESC
            LIMIT 20
        """, (extreme_threshold,))
        examples = [
            f"{row[0]} on {row[1]}: ${row[2]:,.0f} ({row[3]} total rows)"
            for row in cur.fetchall()
        ]

        # Get ticker count
        cur.execute("""
            SELECT COUNT(DISTINCT ticker)
            FROM prices
            WHERE close > ?
        """, (extreme_threshold,))
        ticker_count = cur.fetchone()[0]
    else:
        examples = []
        ticker_count = 0

    return {
        'passed': True,  # Informational only, always passes
        'measured': {
            'extreme_price_count': extreme_count,
            'tickers_affected': ticker_count,
            'threshold': extreme_threshold,
            'examples': examples
        },
        'expected': {
            'threshold_type': 'informational',
            'threshold_display': f'${extreme_threshold:,.0f}',
            'note': 'Legitimate split-adjusted values; review for potential corruption'
        }
    }


def check_market_cap_plausibility(conn):
    """
    No companies.market_cap above low-trillions ceiling ($5T).

    Roughly ten companies worldwide exceed $1T market cap. Anything above $5T
    is physically impossible given current global markets — signals the recompute
    script wrote garbage (e.g., five-year-old split-adjusted price × current share count).

    Example of live corruption caught:
      GNLN  $59.3T   (asof 2021-02-12)  — exceeds global GDP
      HSDT  $34.5T
      PKG   $21.4T

    Threshold: $10T (10,000,000,000,000)
    Threshold type: Target (physically impossible values, zero tolerance)
    """
    cur = conn.cursor()

    # Ceiling: $10 trillion (updated 2026-08-31 for NVDA at $5.3T)
    ceiling = 10e12

    # Count companies above ceiling
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE market_cap > ?
    """, (ceiling,))
    above_ceiling = cur.fetchone()[0]

    if above_ceiling > 0:
        # Get examples
        cur.execute("""
            SELECT ticker, market_cap, market_cap_asof
            FROM companies
            WHERE market_cap > ?
            ORDER BY market_cap DESC
            LIMIT 20
        """, (ceiling,))
        examples = [
            f"{row[0]}: ${row[1]/1e12:.1f}T (asof {row[2] or 'NULL'})"
            for row in cur.fetchall()
        ]
    else:
        examples = []

    return {
        'passed': above_ceiling == 0,
        'measured': {
            'above_ceiling_count': above_ceiling,
            'ceiling_usd': ceiling,
            'examples': examples
        },
        'expected': {
            'market_caps_above_ceiling': 0,
            'ceiling_usd': ceiling,
            'ceiling_display': '$10T',
            'note': 'Physically impossible; signals corrupt recompute (stale price × current shares)',
            'threshold_type': 'target'
        }
    }


def check_market_cap_coverage(conn):
    """
    Fraction of ACTIVE companies with insider purchases that have a usable market cap.

    Scoped to companies that:
    1. Have insider purchases (matter for scoring)
    2. Are ACTIVE (have a recent price, reusing coverage.prices derivation)

    Delisted/inactive companies cannot have a current market cap (no current price exists),
    so counting them in the denominator guarantees a permanently-red signal that will be
    ignored or switched off.

    Third occurrence of denominator problem (after coverage.prices and this check's first version):
    entities *incapable* of satisfying the check should not be counted against it.

    A company without market_cap gets tier='unknown' and TIER_WEIGHTS['unknown']=0.0, so
    every size-adjusted score collapses to zero and they silently disappear from the scanner.

    Threshold: 90% (target, correct denominator not lowered bar)
    Threshold type: Target (presence is as much an invariant as plausibility)
    """
    cur = conn.cursor()

    # Get the most recent price date (for active threshold)
    cur.execute("SELECT MAX(date) FROM prices")
    max_date = cur.fetchone()[0]

    if not max_date:
        return {
            'passed': False,
            'measured': {},
            'expected': {'min_coverage_pct': 90.0, 'threshold_type': 'target'},
            'details': 'No price data in database'
        }

    # Recent cutoff: ~1 month (same reasoning as coverage.prices inactive threshold)
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d')
    recent_cutoff = (max_date_dt - timedelta(days=30)).strftime('%Y-%m-%d')

    # ACTIVE companies with insider purchases (the ones that matter AND can have current market cap)
    cur.execute("""
        SELECT COUNT(DISTINCT c.ticker)
        FROM companies c
        INNER JOIN insider_transactions it ON c.id = it.company_id
        INNER JOIN prices p ON c.ticker = p.ticker
        WHERE it.transaction_type = 'P'
          AND c.ticker != 'NONE'
          AND p.date >= ?
    """, (recent_cutoff,))
    active_with_purchases = cur.fetchone()[0]

    # Of those, how many have usable market cap
    cur.execute("""
        SELECT COUNT(DISTINCT c.ticker)
        FROM companies c
        INNER JOIN insider_transactions it ON c.id = it.company_id
        INNER JOIN prices p ON c.ticker = p.ticker
        WHERE it.transaction_type = 'P'
          AND c.ticker != 'NONE'
          AND p.date >= ?
          AND c.market_cap IS NOT NULL
    """, (recent_cutoff,))
    with_market_cap = cur.fetchone()[0]

    coverage_pct = (with_market_cap / active_with_purchases * 100) if active_with_purchases > 0 else 0
    min_pct = 90.0

    # Count inactive with purchases for context
    cur.execute("""
        SELECT COUNT(DISTINCT c.ticker)
        FROM companies c
        INNER JOIN insider_transactions it ON c.id = it.company_id
        WHERE it.transaction_type = 'P'
          AND c.ticker != 'NONE'
          AND c.ticker NOT IN (
              SELECT DISTINCT ticker FROM prices WHERE date >= ?
          )
    """, (recent_cutoff,))
    inactive_with_purchases = cur.fetchone()[0]

    # Get examples of active companies missing market cap
    if with_market_cap < active_with_purchases:
        cur.execute("""
            SELECT c.ticker, c.name, COUNT(it.id) as purchase_count
            FROM companies c
            INNER JOIN insider_transactions it ON c.id = it.company_id
            INNER JOIN prices p ON c.ticker = p.ticker
            WHERE it.transaction_type = 'P'
              AND c.ticker != 'NONE'
              AND p.date >= ?
              AND c.market_cap IS NULL
            GROUP BY c.ticker, c.name
            ORDER BY purchase_count DESC
            LIMIT 20
        """, (recent_cutoff,))
        examples = [
            f"{row[0]} ({row[2]} purchases, {row[1] or 'no name'})"
            for row in cur.fetchall()
        ]
    else:
        examples = []

    return {
        'passed': coverage_pct >= min_pct,
        'measured': {
            'active_with_purchases': active_with_purchases,
            'active_with_market_cap': with_market_cap,
            'inactive_with_purchases': inactive_with_purchases,
            'missing_market_cap': active_with_purchases - with_market_cap,
            'coverage_pct': round(coverage_pct, 1),
            'recent_cutoff': recent_cutoff,
            'examples': examples
        },
        'expected': {
            'min_coverage_pct': min_pct,
            'note': 'Scoped to ACTIVE companies (recent price); delisted excluded from denominator',
            'threshold_type': 'target'
        }
    }


def check_market_cap_freshness(conn):
    """
    Fraction of companies with market cap that is freshly derived (recent market_cap_asof).

    "has a market cap" conflates fresh-derived with stale-retained (preserved legacy values).
    If the derived fraction quietly falls while total coverage holds steady, the pipeline
    is coasting on stale values and nothing reports it.

    This check splits coverage (CRITICAL) from freshness (WARN): coverage says "can score",
    freshness says "pipeline is working".

    Threshold: 80% of companies with market cap should have fresh derivation (WARN, informational)
    Threshold type: Informational (pipeline health signal)
    """
    cur = conn.cursor()

    # Get the most recent price date
    cur.execute("SELECT MAX(date) FROM prices")
    max_date = cur.fetchone()[0]

    if not max_date:
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'informational'},
            'details': 'No price data in database'
        }

    # Fresh threshold: market_cap_asof within 90 days of max_date
    max_date_dt = datetime.strptime(max_date, '%Y-%m-%d')
    fresh_cutoff = (max_date_dt - timedelta(days=90)).strftime('%Y-%m-%d')

    # Companies with market cap
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE market_cap IS NOT NULL
          AND ticker != 'NONE'
    """)
    with_market_cap = cur.fetchone()[0]

    # Of those, how many have fresh market_cap_asof
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE market_cap IS NOT NULL
          AND ticker != 'NONE'
          AND market_cap_asof IS NOT NULL
          AND market_cap_asof >= ?
    """, (fresh_cutoff,))
    with_fresh = cur.fetchone()[0]

    # Count with market cap but NULL asof (no derivation date)
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE market_cap IS NOT NULL
          AND ticker != 'NONE'
          AND market_cap_asof IS NULL
    """)
    with_null_asof = cur.fetchone()[0]

    freshness_pct = (with_fresh / with_market_cap * 100) if with_market_cap > 0 else 0
    min_pct = 80.0

    return {
        'passed': True,  # Informational only, always passes
        'measured': {
            'total_with_market_cap': with_market_cap,
            'with_fresh_asof': with_fresh,
            'with_null_asof': with_null_asof,
            'with_stale_asof': with_market_cap - with_fresh - with_null_asof,
            'freshness_pct': round(freshness_pct, 1),
            'fresh_cutoff': fresh_cutoff
        },
        'expected': {
            'min_freshness_pct': min_pct,
            'threshold_type': 'informational',
            'note': 'Coverage (CRITICAL) says "can score"; freshness (WARN) says "pipeline working"'
        }
    }


def check_market_cap_regression(conn):
    """
    Usable market caps must not drop materially between runs.

    Uses data_sources provenance for 'market_cap' as baseline. This is the check
    that would have caught the exact event: recompute script rejecting implausible
    values wrote NULL instead of preserving, and 2,164 companies lost usable market caps.

    The plausibility check only bounds the upper direction; this guards deletion.

    Threshold: ≤10% drop
    Threshold type: Regression guard (deletion is the easiest way to satisfy a
                    "too much of X" check, and exactly what an automated fix will do)
    """
    cur = conn.cursor()

    # Current count of usable market caps
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE market_cap IS NOT NULL
          AND ticker != 'NONE'
    """)
    current_count = cur.fetchone()[0]

    # Get previous count from provenance
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from provenance import get_provenance
        prov = get_provenance(db_path, 'market_cap')
        previous_count = prov.get('coverage_num') if prov else None
    except (ImportError, AttributeError):
        previous_count = None

    if previous_count is None:
        return {
            'passed': True,
            'measured': {
                'current_count': current_count,
                'previous_count': None
            },
            'expected': {
                'max_drop_pct': 10,
                'threshold_type': 'regression_guard',
                'note': 'First run; recording baseline'
            }
        }

    # Check for material drop
    drop_count = previous_count - current_count
    drop_pct = (drop_count / previous_count * 100) if previous_count > 0 else 0

    return {
        'passed': drop_pct <= 10,
        'measured': {
            'current_count': current_count,
            'previous_count': previous_count,
            'drop_count': drop_count,
            'drop_pct': round(drop_pct, 1)
        },
        'expected': {
            'max_drop_pct': 10,
            'note': 'Guards deletion (NULL writing) after plausibility rejection',
            'threshold_type': 'regression_guard'
        }
    }


def check_market_cap_input_staleness(conn):
    """
    Companies whose market_cap_asof is far behind the latest available price date.

    Market cap derived from stale inputs (e.g., 2021 price × current shares) produces
    nonsense values. This counts how many companies have their market_cap_asof more
    than 1 year behind their latest available price.

    Current count: 837 companies (1+ year stale)

    Threshold type: Informational (WARN, monitors for growth)
    """
    cur = conn.cursor()

    # Stale threshold: 365 days
    stale_threshold_days = 365

    # For each company with market_cap_asof, find the latest price date and compute staleness
    cur.execute("""
        SELECT c.ticker, c.market_cap_asof, MAX(p.date) as latest_price,
               CAST((JULIANDAY(MAX(p.date)) - JULIANDAY(c.market_cap_asof)) AS INTEGER) as staleness_days
        FROM companies c
        INNER JOIN prices p ON c.ticker = p.ticker
        WHERE c.market_cap_asof IS NOT NULL
        GROUP BY c.ticker, c.market_cap_asof
        HAVING staleness_days > ?
        ORDER BY staleness_days DESC
    """, (stale_threshold_days,))
    stale_inputs = cur.fetchall()

    count = len(stale_inputs)

    return {
        'passed': True,  # Informational only, always passes
        'measured': {
            'stale_input_count': count,
            'stale_threshold_days': stale_threshold_days,
            'examples': [
                f"{row[0]}: asof {row[1]}, latest price {row[2]} ({row[3]}d stale)"
                for row in stale_inputs[:20]
            ]
        },
        'expected': {
            'threshold_type': 'informational',
            'note': 'Market cap from stale inputs (>1yr behind latest price) — monitors for growth'
        }
    }


def check_shares_basis_switches(conn):
    """
    Consecutive-quarter share count changes that look like reporting-basis switches.

    A large consecutive-quarter multiplier (roughly 5x or 1/5x) that is NOT explained
    by an entry in split_events signals either:
      - Reporting basis switch (ADR shares vs ordinary shares)
      - Bad data (incorrect XBRL parse or vendor error)

    Example: BABA 2025-03-31 → 2026-03-31
      18,474M → 1,858M  (9.94x drop, no split recorded)
      This is an ADR/ordinary switch, not a real corporate action.

    Catches the root cause of market cap basis mismatches.

    Threshold type: Informational (WARN, surfaces data quality issues)
    """
    cur = conn.cursor()

    # Basis switch threshold: 5x or 1/5x consecutive quarter change
    basis_switch_threshold = 5.0

    # Get all companies with at least 2 quarters of share data
    cur.execute("""
        SELECT c.ticker, c.id
        FROM companies c
        WHERE EXISTS (
            SELECT 1 FROM shares_outstanding
            WHERE company_id = c.id
            GROUP BY company_id
            HAVING COUNT(*) >= 2
        )
        ORDER BY c.ticker
    """)
    companies = cur.fetchall()

    basis_switches = []

    for ticker, company_id in companies:
        # Get shares in chronological order
        cur.execute("""
            SELECT date, shares
            FROM shares_outstanding
            WHERE company_id = ?
            ORDER BY date
        """, (company_id,))
        share_history = cur.fetchall()

        if len(share_history) < 2:
            continue

        # Check consecutive quarters for large multipliers
        for i in range(1, len(share_history)):
            prev_date, prev_shares = share_history[i-1]
            curr_date, curr_shares = share_history[i]

            if prev_shares <= 0 or curr_shares <= 0:
                continue

            # Calculate multiplier (both directions)
            multiplier = max(curr_shares / prev_shares, prev_shares / curr_shares)

            if multiplier >= basis_switch_threshold:
                # Check if there's a split event that explains this
                # Look for splits within +/- 90 days of the current date
                cur.execute("""
                    SELECT COUNT(*)
                    FROM split_events
                    WHERE ticker = ?
                      AND date >= date(?, '-90 days')
                      AND date <= date(?, '+90 days')
                """, (ticker, curr_date, curr_date))
                has_split = cur.fetchone()[0] > 0

                if not has_split:
                    # Unexplained large change - likely a basis switch
                    basis_switches.append({
                        'ticker': ticker,
                        'from_date': prev_date,
                        'to_date': curr_date,
                        'from_shares': prev_shares,
                        'to_shares': curr_shares,
                        'multiplier': round(multiplier, 2)
                    })

    # Get unique ticker count
    affected_tickers = set(s['ticker'] for s in basis_switches)

    # Sort by multiplier (descending) to show most extreme cases first
    basis_switches_sorted = sorted(basis_switches, key=lambda s: s['multiplier'], reverse=True)

    return {
        'passed': True,  # Informational only, always passes
        'measured': {
            'basis_switch_count': len(basis_switches),
            'affected_tickers': len(affected_tickers),
            'threshold_multiplier': basis_switch_threshold,
            'examples': [
                f"{s['ticker']}: {s['from_shares']/1e6:.1f}M → {s['to_shares']/1e6:.1f}M ({s['multiplier']}x, {s['from_date']} → {s['to_date']})"
                for s in basis_switches_sorted[:20]
            ]
        },
        'expected': {
            'threshold_type': 'informational',
            'note': 'Large consecutive-quarter share changes (≥5x) without split events signal basis switches or bad data'
        }
    }


def check_ticker_cik_absent_from_sec(conn):
    """
    Companies whose CIK is absent from SEC's company_tickers.json map.

    Mostly delisted or OTC-only companies. Unknowable/not actionable.
    This is an informational coverage statistic, not a validity check.

    Threshold type: Informational (no threshold)
    """
    cur = conn.cursor()

    # Fetch SEC ticker map
    url = "https://www.sec.gov/files/company_tickers.json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "InsiderSignalTracker oriol.diaz@ozoneproject.com",
        "Accept": "application/json"
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        logger.warning(f"Failed to fetch SEC ticker map: {e}")
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'informational'},
            'details': f'Could not fetch SEC ticker map: {e}'
        }

    # Build set of CIKs in SEC map
    sec_ciks = set(int(entry['cik_str']) for entry in data.values())

    # Get all companies with CIKs
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE ticker != 'NONE' AND cik IS NOT NULL
    """)
    total_with_cik = cur.fetchone()[0]

    # Count those whose CIK is absent from SEC map
    cur.execute("""
        SELECT cik, ticker, name
        FROM companies
        WHERE ticker != 'NONE' AND cik IS NOT NULL
    """)
    companies = cur.fetchall()

    absent_from_sec = []
    for cik, ticker, name in companies:
        if cik not in sec_ciks:
            absent_from_sec.append((ticker, cik, name or ''))

    return {
        'passed': True,  # Informational only
        'measured': {
            'total_companies_with_cik': total_with_cik,
            'cik_absent_from_sec_count': len(absent_from_sec),
            'pct_absent': round(len(absent_from_sec) / total_with_cik * 100, 1) if total_with_cik > 0 else 0,
            'examples': [f"{t} (CIK {c})" for t, c, _ in absent_from_sec[:10]] if absent_from_sec else []
        },
        'expected': {
            'threshold_type': 'informational',
            'note': 'CIK absent from SEC map (mostly delisted/OTC)'
        }
    }


def check_ticker_validity_for_cik(conn):
    """
    Companies whose ticker does not match their CIK per SEC's company_tickers.json.

    Actionable: mostly stale symbols after rebrands (ZI→GTM, SQ→XYZ, ABC→COR).
    These should be updated via fix_ticker_symbols.py or quarantined.

    Threshold type: Target (should eventually reach zero via systematic fixes)
    """
    cur = conn.cursor()

    # Fetch SEC ticker map
    url = "https://www.sec.gov/files/company_tickers.json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "InsiderSignalTracker oriol.diaz@ozoneproject.com",
        "Accept": "application/json"
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        logger.warning(f"Failed to fetch SEC ticker map: {e}")
        return {
            'passed': False,
            'measured': {},
            'expected': {'threshold_type': 'target'},
            'details': f'Could not fetch SEC ticker map: {e}'
        }

    # Build CIK -> set of valid tickers
    cik_to_tickers = defaultdict(set)
    for entry in data.values():
        cik = int(entry['cik_str'])
        ticker = entry['ticker'].upper()
        cik_to_tickers[cik].add(ticker)

    # Get all companies with CIKs present in SEC map
    cur.execute("""
        SELECT ticker, cik, name
        FROM companies
        WHERE ticker != 'NONE' AND cik IS NOT NULL
    """)
    companies = cur.fetchall()

    # Filter to those whose CIK is in the SEC map
    invalid_tickers = []
    for ticker, cik, name in companies:
        if cik not in cik_to_tickers:
            continue  # CIK absent from SEC — covered by separate check

        valid_tickers = cik_to_tickers[cik]
        if ticker.upper() not in valid_tickers:
            invalid_tickers.append((ticker, cik, name or '', list(valid_tickers)[:3]))

    return {
        'passed': len(invalid_tickers) == 0,
        'measured': {
            'invalid_ticker_count': len(invalid_tickers),
            'examples': [
                f"{t} (CIK {c}, valid: {v})"
                for t, c, _, v in invalid_tickers[:10]
            ] if invalid_tickers else []
        },
        'expected': {
            'invalid_tickers': 0,
            'note': 'Ticker does not match CIK per SEC map (mostly stale rebrands)',
            'threshold_type': 'target'
        }
    }


def check_no_duplicate_tickers(conn):
    """No duplicate companies.ticker.

    Threshold type: Target (zero tolerance)
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT ticker, COUNT(*) as cnt
        FROM companies
        WHERE ticker != 'NONE'
        GROUP BY ticker
        HAVING cnt > 1
    """)
    duplicates = cur.fetchall()

    return {
        'passed': len(duplicates) == 0,
        'measured': {
            'duplicate_count': len(duplicates),
            'examples': [f"{row[0]} ({row[1]} rows)" for row in duplicates[:10]]
        },
        'expected': {'duplicate_tickers': 0, 'threshold_type': 'target'}
    }


def check_no_orphaned_transactions(conn):
    """No insider_transactions orphaned from companies.

    Threshold type: Target (zero tolerance)
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM insider_transactions
        WHERE company_id NOT IN (SELECT id FROM companies)
    """)
    orphaned = cur.fetchone()[0]

    if orphaned > 0:
        # Get examples
        cur.execute("""
            SELECT company_id, COUNT(*) as cnt
            FROM insider_transactions
            WHERE company_id NOT IN (SELECT id FROM companies)
            GROUP BY company_id
            ORDER BY cnt DESC
            LIMIT 10
        """)
        examples = [f"company_id={row[0]} ({row[1]} txns)" for row in cur.fetchall()]
    else:
        examples = []

    return {
        'passed': orphaned == 0,
        'measured': {
            'orphaned_transaction_count': orphaned,
            'examples': examples
        },
        'expected': {'orphaned_transactions': 0, 'threshold_type': 'target'}
    }


def check_derived_artifacts_exist(conn):
    """
    historical_clusters.csv, latest_signals.csv, and insider_frozen.json.gz
    exist and are non-empty.

    Uses runtime path resolution matching actual usage patterns.

    Threshold type: Target (all artifacts must exist and be non-empty)
    """
    artifacts = {
        'historical_clusters.csv': _resolve_historical_clusters_path(),
        'latest_signals.csv': _resolve_latest_signals_path(),
        'insider_frozen.json.gz': _resolve_insider_frozen_path()
    }

    results = {}
    all_exist = True

    for filename, filepath in artifacts.items():
        exists = filepath.exists()
        size = filepath.stat().st_size if exists else 0

        results[filename] = {
            'exists': exists,
            'size_bytes': size,
            'path': str(filepath)
        }

        if not exists or size == 0:
            all_exist = False

    return {
        'passed': all_exist,
        'measured': {'files': results},
        'expected': {
            'all_must_exist': True,
            'all_must_be_non_empty': True,
            'threshold_type': 'target'
        }
    }


def check_derived_artifacts_consistency(conn):
    """
    Tickers referenced in historical_clusters.csv exist in companies table.

    This catches cases where the CSV contains stale/deleted tickers that would
    produce join failures or silent data loss.

    Reports which path was resolved (data/ vs packages/tracker/output/) and row count
    to distinguish between "stale copy" and "corrupt artifact".

    Threshold type: Target (zero invalid tickers in derived artifacts)
    """
    cur = conn.cursor()

    # Get all valid tickers from companies
    cur.execute("SELECT ticker FROM companies WHERE ticker != 'NONE'")
    valid_tickers = {row[0] for row in cur.fetchall()}

    # Read historical_clusters.csv
    clusters_path = _resolve_historical_clusters_path()
    if not clusters_path.exists():
        return {
            'passed': True,
            'measured': {'resolved_path': str(clusters_path)},
            'expected': {'threshold_type': 'target'},
            'details': 'historical_clusters.csv not found; check deferred'
        }

    try:
        with open(clusters_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            cluster_tickers = {row['ticker'] for row in rows if 'ticker' in row}
            total_rows = len(rows)
    except Exception as e:
        return {
            'passed': False,
            'measured': {'resolved_path': str(clusters_path)},
            'expected': {'threshold_type': 'target'},
            'details': f'Failed to read historical_clusters.csv: {e}'
        }

    # Find tickers in CSV but not in companies
    invalid_tickers = cluster_tickers - valid_tickers

    return {
        'passed': len(invalid_tickers) == 0,
        'measured': {
            'resolved_path': str(clusters_path),
            'total_rows': total_rows,
            'total_tickers_in_clusters_csv': len(cluster_tickers),
            'invalid_ticker_count': len(invalid_tickers),
            'examples': list(invalid_tickers)[:10] if invalid_tickers else []
        },
        'expected': {
            'invalid_tickers': 0,
            'note': 'All tickers in historical_clusters.csv must exist in companies table',
            'threshold_type': 'target'
        }
    }


# Shared mapping used by both contradiction and coverage checks
# Mapping: failure_table -> (data_table, description, query)
# Query returns (entity_id, reason, data_row_count, latest_data_item)
FAILURE_MAPPINGS = {
    'benchmark_backfill_failures': (
        'prices',
        'benchmarks',
        """
        SELECT f.ticker, f.reason, COUNT(p.date) as price_rows, MAX(p.date) as latest_price
        FROM benchmark_backfill_failures f
        INNER JOIN prices p ON f.ticker = p.ticker
        WHERE f.ticker IN ('SPY', 'IWM', 'MDY')
        GROUP BY f.ticker, f.reason
        ORDER BY price_rows DESC
        """
    ),
    'price_backfill_failures': (
        'prices',
        'company prices',
        """
        SELECT f.ticker, f.reason, COUNT(p.date) as price_rows, MAX(p.date) as latest_price
        FROM price_backfill_failures f
        INNER JOIN prices p ON f.ticker = p.ticker
        GROUP BY f.ticker, f.reason
        ORDER BY price_rows DESC
        """
    ),
    'quarter_index_failures': (
        'insider_transactions',
        'current-quarter Form 4 ingestion',
        """
        SELECT f.cik, f.ticker, f.reason, COUNT(it.id) as txn_count,
               f.year || 'Q' || f.quarter as quarter_id
        FROM quarter_index_failures f
        INNER JOIN companies c ON f.cik = c.cik
        INNER JOIN insider_transactions it ON c.id = it.company_id
        WHERE it.filing_date >= date(printf('%04d-%02d-01', f.year, (f.quarter - 1) * 3 + 1))
          AND it.filing_date < date(printf('%04d-%02d-01', f.year, (f.quarter - 1) * 3 + 1), '+3 months')
        GROUP BY f.cik, f.ticker, f.reason, f.year, f.quarter
        ORDER BY txn_count DESC
        """
    ),
    'shares_backfill_failures': (
        'shares_outstanding',
        'share buyback data',
        """
        SELECT c.ticker, f.reason, COUNT(s.date) as shares_rows, f.cik
        FROM shares_backfill_failures f
        INNER JOIN companies c ON f.cik = c.cik
        INNER JOIN shares_outstanding s ON c.id = s.company_id
        GROUP BY c.ticker, f.reason, f.cik
        ORDER BY shares_rows DESC
        """
    ),
    'ticker_fix_failures': (
        'companies',
        'ticker symbols',
        """
        SELECT f.ticker, f.reason, 1 as present, c.id
        FROM ticker_fix_failures f
        INNER JOIN companies c ON f.company_id = c.id
        """
    )
}


def check_failure_table_contradictions(conn):
    """
    No entity may be simultaneously in a *_failures table and have its corresponding data.

    CRITICAL severity — this is a logic error in the pipeline's own bookkeeping.
    An entity marked permanently failed while its data is present means the reconciliation
    contract (DB-derived work list, clear-failure-on-success) was violated.

    Found live bug: 743 tickers including SPY were marked 'no_data' in price_backfill_failures
    but held current prices, causing backfill to skip them forever and silently corrupt backtests.

    Principle: THE DATA BEING WRONG BLOCKS A RELEASE.
    This check guards data integrity, not contract completeness (see check_failure_table_coverage).

    Mapping from failure table to (data_table, key_column, join_predicate):
    - benchmark_backfill_failures → prices (ticker, benchmarks only)
    - price_backfill_failures → prices (ticker, all companies)
    - quarter_index_failures → insider_transactions (cik, scoped to quarter range)
    - shares_backfill_failures → shares_outstanding (cik, via companies join)
    - ticker_fix_failures → companies (company_id)

    Threshold type: Target (zero tolerance — logical contradiction)
    """
    cur = conn.cursor()

    # Discover all *_failures tables
    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE '%_failures'
        ORDER BY name
    """)
    failure_tables = [row[0] for row in cur.fetchall()]

    all_contradictions = []

    for failure_table in failure_tables:
        if failure_table not in FAILURE_MAPPINGS:
            # Unmapped tables are handled by check_failure_table_coverage (WARN)
            continue

        data_table, description, query = FAILURE_MAPPINGS[failure_table]

        # Execute the contradiction query
        try:
            cur.execute(query)
            contradictions = cur.fetchall()

            if contradictions:
                for row in contradictions[:5]:  # Top 5 per table
                    all_contradictions.append({
                        'failure_table': failure_table,
                        'data_table': data_table,
                        'description': description,
                        'entity': row[0],
                        'reason': row[1],
                        'data_count': row[2]
                    })
        except Exception as e:
            # Table might be empty or query might fail - log but don't crash
            logger.warning(f"Failed to check {failure_table}: {e}")
            continue

    # Format examples for reporting
    examples = [
        f"{c['entity']} in {c['failure_table']} (reason={c['reason']}, {c['data_count']} {c['description']} rows)"
        for c in all_contradictions[:20]
    ]

    return {
        'passed': len(all_contradictions) == 0,
        'measured': {
            'contradiction_count': len(all_contradictions),
            'examples': examples
        },
        'expected': {
            'contradictions': 0,
            'note': 'Entity cannot be both failed and covered (logic error in pipeline bookkeeping)',
            'threshold_type': 'target'
        }
    }


def check_failure_table_coverage(conn):
    """
    All *_failures tables must be mapped to a contradiction check.

    WARN severity — an unmapped table is a coverage gap in the contract, not a defect in the data.
    Someone added a capability faster than the contract caught up. This deserves visibility,
    not a blocked release.

    Principle: THE CONTRACT BEING INCOMPLETE DOES NOT BLOCK A RELEASE.
    A gate that blocks on its own incompleteness will eventually be switched off, and then it
    blocks on nothing at all.

    Unmapped tables discovered during development:
    - benchmark_backfill_failures, shares_backfill_failures, ticker_fix_failures appeared
      during reconciliation work (3 tables in one PR)
    - quarter_index_failures created by backfill_quarter_index.py on first monthly run

    Discovering the coupling during a release, at whatever hour a scheduled job fires, is the
    worst possible moment. A loud WARN surfaces the gap without blocking valid work.

    Threshold type: Informational (visibility, not enforcement)
    """
    cur = conn.cursor()

    # Discover all *_failures tables
    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name LIKE '%_failures'
        ORDER BY name
    """)
    failure_tables = [row[0] for row in cur.fetchall()]

    unmapped_tables = [t for t in failure_tables if t not in FAILURE_MAPPINGS]

    return {
        'passed': len(unmapped_tables) == 0,
        'measured': {
            'total_failure_tables': len(failure_tables),
            'mapped_tables': len([t for t in failure_tables if t in FAILURE_MAPPINGS]),
            'unmapped_tables': unmapped_tables
        },
        'expected': {
            'unmapped_tables': 0,
            'note': 'All *_failures tables should be mapped; unmapped tables represent coverage gaps',
            'threshold_type': 'informational'
        },
        'details': f"Unmapped tables: {', '.join(unmapped_tables)}" if unmapped_tables else None
    }


def _contract_compute_raw_market_price(ticker, transaction_date, adjusted_close, cur):
    """
    Compute as-transacted market price from split-back-adjusted close.

    Same formula as cleanup_corrupt_prices.py and historical_backtest.py:
    raw_market = adjusted_close × PROD(ratio for splits after txn_date)

    Returns (raw_market_price, split_count)
    """
    cur.execute("""
        SELECT ratio
        FROM split_events
        WHERE ticker = ? AND date > ?
        ORDER BY date
    """, (ticker, transaction_date))

    splits = cur.fetchall()
    cumulative_ratio = 1.0
    for (ratio,) in splits:
        cumulative_ratio *= ratio

    raw_market = adjusted_close * cumulative_ratio
    return raw_market, len(splits)


def check_insider_transaction_price_plausibility(conn):
    """
    No corrupt insider_transactions.price values in purchase (type='P') transactions.

    Four categories of price issues:
    1. **Hard bound violations**: price > $1M/share (BRK-A at ~$700K must survive)
    2. **Market cap sanity violations**: Transaction value (price × shares) exceeds
       company market cap. Catches cases where aggregate value was written into the
       price field (e.g., NUTX: 31,746 shares × $0.63 = "price" $20,000, giving
       $635M transaction value vs $1.3B market cap = 49%). This rule catches NUTX
       where the market divergence rule cannot (no price data before 2022).
    3. **Market divergence (corrupt transactions)**: tx_price / raw_market > 1000x,
       where raw_market is plausible (between $0.0001 and $10,000). These are
       transactions where the share count or aggregate value was written into the
       price field.
    4. **Corrupt market prices** (WARN): Our prices table shows implausible values
       (outside [$0.0001, $10,000] after split correction). Examples: MULN showing
       $2.7e14/share. These are flagged but NOT counted as transaction corruption.

    Market divergence uses CORRECT basis (split-adjusted):
    - Prices table: back-adjusted to recent reference date
    - Transaction prices: as-transacted
    - Comparison: raw_market = adjusted_close × PROD(split_ratio for splits after txn_date)

    This fixes the prior basis mismatch that destroyed 4,065 good rows (e.g., AMZN 2020
    purchases at $1,900 flagged as corrupt because our adjusted close showed $95 after
    the 2022 20:1 split).

    Scope: transaction_type='P' only (purchases matter for insider signals).

    Severity: CRITICAL for corrupt transactions, WARN for corrupt market prices.

    Threshold type: Target (zero corrupt transaction prices after cleanup)
    """
    cur = conn.cursor()

    MAX_PLAUSIBLE_PRICE = 1_000_000.0
    MIN_PLAUSIBLE_MARKET = 0.0001
    MAX_PLAUSIBLE_MARKET = 10000.0
    HIGH_DIVERGENCE_RATIO = 1000.0
    MIN_PLAUSIBLE_MCAP = 1_000_000.0      # $1M
    MAX_PLAUSIBLE_MCAP = 5_000_000_000_000.0  # $5T
    MCAP_RATIO_THRESHOLD = 1.0  # 100% of market cap

    # Check 1: Prices above hard ceiling
    cur.execute(f"""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL AND it.price > {MAX_PLAUSIBLE_PRICE}
        ORDER BY it.price DESC
        LIMIT 20
    """)
    ceiling_violations = cur.fetchall()

    # Check 2: Market cap sanity (transaction value > company market cap)
    cur.execute("""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted, c.market_cap
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL
          AND it.price > 0
          AND c.market_cap IS NOT NULL
        ORDER BY c.ticker, it.transaction_date
    """)

    mcap_transactions = cur.fetchall()
    mcap_violations = []

    for row_id, ticker, txn_date, tx_price, shares, market_cap in mcap_transactions:
        # Skip if already caught by ceiling check
        if tx_price > MAX_PLAUSIBLE_PRICE:
            continue

        tx_value = tx_price * shares

        # Check if market cap is plausible
        mcap_plausible = MIN_PLAUSIBLE_MCAP <= market_cap <= MAX_PLAUSIBLE_MCAP

        if mcap_plausible:
            ratio = tx_value / market_cap
            if ratio > MCAP_RATIO_THRESHOLD:
                mcap_violations.append((ticker, txn_date, tx_price, shares, tx_value, market_cap, ratio))

    # Check 3: Market divergence with split-corrected basis
    cur.execute("""
        SELECT it.id, c.ticker, it.transaction_date, it.price, it.shares_transacted
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL
          AND it.price > 0
        ORDER BY c.ticker, it.transaction_date
    """)

    transactions = cur.fetchall()
    corrupt_transactions = []  # High side divergence with plausible market
    corrupt_market_prices = []  # Market price itself is implausible

    for row_id, ticker, txn_date, tx_price, shares in transactions:
        # Skip if already caught by ceiling check
        if tx_price > MAX_PLAUSIBLE_PRICE:
            continue

        # Get adjusted market close
        cur.execute("""
            SELECT close
            FROM prices
            WHERE ticker = ? AND date <= ?
            ORDER BY date DESC
            LIMIT 1
        """, (ticker, txn_date))

        market_row = cur.fetchone()
        if not market_row:
            continue

        adjusted_close = market_row[0]
        if adjusted_close <= 0:
            continue

        # Compute as-transacted market price
        raw_market, split_count = _contract_compute_raw_market_price(ticker, txn_date, adjusted_close, cur)

        # Check plausibility
        market_plausible = MIN_PLAUSIBLE_MARKET <= raw_market <= MAX_PLAUSIBLE_MARKET
        ratio = tx_price / raw_market if raw_market > 0 else 0

        # Categorize
        if market_plausible and ratio > HIGH_DIVERGENCE_RATIO:
            corrupt_transactions.append((ticker, txn_date, tx_price, shares, raw_market, ratio, split_count))
        elif not market_plausible:
            corrupt_market_prices.append((ticker, txn_date, tx_price, shares, raw_market, split_count))

    # Format examples
    ceiling_examples = [
        f"{ticker} on {txn_date}: price=${price:,.0f}, shares={shares:,.0f} (above_ceiling)"
        for _, ticker, txn_date, price, shares in ceiling_violations[:10]
    ]

    mcap_examples = [
        f"{ticker} on {txn_date}: tx_value=${tx_value/1e9:.2f}B, market_cap=${market_cap/1e9:.2f}B, ratio={ratio:.1f}x"
        for ticker, txn_date, tx_price, shares, tx_value, market_cap, ratio in mcap_violations[:10]
    ]

    divergence_examples = [
        f"{ticker} on {txn_date}: tx=${tx_price:,.2f}, raw_market=${raw_market:.4f}, ratio={ratio:.1f}x ({split_count} splits)"
        for ticker, txn_date, tx_price, shares, raw_market, ratio, split_count in corrupt_transactions[:10]
    ]

    market_corrupt_examples = [
        f"{ticker} on {txn_date}: tx=${tx_price:,.2f}, raw_market=${raw_market:.2e} (outside plausible range)"
        for ticker, txn_date, tx_price, shares, raw_market, split_count in corrupt_market_prices[:10]
    ]

    # Combine corrupt transaction counts
    total_corrupt = len(ceiling_violations) + len(mcap_violations) + len(corrupt_transactions)

    return {
        'passed': total_corrupt == 0,
        'measured': {
            'ceiling_violations': len(ceiling_violations),
            'market_cap_violations': len(mcap_violations),
            'market_divergence_corrupt_transactions': len(corrupt_transactions),
            'total_corrupt_transactions': total_corrupt,
            'corrupt_market_prices_flagged': len(corrupt_market_prices),
            'ceiling_examples': ceiling_examples,
            'mcap_examples': mcap_examples,
            'divergence_examples': divergence_examples,
            'corrupt_market_examples': market_corrupt_examples
        },
        'expected': {
            'corrupt_transaction_prices': 0,
            'max_plausible_price': MAX_PLAUSIBLE_PRICE,
            'mcap_ratio_threshold': MCAP_RATIO_THRESHOLD,
            'high_divergence_ratio': HIGH_DIVERGENCE_RATIO,
            'note': 'Market cap sanity + split-corrected market divergence checks; corrupt market prices (WARN) reported separately',
            'threshold_type': 'target'
        }
    }


def check_aggregate_value_as_price(conn):
    """
    No insider transactions where aggregate value was written into the price field.

    Detects group-level pattern: within a (ticker, transaction_date, price) group,
    flag when 3+ distinct insiders (reporting_cik) share an identical price AND
    that price is >= $1,000 and an exact multiple of $1,000.

    This catches cases where the total transaction value was mistakenly written
    into the price field (e.g., NUTX: 5 insiders at $20,000 "price" on 2020-08-12,
    implying $0.63/share vs no price history to compare against).

    Cannot be detected at ingestion time — this is a group-level pattern, and
    insiders' Form 4s for a given date arrive over days or weeks. No single row
    carries enough information to detect it.

    The round-number requirement (exact $1k multiple) separates dollar amounts
    from share prices. Across all 171,077 type-'P' transactions, 4,781 groups
    have >=3 distinct insiders at identical price, but only 3 match the round
    >=$$1k multiple criterion — all three are corrupt, zero false positives.

    Threshold type: Target (zero tolerance after cleanup)
    """
    cur = conn.cursor()

    # Find groups with 3+ distinct insiders at identical price >= $1k and exact multiple
    cur.execute("""
        SELECT c.ticker, it.transaction_date, it.price,
               COUNT(DISTINCT it.reporting_cik) as insider_count,
               SUM(it.shares_transacted) as total_shares,
               COUNT(it.id) as txn_count
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.price IS NOT NULL
          AND it.price >= 1000.0
          AND it.price = CAST(it.price / 1000.0 AS INTEGER) * 1000.0
        GROUP BY c.ticker, it.transaction_date, it.price
        HAVING COUNT(DISTINCT it.reporting_cik) >= 3
        ORDER BY it.price DESC
    """)

    violations = cur.fetchall()

    examples = [
        f"{ticker} on {txn_date}: price=${price:,.0f}, {insider_count} insiders, {txn_count} txns, {total_shares:,.0f} shares"
        for ticker, txn_date, price, insider_count, total_shares, txn_count in violations[:20]
    ]

    return {
        'passed': len(violations) == 0,
        'measured': {
            'aggregate_value_as_price_count': len(violations),
            'examples': examples
        },
        'expected': {
            'aggregate_value_as_price_violations': 0,
            'note': '>=3 insiders at identical round >=$$1k price (aggregate value written as price)',
            'threshold_type': 'target'
        }
    }


def check_no_prices_for_unknown_tickers(conn):
    """
    No prices for tickers absent from companies table (except benchmark ETFs).

    Benchmark ETFs (SPY, IWM, MDY, QQQ, ^IXIC, URTH, ACWI) are legitimately in prices
    without companies entries. All other tickers in prices must exist in companies,
    or they represent orphaned data.

    Threshold type: Target (zero tolerance for non-benchmark orphans)
    """
    cur = conn.cursor()

    # Find tickers in prices but not in companies (excluding benchmarks)
    cur.execute("""
        SELECT DISTINCT p.ticker
        FROM prices p
        WHERE p.ticker NOT IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
          AND p.ticker NOT IN ('SPY', 'IWM', 'MDY', 'QQQ', '^IXIC', 'URTH', 'ACWI')
        ORDER BY p.ticker
    """)
    orphans = [row[0] for row in cur.fetchall()]

    return {
        'passed': len(orphans) == 0,
        'measured': {
            'orphan_ticker_count': len(orphans),
            'examples': orphans[:20] if orphans else []
        },
        'expected': {
            'orphan_tickers': 0,
            'note': 'Prices for unknown tickers (benchmarks SPY/IWM/MDY/QQQ/^IXIC/URTH/ACWI exempted)',
            'threshold_type': 'target'
        }
    }


def check_silently_never_attempted_prices(conn):
    """
    Companies whose ticker has no price rows AND no entry in price_backfill_failures.

    This is the "silently never attempted" state, distinct from both "covered" and
    "known-failed". Entities in this state quietly fall out of the pipeline without
    anything noticing — the reconciliation logic is missing them.

    Given universe is ~7,630, coverage ~5,300, known failures ~2,300, the residual
    should be near zero. A growing count signals the reconciliation logic is degrading.

    Threshold type: Target (should be near zero, but WARN not CRITICAL — this is a
    coverage gap rather than data corruption)
    """
    cur = conn.cursor()

    # Get total universe
    cur.execute("""
        SELECT COUNT(*)
        FROM companies
        WHERE ticker != 'NONE'
    """)
    total_universe = cur.fetchone()[0]

    # Get companies with price coverage
    cur.execute("""
        SELECT COUNT(DISTINCT ticker)
        FROM prices
        WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
    """)
    with_prices = cur.fetchone()[0]

    # Get companies with known failures
    cur.execute("""
        SELECT COUNT(*)
        FROM price_backfill_failures
        WHERE ticker IN (SELECT ticker FROM companies WHERE ticker != 'NONE')
    """)
    known_failures = cur.fetchone()[0]

    # Never attempted = universe - covered - known_failed
    never_attempted = total_universe - with_prices - known_failures

    # Get examples
    cur.execute("""
        SELECT c.ticker, c.name, c.cik
        FROM companies c
        WHERE c.ticker != 'NONE'
          AND c.ticker NOT IN (SELECT ticker FROM prices)
          AND c.ticker NOT IN (SELECT ticker FROM price_backfill_failures)
        ORDER BY c.ticker
        LIMIT 20
    """)
    examples = [
        f"{row[0]} (CIK {row[2] or 'NULL'}, {row[1] or 'no name'})"
        for row in cur.fetchall()
    ]

    return {
        'passed': never_attempted <= 100,  # Allow small residual, but warn if growing
        'measured': {
            'total_universe': total_universe,
            'with_prices': with_prices,
            'known_failures': known_failures,
            'never_attempted': never_attempted,
            'never_attempted_pct': round(never_attempted / total_universe * 100, 1) if total_universe > 0 else 0,
            'examples': examples
        },
        'expected': {
            'never_attempted_target': 0,
            'never_attempted_warn_threshold': 100,
            'note': 'Silently never attempted = universe - covered - known_failed',
            'threshold_type': 'target'
        }
    }


def check_base_rate_tables_exist(conn):
    """Base-rate tables exist and are non-empty.

    CRITICAL: publishing an artifact whose scorer returns nothing is a broken release.
    Tables are created by CREATE TABLE IF NOT EXISTS in scoring module, not by init_db.py.

    Threshold type: Target (zero tolerance)
    """
    cur = conn.cursor()

    required_tables = ['segment_base_rates', 'tier_base_rates']
    issues = {}

    for table in required_tables:
        # Check if table exists
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name = ?
        """, (table,))
        exists = cur.fetchone() is not None

        if not exists:
            issues[table] = {'exists': False, 'row_count': 0}
        else:
            # Check if non-empty
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = cur.fetchone()[0]
            if row_count == 0:
                issues[table] = {'exists': True, 'row_count': 0}

    return {
        'passed': len(issues) == 0,
        'measured': {
            'missing_or_empty_tables': issues if issues else 'none',
            'required_tables': required_tables
        },
        'expected': {
            'all_tables_exist': True,
            'all_tables_non_empty': True,
            'threshold_type': 'target',
            'note': 'Tables created by scoring module, not init_db.py; missing tables break scoring'
        }
    }


def check_segment_coverage_maintained(conn):
    """Segment coverage has not materially collapsed.

    Current baseline: 24 reportable segments (n>=60) covering 4,602 clusters,
    17 suppressed covering 547. WARN on material drop.

    Threshold type: Regression guard (detects training degradation)
    """
    cur = conn.cursor()

    # Check if tables exist first
    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='segment_base_rates'
    """)
    if not cur.fetchone():
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'regression_guard'},
            'details': 'segment_base_rates table does not exist yet'
        }

    # Get latest year
    cur.execute("SELECT MAX(year) FROM segment_base_rates")
    latest_year = cur.fetchone()[0]

    if not latest_year:
        return {
            'passed': False,
            'measured': {'latest_year': None},
            'expected': {'threshold_type': 'regression_guard'},
            'details': 'No data in segment_base_rates'
        }

    # MIN_SEGMENT_SIZE is hardcoded as 60 in the base_rates module
    MIN_SEGMENT_SIZE = 60

    # Count reportable and suppressed segments for latest year
    cur.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE n_samples >= {MIN_SEGMENT_SIZE}) as reportable,
            SUM(n_samples) FILTER (WHERE n_samples >= {MIN_SEGMENT_SIZE}) as reportable_clusters,
            COUNT(*) FILTER (WHERE n_samples < {MIN_SEGMENT_SIZE}) as suppressed,
            SUM(n_samples) FILTER (WHERE n_samples < {MIN_SEGMENT_SIZE}) as suppressed_clusters
        FROM segment_base_rates
        WHERE year = ?
    """, (latest_year,))
    row = cur.fetchone()
    reportable, reportable_clusters, suppressed, suppressed_clusters = row

    # Thresholds: allow 25% drop from baseline
    baseline_reportable = 24
    baseline_reportable_clusters = 4602
    min_reportable = int(baseline_reportable * 0.75)
    min_reportable_clusters = int(baseline_reportable_clusters * 0.75)

    passed = reportable >= min_reportable and reportable_clusters >= min_reportable_clusters

    return {
        'passed': passed,
        'measured': {
            'latest_year': latest_year,
            'reportable_segments': reportable or 0,
            'reportable_clusters': reportable_clusters or 0,
            'suppressed_segments': suppressed or 0,
            'suppressed_clusters': suppressed_clusters or 0,
        },
        'expected': {
            'min_reportable_segments': min_reportable,
            'min_reportable_clusters': min_reportable_clusters,
            'baseline_reportable': baseline_reportable,
            'baseline_reportable_clusters': baseline_reportable_clusters,
            'threshold_type': 'regression_guard',
            'note': 'Material drop signals training degradation'
        }
    }


def check_base_rate_sanity(conn):
    """Every hit_rate lies within its own [ci_lower, ci_upper].

    WARN severity: these are mathematical invariants; violations signal
    computation bugs in the training pipeline, not data issues.

    Also checks that all rates are in [0,1].

    Threshold type: Target (sanity check on model outputs)
    """
    cur = conn.cursor()

    # Check if tables exist first
    for table in ['segment_base_rates', 'tier_base_rates']:
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name = ?
        """, (table,))
        if not cur.fetchone():
            return {
                'passed': True,
                'measured': {},
                'expected': {'threshold_type': 'target'},
                'details': f'{table} table does not exist yet'
            }

    issues = []

    # Check segment_base_rates
    cur.execute("""
        SELECT sector, size_tier, year, hit_rate, ci_lower, ci_upper
        FROM segment_base_rates
        WHERE hit_rate < ci_lower OR hit_rate > ci_upper
           OR hit_rate < 0 OR hit_rate > 1
           OR ci_lower < 0 OR ci_lower > 1
           OR ci_upper < 0 OR ci_upper > 1
    """)
    for row in cur.fetchall():
        sector, size_tier, year, hit_rate, ci_lower, ci_upper = row
        reason = []
        if hit_rate < ci_lower or hit_rate > ci_upper:
            reason.append(f"rate {hit_rate:.3f} not in [{ci_lower:.3f}, {ci_upper:.3f}]")
        if not (0 <= hit_rate <= 1):
            reason.append(f"rate {hit_rate:.3f} out of [0,1]")
        if not (0 <= ci_lower <= 1) or not (0 <= ci_upper <= 1):
            reason.append(f"CI bounds out of [0,1]")
        issues.append(f"{sector}/{size_tier}/{year}: {', '.join(reason)}")

    # Check tier_base_rates
    cur.execute("""
        SELECT size_tier, year, hit_rate, ci_lower, ci_upper
        FROM tier_base_rates
        WHERE hit_rate < ci_lower OR hit_rate > ci_upper
           OR hit_rate < 0 OR hit_rate > 1
           OR ci_lower < 0 OR ci_lower > 1
           OR ci_upper < 0 OR ci_upper > 1
    """)
    for row in cur.fetchall():
        size_tier, year, hit_rate, ci_lower, ci_upper = row
        reason = []
        if hit_rate < ci_lower or hit_rate > ci_upper:
            reason.append(f"rate {hit_rate:.3f} not in [{ci_lower:.3f}, {ci_upper:.3f}]")
        if not (0 <= hit_rate <= 1):
            reason.append(f"rate {hit_rate:.3f} out of [0,1]")
        if not (0 <= ci_lower <= 1) or not (0 <= ci_upper <= 1):
            reason.append(f"CI bounds out of [0,1]")
        issues.append(f"{size_tier}/{year}: {', '.join(reason)}")

    return {
        'passed': len(issues) == 0,
        'measured': {
            'violation_count': len(issues),
            'examples': issues[:20] if issues else []
        },
        'expected': {
            'violations': 0,
            'note': 'Rate must be in its own CI and all values in [0,1]',
            'threshold_type': 'target'
        }
    }


def check_base_rate_training_staleness(conn):
    """Most recent training year is not stale relative to newest cluster.

    WARN: if the latest base-rate year is >1 year behind the newest cluster,
    the model is not being retrained on fresh data.

    Threshold type: Informational (monitors for training staleness)
    """
    cur = conn.cursor()

    # Check if tables exist first
    cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='segment_base_rates'
    """)
    if not cur.fetchone():
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'informational'},
            'details': 'segment_base_rates table does not exist yet'
        }

    # Get latest training year
    cur.execute("SELECT MAX(year) FROM segment_base_rates")
    latest_training_year = cur.fetchone()[0]

    if not latest_training_year:
        return {
            'passed': True,
            'measured': {'latest_training_year': None},
            'expected': {'threshold_type': 'informational'},
            'details': 'No data in segment_base_rates'
        }

    # Get newest cluster year from historical_clusters.csv
    clusters_path = _resolve_historical_clusters_path()
    if not clusters_path.exists():
        return {
            'passed': True,
            'measured': {
                'latest_training_year': latest_training_year,
                'clusters_path': str(clusters_path)
            },
            'expected': {'threshold_type': 'informational'},
            'details': 'historical_clusters.csv not found'
        }

    try:
        import csv as csv_module
        with open(clusters_path, 'r') as f:
            reader = csv_module.DictReader(f)
            rows = list(reader)
            if not rows:
                newest_cluster_year = None
            else:
                # Extract year from signal_date column
                signal_dates = [row['signal_date'] for row in rows if 'signal_date' in row]
                if signal_dates:
                    newest_cluster_year = max(int(d[:4]) for d in signal_dates if d and len(d) >= 4)
                else:
                    newest_cluster_year = None
    except Exception as e:
        return {
            'passed': True,
            'measured': {'latest_training_year': latest_training_year},
            'expected': {'threshold_type': 'informational'},
            'details': f'Failed to read clusters CSV: {e}'
        }

    if newest_cluster_year is None:
        return {
            'passed': True,
            'measured': {'latest_training_year': latest_training_year},
            'expected': {'threshold_type': 'informational'},
            'details': 'No cluster dates found in CSV'
        }

    staleness_years = newest_cluster_year - latest_training_year
    stale_threshold_years = 1

    passed = staleness_years <= stale_threshold_years

    result = {
        'passed': passed,
        'measured': {
            'latest_training_year': latest_training_year,
            'newest_cluster_year': newest_cluster_year,
            'staleness_years': staleness_years
        },
        'expected': {
            'max_staleness_years': stale_threshold_years,
            'threshold_type': 'informational',
            'note': 'Training year >1 year behind newest cluster signals stale model'
        }
    }

    if not passed:
        result['details'] = (
            f"Base rates are {staleness_years} years stale (training year {latest_training_year} "
            f"vs newest cluster year {newest_cluster_year}). Base rates are not being retrained on fresh clusters."
        )

    return result


def check_catastrophic_row_loss(conn):
    """
    Row counts for major tables are not materially below the previous release.

    Uses pipeline.provenance to compare against the last successful run's counts.
    A >10% drop in any major table is catastrophic and blocks release.

    Threshold type: Regression guard (prevents shipping data loss)
    """
    cur = conn.cursor()

    # Import provenance module (sibling)
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from provenance import get_provenance
    except ImportError:
        return {
            'passed': True,
            'measured': {},
            'expected': {'threshold_type': 'regression_guard'},
            'details': 'Provenance module not available'
        }

    # Tables to monitor
    tables = ['companies', 'insider_transactions', 'prices', 'shares_outstanding']
    current_counts = {}

    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        current_counts[table] = cur.fetchone()[0]

    # Try to get previous baseline from provenance
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    all_provenance = get_provenance(db_path)

    if not all_provenance:
        return {
            'passed': True,
            'measured': {
                'current_counts': current_counts,
                'has_provenance': False
            },
            'expected': {
                'note': 'No provenance data; recording baseline for future runs',
                'threshold_type': 'regression_guard'
            }
        }

    # Build previous counts from provenance
    previous_counts = {}
    for source_info in all_provenance:
        source = source_info.get('source')
        rows = source_info.get('rows_written')

        # Map source names to table names
        if source == 'insider_transactions' and rows:
            previous_counts['insider_transactions'] = rows
        elif source == 'prices' and rows:
            previous_counts['prices'] = rows
        elif source == 'shares_outstanding' and rows:
            previous_counts['shares_outstanding'] = rows

    if not previous_counts:
        return {
            'passed': True,
            'measured': {
                'current_counts': current_counts,
                'has_provenance': True,
                'previous_counts': 'none'
            },
            'expected': {
                'note': 'Provenance exists but no baseline counts recorded yet',
                'threshold_type': 'regression_guard'
            }
        }

    # Check for catastrophic drops (>10%)
    issues = {}
    for table, current in current_counts.items():
        if table not in previous_counts:
            continue

        previous = previous_counts[table]
        if previous == 0:
            continue

        drop_pct = ((previous - current) / previous) * 100

        if drop_pct > 10:  # >10% drop is catastrophic
            issues[table] = {
                'previous': previous,
                'current': current,
                'drop_pct': round(drop_pct, 1)
            }

    return {
        'passed': len(issues) == 0,
        'measured': {
            'current_counts': current_counts,
            'previous_counts': previous_counts,
            'catastrophic_drops': issues if issues else 'none'
        },
        'expected': {
            'max_drop_pct': 10,
            'note': '>10% drop in any major table blocks release',
            'threshold_type': 'regression_guard'
        }
    }


# ── Check registry ──

CHECKS = [
    # Freshness
    {
        'id': 'freshness.insider_transactions',
        'description': 'Newest insider transaction within 7 days',
        'severity': CRITICAL,
        'check_fn': check_insider_transactions_freshness
    },
    {
        'id': 'freshness.monthly_volume',
        'description': 'Recent complete month transaction volume >= 70% of historical median',
        'severity': WARN,
        'check_fn': check_insider_transactions_monthly_volume
    },

    # Coverage
    {
        'id': 'coverage.share_buyback',
        'description': 'Share buyback coverage >= 85% of eligible companies',
        'severity': CRITICAL,
        'check_fn': check_share_buyback_coverage
    },
    {
        'id': 'coverage.prices',
        'description': 'Price coverage >= 95% of eligible (excludes known-failed + inactive >90d)',
        'severity': CRITICAL,
        'check_fn': check_price_coverage
    },
    {
        'id': 'coverage.ambiguous_price_age',
        'description': 'Companies with price age 1-3 months (ambiguous: delisted vs thin trading)',
        'severity': WARN,
        'check_fn': check_ambiguous_price_age
    },
    {
        'id': 'coverage.inactive_growth',
        'description': 'Inactive population (>90d) grows ≤50 per run (spike signals fetch breakage)',
        'severity': WARN,
        'check_fn': check_inactive_price_growth
    },
    {
        'id': 'coverage.benchmark_etfs',
        'description': 'All benchmarks (SPY/IWM/MDY/QQQ/^IXIC/URTH/ACWI) continuous across backtest window',
        'severity': CRITICAL,
        'check_fn': check_benchmark_etf_coverage
    },

    # Integrity
    {
        'id': 'integrity.date_formats',
        'description': 'Zero malformed or impossible dates (catches "0022-10-12" century errors)',
        'severity': CRITICAL,
        'check_fn': check_date_format_integrity
    },
    {
        'id': 'integrity.price_values',
        'description': 'Zero non-positive prices (close <= 0 is invalid)',
        'severity': CRITICAL,
        'check_fn': check_price_values
    },
    {
        'id': 'integrity.extreme_price_values',
        'description': 'Prices >$100k (informational: flags split-adjusted extremes for review)',
        'severity': WARN,
        'check_fn': check_extreme_price_values
    },
    {
        'id': 'integrity.market_cap_plausibility',
        'description': 'No market cap above $5T (physically impossible)',
        'severity': CRITICAL,
        'check_fn': check_market_cap_plausibility
    },
    {
        'id': 'integrity.market_cap_coverage',
        'description': 'Market cap coverage >=90% for companies with insider purchases',
        'severity': CRITICAL,
        'check_fn': check_market_cap_coverage
    },
    {
        'id': 'integrity.market_cap_regression',
        'description': 'Usable market caps not >10% drop vs previous (guards deletion)',
        'severity': CRITICAL,
        'check_fn': check_market_cap_regression
    },
    {
        'id': 'integrity.market_cap_freshness',
        'description': 'Market cap freshness >=80% (pipeline health: fresh-derived vs stale-retained)',
        'severity': WARN,
        'check_fn': check_market_cap_freshness
    },
    {
        'id': 'integrity.market_cap_input_staleness',
        'description': 'Market cap inputs >1yr stale (monitors for growth)',
        'severity': WARN,
        'check_fn': check_market_cap_input_staleness
    },
    {
        'id': 'integrity.shares_basis_switches',
        'description': 'Consecutive-quarter share changes ≥5x without split events (basis switches)',
        'severity': WARN,
        'check_fn': check_shares_basis_switches
    },
    {
        'id': 'integrity.ticker_cik_coverage',
        'description': 'Companies whose CIK is absent from SEC map (informational)',
        'severity': WARN,
        'check_fn': check_ticker_cik_absent_from_sec
    },
    {
        'id': 'integrity.ticker_validity',
        'description': 'Ticker matches CIK per SEC map (actionable stale symbols)',
        'severity': WARN,
        'check_fn': check_ticker_validity_for_cik
    },
    {
        'id': 'integrity.no_duplicate_tickers',
        'description': 'No duplicate companies.ticker',
        'severity': CRITICAL,
        'check_fn': check_no_duplicate_tickers
    },
    {
        'id': 'integrity.no_orphaned_transactions',
        'description': 'No insider_transactions orphaned from companies',
        'severity': CRITICAL,
        'check_fn': check_no_orphaned_transactions
    },
    {
        'id': 'integrity.transaction_price_plausibility',
        'description': 'No corrupt insider_transactions.price values (ceiling violations or market divergence)',
        'severity': CRITICAL,
        'check_fn': check_insider_transaction_price_plausibility
    },
    {
        'id': 'integrity.aggregate_value_as_price',
        'description': 'No aggregate-value-as-price violations (>=3 insiders at identical round >=$$1k price)',
        'severity': CRITICAL,
        'check_fn': check_aggregate_value_as_price
    },

    # Derived artifacts
    {
        'id': 'artifacts.files_exist',
        'description': 'historical_clusters.csv, latest_signals.csv, insider_frozen.json.gz exist and non-empty',
        'severity': CRITICAL,
        'check_fn': check_derived_artifacts_exist
    },
    {
        'id': 'artifacts.consistency',
        'description': 'Tickers in historical_clusters.csv exist in companies table',
        'severity': CRITICAL,
        'check_fn': check_derived_artifacts_consistency
    },

    # Internal consistency (bookkeeping vs data)
    {
        'id': 'consistency.failure_table_contradictions',
        'description': 'No entity in both *_failures tables and their corresponding data',
        'severity': CRITICAL,
        'check_fn': check_failure_table_contradictions
    },
    {
        'id': 'consistency.failure_table_coverage',
        'description': 'All *_failures tables must be mapped to a contradiction check',
        'severity': WARN,
        'check_fn': check_failure_table_coverage
    },
    {
        'id': 'consistency.orphan_prices',
        'description': 'No prices for tickers absent from companies (benchmarks exempted)',
        'severity': CRITICAL,
        'check_fn': check_no_prices_for_unknown_tickers
    },
    {
        'id': 'consistency.never_attempted_prices',
        'description': 'Companies silently never attempted (no prices, no failure record)',
        'severity': WARN,
        'check_fn': check_silently_never_attempted_prices
    },

    # Catastrophic loss guard
    {
        'id': 'catastrophic_loss.row_counts',
        'description': 'Row counts not >10% below previous release',
        'severity': WARN,
        'check_fn': check_catastrophic_row_loss
    },

    # Base-rate model
    {
        'id': 'base_rates.tables_exist',
        'description': 'Base-rate tables exist and are non-empty',
        'severity': CRITICAL,
        'check_fn': check_base_rate_tables_exist
    },
    {
        'id': 'base_rates.segment_coverage',
        'description': 'Segment coverage maintained (>=18 reportable, >=3451 clusters)',
        'severity': WARN,
        'check_fn': check_segment_coverage_maintained
    },
    {
        'id': 'base_rates.sanity',
        'description': 'All hit_rates within their CIs and in [0,1]',
        'severity': WARN,
        'check_fn': check_base_rate_sanity
    },
    {
        'id': 'base_rates.training_staleness',
        'description': 'Training year not >1 year behind newest cluster (informational)',
        'severity': WARN,
        'check_fn': check_base_rate_training_staleness
    }
]


def get_check_by_id(check_id):
    """Get a check by its ID, or None if not found."""
    for check in CHECKS:
        if check['id'] == check_id:
            return check
    return None


def list_checks():
    """Return list of all check IDs with descriptions and severities."""
    return [
        {
            'id': c['id'],
            'description': c['description'],
            'severity': c['severity']
        }
        for c in CHECKS
    ]
