#!/usr/bin/env python3
"""
Backfill current-quarter Form 4 filings via live SEC XML.

The SEC bulk dataset for the current open quarter is not yet published (404),
so this script fetches individual Form 4 XMLs from the SEC submissions API
for every company in the DB that has a CIK.  It is resumable: completed
companies are checkpointed to disk (keyed by quarter-start date) and skipped
on restart.

Usage:
    python backfill_quarter_live.py [--since YYYY-MM-DD]
"""

import sys
import os
import json
import logging
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ingestion.data_loader import get_db
from data_ingestion.edgar_client import fetch_form4_filings, parse_form4_xml, get_rate_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "insider_signals.db")
CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")


# -- Checkpoint helpers --

def _checkpoint_path(since):
    """Get checkpoint file path for backfill run for the given since date."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"backfill_q_{since}.json")


def _load_checkpoint(since):
    """Load set of completed company ids for backfill run for the given since date."""
    path = _checkpoint_path(since)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        return set(data.get("completed", []))
    return set()


def _save_checkpoint(since, completed_set):
    """Save completed company ids for backfill run for the given since date."""
    path = _checkpoint_path(since)
    with open(path, "w") as f:
        json.dump(
            {"completed": list(completed_set), "updated": datetime.now().isoformat()},
            f,
        )


def _clear_checkpoint(since):
    """Remove the checkpoint file after a full successful run."""
    path = _checkpoint_path(since)
    if os.path.exists(path):
        os.remove(path)


# -- Quarter date helper --

def _current_quarter_start():
    """Return first day of the current calendar quarter as 'YYYY-MM-DD'."""
    now = datetime.now()
    q_start_month = ((now.month - 1) // 3) * 3 + 1
    return f"{now.year}-{q_start_month:02d}-01"


# -- Main backfill logic --

def run_backfill(since):
    """Backfill Form 4 filings since `since` for all companies with a CIK."""
    completed = _load_checkpoint(since)
    logger.info(f"Checkpoint: {len(completed)} companies already completed")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, ticker, cik FROM companies WHERE cik IS NOT NULL ORDER BY ticker"
    )
    companies = cur.fetchall()
    conn.close()

    logger.info(f"Universe: {len(companies)} companies with CIK | Since: {since}")

    # Open a single connection for all inserts; commit per company.
    conn = get_db()

    total_inserted = 0
    errors = 0
    transient_errors = 0
    processed = 0

    try:
        for idx, (company_id, ticker, cik) in enumerate(companies):
            company_key = str(company_id)

            if company_key in completed:
                continue

            try:
                filings = fetch_form4_filings(cik, limit=None, since_date=since)
                company_inserted = 0
                parse_failures = 0

                for filing in filings:
                    try:
                        txns = parse_form4_xml(
                            filing["cik"],
                            filing["accession_number"],
                            filing["primary_doc"],
                        )
                    except Exception as e:
                        logger.warning(
                            f"{ticker} ({cik}): failed to parse filing "
                            f"{filing.get('accession_number', '?')}: {e}"
                        )
                        parse_failures += 1
                        continue

                    for txn in txns:
                        try:
                            insert_cur = conn.execute(
                                """
                                INSERT OR IGNORE INTO insider_transactions
                                (company_id, filing_date, transaction_date, reporting_name,
                                 reporting_cik, transaction_type, shares_transacted, price,
                                 shares_owned_after, source, raw_json)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDGAR', ?)
                                """,
                                (
                                    company_id,
                                    filing["filing_date"],
                                    txn["transaction_date"],
                                    txn["insider_name"],
                                    txn["insider_cik"],
                                    txn["transaction_code"],
                                    txn["shares"],
                                    txn["price"],
                                    txn["shares_owned_after"],
                                    json.dumps(txn),
                                ),
                            )
                            company_inserted += insert_cur.rowcount
                        except Exception as e:
                            logger.warning(f"{ticker}: insert error: {e}")

                conn.commit()
                total_inserted += company_inserted
                if filings and parse_failures == len(filings) and company_inserted == 0:
                    logger.error(f"{ticker} ({cik}): all {len(filings)} filings failed to parse - marking complete to avoid infinite retry")
                    errors += 1
                completed.add(company_key)
                processed += 1

            except Exception as e:
                err_str = str(e)
                logger.warning(f"{ticker} ({cik}): error - {e}")
                # Do not mark complete on rate-limit / server errors so they retry next run.
                if "503" in err_str or "Failed after" in err_str or "429" in err_str:
                    transient_errors += 1
                else:
                    errors += 1
                    completed.add(company_key)
                    processed += 1

            # Progress log and checkpoint save every 200 companies.
            if (idx + 1) % 200 == 0:
                stats = get_rate_stats()
                logger.info(
                    f"Progress: {idx + 1}/{len(companies)} companies | "
                    f"{total_inserted} new txns | "
                    f"errors={errors} | "
                    f"delay={stats['current_delay']:.2f}s 503s={stats['total_503s']}"
                )
                _save_checkpoint(since, completed)

        # Final checkpoint save.
        _save_checkpoint(since, completed)

        all_done = all(str(co[0]) in completed for co in companies)

        print(
            f"\n{'=' * 60}\n"
            f"BACKFILL COMPLETE\n"
            f"{'=' * 60}\n"
            f"Companies processed:          {processed}\n"
            f"Total new transactions:       {total_inserted}\n"
            f"Errors (persistent):          {errors}\n"
            f"Errors (transient/retry):     {transient_errors}\n"
            f"All companies completed:      {all_done}\n"
        )

        if all_done:
            _clear_checkpoint(since)
            logger.info("All companies completed - checkpoint cleared")
        else:
            remaining = sum(1 for co in companies if str(co[0]) not in completed)
            logger.info(f"{remaining} companies remain - checkpoint preserved for retry")

    finally:
        conn.close()

    return {"processed": processed, "inserted": total_inserted, "errors": errors, "transient_errors": transient_errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill current-quarter Form 4 filings for the full company universe"
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date for filings (default: first day of current calendar quarter)",
    )
    args = parser.parse_args()

    since = args.since or _current_quarter_start()
    logger.info(f"Starting backfill since {since}")

    run_backfill(since)
