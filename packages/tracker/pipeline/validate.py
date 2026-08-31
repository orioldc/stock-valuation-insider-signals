#!/usr/bin/env python3
"""
Pipeline validation gate — run data quality checks and block release on failure.

Usage:
    # Run all checks
    python -m tracker.pipeline.validate

    # Run specific check
    python -m tracker.pipeline.validate --check freshness.insider_transactions

    # List all checks
    python -m tracker.pipeline.validate --list

    # Write JSON report
    python -m tracker.pipeline.validate --json-report /path/to/report.json

Exit codes:
    0 - All CRITICAL checks passed (WARN failures OK)
    1 - One or more CRITICAL checks failed
    2 - Validation error (e.g., DB not found, check crashed)

The JSON report contains machine-readable results for each check:
    {
        "run_timestamp": "2026-08-31T10:30:00",
        "database_path": "/path/to/insider_signals.db",
        "checks": [
            {
                "id": "freshness.insider_transactions",
                "description": "...",
                "severity": "CRITICAL",
                "passed": true,
                "measured": {...},
                "expected": {...},
                "details": "..."
            },
            ...
        ],
        "summary": {
            "total": 11,
            "passed": 8,
            "failed": 3,
            "critical_failed": 1
        }
    }
"""

import sys
import os
import sqlite3
import argparse
import logging
import json
from datetime import datetime

# Add parent directory to path so we can import from tracker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.contract import CHECKS, get_check_by_id, list_checks, CRITICAL, WARN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


def get_db_path():
    """Get database path relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tracker_dir = os.path.dirname(script_dir)
    db_path = os.path.join(tracker_dir, "db", "insider_signals.db")
    return db_path


def run_check(conn, check_spec):
    """
    Run a single check.

    Returns dict with check metadata and result:
        {
            'id': str,
            'description': str,
            'severity': str,
            'passed': bool,
            'measured': dict,
            'expected': dict,
            'details': str (optional),
            'error': str (if check crashed)
        }
    """
    check_id = check_spec['id']
    description = check_spec['description']
    severity = check_spec['severity']
    check_fn = check_spec['check_fn']

    try:
        result = check_fn(conn)
        return {
            'id': check_id,
            'description': description,
            'severity': severity,
            'passed': result.get('passed', False),
            'measured': result.get('measured', {}),
            'expected': result.get('expected', {}),
            'details': result.get('details', '')
        }
    except Exception as e:
        logger.exception(f"Check {check_id} crashed: {e}")
        return {
            'id': check_id,
            'description': description,
            'severity': severity,
            'passed': False,
            'measured': {},
            'expected': {},
            'error': str(e)
        }


def format_value(val):
    """Format a value for display (pretty-print dicts/lists)."""
    if isinstance(val, dict):
        # Format nested dicts compactly
        items = [f"{k}={format_value(v)}" for k, v in val.items()]
        return "{" + ", ".join(items) + "}"
    elif isinstance(val, list):
        if not val:
            return "[]"
        # Show first few items
        if len(val) <= 3:
            return str(val)
        return f"[{', '.join(map(str, val[:3]))}, ... +{len(val)-3} more]"
    else:
        return str(val)


def print_report(results, verbose=True):
    """Print human-readable report to console."""
    print("=" * 80)
    print("DATA QUALITY VALIDATION REPORT")
    print("=" * 80)
    print(f"Run timestamp: {results['run_timestamp']}")
    print(f"Database: {results['database_path']}")
    print()

    # Group by severity
    critical_checks = [c for c in results['checks'] if c['severity'] == CRITICAL]
    warn_checks = [c for c in results['checks'] if c['severity'] == WARN]

    for severity, checks in [(CRITICAL, critical_checks), (WARN, warn_checks)]:
        print(f"\n{severity} CHECKS ({len(checks)} total)")
        print("-" * 80)

        for check in checks:
            status = "✓ PASS" if check['passed'] else "✗ FAIL"
            status_mark = "✓" if check['passed'] else "✗"

            print(f"\n{status_mark} {check['id']}")
            print(f"  {check['description']}")

            if not check['passed'] or verbose:
                # Show measured values
                if check.get('measured'):
                    print(f"  Measured: {format_value(check['measured'])}")
                if check.get('expected'):
                    print(f"  Expected: {format_value(check['expected'])}")

            if check.get('details'):
                print(f"  Details: {check['details']}")

            if check.get('error'):
                print(f"  ERROR: {check['error']}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    summary = results['summary']
    print(f"  Total checks:        {summary['total']}")
    print(f"  Passed:              {summary['passed']}")
    print(f"  Failed:              {summary['failed']}")
    print(f"  CRITICAL failed:     {summary['critical_failed']}")
    print()

    if summary['critical_failed'] > 0:
        print("❌ VALIDATION FAILED — One or more CRITICAL checks failed")
        print("   This artifact MUST NOT be released")
    else:
        print("✅ VALIDATION PASSED — All CRITICAL checks passed")
        if summary['failed'] > 0:
            print(f"   ({summary['failed']} WARN checks failed — advisory only)")

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Run data quality validation checks on the insider signals database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all checks
  python -m tracker.pipeline.validate

  # Run specific check
  python -m tracker.pipeline.validate --check freshness.insider_transactions

  # List all checks
  python -m tracker.pipeline.validate --list

  # Write JSON report
  python -m tracker.pipeline.validate --json-report validation_report.json
        """
    )
    parser.add_argument(
        '--check',
        metavar='CHECK_ID',
        help='Run only the specified check (use --list to see IDs)'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all available checks and exit'
    )
    parser.add_argument(
        '--json-report',
        metavar='PATH',
        help='Write machine-readable JSON report to this path'
    )
    parser.add_argument(
        '--db',
        metavar='PATH',
        help='Path to database (default: auto-detect from script location)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show details for passing checks too (default: only failures)'
    )

    args = parser.parse_args()

    # List checks
    if args.list:
        print("Available checks:")
        print()
        for check_info in list_checks():
            print(f"  {check_info['id']}")
            print(f"    {check_info['description']}")
            print(f"    Severity: {check_info['severity']}")
            print()
        return 0

    # Get database path
    db_path = args.db or get_db_path()
    if not os.path.exists(db_path):
        logger.error(f"Database not found: {db_path}")
        return 2

    # Open database in read-only mode
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    except Exception as e:
        logger.error(f"Failed to open database: {e}")
        return 2

    # Determine which checks to run
    if args.check:
        check_spec = get_check_by_id(args.check)
        if not check_spec:
            logger.error(f"Check not found: {args.check}")
            logger.info("Use --list to see available checks")
            return 2
        checks_to_run = [check_spec]
    else:
        checks_to_run = CHECKS

    # Run checks
    logger.info(f"Running {len(checks_to_run)} checks against {db_path}")
    check_results = []

    for check_spec in checks_to_run:
        logger.debug(f"Running check: {check_spec['id']}")
        result = run_check(conn, check_spec)
        check_results.append(result)

    conn.close()

    # Build results structure
    passed = sum(1 for c in check_results if c['passed'])
    failed = len(check_results) - passed
    critical_failed = sum(1 for c in check_results if c['severity'] == CRITICAL and not c['passed'])

    results = {
        'run_timestamp': datetime.now().isoformat(),
        'database_path': db_path,
        'checks': check_results,
        'summary': {
            'total': len(check_results),
            'passed': passed,
            'failed': failed,
            'critical_failed': critical_failed
        }
    }

    # Print report
    print_report(results, verbose=args.verbose)

    # Write JSON report if requested
    if args.json_report:
        try:
            with open(args.json_report, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info(f"JSON report written to: {args.json_report}")
        except Exception as e:
            logger.error(f"Failed to write JSON report: {e}")
            return 2

    # Exit with appropriate code
    if critical_failed > 0:
        return 1
    else:
        return 0


if __name__ == '__main__':
    sys.exit(main())
