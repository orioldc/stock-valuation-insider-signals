#!/usr/bin/env python3
"""
Market Cap Recomputation Script — Derive market caps from latest prices and shares.

This script fixes the structural staleness in the monthly workflow: refresh.py Phase 2.7
computes market caps BEFORE prices and shares are backfilled, leaving every company's
tier one cycle behind reality. This script runs AFTER all data refresh jobs complete,
recomputing market caps from the freshest available data.

Market cap drives get_tier(), which determines both tier weights and the peer bucket
for percentile ranking. A stale market cap silently puts companies in the wrong size
tier, undermining the entire composite score.

Features:
  - Idempotent: re-running is a clean no-op
  - Provenance: records run metadata for freshness introspection
  - Batched commits: progress logging every 500 companies
  - Schema migration: adds market_cap_asof column if missing (CI seeds from old release)
  - Dry-run mode (default): reports what would change without writing

Usage:
    python scripts/recompute_market_caps.py                # dry-run (reports only)
    python scripts/recompute_market_caps.py --write        # apply to DB
"""

import sys
import os
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta
import yfinance as yf

# Add tracker to path for provenance
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_DIR = os.path.join(SCRIPT_DIR, "..")
sys.path.insert(0, TRACKER_DIR)

# Add signals module to path for get_tier
SIGNALS_DIR = os.path.join(TRACKER_DIR, "signals")
sys.path.insert(0, SIGNALS_DIR)

from pipeline.provenance import record_run
from signals.size_adjustment import get_tier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(TRACKER_DIR, "db", "insider_signals.db")
BATCH_SIZE = 500  # Commit after this many companies

# Market cap computation guards
MAX_PRICE_AGE_DAYS = 30      # Only use prices within 30 days of run date
MAX_DATE_MISALIGNMENT_DAYS = 400  # Price and shares dates must be within 400 days
# Justification for 400d: market_cap feeds get_tier() with boundaries at $300M/$2B/$10B/$200B
# (6x-7x gaps). Shares drift by a few percent annually from dilution/buybacks, which cannot
# cross a 6x tier boundary. 400d covers quarterly reporting + filing lag + one missed quarter.
MAX_PLAUSIBLE_MARKET_CAP = 5e12   # $5T ceiling - accommodates largest companies (MSFT, AAPL, NVDA)

# ADR cross-check threshold: only validate companies above this size
# (avoids network calls for small-cap universe; ADR errors only affect mega/large caps)
CROSS_CHECK_THRESHOLD = 500e9    # $500B

# ADR cross-check tolerance: reject if derived/yfinance ratio exceeds this
# (3x accommodates methodology differences; ADR unit errors typically 5-10x)
MAX_DISCREPANCY_RATIO = 3.0


def get_db(db_path=None):
    """Get database connection with busy timeout for concurrent access."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def _ensure_market_cap_columns(conn):
    """
    Add market_cap_asof and market_cap_source columns if missing.

    CI seeds from the previous release and only runs init_db.py on a full rebuild,
    so columns added there never reach the artifact. We guard every script that
    uses these columns.
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(companies)")
    columns = [col[1] for col in cur.fetchall()]

    added = []

    if "market_cap_asof" not in columns:
        try:
            cur.execute("ALTER TABLE companies ADD COLUMN market_cap_asof TEXT")
            added.append("market_cap_asof")
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                logger.warning(f"Failed to add market_cap_asof column: {e}")

    if "market_cap_source" not in columns:
        try:
            cur.execute("ALTER TABLE companies ADD COLUMN market_cap_source TEXT")
            added.append("market_cap_source")
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                logger.warning(f"Failed to add market_cap_source column: {e}")

    if added:
        conn.commit()
        logger.info(f"Schema migration: added {', '.join(added)} column(s)")
        return True

    return True


def _cross_check_with_yfinance(ticker, derived_mcap):
    """
    Cross-check derived market cap against yfinance's reported marketCap.

    Returns (use_yfinance, mcap_to_use, source_label, reason).

    ADR errors produce values in the legitimate range (TM $2.5T, HSBC $1.8T sit
    between AMZN $2.9T and AVGO $1.8T), so no absolute threshold separates them.
    Instead, compare against yfinance's ground-truth marketCap: a relative check
    catches any units error (ADR or otherwise) without needing share-class metadata.

    When they disagree significantly, use yfinance's value if it's usable (>0).

    Args:
        ticker: Stock ticker symbol
        derived_mcap: Our computed market cap (price × shares)

    Returns:
        (use_yfinance, mcap_to_use, source_label, reason)
        - use_yfinance: True if we should use yfinance's value instead of derived
        - mcap_to_use: The market cap to write (yfinance or derived)
        - source_label: 'derived' or 'yfinance_crosscheck' or None (for NULL)
        - reason: Human-readable explanation
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info or 'marketCap' not in info or info['marketCap'] is None:
            # yfinance fetch failed or no market cap available
            # Use our derived value (already validated locally)
            return (False, derived_mcap, "derived", "yfinance_unavailable")

        yf_mcap = float(info['marketCap'])

        if yf_mcap <= 0:
            # Invalid yfinance data - use our derived value
            return (False, derived_mcap, "derived", "yfinance_invalid")

        # Compare ratio (use max/min to handle both directions)
        ratio = max(derived_mcap, yf_mcap) / min(derived_mcap, yf_mcap)

        if ratio > MAX_DISCREPANCY_RATIO:
            # Significant disagreement - use yfinance's value (it's an independent source)
            return (True, yf_mcap, "yfinance_crosscheck", f"discrepancy_{ratio:.1f}x")

        # Within tolerance - use our derived value
        return (False, derived_mcap, "derived", f"validated_{ratio:.2f}x")

    except Exception as e:
        # Network error or other exception - use our derived value
        logger.debug(f"{ticker}: yfinance cross-check failed: {e}")
        return (False, derived_mcap, "derived", f"yfinance_error")


def compute_market_caps(dry_run=True, db_path=None):
    """
    Recompute market caps for all companies from latest prices and shares.

    For each company:
      1. Get latest price (from prices table)
      2. Get latest shares outstanding (from shares_outstanding table)
      3. Apply guards to reject impossible values:
         - Price recency: only use prices within 30 days of run date
         - Temporal alignment: price and shares dates within 180 days of each other
         - Input sanity: close > 0, shares > 0
         - Output plausibility: market cap < $3T
      4. Compute market_cap = price * shares
      5. Set market_cap_asof to the price date used
      6. Update companies table

    Companies lacking price or shares data, or failing any guard, keep their
    existing market_cap (a stale tier beats an impossible one).

    Returns dict with run statistics.
    """
    logger.info("=" * 60)
    logger.info("MARKET CAP RECOMPUTATION")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN MODE — No data will be written")

    logger.info(f"Guards: price_age<{MAX_PRICE_AGE_DAYS}d, date_align<{MAX_DATE_MISALIGNMENT_DAYS}d, "
                f"mcap<${MAX_PLAUSIBLE_MARKET_CAP/1e12:.1f}T")

    conn = get_db(db_path)
    cur = conn.cursor()

    # Ensure market_cap_asof and market_cap_source columns exist
    _ensure_market_cap_columns(conn)

    # Get all companies
    cur.execute("SELECT id, ticker, market_cap FROM companies ORDER BY ticker")
    all_companies = cur.fetchall()
    total = len(all_companies)

    logger.info(f"Processing {total:,} companies")

    run_date = datetime.now().date()

    updated = 0
    skipped_no_data = 0
    skipped_stale_price = 0
    skipped_misaligned = 0
    skipped_invalid_input = 0
    skipped_implausible = 0
    changed_mcap_count = 0
    changed_tier_count = 0
    mcap_changes = []  # (ticker, old_mcap, new_mcap, old_tier, new_tier)
    rejections = []  # (ticker, reason, details) for data quality logging
    yfinance_checks_performed = 0
    yfinance_values_used = 0  # Count of times we used yfinance's value instead of derived

    for i, (company_id, ticker, old_mcap) in enumerate(all_companies):
        # Get latest price with date
        price_row = cur.execute("""
            SELECT date, close FROM prices
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT 1
        """, (ticker,)).fetchone()

        # Get latest shares outstanding with date
        shares_row = cur.execute("""
            SELECT date, shares FROM shares_outstanding
            WHERE company_id = ?
            ORDER BY date DESC
            LIMIT 1
        """, (company_id,)).fetchone()

        # Guard 1: Check data availability
        if not price_row or not shares_row or price_row[1] is None or shares_row[1] is None:
            skipped_no_data += 1
            continue

        price_date_str = price_row[0]
        close_price = float(price_row[1])
        shares_date_str = shares_row[0]
        shares = float(shares_row[1])

        # Parse dates
        try:
            price_date = datetime.strptime(price_date_str, "%Y-%m-%d").date()
            shares_date = datetime.strptime(shares_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            skipped_invalid_input += 1
            rejections.append((ticker, "invalid_date",
                             f"price_date={price_date_str}, shares_date={shares_date_str}"))
            continue

        # Guard 2: Price recency check
        price_age_days = (run_date - price_date).days
        if price_age_days > MAX_PRICE_AGE_DAYS:
            skipped_stale_price += 1
            if price_age_days > 365:  # Log extreme staleness
                rejections.append((ticker, "stale_price",
                                 f"price_date={price_date_str} ({price_age_days} days old)"))
            continue

        # Guard 3: Temporal alignment check
        date_misalignment_days = abs((price_date - shares_date).days)
        if date_misalignment_days > MAX_DATE_MISALIGNMENT_DAYS:
            skipped_misaligned += 1
            if date_misalignment_days > 365:  # Log severe misalignment
                rejections.append((ticker, "misaligned_dates",
                                 f"price={price_date_str}, shares={shares_date_str} ({date_misalignment_days}d apart)"))
            continue

        # Guard 4: Input sanity check
        if close_price <= 0 or shares <= 0:
            skipped_invalid_input += 1
            rejections.append((ticker, "invalid_input",
                             f"close={close_price}, shares={shares}"))
            continue

        # Compute market cap
        derived_mcap = close_price * shares
        mcap_source = "derived"

        # Guard 5: ADR cross-check for large companies
        # ADR unit errors (5:1 ratios) produce values in the legitimate range (TM $2.5T,
        # HSBC $1.8T sit between AMZN $2.9T and AVGO $1.8T), so absolute thresholds fail.
        # Instead, cross-check against yfinance's ground-truth marketCap for companies
        # above $500B where the stakes justify a network call.
        #
        # Run this BEFORE the $5T ceiling so TSM can benefit from yfinance's correct value.
        if derived_mcap >= CROSS_CHECK_THRESHOLD:
            yfinance_checks_performed += 1
            use_yfinance, mcap_to_use, source_label, reason = _cross_check_with_yfinance(ticker, derived_mcap)

            if use_yfinance:
                # Significant disagreement - use yfinance's value
                yfinance_values_used += 1
                new_mcap = mcap_to_use
                mcap_source = source_label
                rejections.append((ticker, "adr_corrected",
                                 f"derived=${derived_mcap/1e9:.1f}B, yfinance=${new_mcap/1e9:.1f}B ({reason}), "
                                 f"using yfinance value"))
                logger.debug(f"{ticker}: Using yfinance value ${new_mcap/1e9:.1f}B instead of derived ${derived_mcap/1e9:.1f}B")
            else:
                new_mcap = derived_mcap
                mcap_source = source_label
        else:
            new_mcap = derived_mcap

        # Guard 6: Output plausibility check (on the FINAL value we're going to use)
        if new_mcap > MAX_PLAUSIBLE_MARKET_CAP:
            skipped_implausible += 1
            rejections.append((ticker, "implausible_mcap",
                             f"${new_mcap/1e12:.1f}T (source={mcap_source}, "
                             f"price={close_price:,.0f}, shares={shares:,.0f}, "
                             f"price_date={price_date_str}, shares_date={shares_date_str})"))
            continue

        # All guards passed: compute tier and update
        old_tier = get_tier(old_mcap)
        new_tier = get_tier(new_mcap)

        # Track changes
        if old_mcap is None or abs(new_mcap - old_mcap) > 0.01:
            changed_mcap_count += 1
            pct_change = None
            if old_mcap and old_mcap > 0:
                pct_change = (new_mcap - old_mcap) / old_mcap * 100
                if abs(pct_change) > 10:
                    mcap_changes.append((ticker, old_mcap, new_mcap, pct_change, old_tier, new_tier))

        if old_tier != new_tier:
            changed_tier_count += 1

        if not dry_run:
            # Always write market_cap and market_cap_source
            # market_cap_asof only if column exists (schema migration succeeded)
            cur.execute("""
                UPDATE companies
                SET market_cap = ?, market_cap_asof = ?, market_cap_source = ?
                WHERE id = ?
            """, (new_mcap, price_date_str, mcap_source, company_id))

        updated += 1

        # Batched commits and progress logging
        if (i + 1) % BATCH_SIZE == 0:
            logger.info(f"Progress: {i+1:,}/{total:,} ({updated:,} updated, "
                       f"{skipped_stale_price + skipped_misaligned + skipped_invalid_input + skipped_implausible + skipped_no_data:,} skipped)")
            if not dry_run:
                conn.commit()

    # Final commit
    if not dry_run:
        conn.commit()

    conn.close()

    # Report summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total companies:                {total:,}")
    logger.info(f"Market caps {'updated' if not dry_run else 'would update'}:            {updated:,}")
    logger.info(f"Skipped (no data):              {skipped_no_data:,}")
    logger.info(f"Skipped (stale price):          {skipped_stale_price:,}")
    logger.info(f"Skipped (misaligned dates):     {skipped_misaligned:,}")
    logger.info(f"Skipped (invalid input):        {skipped_invalid_input:,}")
    logger.info(f"Skipped (implausible result):   {skipped_implausible:,}")
    logger.info(f"yfinance cross-checks:          {yfinance_checks_performed:,}")
    logger.info(f"yfinance values used:           {yfinance_values_used:,}")
    logger.info(f"Changed market cap:             {changed_mcap_count:,}")
    logger.info(f"Changed size tier:              {changed_tier_count:,}")

    if changed_tier_count > 0:
        logger.warning("")
        logger.warning("!" * 60)
        logger.warning(f"TIER CHANGES DETECTED: {changed_tier_count} companies")
        logger.warning("This means size-adjusted scores in the current artifact")
        logger.warning("were computed against stale market caps.")
        logger.warning("!" * 60)

    # Show top market cap changes (>10%)
    if mcap_changes:
        logger.info("")
        logger.info("=" * 60)
        logger.info("TOP MARKET CAP CHANGES (>10%, up to 20)")
        logger.info("=" * 60)
        mcap_changes.sort(key=lambda x: abs(x[3]), reverse=True)
        for ticker, old_mcap, new_mcap, pct_change, old_tier, new_tier in mcap_changes[:20]:
            tier_change = ""
            if old_tier != new_tier:
                tier_change = f" [TIER: {old_tier} → {new_tier}]"
            old_b = old_mcap / 1e9 if old_mcap else 0
            new_b = new_mcap / 1e9
            logger.info(f"  {ticker:<6}: ${old_b:>8.2f}B → ${new_b:>8.2f}B ({pct_change:+.1f}%){tier_change}")

    # Show data quality findings
    if rejections:
        logger.info("")
        logger.info("=" * 60)
        logger.info("DATA QUALITY FINDINGS (sample, up to 20)")
        logger.info("=" * 60)
        # Group by reason
        by_reason = {}
        for ticker, reason, details in rejections:
            if reason not in by_reason:
                by_reason[reason] = []
            by_reason[reason].append((ticker, details))

        for reason, items in by_reason.items():
            logger.info(f"\n{reason.upper()} ({len(items)} total):")
            for ticker, details in items[:10]:  # Show max 10 per reason
                logger.info(f"  {ticker}: {details}")

    return {
        'total': total,
        'updated': updated,
        'skipped_no_data': skipped_no_data,
        'skipped_stale_price': skipped_stale_price,
        'skipped_misaligned': skipped_misaligned,
        'skipped_invalid_input': skipped_invalid_input,
        'skipped_implausible': skipped_implausible,
        'yfinance_checks_performed': yfinance_checks_performed,
        'yfinance_values_used': yfinance_values_used,
        'changed_mcap_count': changed_mcap_count,
        'changed_tier_count': changed_tier_count,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Recompute market caps from latest prices and shares"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply changes to database (default: dry-run only)"
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})"
    )
    args = parser.parse_args()

    dry_run = not args.write
    db_path = args.db

    if dry_run:
        # Dry-run: just compute and report
        stats = compute_market_caps(dry_run=True, db_path=db_path)
    else:
        # Write mode: use provenance tracking
        with record_run(db_path, 'market_cap') as run:
            stats = compute_market_caps(dry_run=False, db_path=db_path)
            run.rows_written = stats['updated']
            run.coverage(stats['updated'], stats['total'])
            # Also track skip reasons in metadata for data quality monitoring
            run.metadata = {
                'skipped_no_data': stats['skipped_no_data'],
                'skipped_stale_price': stats['skipped_stale_price'],
                'skipped_misaligned': stats['skipped_misaligned'],
                'skipped_invalid_input': stats['skipped_invalid_input'],
                'skipped_implausible': stats['skipped_implausible'],
                'yfinance_checks_performed': stats['yfinance_checks_performed'],
                'yfinance_values_used': stats['yfinance_values_used'],
            }

        logger.info("")
        logger.info(f"Provenance recorded: source='market_cap'")


if __name__ == "__main__":
    main()
