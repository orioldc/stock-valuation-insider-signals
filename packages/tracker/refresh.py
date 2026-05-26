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
    ingest_incremental, ingest_shares_outstanding, populate_sector_yfinance,
    get_latest_filing_date,
)
from data_ingestion.edgar_client import fetch_company_tickers, get_rate_stats
from signals.composite_scorer import score_universe

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


def run_weekly_refresh(skip_shares=False, skip_sectors=False,
                       max_tickers=None, skip_ingest=False, include_expanded=False):
    """
    Run incremental refresh pipeline with checkpoint support.
    Safe to run multiple times — will skip already-completed tickers.
    """
    start_time = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Phase 0: Bulk ingest (all SEC EDGAR data, no filtering) ──
    logger.info("=" * 60)
    logger.info("PHASE 0: Bulk EDGAR ingestion (full universe)")
    logger.info("=" * 60)
    if not skip_ingest:
        from data_ingestion.bulk_edgar import ingest_all_bulk
        bulk_result = ingest_all_bulk(start_year=2020, ticker_filter=None, force=False)
        logger.info(f"Bulk ingest: {bulk_result['total_transactions']} new transactions")

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
        # ── Phase 1: Incremental insider transaction ingestion ──
        logger.info("=" * 60)
        logger.info("PHASE 1: Incremental Form 4 ingestion")
        logger.info("=" * 60)

        phase1_done = _load_checkpoint("phase1_ingest")
        logger.info(f"  Checkpoint: {len(phase1_done)} tickers already completed")
        phase1_skipped = 0

        for i, ticker in enumerate(incremental_tickers):
            if ticker in phase1_done:
                phase1_skipped += 1
                continue

            if (i + 1) % 100 == 0:
                elapsed = time.time() - start_time
                stats = get_rate_stats()
                logger.info(
                    f"  Progress: {i+1}/{len(incremental_tickers)} ({elapsed:.0f}s elapsed, "
                    f"{new_txn_total} new txns, delay={stats['current_delay']:.2f}s, "
                    f"503s={stats['total_503s']})"
                )
                _save_checkpoint("phase1_ingest", phase1_done)

            try:
                count = ingest_incremental(ticker, ticker_map)
                if count > 0:
                    new_txn_total += count
                    tickers_with_new.append((ticker, count))
                phase1_done.add(ticker)
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "Failed after" in err_str:
                    errors.append(f"{ticker}: {e} (will retry next run)")
                    logger.warning(f"Persistent 503 on {ticker} — skipping for now, will retry next run")
                else:
                    errors.append(f"{ticker}: {e}")
                    logger.warning(f"Error on {ticker}: {e}")
                    phase1_done.add(ticker)

        _save_checkpoint("phase1_ingest", phase1_done)
        remaining = len(incremental_tickers) - len(phase1_done)
        logger.info(
            f"Phase 1 complete: {new_txn_total} new transactions across {len(tickers_with_new)} tickers "
            f"(skipped {phase1_skipped} from checkpoint, {remaining} remaining for retry)"
        )

        # ── Phase 2: Refresh shares outstanding ──
        shares_refreshed = 0
        if not skip_shares:
            logger.info("=" * 60)
            logger.info("PHASE 2: Shares outstanding refresh")
            logger.info("=" * 60)

            phase2_done = _load_checkpoint("phase2_shares")
            logger.info(f"  Checkpoint: {len(phase2_done)} tickers already completed")

            for i, ticker in enumerate(incremental_tickers):
                if ticker in phase2_done:
                    continue

                if (i + 1) % 100 == 0:
                    logger.info(f"  Shares progress: {i+1}/{len(incremental_tickers)}")
                    _save_checkpoint("phase2_shares", phase2_done)

                try:
                    count = ingest_shares_outstanding(ticker, ticker_map)
                    shares_refreshed += count
                    phase2_done.add(ticker)
                except Exception as e:
                    if "503" not in str(e) and "Failed after" not in str(e):
                        phase2_done.add(ticker)
                    errors.append(f"{ticker} (shares): {e}")

            _save_checkpoint("phase2_shares", phase2_done)
            logger.info(f"Phase 2 complete: {shares_refreshed} shares records refreshed")

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
    completed_tickers = len(phase1_done) if not skip_ingest else len(incremental_tickers)
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
            return pd.read_csv(path)
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
                        f"Share Δ4Q: {row.get('share_delta_4q', 0):.2f}%")
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
