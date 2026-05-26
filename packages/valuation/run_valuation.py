#!/usr/bin/env python3
"""
Damodaran Investment Valuation Agent — CLI Entrypoint

Usage:
    python run_valuation.py TICKER [options]

Options:
    --format telegram   Output condensed Telegram-friendly summary to stdout
    --format full       Print full report to stdout (default: save to file only)
    --no-cache          Force fresh data fetch (bypass all caches)
    --output-dir PATH   Where to save report (default: ./output/reports/)

Examples:
    python run_valuation.py AAPL
    python run_valuation.py MSFT --format telegram
    python run_valuation.py JPM --no-cache
    python run_valuation.py AAPL --format full
"""

import sys
import argparse
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    parser = argparse.ArgumentParser(
        description="Damodaran-style investment valuation agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("ticker", help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument(
        "--format",
        choices=["telegram", "full", "none"],
        default="none",
        help="Output format: telegram (condensed), full (print report), none (save only)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force fresh data fetch, bypass all caches",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to save the report (default: output/reports/)",
    )

    args = parser.parse_args()

    from agent.orchestrator import run_valuation

    results = run_valuation(
        ticker=args.ticker.upper(),
        output_dir=args.output_dir,
        telegram_format=(args.format == "telegram"),
        no_cache=args.no_cache,
    )

    if args.format == "telegram":
        summary = results.get("telegram_summary", "No summary generated.")
        print("\n" + "="*50)
        print("TELEGRAM SUMMARY:")
        print("="*50)
        print(summary)

    elif args.format == "full":
        report_path = results.get("report_path")
        if report_path and Path(report_path).exists():
            print("\n" + "="*50)
            print(Path(report_path).read_text())

    # Always print the report path if saved
    if results.get("report_path"):
        print(f"\nReport: {results['report_path']}")

    # Exit with error if critical failures occurred
    critical_errors = [e for e in results.get("errors", []) if "failed" in e.lower()]
    if critical_errors and not results.get("synthesis"):
        sys.exit(1)


if __name__ == "__main__":
    main()
