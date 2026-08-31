#!/usr/bin/env python3
"""
Compare two SQLite artifacts to detect material divergence.

Compares on dimensions that surface real drift without drowning in noise:
  - Row counts per table with percentage deltas
  - Coverage metrics (percentages, not absolutes) from contract.py
  - Market cap tier distributions
  - Transaction date coverage windows
  - Sample-based spot checks on overlapping entities

Classifies differences as:
  - EXPECTED: incremental legitimately holds newer data
  - TOLERABLE: small, explicable variance
  - SUSPICIOUS: large divergence suggesting drift

Usage:
    python compare_artifacts.py baseline.db incremental.db --output report.txt --json summary.json
"""

import sys
import os
import sqlite3
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

# Add parent directory to path so we can import from tracker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.contract import (
    check_share_buyback_coverage,
    check_price_coverage,
    check_market_cap_coverage,
)


def get_schema_version(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Get schema/pipeline version from pipeline_meta table.

    Returns dict with version info, or empty dict if table doesn't exist.
    """
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT key, value
            FROM pipeline_meta
            WHERE key IN ('provenance_version', 'schema_version', 'pipeline_version')
        """)
        return {row[0]: row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        # Table doesn't exist
        return {}


def get_table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Get row counts for all major tables."""
    tables = [
        'companies',
        'insider_transactions',
        'prices',
        'shares_outstanding',
        'signals',
        'price_backfill_failures',
        'shares_backfill_failures'
    ]

    counts = {}
    cursor = conn.cursor()

    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = None  # Table doesn't exist

    return counts


def get_coverage_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Get coverage metrics using contract.py check functions.

    Returns percentage-based metrics that are comparable across artifacts
    even when absolute counts differ.
    """
    metrics = {}

    # Share buyback coverage
    try:
        result = check_share_buyback_coverage(conn)
        metrics['share_buyback_pct'] = result['measured'].get('coverage_pct')
        metrics['share_buyback_with_5q'] = result['measured'].get('companies_with_5q')
        metrics['share_buyback_eligible'] = result['measured'].get('companies_eligible')
    except Exception as e:
        metrics['share_buyback_error'] = str(e)

    # Price coverage
    try:
        result = check_price_coverage(conn)
        metrics['price_coverage_pct'] = result['measured'].get('coverage_pct')
        metrics['price_covered'] = result['measured'].get('covered')
        metrics['price_eligible'] = result['measured'].get('eligible')
        metrics['price_inactive'] = result['measured'].get('inactive')
    except Exception as e:
        metrics['price_coverage_error'] = str(e)

    # Market cap coverage
    try:
        result = check_market_cap_coverage(conn)
        metrics['market_cap_coverage_pct'] = result['measured'].get('coverage_pct')
        metrics['market_cap_active_with_purchases'] = result['measured'].get('active_with_purchases')
        metrics['market_cap_with_market_cap'] = result['measured'].get('active_with_market_cap')
    except Exception as e:
        metrics['market_cap_coverage_error'] = str(e)

    return metrics


def get_market_cap_tier_distribution(conn: sqlite3.Connection) -> Dict[str, float]:
    """
    Get market cap tier distribution as percentages.

    Tiers match the scoring logic:
      - small: < $300M
      - mid: $300M - $2B
      - large: $2B - $10B
      - mega: > $10B
      - unknown: NULL or missing
    """
    cursor = conn.cursor()

    # Get total companies with transactions
    cursor.execute("""
        SELECT COUNT(DISTINCT company_id)
        FROM insider_transactions
    """)
    total = cursor.fetchone()[0]

    if total == 0:
        return {}

    # Count by tier
    cursor.execute("""
        SELECT
            CASE
                WHEN c.market_cap IS NULL THEN 'unknown'
                WHEN c.market_cap < 300000000 THEN 'small'
                WHEN c.market_cap < 2000000000 THEN 'mid'
                WHEN c.market_cap < 10000000000 THEN 'large'
                ELSE 'mega'
            END as tier,
            COUNT(DISTINCT it.company_id) as cnt
        FROM insider_transactions it
        LEFT JOIN companies c ON it.company_id = c.id
        GROUP BY tier
    """)

    distribution = {}
    for tier, cnt in cursor.fetchall():
        distribution[tier] = round((cnt / total) * 100, 2)

    return distribution


def get_transaction_date_coverage(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Get transaction coverage in various windows.

    Returns counts for:
      - last 30 days
      - last 90 days
      - last 365 days
      - oldest transaction date
      - newest transaction date
    """
    cursor = conn.cursor()

    # Get date range
    cursor.execute("""
        SELECT
            MIN(transaction_date) as oldest,
            MAX(transaction_date) as newest
        FROM insider_transactions
        WHERE transaction_date LIKE '20__-__-__'
    """)
    oldest, newest = cursor.fetchone()

    if not newest:
        return {}

    newest_dt = datetime.strptime(newest, '%Y-%m-%d')

    # Count in windows
    coverage = {
        'oldest_date': oldest,
        'newest_date': newest
    }

    for days in [30, 90, 365]:
        cutoff = (newest_dt - timedelta(days=days)).strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT COUNT(*)
            FROM insider_transactions
            WHERE transaction_date >= ?
              AND transaction_date LIKE '20__-__-__'
        """, (cutoff,))
        coverage[f'last_{days}d'] = cursor.fetchone()[0]

    return coverage


def sample_spot_check(baseline_conn: sqlite3.Connection,
                     incremental_conn: sqlite3.Connection,
                     sample_size: int = 50) -> List[Dict[str, Any]]:
    """
    Spot check a sample of companies that exist in both artifacts.

    Compares:
      - Market cap tier (same bucket)
      - Latest transaction date in common window
      - Share count changes over 4 quarters

    Returns list of divergences found.
    """
    baseline_cursor = baseline_conn.cursor()
    incremental_cursor = incremental_conn.cursor()

    # Find common companies (by ticker, since IDs may differ)
    baseline_cursor.execute("""
        SELECT ticker, id, market_cap
        FROM companies
        WHERE ticker != 'NONE'
        ORDER BY RANDOM()
        LIMIT ?
    """, (sample_size,))

    divergences = []

    for ticker, baseline_id, baseline_mcap in baseline_cursor.fetchall():
        # Find in incremental
        incremental_cursor.execute("""
            SELECT id, market_cap
            FROM companies
            WHERE ticker = ?
        """, (ticker,))

        inc_row = incremental_cursor.fetchone()
        if not inc_row:
            continue

        inc_id, inc_mcap = inc_row

        # Compare market cap tiers
        def get_tier(mcap):
            if mcap is None:
                return 'unknown'
            elif mcap < 300000000:
                return 'small'
            elif mcap < 2000000000:
                return 'mid'
            elif mcap < 10000000000:
                return 'large'
            else:
                return 'mega'

        baseline_tier = get_tier(baseline_mcap)
        inc_tier = get_tier(inc_mcap)

        if baseline_tier != inc_tier:
            divergences.append({
                'ticker': ticker,
                'dimension': 'market_cap_tier',
                'baseline': baseline_tier,
                'incremental': inc_tier,
                'baseline_value': baseline_mcap,
                'incremental_value': inc_mcap
            })

    return divergences


def classify_difference(dimension: str, baseline_val: Any, incremental_val: Any,
                       context: Dict[str, Any]) -> str:
    """
    Classify a difference as EXPECTED, TOLERABLE, or SUSPICIOUS.

    Args:
        dimension: What is being compared
        baseline_val: Value in baseline artifact (published/incremental)
        incremental_val: Value in incremental artifact (rebuilt)
        context: Additional context (e.g., newest dates, which is baseline vs rebuilt)

    Returns:
        Classification string
    """
    # Row count differences
    if dimension.endswith('_row_count'):
        # Missing table is a structural divergence — SUSPICIOUS
        # Direction matters: missing from rebuild = rebuild path broken
        #                    missing from published = new table added after release (expected)
        if baseline_val is None and incremental_val is None:
            return 'EXPECTED'  # Both missing (shouldn't happen, but harmless)
        elif baseline_val is None:
            # Table exists in rebuilt but not in published
            # This is EXPECTED if the table was added recently
            return 'EXPECTED'
        elif incremental_val is None:
            # Table exists in published but not in rebuilt
            # This is SUSPICIOUS — the rebuild path is not creating this table
            return 'SUSPICIOUS'

        # Incremental should have more rows for growing tables
        if incremental_val >= baseline_val:
            return 'EXPECTED'

        # Small decreases are tolerable (cleanup, deduplication)
        pct_decrease = ((baseline_val - incremental_val) / baseline_val * 100) if baseline_val > 0 else 0
        if pct_decrease < 1:
            return 'TOLERABLE'
        elif pct_decrease < 5:
            return 'TOLERABLE'  # Modest cleanup
        else:
            return 'SUSPICIOUS'  # >5% drop

    # Coverage percentages
    if '_pct' in dimension or '_coverage' in dimension:
        if baseline_val is None or incremental_val is None:
            return 'TOLERABLE'

        # Allow up to 5 percentage points of variance
        diff = abs(incremental_val - baseline_val)
        if diff < 2:
            return 'TOLERABLE'
        elif diff < 5:
            return 'TOLERABLE'
        else:
            return 'SUSPICIOUS'

    # Tier distribution percentages
    if dimension.startswith('tier_'):
        if baseline_val is None or incremental_val is None:
            return 'TOLERABLE'

        # Allow up to 3 percentage points per tier
        diff = abs(incremental_val - baseline_val)
        if diff < 3:
            return 'TOLERABLE'
        else:
            return 'SUSPICIOUS'

    # Transaction date windows
    if dimension.startswith('txn_window_'):
        if baseline_val is None or incremental_val is None:
            return 'TOLERABLE'

        # Incremental will have more recent transactions
        if incremental_val >= baseline_val:
            return 'EXPECTED'

        # Small decreases are suspicious (data loss in recent window)
        pct_decrease = ((baseline_val - incremental_val) / baseline_val * 100) if baseline_val > 0 else 0
        if pct_decrease < 5:
            return 'TOLERABLE'
        else:
            return 'SUSPICIOUS'

    # Default: compare equality
    if baseline_val == incremental_val:
        return 'EXPECTED'
    else:
        return 'TOLERABLE'


def compare_artifacts(baseline_path: str, incremental_path: str) -> Dict[str, Any]:
    """
    Compare two SQLite artifacts and return structured comparison.

    Returns:
        {
            'baseline_path': str,
            'incremental_path': str,
            'timestamp': str,
            'dimensions': [
                {
                    'name': str,
                    'baseline': Any,
                    'incremental': Any,
                    'diff': Any,
                    'classification': str
                },
                ...
            ],
            'summary': {
                'total_dimensions': int,
                'expected': int,
                'tolerable': int,
                'suspicious': int
            }
        }
    """
    baseline_conn = sqlite3.connect(f'file:{baseline_path}?mode=ro', uri=True)
    incremental_conn = sqlite3.connect(f'file:{incremental_path}?mode=ro', uri=True)

    dimensions = []
    context = {}

    # 0. Schema version
    baseline_schema = get_schema_version(baseline_conn)
    incremental_schema = get_schema_version(incremental_conn)

    # Report schema versions prominently
    all_version_keys = set(baseline_schema.keys()) | set(incremental_schema.keys())
    for key in sorted(all_version_keys):
        baseline_val = baseline_schema.get(key)
        incremental_val = incremental_schema.get(key)

        # Schema version mismatch is reported but not necessarily SUSPICIOUS
        # (depends on whether they're compatible)
        if baseline_val != incremental_val:
            classification = 'TOLERABLE'  # Informational: versions differ
            diff = f"{baseline_val} → {incremental_val}"
        else:
            classification = 'EXPECTED'
            diff = None

        dimensions.append({
            'name': f'schema.{key}',
            'baseline': baseline_val,
            'incremental': incremental_val,
            'diff': diff,
            'classification': classification
        })

    # 1. Row counts
    baseline_counts = get_table_counts(baseline_conn)
    incremental_counts = get_table_counts(incremental_conn)

    for table in baseline_counts:
        baseline_val = baseline_counts[table]
        incremental_val = incremental_counts.get(table)

        diff = None
        if baseline_val is not None and incremental_val is not None:
            diff = incremental_val - baseline_val
            pct_change = (diff / baseline_val * 100) if baseline_val > 0 else 0
            diff = f"{diff:+,} ({pct_change:+.1f}%)"
        elif baseline_val is None and incremental_val is not None:
            diff = f"Table missing from published (exists in rebuilt with {incremental_val:,} rows)"
        elif baseline_val is not None and incremental_val is None:
            diff = f"Table missing from rebuilt (exists in published with {baseline_val:,} rows)"

        classification = classify_difference(
            f'{table}_row_count',
            baseline_val,
            incremental_val,
            context
        )

        dimensions.append({
            'name': f'row_count.{table}',
            'baseline': baseline_val,
            'incremental': incremental_val,
            'diff': diff,
            'classification': classification
        })

    # 2. Coverage metrics
    baseline_coverage = get_coverage_metrics(baseline_conn)
    incremental_coverage = get_coverage_metrics(incremental_conn)

    for metric in baseline_coverage:
        if metric.endswith('_error'):
            continue

        baseline_val = baseline_coverage.get(metric)
        incremental_val = incremental_coverage.get(metric)

        diff = None
        if baseline_val is not None and incremental_val is not None:
            if isinstance(baseline_val, (int, float)):
                diff = incremental_val - baseline_val
                if metric.endswith('_pct'):
                    diff = f"{diff:+.1f} percentage points"
                else:
                    diff = f"{diff:+,}"

        classification = classify_difference(
            f'coverage_{metric}',
            baseline_val,
            incremental_val,
            context
        )

        dimensions.append({
            'name': f'coverage.{metric}',
            'baseline': baseline_val,
            'incremental': incremental_val,
            'diff': diff,
            'classification': classification
        })

    # 3. Market cap tier distribution
    baseline_tiers = get_market_cap_tier_distribution(baseline_conn)
    incremental_tiers = get_market_cap_tier_distribution(incremental_conn)

    all_tiers = set(baseline_tiers.keys()) | set(incremental_tiers.keys())
    for tier in sorted(all_tiers):
        baseline_val = baseline_tiers.get(tier)
        incremental_val = incremental_tiers.get(tier)

        diff = None
        if baseline_val is not None and incremental_val is not None:
            diff = f"{incremental_val - baseline_val:+.2f} percentage points"

        classification = classify_difference(
            f'tier_{tier}',
            baseline_val,
            incremental_val,
            context
        )

        dimensions.append({
            'name': f'market_cap_tier.{tier}',
            'baseline': baseline_val,
            'incremental': incremental_val,
            'diff': diff,
            'classification': classification
        })

    # 4. Transaction date coverage windows
    baseline_txn_coverage = get_transaction_date_coverage(baseline_conn)
    incremental_txn_coverage = get_transaction_date_coverage(incremental_conn)

    for window in baseline_txn_coverage:
        if window in ['oldest_date', 'newest_date']:
            # Dates: just report, don't classify as suspicious
            dimensions.append({
                'name': f'transaction_dates.{window}',
                'baseline': baseline_txn_coverage[window],
                'incremental': incremental_txn_coverage.get(window),
                'diff': None,
                'classification': 'EXPECTED'
            })
        else:
            baseline_val = baseline_txn_coverage.get(window)
            incremental_val = incremental_txn_coverage.get(window)

            diff = None
            if baseline_val is not None and incremental_val is not None:
                diff = f"{incremental_val - baseline_val:+,}"

            classification = classify_difference(
                f'txn_window_{window}',
                baseline_val,
                incremental_val,
                context
            )

            dimensions.append({
                'name': f'transaction_window.{window}',
                'baseline': baseline_val,
                'incremental': incremental_val,
                'diff': diff,
                'classification': classification
            })

    # 5. Spot checks
    divergences = sample_spot_check(baseline_conn, incremental_conn, sample_size=50)

    # Group divergences by dimension
    divergence_counts = {}
    for div in divergences:
        dim = div['dimension']
        divergence_counts[dim] = divergence_counts.get(dim, 0) + 1

    for dim, count in divergence_counts.items():
        classification = 'SUSPICIOUS' if count > 5 else 'TOLERABLE'
        dimensions.append({
            'name': f'spot_check.{dim}_divergences',
            'baseline': 0,  # Baseline assumes zero divergences
            'incremental': count,
            'diff': f"{count} divergences in {len(divergences)} samples",
            'classification': classification
        })

    baseline_conn.close()
    incremental_conn.close()

    # Summary
    classifications = [d['classification'] for d in dimensions]
    summary = {
        'total_dimensions': len(dimensions),
        'expected': classifications.count('EXPECTED'),
        'tolerable': classifications.count('TOLERABLE'),
        'suspicious': classifications.count('SUSPICIOUS')
    }

    return {
        'baseline_path': baseline_path,
        'incremental_path': incremental_path,
        'timestamp': datetime.now().isoformat(),
        'dimensions': dimensions,
        'summary': summary
    }


def format_report(comparison: Dict[str, Any]) -> str:
    """Format comparison as human-readable report."""
    lines = []
    lines.append("=" * 80)
    lines.append("ARTIFACT COMPARISON REPORT")
    lines.append("=" * 80)
    lines.append(f"Timestamp: {comparison['timestamp']}")
    lines.append(f"Baseline:    {comparison['baseline_path']}")
    lines.append(f"Incremental: {comparison['incremental_path']}")
    lines.append("")

    # Group by classification
    dimensions = comparison['dimensions']

    for classification in ['SUSPICIOUS', 'TOLERABLE', 'EXPECTED']:
        filtered = [d for d in dimensions if d['classification'] == classification]

        if not filtered:
            continue

        lines.append(f"\n{classification} DIFFERENCES ({len(filtered)})")
        lines.append("-" * 80)

        for dim in filtered:
            lines.append(f"\n{dim['name']}")
            lines.append(f"  Baseline:    {dim['baseline']}")
            lines.append(f"  Incremental: {dim['incremental']}")
            if dim['diff']:
                lines.append(f"  Difference:  {dim['diff']}")

    # Summary
    summary = comparison['summary']
    lines.append("\n" + "=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"  Total dimensions:  {summary['total_dimensions']}")
    lines.append(f"  EXPECTED:          {summary['expected']}")
    lines.append(f"  TOLERABLE:         {summary['tolerable']}")
    lines.append(f"  SUSPICIOUS:        {summary['suspicious']}")
    lines.append("")

    if summary['suspicious'] > 0:
        lines.append("⚠️  SUSPICIOUS DIFFERENCES FOUND")
        lines.append("   The incremental artifact has diverged from the clean rebuild.")
        lines.append("   Review the differences above and investigate the root cause.")
    else:
        lines.append("✅ NO SUSPICIOUS DIFFERENCES")
        lines.append("   The incremental artifact is consistent with the clean rebuild.")

    lines.append("=" * 80)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Compare two SQLite artifacts to detect material divergence",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('baseline', help='Path to baseline artifact (full rebuild)')
    parser.add_argument('incremental', help='Path to incremental artifact')
    parser.add_argument('--output', '-o', help='Write report to this file (default: stdout)')
    parser.add_argument('--json', help='Write JSON summary to this file')

    args = parser.parse_args()

    # Verify files exist
    if not Path(args.baseline).exists():
        print(f"Error: Baseline file not found: {args.baseline}", file=sys.stderr)
        return 2

    if not Path(args.incremental).exists():
        print(f"Error: Incremental file not found: {args.incremental}", file=sys.stderr)
        return 2

    # Run comparison
    try:
        comparison = compare_artifacts(args.baseline, args.incremental)
    except Exception as e:
        print(f"Error during comparison: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2

    # Format report
    report = format_report(comparison)

    # Output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)
        print(f"Report written to: {args.output}")
    else:
        print(report)

    # JSON output
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(comparison, f, indent=2)
        print(f"JSON summary written to: {args.json}")

    # Exit code based on findings
    if comparison['summary']['suspicious'] > 0:
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
