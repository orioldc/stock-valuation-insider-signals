#!/usr/bin/env python3
"""Fix malformed ticker symbols in the companies table.

59 of 7,630 companies have malformed ticker symbols that prevent them from joining
to price data. This script resolves them against SEC's authoritative CIK-to-ticker
mapping (https://www.sec.gov/files/company_tickers.json), handles collisions
conservatively, and reports all changes.

Observed malformations:
- Exchange prefixes/suffixes: NASDAQ:RMR, NYSE: SCS, NTIP-NYSE
- Multiple tickers: BCDA;BCDAW, GTII/GTBIF, HEI, HEI.A, PARAA,PARA
- Parentheses: (SIRI), (NYSE:FBC)
- Placeholders: -, --, N/A, [ NONE ]
- Invalid characters: 1314152 (CIK in ticker field), *H6ZMFDX, BJ$4PAFJ

Usage:
    python fix_ticker_symbols.py              # dry-run (reports only)
    python fix_ticker_symbols.py --write      # apply fixes to DB
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
from datetime import datetime

import requests

# Add tracker to path for provenance
SCRIPT_DIR = Path(__file__).resolve().parent
TRACKER_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(TRACKER_DIR))
from pipeline.provenance import record_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# SEC requires User-Agent format: "Company/App Contact@email.com"
# Browser-spoofing UAs and noreply emails get flagged and IP-blocked.
USER_AGENT = "InsiderSignalTracker oriol.diaz@ozoneproject.com"

# Paths
DB_PATH = SCRIPT_DIR.parent / "db" / "insider_signals.db"

# Valid ticker pattern: 1-7 uppercase alphanumeric plus dot/hyphen
VALID_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")


def _ensure_failures_table(conn: sqlite3.Connection):
    """
    Create ticker_fix_failures table if it doesn't exist.

    Persists unresolvable tickers so we don't retry them every run.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticker_fix_failures (
            company_id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            reason TEXT NOT NULL,
            last_attempt TEXT NOT NULL
        )
    """)
    conn.commit()


def _record_permanent_failure(conn: sqlite3.Connection, company_id: int, ticker: str, reason: str):
    """Record a permanent failure in the DB so it won't be retried next run."""
    timestamp = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO ticker_fix_failures (company_id, ticker, reason, last_attempt)
        VALUES (?, ?, ?, ?)
    """, (company_id, ticker, reason, timestamp))
    conn.commit()


def fetch_sec_ticker_map() -> Dict[int, str]:
    """Fetch CIK -> ticker mapping from SEC.

    Returns:
        Dict mapping CIK (int) to ticker symbol (str)
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": USER_AGENT}

    try:
        logger.info(f"Fetching SEC ticker map from {url}")
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # SEC format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}...}
        cik_to_ticker = {}
        for entry in data.values():
            cik = int(entry["cik_str"])
            ticker = entry["ticker"]
            cik_to_ticker[cik] = ticker

        logger.info(f"Loaded {len(cik_to_ticker)} CIK->ticker mappings from SEC")
        return cik_to_ticker

    except Exception as e:
        logger.error(f"Failed to fetch SEC ticker map: {e}")
        sys.exit(1)


def identify_malformed_tickers(conn: sqlite3.Connection) -> list:
    """Identify companies with malformed ticker symbols.

    Returns:
        List of (id, ticker, cik, name) tuples
    """
    # Query covers all observed malformations:
    # - Special chars: :, ;, /, comma-space, parens, brackets, *, $
    # - Placeholders: -, --, N/A variants
    # - Invalid start: not uppercase letter
    # - Length: > 7 chars
    # - Whitespace
    query = """
        SELECT id, ticker, cik, name
        FROM companies
        WHERE ticker GLOB '*:*'
           OR ticker GLOB '*;*'
           OR ticker GLOB '*/*'
           OR ticker GLOB '*, *'
           OR ticker GLOB '*(*'
           OR ticker = '-'
           OR ticker = '--'
           OR ticker GLOB '[[]N*'
           OR ticker = 'N/A'
           OR ticker GLOB '*[*'
           OR ticker NOT GLOB '[A-Z]*'
           OR LENGTH(ticker) > 7
           OR ticker GLOB '* *'
           OR ticker GLOB '*$*'
           OR ticker GLOB '*_*'
        ORDER BY ticker
    """

    rows = conn.execute(query).fetchall()
    logger.info(f"Found {len(rows)} companies with malformed tickers")
    return rows


def parse_ticker_fallback(malformed: str) -> Optional[str]:
    """Attempt to extract a valid ticker from malformed string.

    For cases where SEC map has no entry for the CIK, try parsing:
    - Strip exchange prefixes: NASDAQ:, NYSE:, etc.
    - Strip exchange suffixes: -NYSE, etc.
    - Take first ticker from multi-ticker fields: HEI, HEI.A -> HEI
    - Strip parentheses: (SIRI) -> SIRI
    - Strip brackets and spaces: [ NONE ] -> NONE

    Args:
        malformed: The malformed ticker string

    Returns:
        Parsed ticker if valid, None otherwise
    """
    cleaned = malformed.strip()

    # Strip exchange prefixes (NASDAQ:, NYSE:, ASX:, OTC:, OTCQB:)
    cleaned = re.sub(r"^(NASDAQ|NYSE|ASX|OTC|OTCQB):\s*", "", cleaned)

    # Strip exchange suffixes (-NYSE, -UN, etc.)
    cleaned = re.sub(r"-[A-Z]{2,}$", "", cleaned)

    # Strip parentheses
    cleaned = cleaned.strip("()")

    # Strip brackets and internal spaces
    cleaned = cleaned.strip("[]")
    cleaned = cleaned.replace(" ", "")

    # Take first ticker from comma/semicolon/slash-separated list
    for sep in [",", ";", "/"]:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()
            break

    # Take first from space-separated (e.g., "Z AND ZG")
    if " " in cleaned:
        parts = cleaned.split()
        # Try to find the ticker part
        for part in parts:
            if VALID_TICKER.match(part):
                cleaned = part
                break

    # Validate
    if VALID_TICKER.match(cleaned):
        return cleaned

    return None


def count_insider_transactions(conn: sqlite3.Connection, company_id: int) -> int:
    """Count insider transactions for a company."""
    result = conn.execute(
        "SELECT COUNT(*) FROM insider_transactions WHERE company_id = ?",
        (company_id,)
    ).fetchone()
    return result[0] if result else 0


def resolve_tickers(
    malformed_companies: list,
    cik_to_ticker: Dict[int, str],
    conn: sqlite3.Connection
) -> Tuple[list, list, list]:
    """Resolve malformed tickers using SEC map and fallback parsing.

    Returns:
        Tuple of (resolved, collisions, unresolved) lists
        - resolved: (id, old_ticker, new_ticker, source, cik, name, txn_count)
        - collisions: (id, old_ticker, new_ticker, existing_id, existing_name, txn_count, existing_txn_count)
        - unresolved: (id, ticker, cik, name, reason)
    """
    resolved = []
    collisions = []
    unresolved = []

    # Build reverse map: ticker -> company_id for collision detection
    ticker_to_id = {}
    for row in conn.execute("SELECT id, ticker FROM companies"):
        ticker_to_id[row[1]] = row[0]

    # Track resolved tickers in this batch to detect intra-batch collisions
    resolved_ticker_map = {}  # new_ticker -> list of (company_id, old_ticker, cik, name, txn_count)

    for company_id, old_ticker, cik, name in malformed_companies:
        txn_count = count_insider_transactions(conn, company_id)

        # Try SEC map first
        new_ticker = None
        source = None

        if cik and cik in cik_to_ticker:
            new_ticker = cik_to_ticker[cik]
            source = "SEC"

        # Fallback to parsing
        if not new_ticker:
            parsed = parse_ticker_fallback(old_ticker)
            if parsed:
                new_ticker = parsed
                source = "parsed"

        if not new_ticker:
            reason = "No SEC mapping" if cik else "No CIK"
            if cik and cik not in cik_to_ticker:
                reason = f"CIK {cik} not in SEC map"
            unresolved.append((company_id, old_ticker, cik, name, reason))
            continue

        # Check for collision with existing ticker in DB
        if new_ticker in ticker_to_id and ticker_to_id[new_ticker] != company_id:
            existing_id = ticker_to_id[new_ticker]
            existing = conn.execute(
                "SELECT name FROM companies WHERE id = ?", (existing_id,)
            ).fetchone()
            existing_name = existing[0] if existing else "Unknown"
            existing_txn_count = count_insider_transactions(conn, existing_id)

            collisions.append((
                company_id, old_ticker, new_ticker, existing_id,
                existing_name, txn_count, existing_txn_count
            ))
            continue

        # Check for collision within this batch (multiple malformed tickers resolving to same new ticker)
        if new_ticker in resolved_ticker_map:
            # Mark this as unresolved due to intra-batch collision
            other_entries = resolved_ticker_map[new_ticker]
            reason = f"Multiple malformed tickers resolve to '{new_ticker}': " + \
                     ", ".join(f"'{e[1]}' (CIK {e[2]})" for e in other_entries)
            unresolved.append((company_id, old_ticker, cik, name, reason))
            continue

        # Track this resolution
        if new_ticker not in resolved_ticker_map:
            resolved_ticker_map[new_ticker] = []
        resolved_ticker_map[new_ticker].append((company_id, old_ticker, cik, name, txn_count))

        resolved.append((company_id, old_ticker, new_ticker, source, cik, name, txn_count))

    return resolved, collisions, unresolved


def apply_fixes(conn: sqlite3.Connection, resolved: list, unresolved: list, collisions: list):
    """Apply ticker fixes to database and record permanent failures.

    Args:
        conn: Database connection
        resolved: List of (id, old_ticker, new_ticker, source, cik, name, txn_count)
        unresolved: List of (id, ticker, cik, name, reason)
        collisions: List of (id, old_ticker, new_ticker, existing_id, existing_name, txn_count, existing_txn_count)
    """
    cursor = conn.cursor()

    # Apply successful resolutions
    for company_id, old_ticker, new_ticker, source, cik, name, txn_count in resolved:
        cursor.execute(
            "UPDATE companies SET ticker = ? WHERE id = ?",
            (new_ticker, company_id)
        )
        # Write-through: success clears any existing failure record
        cursor.execute("DELETE FROM ticker_fix_failures WHERE company_id = ?", (company_id,))
        logger.info(f"Updated: {old_ticker} -> {new_ticker} (CIK {cik}, {source})")

    # Record permanent failures for unresolved tickers
    for company_id, ticker, cik, name, reason in unresolved:
        _record_permanent_failure(conn, company_id, ticker, reason)

    # Record permanent failures for collisions
    for company_id, old_ticker, new_ticker, existing_id, existing_name, txn_count, existing_txn_count in collisions:
        _record_permanent_failure(conn, company_id, old_ticker, f"collision_on_{new_ticker}")

    conn.commit()
    logger.info(f"Applied {len(resolved)} ticker fixes, recorded {len(unresolved) + len(collisions)} permanent failures")


def verify_database(conn: sqlite3.Connection):
    """Verify database state after fixes.

    Returns:
        Dict with verification metrics
    """
    # Count remaining malformed
    malformed = identify_malformed_tickers(conn)

    # Check for duplicate tickers
    duplicates = conn.execute("""
        SELECT ticker, COUNT(*) as cnt
        FROM companies
        GROUP BY ticker
        HAVING cnt > 1
    """).fetchall()

    # Total count
    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    # Count tickers with price data
    with_prices = conn.execute("""
        SELECT COUNT(DISTINCT c.id)
        FROM companies c
        JOIN prices p ON c.ticker = p.ticker
    """).fetchone()[0]

    return {
        "total_companies": total,
        "malformed_remaining": len(malformed),
        "duplicate_tickers": len(duplicates),
        "companies_with_prices": with_prices,
        "duplicates": duplicates
    }


def main():
    parser = argparse.ArgumentParser(
        description="Fix malformed ticker symbols in companies table"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply fixes to database (default: dry-run only)"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode (alias for not using --write)"
    )
    args = parser.parse_args()

    # Normalize dry_run flag
    dry_run = not args.write or args.dry_run

    if not args.db.exists():
        logger.error(f"Database not found: {args.db}")
        sys.exit(1)

    # Fetch SEC data
    cik_to_ticker = fetch_sec_ticker_map()

    # Connect to DB
    conn = sqlite3.connect(args.db)

    # Ensure failures table exists
    _ensure_failures_table(conn)

    # Purge stale failure records for companies that now have valid tickers
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM ticker_fix_failures
        WHERE company_id IN (
            SELECT id FROM companies
            WHERE ticker GLOB '[A-Z]*'
              AND LENGTH(ticker) <= 7
              AND ticker NOT GLOB '*:*'
              AND ticker NOT GLOB '*;*'
              AND ticker NOT GLOB '*/*'
              AND ticker NOT GLOB '*, *'
              AND ticker NOT GLOB '*(*'
              AND ticker != '-'
              AND ticker != '--'
        )
    """)
    purged = cur.rowcount
    if purged > 0:
        logger.info(f"Purged {purged} stale failure records for companies with now-valid tickers")
        conn.commit()

    # Identify malformed tickers
    malformed = identify_malformed_tickers(conn)

    if not malformed:
        logger.info("No malformed tickers found")
        return

    # Resolve
    resolved, collisions, unresolved = resolve_tickers(malformed, cik_to_ticker, conn)

    # Report
    logger.info("\n" + "=" * 80)
    logger.info("RESOLUTION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total malformed: {len(malformed)}")
    logger.info(f"Resolved: {len(resolved)}")
    logger.info(f"Collisions: {len(collisions)}")
    logger.info(f"Unresolved: {len(unresolved)}")

    if resolved:
        logger.info("\n" + "-" * 80)
        logger.info("RESOLVED TICKERS")
        logger.info("-" * 80)
        for company_id, old_ticker, new_ticker, source, cik, name, txn_count in resolved:
            logger.info(f"{old_ticker:20s} -> {new_ticker:10s} (CIK {cik}, {source}, {txn_count} txns)")
            logger.info(f"  {name}")

    if collisions:
        logger.info("\n" + "-" * 80)
        logger.info("COLLISIONS (NOT FIXED)")
        logger.info("-" * 80)
        logger.info("These tickers would create duplicates:")
        for company_id, old_ticker, new_ticker, existing_id, existing_name, txn_count, existing_txn_count in collisions:
            malformed_company = [x for x in malformed if x[0] == company_id][0]
            logger.info(f"\nCollision on '{new_ticker}':")
            logger.info(f"  Malformed: id={company_id}, ticker='{old_ticker}', name='{malformed_company[3]}', txns={txn_count}")
            logger.info(f"  Existing:  id={existing_id}, ticker='{new_ticker}', name='{existing_name}', txns={existing_txn_count}")

    if unresolved:
        logger.info("\n" + "-" * 80)
        logger.info("UNRESOLVED TICKERS")
        logger.info("-" * 80)
        for company_id, ticker, cik, name, reason in unresolved:
            logger.info(f"{ticker:20s} CIK {cik} - {reason}")
            logger.info(f"  {name}")

    # Apply if --write
    if args.write:
        if not resolved and not unresolved and not collisions:
            logger.info("\nNothing to apply (no changes)")
        else:
            # Use provenance tracking
            with record_run(str(args.db), 'ticker_fixes') as run:
                logger.info(f"\nApplying {len(resolved)} fixes and recording {len(unresolved) + len(collisions)} failures...")
                apply_fixes(conn, resolved, unresolved, collisions)

                # Verify
                logger.info("\n" + "=" * 80)
                logger.info("VERIFICATION")
                logger.info("=" * 80)
                metrics = verify_database(conn)
                logger.info(f"Total companies: {metrics['total_companies']}")
                logger.info(f"Malformed remaining: {metrics['malformed_remaining']}")
                logger.info(f"Duplicate tickers: {metrics['duplicate_tickers']}")
                logger.info(f"Companies with price data: {metrics['companies_with_prices']}")

                if metrics['duplicate_tickers'] > 0:
                    logger.warning("WARNING: Duplicate tickers detected:")
                    for ticker, count in metrics['duplicates']:
                        logger.warning(f"  {ticker}: {count} occurrences")

                # Check how many resolved tickers now have prices
                resolved_ids = [x[0] for x in resolved]
                with_prices = conn.execute(f"""
                    SELECT COUNT(DISTINCT c.id)
                    FROM companies c
                    JOIN prices p ON c.ticker = p.ticker
                    WHERE c.id IN ({','.join('?' * len(resolved_ids))})
                """, resolved_ids).fetchone()[0]

                logger.info(f"\nOf {len(resolved)} fixed tickers, {with_prices} now have price data available")

                # Check how many with insider purchases were fixed
                with_purchases = sum(1 for x in resolved if x[6] > 0)
                logger.info(f"Fixed {with_purchases} tickers with insider purchase transactions")

                # Record provenance
                run.rows_written = len(resolved)
                run.coverage(len(resolved), len(malformed))
                run.permanent_failures = len(unresolved) + len(collisions)

                logger.info("")
                logger.info(f"Provenance recorded: source='ticker_fixes'")
    else:
        logger.info("\n" + "=" * 80)
        logger.info("DRY RUN - No changes applied")
        logger.info("Run with --write to apply fixes")
        logger.info("=" * 80)

    conn.close()


if __name__ == "__main__":
    main()
