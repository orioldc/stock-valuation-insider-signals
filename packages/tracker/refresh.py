#!/usr/bin/env python3
"""
Weekly Refresh Script — Incremental data update for Insider Signal Tracker.

Fetches only NEW Form 4 filings since last ingestion, refreshes shares outstanding,
re-runs cluster detection and composite scoring, and generates a summary report.

Features:
  - Checkpoint tracking: saves progress to disk so retries skip completed tickers
  - Adaptive rate limiting: slows down on SEC 503s, recovers gradually
  - Resilient: individual ticker failures don't stop the pipeline
"""

import sys
import os
import time
import json
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import (
    load_universe, load_full_universe, load_active_universe, get_db,
    populate_sector_yfinance,
    get_latest_filing_date,
)
from data_ingestion.edgar_client import fetch_company_tickers, get_rate_stats
from signals.composite_scorer import score_universe
from signals.share_count_change import fmt_pct

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")


# ── Checkpoint helpers ──

def _checkpoint_path(phase):
    """Get checkpoint file path for a given phase."""
    today = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"{today}_{phase}.json")


def _load_checkpoint(phase):
    """Load set of completed tickers for a phase."""
    path = _checkpoint_path(phase)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        return set(data.get("completed", []))
    return set()


def _save_checkpoint(phase, completed_set):
    """Save completed tickers for a phase."""
    path = _checkpoint_path(phase)
    with open(path, "w") as f:
        json.dump({"completed": list(completed_set), "updated": datetime.now().isoformat()}, f)


def _clear_checkpoints():
    """Clear today's checkpoint files (call after successful full run)."""
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(CHECKPOINT_DIR):
        for fname in os.listdir(CHECKPOINT_DIR):
            if fname.startswith(today):
                os.remove(os.path.join(CHECKPOINT_DIR, fname))


def _quarters_after(date_str):
    """(year, quarter) pairs from the quarter after date_str up to today's quarter.

    If date_str is None (no bulk data at all), returns just the current quarter.
    """
    now = datetime.now()
    end = (now.year, (now.month - 1) // 3 + 1)
    if not date_str:
        return [end]
    y, q = int(date_str[:4]), (int(date_str[5:7]) - 1) // 3 + 1
    out = []
    while (y, q) < end:
        q += 1
        if q == 5:
            y, q = y + 1, 1
        out.append((y, q))
    return out


def run_weekly_refresh(skip_shares=False, skip_sectors=False,
                       max_tickers=None, skip_ingest=False, include_expanded=False):
    """
    Run incremental refresh pipeline with checkpoint support.
    Safe to run multiple times — will skip already-completed tickers.
    """
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Phase 0: Rebuild insider transactions from SEC's quarterly bulk files ──
    # The table is rebuilt from scratch every run so no stale or duplicated row
    # from an earlier release can survive. Takes ~10 minutes.
    logger.info("=" * 60)
    logger.info("PHASE 0: Rebuild insider transactions from SEC bulk files")
    logger.info("=" * 60)
    if not skip_ingest:
        from data_ingestion.bulk_edgar import rebuild_from_bulk
        bulk_result = rebuild_from_bulk(start_year=2020)
        logger.info(f"Bulk rebuild: {bulk_result['total_transactions']} transactions, "
                    f"{bulk_result['amended_rows_removed']} replaced by amendments, "
                    f"{bulk_result['duplicate_rows_removed']} repeated trades removed")

    # Build universe dynamically from DB
    # For scoring: tickers with purchases in last 2 years
    tickers = load_universe()
    # For incremental XML refresh: tickers with activity in last 6 months
    incremental_tickers = load_active_universe(months=6)

    if max_tickers:
        tickers = tickers[:max_tickers]
        incremental_tickers = incremental_tickers[:max_tickers]

    logger.info(f"Scoring universe: {len(tickers)} tickers | Incremental refresh: {len(incremental_tickers)} tickers")

    # Fetch ticker map once (only needed for ingestion)
    ticker_map = None
    if not skip_ingest:
        ticker_map = fetch_company_tickers()
        if not ticker_map:
            logger.error("Failed to fetch ticker map from SEC. Aborting.")
            return

    errors = []

    # ── Phases 1-2.5: Data ingestion ──
    new_txn_total = 0
    tickers_with_new = []
    shares_refreshed = 0

    if skip_ingest:
        logger.info("SKIPPING ingestion phases 1-2.5 (--skip-ingest)")
    else:
        # ── Phase 1: Filings newer than the bulk files ──
        # SEC's quarterly index lists every filing, so every tracked company
        # with a new filing is reached, not only recently active ones.
        logger.info("=" * 60)
        logger.info("PHASE 1: Filings not yet in the bulk files (quarterly index)")
        logger.info("=" * 60)
        from backfill_quarter_index import run_backfill, _clear_checkpoint
        from data_ingestion.data_loader import _bulk_coverage_end
        from data_ingestion.form4_rules import (
            apply_amendments, merge_related_owner_filings, remove_duplicate_filings,
            remove_self_reported)
        from data_ingestion.company_identity import (
            assign_tickers, merge_duplicate_companies, merge_predecessors)
        from data_ingestion.edgar_client import fetch_sec_company_list

        conn = get_db()
        coverage_end = _bulk_coverage_end(conn)
        conn.close()
        for year, quarter in _quarters_after(coverage_end):
            # The table was just rebuilt, so a checkpoint from an earlier
            # attempt today would skip companies whose rows no longer exist.
            _clear_checkpoint(year, quarter)
            try:
                result = run_backfill(year, quarter)
                new_txn_total += result["inserted"]
            except Exception as e:
                errors.append(f"{year}q{quarter} index tail: {e}")
                logger.error(f"Index tail for {year}q{quarter} failed: {e}")

        # The tail can bring a new holding company's first filings (and its
        # claim on an existing ticker), so redo the company matching.
        sec_map, sec_titles = fetch_sec_company_list()
        conn = get_db()
        merge_duplicate_companies(conn)
        merge_predecessors(conn, sec_map)
        assign_tickers(conn, sec_map, sec_titles)
        # Written by the retired fix_ticker_symbols.py; assign_tickers replaces it.
        conn.execute("DROP TABLE IF EXISTS ticker_fix_failures")
        amended = apply_amendments(conn)
        repeated = remove_duplicate_filings(conn)
        repeated += merge_related_owner_filings(conn)
        self_reported = remove_self_reported(conn)
        conn.close()
        logger.info(f"Phase 1 complete: {new_txn_total} new transactions, "
                    f"{amended} rows replaced by amendments, {repeated} repeated trades removed, "
                    f"{self_reported} filed under the company's own name removed")

        # ── Phase 2: Rebuild share counts ──
        if not skip_shares:
            logger.info("=" * 60)
            logger.info("PHASE 2: Share counts from SEC company facts")
            logger.info("=" * 60)
            from data_ingestion.share_counts import refresh_share_tables
            conn = get_db()
            # Written by the retired backfill_shares_outstanding.py.
            conn.execute("DROP TABLE IF EXISTS shares_backfill_failures")
            stats = refresh_share_tables(conn)
            conn.close()
            logger.info(f"Phase 2 complete: {stats}")

        # ── Phase 2.5: Populate missing sectors ──
        if not skip_sectors:
            logger.info("=" * 60)
            logger.info("PHASE 2.5: Populating missing sectors via yfinance")
            logger.info("=" * 60)

            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT ticker FROM companies WHERE sector IS NULL OR sector = '' OR sector = 'Unknown'")
            missing = [r[0] for r in cur.fetchall()]
            conn.close()

            if missing:
                logger.info(f"  {len(missing)} tickers missing sector data")
                for i, ticker in enumerate(missing):
                    try:
                        populate_sector_yfinance(ticker)
                    except Exception:
                        pass
                    if (i + 1) % 50 == 0:
                        logger.info(f"  Sector progress: {i+1}/{len(missing)}")
                    time.sleep(0.15)
            else:
                logger.info("  All tickers have sector data")

    # ── Phase 2.6: Refresh prices ──
    logger.info("=" * 60)
    logger.info("PHASE 2.6: Price refresh (yfinance)")
    logger.info("=" * 60)
    try:
        from backtest.simple_backtest import fetch_prices
        fetch_prices(tickers)
        logger.info("Prices refreshed")
    except Exception as e:
        logger.warning(f"Price refresh failed: {e}")

    # ── Phase 2.7: Refresh market cap from prices × shares ──
    logger.info("=" * 60)
    logger.info("PHASE 2.7: Refreshing market cap from latest prices and shares")
    logger.info("=" * 60)

    conn = get_db()
    cur = conn.cursor()

    # Check if market_cap_asof column exists (tolerate old DBs)
    cur.execute("PRAGMA table_info(companies)")
    columns = [col[1] for col in cur.fetchall()]
    has_asof_column = "market_cap_asof" in columns

    # Migrate schema if needed: add market_cap_asof column
    if not has_asof_column:
        try:
            cur.execute("ALTER TABLE companies ADD COLUMN market_cap_asof TEXT")
            conn.commit()
            has_asof_column = True
            logger.info("  Schema migration: added market_cap_asof column")
        except Exception as e:
            # Column already exists (race condition) or other error
            if "duplicate column" in str(e).lower():
                has_asof_column = True
                logger.info("  Schema migration: market_cap_asof column already exists")
            else:
                logger.warning(f"  Failed to add market_cap_asof column: {e}")
                # Continue without asof tracking rather than aborting the whole refresh

    # Get all companies
    cur.execute("SELECT id, ticker FROM companies")
    all_companies = cur.fetchall()
    logger.info(f"  Refreshing market cap for {len(all_companies)} companies")

    updated_count = 0
    preserved_stale = 0

    for i, (company_id, ticker) in enumerate(all_companies):
        # Get latest price
        price_row = cur.execute("""
            SELECT date, close FROM prices
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT 1
        """, (ticker,)).fetchone()

        # Latest share count from the year before the price (an older count
        # times today's price is not today's market cap)
        shares_row = cur.execute("""
            SELECT shares FROM shares_outstanding
            WHERE company_id = ? AND date >= date(?, '-400 days')
            ORDER BY date DESC
            LIMIT 1
        """, (company_id, price_row[0] if price_row else None)).fetchone()

        if price_row and shares_row and price_row[1] and shares_row[0]:
            # Both available: compute market cap
            price_date = price_row[0]
            close_price = float(price_row[1])
            shares = float(shares_row[0])
            computed_market_cap = close_price * shares

            # Validate before writing
            from data_ingestion.data_loader import _validate_market_cap_value
            valid, reason = _validate_market_cap_value(computed_market_cap)

            if valid:
                market_cap = computed_market_cap
                if has_asof_column:
                    cur.execute("""
                        UPDATE companies
                        SET market_cap = ?, market_cap_asof = ?
                        WHERE id = ?
                    """, (market_cap, price_date, company_id))
                else:
                    cur.execute("""
                        UPDATE companies
                        SET market_cap = ?
                        WHERE id = ?
                    """, (market_cap, company_id))
                updated_count += 1
            else:
                # Implausible value: NULL it instead of writing
                logger.warning(f"{ticker}: Rejecting implausible computed market cap: ${computed_market_cap/1e12:.2f}T ({reason})")
                if has_asof_column:
                    cur.execute("""
                        UPDATE companies
                        SET market_cap = NULL, market_cap_asof = NULL
                        WHERE id = ?
                    """, (company_id,))
                else:
                    cur.execute("""
                        UPDATE companies
                        SET market_cap = NULL
                        WHERE id = ?
                    """, (company_id,))
                preserved_stale += 1  # Count as preserved (didn't update to a valid value)
        else:
            # Missing price or shares: preserve existing market_cap (stale is better than None)
            preserved_stale += 1

        if (i + 1) % 500 == 0:
            logger.info(f"  Progress: {i+1}/{len(all_companies)} ({updated_count} updated, {preserved_stale} preserved)")
            conn.commit()

    conn.commit()
    conn.close()
    logger.info(f"Phase 2.7 complete: {updated_count} market caps refreshed, {preserved_stale} preserved (no price/shares data)")

    # ── Phase 3: Re-run scoring ──
    logger.info("=" * 60)
    logger.info("PHASE 3: Cluster detection & composite scoring")
    logger.info("=" * 60)

    old_signals = _load_old_signals()
    df = score_universe(tickers)

    df.to_csv(os.path.join(OUTPUT_DIR, "latest_signals.csv"), index=False)
    logger.info(f"Saved latest_signals.csv with {len(df)} rows")

    # ── Record detected clusters in forward tracker ──
    try:
        from signals.forward_tracker import record_signal
        clusters = df[df["cluster_detected"] == True]
        for _, row in clusters.iterrows():
            record_signal(row["ticker"], datetime.now().strftime("%Y-%m-%d"))
        logger.info(f"Recorded {len(clusters)} clusters in forward tracker")
    except Exception as e:
        logger.warning(f"Forward tracker record_signal failed: {e}")

    # ── Phase 4: Generate report ──
    elapsed_total = time.time() - start_time

    # Check coverage
    total_tickers = len(tickers)
    # The bulk rebuild and index tail cover every tracked company at once, so
    # coverage is all-or-nothing: complete unless a tail quarter failed.
    tail_failed = any("index tail" in e for e in errors)
    completed_tickers = 0 if tail_failed else total_tickers
    coverage_pct = (completed_tickers / total_tickers * 100) if total_tickers else 0

    report = _generate_report(
        df, old_signals, tickers, new_txn_total, tickers_with_new,
        shares_refreshed, errors, elapsed_total, completed_tickers, coverage_pct
    )

    report_path = os.path.join(OUTPUT_DIR, "weekly_report.txt")
    with open(report_path, "w") as f:
        f.write(report)
    logger.info(f"Report saved to {report_path}")

    # Clear checkpoints only if we achieved 100% coverage
    if coverage_pct >= 100:
        _clear_checkpoints()
        logger.info("Full coverage achieved — checkpoints cleared")
    else:
        logger.info(f"Coverage: {coverage_pct:.1f}% — checkpoints preserved for retry")

    # ── Update forward returns ──
    try:
        from signals.forward_tracker import update_forward_returns
        update_forward_returns()
        logger.info("Forward returns updated")
    except Exception as e:
        logger.warning(f"Forward tracker update_forward_returns failed: {e}")

    print(report)
    return df


def _load_old_signals():
    try:
        import pandas as pd
        path = os.path.join(OUTPUT_DIR, "latest_signals.csv")
        if os.path.exists(path):
            return pd.read_csv(path, keep_default_na=False, na_values=[''])
    except Exception:
        pass
    return None


def _generate_report(df, old_signals, tickers, new_txn_total, tickers_with_new,
                     shares_refreshed, errors, elapsed, completed_tickers=None,
                     coverage_pct=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clusters = df[df["cluster_detected"] == True]
    top20 = df.head(20)

    stats = get_rate_stats()

    lines = [
        f"{'=' * 60}",
        f"INSIDER SIGNAL TRACKER — WEEKLY REFRESH REPORT",
        f"Generated: {now}",
        f"{'=' * 60}",
        f"",
        f"SUMMARY:",
        f"  Universe size:              {len(tickers)}",
        f"  Tickers completed:          {completed_tickers or '?'} ({coverage_pct:.1f}%)" if coverage_pct else "",
        f"  New transactions ingested:  {new_txn_total}",
        f"  Tickers with new data:      {len(tickers_with_new)}",
        f"  Shares records refreshed:   {shares_refreshed}",
        f"  Errors:                     {len(errors)}",
        f"  SEC 503s encountered:       {stats['total_503s']}",
        f"  Runtime:                    {elapsed:.0f}s ({elapsed/60:.1f}m)",
        f"",
        f"{'=' * 60}",
        f"CLUSTERS DETECTED: {len(clusters)}",
        f"{'=' * 60}",
    ]

    # Remove empty strings
    lines = [l for l in lines if l is not None]

    if not clusters.empty:
        for _, row in clusters.iterrows():
            lines.append(f"  {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
                        f"Cluster Score: {row['cluster_score_raw']:.1f} | "
                        f"Share Δ4Q: {fmt_pct(row.get('share_delta_4q'))}")
    else:
        lines.append("  (none)")

    if old_signals is not None and not old_signals.empty:
        old_cluster_tickers = set(old_signals[old_signals["cluster_detected"] == True]["ticker"])
        new_cluster_tickers = set(clusters["ticker"]) if not clusters.empty else set()

        newly_detected = new_cluster_tickers - old_cluster_tickers
        lost_clusters = old_cluster_tickers - new_cluster_tickers

        lines.extend([
            f"",
            f"CLUSTER CHANGES vs. PREVIOUS:",
            f"  Newly detected:  {', '.join(sorted(newly_detected)) or '(none)'}",
            f"  No longer active: {', '.join(sorted(lost_clusters)) or '(none)'}",
        ])

    lines.extend([
        f"",
        f"{'=' * 60}",
        f"TOP 20 SIGNALS",
        f"{'=' * 60}",
    ])

    for i, row in top20.iterrows():
        cluster_flag = "🔥" if row["cluster_detected"] else "  "
        buyback_flag = "📉" if row.get("share_trend") == "buyback" else "  "
        lines.append(f"  {i+1:>3}. {row['ticker']:<6} | Composite: {row['composite']:.4f} | "
                    f"Cluster: {row.get('cluster_norm', 0):.3f} {cluster_flag} | "
                    f"Buyback: {row.get('share_norm', 0):.3f} {buyback_flag}")

    if tickers_with_new:
        lines.extend([
            f"",
            f"{'=' * 60}",
            f"TICKERS WITH NEW DATA (top 30)",
            f"{'=' * 60}",
        ])
        for ticker, count in sorted(tickers_with_new, key=lambda x: -x[1])[:30]:
            lines.append(f"  {ticker:<6}: {count} new transactions")

    if errors:
        lines.extend([
            f"",
            f"ERRORS ({len(errors)}):",
        ])
        for e in errors[:20]:
            lines.append(f"  - {e}")
        if len(errors) > 20:
            lines.append(f"  ... and {len(errors) - 20} more")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Weekly incremental refresh")
    parser.add_argument("--expanded", action="store_true", help="Include Russell 2000 additions")
    parser.add_argument("--skip-shares", action="store_true", help="Skip shares outstanding refresh")
    parser.add_argument("--skip-sectors", action="store_true", help="Skip sector population")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion phases, run scoring only")
    parser.add_argument("--max-tickers", type=int, help="Limit to N tickers (testing)")
    parser.add_argument("--clear-checkpoints", action="store_true", help="Clear today's checkpoints and start fresh")
    args = parser.parse_args()

    if args.clear_checkpoints:
        _clear_checkpoints()
        logger.info("Checkpoints cleared")

    run_weekly_refresh(
        include_expanded=args.expanded,
        skip_shares=args.skip_shares,
        skip_sectors=args.skip_sectors,
        max_tickers=args.max_tickers,
        skip_ingest=args.skip_ingest,
    )
