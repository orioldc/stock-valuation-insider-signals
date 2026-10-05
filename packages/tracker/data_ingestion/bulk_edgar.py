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
import sys
import requests
from datetime import datetime
from typing import Optional, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from form4_rules import (  # noqa: E402
    TRANSACTION_FORM_TYPES, apply_amendments, merge_related_owner_filings, normalize_cik,
    normalize_price, pick_primary_owner, relationship_from_bulk, remove_duplicate_filings,
    remove_self_reported,
)
from company_identity import (  # noqa: E402
    assign_tickers, company_for_cik, merge_duplicate_companies, merge_predecessors,
    record_issuer_tickers,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")
BULK_DIR = os.path.join(os.path.dirname(__file__), "..", "bulk_data")
CHECKPOINT_FILE = os.path.join(BULK_DIR, "ingested_quarters.json")

# SEC fair-access policy requires a descriptive UA with contact info.
SEC_USER_AGENT = "stock-valuation-insider-signals oriol.diaz@ozoneproject.com"


def _parse_sec_date(d):
    """Parse SEC date format to YYYY-MM-DD, passing through ambiguous formats.

    Handles unambiguous formats only:
    - DD-MON-YYYY (e.g., "15-JUN-2024") -> "2024-06-15"
    - YYYY-MM-DD (pass through)

    Passes through formats that normalize_transaction_date can handle:
    - Two-digit years (YY-MM-DD) - need filing_date anchor for century
    - Timezone suffixes (YYYY-MM-DD-HH:MM) - need truncation
    """
    if not d or not d.strip():
        return None
    d = d.strip()

    # Already YYYY-MM-DD - pass through
    if len(d) == 10 and d[4] == '-' and d[7] == '-':
        return d

    # Timezone suffix (YYYY-MM-DD-HH:MM) - pass through for normalize_transaction_date
    # to truncate
    if len(d) > 10 and d[10] == '-' and d[4] == '-' and d[7] == '-':
        return d

    # Two-digit year (YY-MM-DD) - pass through for normalize_transaction_date
    # to disambiguate using filing_date anchor
    if len(d) == 8 and d[2] == '-' and d[5] == '-':
        return d

    # DD-MON-YYYY - parse and reformat
    try:
        return datetime.strptime(d, "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass

    # YYYY-MM-DD variant - parse and reformat (handles edge cases)
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass

    # Unrecognized format
    return None


def normalize_transaction_date(transaction_date, filing_date):
    """Normalize malformed transaction_date values.

    Handles three known malformations:
    1. Trailing timezone offset (e.g., '2024-06-27-05:00') → truncate to date part
    2. Two-digit year (e.g., '24-02-12') → expand century using filing_date
    3. Zero-padded century (e.g., '0022-10-12') → derive century from filing_date

    Future dates beyond today are rejected as corrupt with no recoverable signal.
    Already-valid ISO dates pass through unchanged, even if transaction_date > filing_date
    (the relationship between two fields is unreliable - we cannot determine which is wrong).

    DELIBERATE ASYMMETRY: Cases 2 and 3 (ambiguous years) enforce txn_date <= filing_date
    and reject candidates that violate it, because without an anchor we cannot choose
    a century at all. Case 4 (complete unambiguous dates) does NOT enforce this
    relationship — we have a valid date and choose not to null it on an unverified
    assumption about which field is wrong. This is intentional: losing data on a guess
    is worse than keeping a possibly-inconsistent but well-formed date.

    Args:
        transaction_date: Raw transaction date string from SEC filing
        filing_date: Filing date string (YYYY-MM-DD), used to disambiguate century

    Returns:
        Normalized YYYY-MM-DD string, or None if irreparably malformed
    """
    if not transaction_date or not transaction_date.strip():
        return None

    raw = transaction_date.strip()

    # Case 1: Trailing timezone offset like '2024-06-27-05:00'
    if len(raw) > 10 and raw[10] == '-':
        candidate = raw[:10]
        # Validate it's a proper date
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
            logger.debug(f"Normalized timezone-suffixed date: {raw} → {candidate}")
            return candidate
        except ValueError:
            pass

    # Case 2: Two-digit year like '24-02-12' or '25-07-25'
    if len(raw) == 8 and raw[2] == '-' and raw[5] == '-':
        yy, mm, dd = raw.split('-')
        if filing_date:
            try:
                filing_year = int(filing_date[:4])
                # Try both 20xx and 19xx
                for century in [2000, 1900]:
                    year = century + int(yy)
                    candidate = f"{year:04d}-{mm}-{dd}"
                    # Validate date is parseable
                    try:
                        txn_dt = datetime.strptime(candidate, "%Y-%m-%d")
                        filing_dt = datetime.strptime(filing_date, "%Y-%m-%d")
                        # Transaction must precede filing and be within ~5 years of it
                        if txn_dt <= filing_dt and abs((txn_dt - filing_dt).days) <= 1825:
                            logger.debug(f"Normalized two-digit year: {raw} → {candidate} (filing: {filing_date})")
                            return candidate
                    except ValueError:
                        continue
            except (ValueError, IndexError):
                pass
        logger.warning(f"Cannot normalize two-digit year date: {raw} (filing: {filing_date})")
        return None

    # Case 3: Zero-padded century like '0022-10-12' (two-digit year padded with zeros)
    if len(raw) == 10 and raw[4] == '-' and raw[7] == '-':
        year_str = raw[:4]
        if year_str.startswith('00') and year_str[2:].isdigit():
            # Zero-padded century detected: '00YY-MM-DD' → derive century from filing_date
            yy = int(year_str[2:])
            mm_dd = raw[4:]
            if filing_date:
                try:
                    filing_year = int(filing_date[:4])
                    # Try both 20xx and 19xx
                    for century in [2000, 1900]:
                        year = century + yy
                        candidate = f"{year:04d}{mm_dd}"
                        # Validate date is parseable
                        try:
                            txn_dt = datetime.strptime(candidate, "%Y-%m-%d")
                            filing_dt = datetime.strptime(filing_date, "%Y-%m-%d")
                            # Transaction must precede filing and be within ~5 years of it
                            if txn_dt <= filing_dt and abs((txn_dt - filing_dt).days) <= 1825:
                                logger.debug(f"Normalized zero-padded century: {raw} → {candidate} (filing: {filing_date})")
                                return candidate
                        except ValueError:
                            continue
                except (ValueError, IndexError):
                    pass
            logger.warning(f"Cannot normalize zero-padded century date: {raw} (filing: {filing_date})")
            return None

    # Case 4: Already YYYY-MM-DD - validate and accept if <= today
    if len(raw) == 10 and raw[4] == '-' and raw[7] == '-':
        try:
            txn_dt = datetime.strptime(raw, "%Y-%m-%d")
            today = datetime.now()

            # Reject future dates beyond today - corrupt with no recoverable signal
            if txn_dt > today:
                logger.warning(f"Future date beyond today: {raw}, setting to NULL")
                return None

            # Transaction is parseable and <= today, accept it
            return raw
        except ValueError:
            pass

    # Unrecognized format
    logger.warning(f"Unrecognized transaction_date format: {raw}")
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
    
    # Build submission lookup: accession -> issuer, form type, dates
    sub_map = {}
    for s in submissions:
        acc = s.get("ACCESSION_NUMBER", "").strip()
        if not acc:
            continue
        doc_type = s.get("DOCUMENT_TYPE", "").strip().upper()
        if doc_type not in TRANSACTION_FORM_TYPES:
            continue
        sub_map[acc] = {
            "cik": normalize_cik(s.get("ISSUERCIK", "")),
            "issuer_name": s.get("ISSUERNAME", "").strip(),
            "ticker": s.get("ISSUERTRADINGSYMBOL", "").strip().upper(),
            "filing_date": _parse_sec_date(s.get("FILING_DATE", "")),
            "document_type": doc_type,
            "date_of_orig_sub": _parse_sec_date(s.get("DATE_OF_ORIG_SUB", "")),
        }

    # Build owner lookup: accession -> main owner (joint filings collapse to one)
    owners_by_acc = {}
    for o in owners:
        acc = o.get("ACCESSION_NUMBER", "").strip()
        if acc in sub_map:
            owners_by_acc.setdefault(acc, []).append({
                "name": o.get("RPTOWNERNAME", "").strip(),
                "cik": normalize_cik(o.get("RPTOWNERCIK", "")),
                "relationship": relationship_from_bulk(
                    o.get("RPTOWNER_RELATIONSHIP", ""), o.get("RPTOWNER_TITLE", "")),
            })
    owner_map = {acc: pick_primary_owner(lst) for acc, lst in owners_by_acc.items()}

    # Number each filing's trade lines in document order (the SK rises with it).
    def _sk(txn):
        try:
            return int(txn.get("NONDERIV_TRANS_SK", "0"))
        except ValueError:
            return 0
    nonderiv = sorted((t for t in nonderiv if t.get("ACCESSION_NUMBER", "").strip() in sub_map),
                      key=lambda t: (t["ACCESSION_NUMBER"].strip(), _sk(t)))

    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    skipped = 0
    errors = 0
    line_by_acc = {}
    company_ids = {}

    for txn in nonderiv:
        acc = txn["ACCESSION_NUMBER"].strip()
        sub = sub_map[acc]
        line_number = line_by_acc[acc] = line_by_acc.get(acc, 0) + 1

        ticker = sub["ticker"]
        if ticker_filter and ticker not in ticker_filter:
            skipped += 1
            continue

        txn_code = txn.get("TRANS_CODE", "").strip()
        shares = _safe_float(txn.get("TRANS_SHARES"))
        price = normalize_price(_safe_float(txn.get("TRANS_PRICEPERSHARE")))
        shares_after = _safe_float(txn.get("SHRS_OWND_FOLWNG_TRANS"))
        filing_date = sub["filing_date"]
        txn_date_raw = _parse_sec_date(txn.get("TRANS_DATE", ""))
        txn_date = normalize_transaction_date(txn_date_raw, filing_date)
        acq_disp = txn.get("TRANS_ACQUIRED_DISP_CD", "").strip()
        owner = owner_map.get(acc) or pick_primary_owner([])

        if not sub["cik"]:
            errors += 1
            continue
        if sub["cik"] not in company_ids:
            company_ids[sub["cik"]] = company_for_cik(conn, sub["cik"], sub["issuer_name"])
        company_id = company_ids[sub["cik"]]

        raw_json = json.dumps({
            "insider_name": owner["name"],
            "insider_cik": owner["cik"],
            "relationship": owner["relationship"],
            "all_owners": owner["all_owners"],
            "transaction_code": txn_code,
            "transaction_date": txn_date,
            "shares": shares,
            "price": price,
            "total_value": (shares * price) if (shares and price) else None,
            "shares_owned_after": shares_after,
            "acq_disp": acq_disp,
            "ownership": txn.get("DIRECT_INDIRECT_OWNERSHIP", "").strip(),
            "ownership_nature": txn.get("NATURE_OF_OWNERSHIP", "").strip(),
            "accession_number": acc,
            "document_type": sub["document_type"],
        })

        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO insider_transactions
                (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
                 transaction_type, shares_transacted, price, shares_owned_after, source, raw_json,
                 accession_number, line_number, document_type, date_of_orig_sub)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDGAR_BULK', ?, ?, ?, ?, ?)
            """, (company_id, filing_date, txn_date, owner["name"], owner["cik"],
                  txn_code, shares, price, shares_after, raw_json,
                  acc, line_number, sub["document_type"], sub["date_of_orig_sub"]))
            inserted += cur.rowcount
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"Insert error: {e}")

    # What each issuer called itself this quarter, for assigning tickers later.
    seen = {}
    for sub in sub_map.values():
        if sub["cik"] and sub["filing_date"]:
            key = (sub["cik"], sub["ticker"])
            name, first, last = seen.get(key, (sub["issuer_name"], sub["filing_date"], sub["filing_date"]))
            if sub["filing_date"] >= last:
                name = sub["issuer_name"]
            seen[key] = (name, min(first, sub["filing_date"]), max(last, sub["filing_date"]))
    record_issuer_tickers(conn, seen)

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
    
    # SEC publishes 2026q2 onward under datastandardsinnovation/; older quarters
    # are still under structureddata/. Try both so either era resolves.
    urls = [
        f"https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{filename}",
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
                # An archive or error page can answer 200 with HTML; keep only real zips.
                if not zipfile.is_zipfile(local_path):
                    os.remove(local_path)
                    logger.warning(f"  {url}: response was not a zip file")
                    continue
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


def rebuild_from_bulk(start_year=2020):
    """Empty insider_transactions and reload it from every bulk quarter.

    SEC publishes a quarter's file a few days after the quarter ends, so the
    current quarter and (early in a quarter) the previous one may not exist
    yet; the per-filing step covers filings after the newest loaded quarter.
    Any other missing quarter would leave a silent hole in the history, so
    this raises instead of publishing a partial table.
    Trades are filed under their issuer's CIK. Once all quarters are in,
    re-registered companies are folded into their successors and every
    company gets the ticker SEC lists for it today (see company_identity).
    Returns the ingest_all_bulk result plus the rows removed by the amendment
    and duplicate-filing passes and the CIKs folded together.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db"))
    from init_db import reset_insider_transactions
    from edgar_client import fetch_sec_company_list

    # Fetched first: without it tickers can't be assigned, so don't load anything.
    sec_map, sec_titles = fetch_sec_company_list()

    conn = sqlite3.connect(DB_PATH)
    reset_insider_transactions(conn)
    conn.close()

    result = ingest_all_bulk(start_year=start_year, force=True)
    statuses = [q["status"] for q in result["quarters"]]
    # Strip up to two trailing unpublished quarters; everything before must be ok.
    trailing = 0
    while trailing < 2 and statuses and statuses[-1 - trailing] != "ok":
        trailing += 1
    failed = [q["quarter"] for q in result["quarters"][:len(statuses) - trailing]
              if q["status"] != "ok"]
    if failed:
        raise RuntimeError(f"Bulk quarters failed to load: {failed}")
    if trailing:
        logger.info(f"Not yet published by SEC (covered per-filing): "
                    f"{[q['quarter'] for q in result['quarters'][-trailing:]]}")

    conn = sqlite3.connect(DB_PATH)
    result["duplicate_companies_merged"] = merge_duplicate_companies(conn)
    result["merged_ciks"] = merge_predecessors(conn, sec_map)
    result["tickers_changed"] = assign_tickers(conn, sec_map, sec_titles)
    result["amended_rows_removed"] = apply_amendments(conn)
    result["duplicate_rows_removed"] = remove_duplicate_filings(conn)
    result["duplicate_rows_removed"] += merge_related_owner_filings(conn)
    result["self_reported_rows_removed"] = remove_self_reported(conn)
    conn.close()
    logger.info(f"Folded {len(result['merged_ciks'])} old CIKs into their successors; "
                f"{result['tickers_changed']} company tickers or names updated")
    logger.info(f"Amendments replaced {result['amended_rows_removed']} superseded rows; "
                f"{result['duplicate_rows_removed']} trades repeated in other filings removed, "
                f"{result['self_reported_rows_removed']} filed under the company's own name removed")
    return result


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
