"""
Bulk EDGAR Insider Transaction Ingestion.

Downloads SEC's pre-parsed quarterly insider transaction data sets (TSV format).
Eliminates the need to fetch/parse individual Form 4 XMLs.

Each ZIP contains TSV files: SUBMISSION, NONDERIV_TRANS, REPORTINGOWNER, etc.
We join on ACCESSION_NUMBER to get full transaction records.
"""

import os
import io
import csv
import json
import sqlite3
import logging
import zipfile
import time
import requests
from datetime import datetime
from typing import Optional, Set

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")
BULK_DIR = os.path.join(os.path.dirname(__file__), "..", "bulk_data")
CHECKPOINT_FILE = os.path.join(BULK_DIR, "ingested_quarters.json")

# SEC fair-access policy requires a descriptive UA with contact info.
SEC_USER_AGENT = "stock-valuation-insider-signals oriol.diaz@ozoneproject.com"


def _parse_sec_date(d):
    """Parse SEC date format (DD-MON-YYYY or YYYY-MM-DD) to YYYY-MM-DD."""
    if not d or not d.strip():
        return None
    d = d.strip()
    # Already YYYY-MM-DD
    if len(d) == 10 and d[4] == '-' and d[7] == '-':
        return d
    # DD-MON-YYYY
    try:
        return datetime.strptime(d, "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            # YY-MM-DD (2-digit year, e.g. "24-05-23" -> "2024-05-23")
            try:
                return datetime.strptime(d, "%y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                return None


def _read_tsv_from_zip(zip_path, tsv_name):
    """Read a TSV file from a ZIP archive, yielding dicts."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        matching = [n for n in zf.namelist() if tsv_name.upper() in n.upper()]
        if not matching:
            logger.warning(f"{tsv_name} not found in {zip_path}")
            return []
        with zf.open(matching[0]) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")
            return list(reader)


def _load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            return set(json.load(f).get("ingested", []))
    return set()


def _save_checkpoint(ingested):
    os.makedirs(BULK_DIR, exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"ingested": list(ingested), "updated": datetime.now().isoformat()}, f)


def _get_or_create_company(conn, ticker, cik, name=None):
    """Get company_id, creating if needed. Matches on CIK first, then ticker."""
    cur = conn.cursor()
    
    if cik:
        cur.execute("SELECT id FROM companies WHERE cik = ?", (int(cik),))
        row = cur.fetchone()
        if row:
            return row[0]
    
    if ticker:
        cur.execute("SELECT id FROM companies WHERE ticker = ?", (ticker,))
        row = cur.fetchone()
        if row:
            if cik:
                cur.execute("UPDATE companies SET cik = ? WHERE id = ?", (int(cik), row[0]))
            return row[0]
    
    cur.execute("INSERT INTO companies (ticker, cik, name) VALUES (?, ?, ?)",
                (ticker or f"CIK{cik}", int(cik) if cik else None, name))
    conn.commit()
    return cur.lastrowid


def _safe_float(val):
    """Parse float from string, returning None on failure."""
    if not val or not val.strip():
        return None
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return None


def ingest_quarter(year, quarter, ticker_filter=None):
    """Ingest one quarter of bulk SEC insider transaction data.
    
    Args:
        year: e.g. 2025
        quarter: 1-4
        ticker_filter: Optional set of uppercase tickers to filter to
    """
    quarter_key = f"{year}q{quarter}"
    zip_path = os.path.join(BULK_DIR, f"{quarter_key}_form345.zip")
    
    if not os.path.exists(zip_path):
        logger.warning(f"ZIP not found: {zip_path}")
        return {"status": "missing", "transactions": 0}
    
    logger.info(f"Ingesting {quarter_key}...")
    t0 = time.time()
    
    # Load TSV data
    submissions = _read_tsv_from_zip(zip_path, "SUBMISSION.tsv")
    nonderiv = _read_tsv_from_zip(zip_path, "NONDERIV_TRANS.tsv")
    owners = _read_tsv_from_zip(zip_path, "REPORTINGOWNER.tsv")
    
    if not submissions or not nonderiv:
        logger.warning(f"Empty data in {quarter_key}")
        return {"status": "empty", "transactions": 0}
    
    logger.info(f"  Loaded: {len(submissions)} submissions, {len(nonderiv)} transactions, {len(owners)} owners")
    
    # Build submission lookup: accession -> {cik, ticker, name, filing_date}
    sub_map = {}
    for s in submissions:
        acc = s.get("ACCESSION_NUMBER", "").strip()
        if not acc:
            continue
        ticker = s.get("ISSUERTRADINGSYMBOL", "").strip().upper()
        sub_map[acc] = {
            "cik": s.get("ISSUERCIK", "").strip().lstrip("0"),
            "issuer_name": s.get("ISSUERNAME", "").strip(),
            "ticker": ticker,
            "filing_date": _parse_sec_date(s.get("FILING_DATE", "")),
        }
    
    # Build owner lookup: accession -> {name, cik, relationship, title}
    owner_map = {}
    for o in owners:
        acc = o.get("ACCESSION_NUMBER", "").strip()
        if not acc:
            continue
        if acc not in owner_map:
            owner_map[acc] = []
        owner_map[acc].append({
            "name": o.get("RPTOWNERNAME", "").strip(),
            "cik": o.get("RPTOWNERCIK", "").strip().lstrip("0"),
            "relationship": o.get("RPTOWNER_RELATIONSHIP", "").strip(),
            "title": o.get("RPTOWNER_TITLE", "").strip(),
        })
    
    # Ingest transactions
    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    skipped = 0
    errors = 0
    
    for txn in nonderiv:
        acc = txn.get("ACCESSION_NUMBER", "").strip()
        sub = sub_map.get(acc)
        if not sub:
            skipped += 1
            continue
        
        ticker = sub["ticker"]
        if ticker_filter and ticker not in ticker_filter:
            skipped += 1
            continue
        
        txn_code = txn.get("TRANS_CODE", "").strip()
        shares = _safe_float(txn.get("TRANS_SHARES"))
        price = _safe_float(txn.get("TRANS_PRICEPERSHARE"))
        shares_after = _safe_float(txn.get("SHRS_OWND_FOLWNG_TRANS"))
        txn_date = _parse_sec_date(txn.get("TRANS_DATE", ""))
        filing_date = sub["filing_date"]
        acq_disp = txn.get("TRANS_ACQUIRED_DISP_CD", "").strip()
        
        # Get owner info
        owner_list = owner_map.get(acc, [])
        if owner_list:
            owner = owner_list[0]
            insider_name = owner["name"]
            insider_cik = owner["cik"]
            relationship = owner["relationship"]
            if owner["title"]:
                relationship = f"{relationship} ({owner['title']})" if relationship else owner["title"]
        else:
            insider_name = ""
            insider_cik = ""
            relationship = "Unknown"
        
        company_id = _get_or_create_company(conn, ticker, sub["cik"], sub["issuer_name"])
        
        raw_json = json.dumps({
            "insider_name": insider_name,
            "insider_cik": insider_cik,
            "relationship": relationship,
            "transaction_code": txn_code,
            "transaction_date": txn_date,
            "shares": shares,
            "price": price,
            "total_value": (shares * price) if (shares and price) else None,
            "shares_owned_after": shares_after,
            "acq_disp": acq_disp,
        })
        
        try:
            conn.execute("""
                INSERT OR IGNORE INTO insider_transactions
                (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
                 transaction_type, shares_transacted, price, shares_owned_after, source, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDGAR_BULK', ?)
            """, (company_id, filing_date, txn_date, insider_name, insider_cik,
                  txn_code, shares, price, shares_after, raw_json))
            inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"Insert error: {e}")
    
    conn.commit()
    conn.close()
    
    elapsed = time.time() - t0
    logger.info(f"  {quarter_key}: {inserted} inserted, {skipped} skipped, {errors} errors ({elapsed:.1f}s)")
    return {"status": "ok", "transactions": inserted, "skipped": skipped, "errors": errors}


def download_quarter(year, quarter, use_wayback=False):
    """Download a quarterly ZIP. Falls back to Wayback Machine if direct fails."""
    os.makedirs(BULK_DIR, exist_ok=True)
    filename = f"{year}q{quarter}_form345.zip"
    local_path = os.path.join(BULK_DIR, filename)
    
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        return local_path
    
    urls = [
        f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{filename}",
    ]
    if use_wayback:
        urls.append(f"https://web.archive.org/web/2026/https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{filename}")
    
    headers = {"User-Agent": SEC_USER_AGENT}
    
    for url in urls:
        try:
            logger.info(f"Downloading {url}...")
            resp = requests.get(url, headers=headers, timeout=120, stream=True, allow_redirects=True)
            if resp.status_code == 200 and int(resp.headers.get("Content-Length", 0)) > 1000:
                with open(local_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info(f"  Downloaded {filename} ({os.path.getsize(local_path) / 1024 / 1024:.1f} MB)")
                return local_path
            else:
                logger.warning(f"  {url}: status {resp.status_code}")
        except Exception as e:
            logger.warning(f"  {url}: {e}")
    
    return None


def ingest_all_bulk(start_year=2020, ticker_filter=None, force=False):
    """Ingest all quarterly bulk data files.
    
    Args:
        start_year: First year to ingest
        ticker_filter: Optional set of uppercase tickers
        force: Re-ingest already-processed quarters
    """
    ingested_set = _load_checkpoint() if not force else set()

    end_year = datetime.now().year
    end_quarter = (datetime.now().month - 1) // 3 + 1
    current_key = f"{end_year}q{end_quarter}"

    total_txns = 0
    results = []

    for year in range(start_year, end_year + 1):
        max_q = end_quarter if year == end_year else 4
        for q in range(1, max_q + 1):
            key = f"{year}q{q}"
            is_current = (key == current_key)

            # The current (open) quarter is still being published by SEC, so never
            # skip it and always pull a fresh copy from live SEC (not Wayback).
            if key in ingested_set and not force and not is_current:
                logger.info(f"Skipping {key} (already ingested)")
                continue

            zip_path = os.path.join(BULK_DIR, f"{key}_form345.zip")
            if is_current:
                # The open quarter is still being published; always fetch fresh from live SEC.
                # Rename the stale copy aside so download_quarter can write to zip_path,
                # then remove the backup only after a successful download.
                stale_path = zip_path + ".stale"
                # Recover from a prior interrupted run that left only the .stale backup.
                if os.path.exists(stale_path) and not os.path.exists(zip_path):
                    os.rename(stale_path, zip_path)
                if os.path.exists(zip_path):
                    os.rename(zip_path, stale_path)
                download_quarter(year, q, use_wayback=False)
                if os.path.exists(zip_path):
                    # Fresh download succeeded; discard the stale backup.
                    if os.path.exists(stale_path):
                        os.remove(stale_path)
                elif os.path.exists(stale_path):
                    # Download failed; restore the stale copy so ingest can still proceed.
                    logger.warning(f"Live SEC download failed for {key}; falling back to cached copy")
                    os.rename(stale_path, zip_path)
            elif not os.path.exists(zip_path):
                download_quarter(year, q, use_wayback=True)

            if not os.path.exists(zip_path):
                results.append({"quarter": key, "status": "download_failed"})
                continue
            
            result = ingest_quarter(year, q, ticker_filter)
            result["quarter"] = key
            results.append(result)
            total_txns += result.get("transactions", 0)
            
            if result["status"] == "ok":
                ingested_set.add(key)
                _save_checkpoint(ingested_set)
    
    logger.info(f"Bulk ingestion complete: {total_txns} transactions across {len(results)} quarters")
    return {"total_transactions": total_txns, "quarters": results}


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Bulk SEC insider transaction ingestion")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--filter-universe", action="store_true",
                        help="[LEGACY] Filter to hardcoded universe (default: ingest ALL)")
    args = parser.parse_args()
    
    ticker_filter = None
    if args.filter_universe:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from data_loader import load_universe
        ticker_filter = set(t.upper() for t in load_universe())
        logger.info(f"Filtering to {len(ticker_filter)} tickers")
    else:
        logger.info("Ingesting ALL tickers (full SEC EDGAR universe)")
    
    result = ingest_all_bulk(start_year=args.start_year, ticker_filter=ticker_filter, force=args.force)
    print(f"\nTotal transactions: {result['total_transactions']}")
    for q in result["quarters"]:
        print(f"  {q['quarter']}: {q.get('transactions', 0)} txns ({q['status']})")
