import os
import requests
import json
import logging
import time
import xml.etree.ElementTree as ET
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SEC requires User-Agent format: "Company/App Contact@email.com"
# Browser-spoofing UAs and noreply emails get flagged and IP-blocked.
# See: https://www.sec.gov/os/accessing-edgar-data
USER_AGENTS = [
    "InsiderSignalTracker oriol.diaz@ozoneproject.com",
]
_ua_index = 0

def _get_headers():
    return {"User-Agent": USER_AGENTS[_ua_index % len(USER_AGENTS)]}

def _rotate_ua():
    global _ua_index
    _ua_index += 1
    logger.info(f"Rotated User-Agent to index {_ua_index % len(USER_AGENTS)}")

HEADERS = _get_headers()  # backwards compat

# ── Adaptive rate limiting ──
# SEC allows 10 req/s but aggressively throttles bots.
# We start conservative and back off further on 503s.
_last_request_time = 0
_base_delay = 0.2          # 5 req/s baseline (conservative)
_current_delay = 0.2       # adaptive — increases on 503
_max_delay = 5.0           # ceiling
_consecutive_503s = 0      # track back-to-back failures
_total_503s = 0            # lifetime count for logging


def _rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _current_delay:
        time.sleep(_current_delay - elapsed)
    _last_request_time = time.time()


def _on_503():
    """Called after every 503 — escalate global delay."""
    global _current_delay, _consecutive_503s, _total_503s
    _consecutive_503s += 1
    _total_503s += 1
    # Exponential backoff on the base rate: 0.2 → 0.4 → 0.8 → 1.6 → 3.2 → 5.0
    _current_delay = min(_current_delay * 2, _max_delay)
    logger.info(f"503 #{_total_503s} — global delay now {_current_delay:.1f}s")


def _on_success():
    """Called after every successful request — slowly recover speed."""
    global _current_delay, _consecutive_503s
    _consecutive_503s = 0
    # Gradually recover toward base delay (don't snap back instantly)
    if _current_delay > _base_delay:
        _current_delay = max(_current_delay * 0.95, _base_delay)


def _get_proxies():
    """Get proxy config from env if available."""
    proxy = os.environ.get("SOCKS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        return {"http": proxy, "https": proxy}
    return None


def _get(url, max_retries=6):
    """GET with adaptive rate limiting, UA rotation, proxy support, and exponential backoff."""
    proxies = _get_proxies()
    for attempt in range(max_retries):
        _rate_limit()
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=30, proxies=proxies)
        except requests.exceptions.Timeout:
            wait = 10 * (attempt + 1)
            logger.warning(f"Timeout on {url}, waiting {wait}s (attempt {attempt+1})")
            time.sleep(wait)
            continue
        except requests.exceptions.ConnectionError:
            wait = 15 * (attempt + 1)
            logger.warning(f"Connection error on {url}, waiting {wait}s (attempt {attempt+1})")
            time.sleep(wait)
            continue

        if resp.status_code == 503:
            _on_503()
            _rotate_ua()  # Try different UA on next attempt
            wait = min(15 * (2 ** attempt), 120)
            logger.warning(f"SEC 503 on {url} — attempt {attempt+1}/{max_retries}, waiting {wait}s")
            time.sleep(wait)
            continue
        
        if resp.status_code == 403:
            _rotate_ua()  # Rotate UA on 403 (IP/UA blocked)
            wait = min(30 * (2 ** attempt), 180)
            logger.warning(f"SEC 403 on {url} — attempt {attempt+1}/{max_retries}, rotating UA, waiting {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 404:
            # Don't retry 404s
            resp.raise_for_status()

        if resp.status_code == 429:
            wait = min(30 * (2 ** attempt), 180)
            logger.warning(f"SEC 429 rate limit on {url} — waiting {wait}s")
            time.sleep(wait)
            continue

        _on_success()
        resp.raise_for_status()
        return resp

    # Exhausted retries
    logger.error(f"Failed after {max_retries} retries: {url}")
    raise requests.exceptions.HTTPError(f"Failed after {max_retries} retries (last status: 503): {url}")


def get_rate_stats():
    """Return current rate limiting stats for logging."""
    return {
        "current_delay": _current_delay,
        "total_503s": _total_503s,
        "consecutive_503s": _consecutive_503s,
    }


def fetch_company_tickers():
    """Fetch ticker -> CIK mapping from SEC, with DB fallback."""
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = _get(url)
        data = resp.json()
        ticker_map = {}
        for key, value in data.items():
            ticker_map[value['ticker']] = value['cik_str']
        logger.info(f"Fetched {len(ticker_map)} tickers from SEC.")
        return ticker_map
    except Exception as e:
        logger.warning(f"Failed to fetch tickers from SEC: {e}")
        # Fallback: load from local DB
        try:
            import sqlite3, os
            db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'db', 'insider_signals.db')
            if os.path.exists(db_path):
                db = sqlite3.connect(db_path)
                rows = db.execute("SELECT DISTINCT ticker, cik FROM companies WHERE cik IS NOT NULL").fetchall()
                db.close()
                if rows:
                    ticker_map = {r[0]: r[1] for r in rows}
                    logger.info(f"Loaded {len(ticker_map)} tickers from local DB (SEC fallback).")
                    return ticker_map
        except Exception as e2:
            logger.error(f"DB fallback also failed: {e2}")
        return None


def fetch_form4_filings(cik, limit=150, since_date="2020-01-01"):
    """
    Fetch Form 4 filings for a given CIK from data.sec.gov submissions API.
    This endpoint is less rate-limited than www.sec.gov archives.
    Returns list of dicts: {filing_date, accession_number, primary_doc}
    """
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        resp = _get(url)
        data = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch filings for CIK {cik}: {e}")
        return []

    def _extract_form4s(filing_data):
        forms = filing_data.get("form", [])
        dates = filing_data.get("filingDate", [])
        accessions = filing_data.get("accessionNumber", [])
        primary_docs = filing_data.get("primaryDocument", [])
        hits = []
        for i, form in enumerate(forms):
            if form == "4":
                fdate = dates[i]
                if fdate < since_date:
                    continue
                hits.append({
                    "filing_date": fdate,
                    "accession_number": accessions[i],
                    "primary_doc": primary_docs[i],
                    "cik": str(cik),
                })
        return hits

    filings_obj = data.get("filings", {})
    results = _extract_form4s(filings_obj.get("recent", {}))

    older_files = filings_obj.get("files", [])
    for file_info in older_files:
        fname = file_info.get("name", "")
        if not fname:
            continue
        older_url = f"https://data.sec.gov/submissions/{fname}"
        try:
            older_resp = _get(older_url)
            older_data = older_resp.json()
            older_form4s = _extract_form4s(older_data)
            if not older_form4s:
                break
            results.extend(older_form4s)
        except Exception as e:
            logger.warning(f"Failed to fetch older filings {fname} for CIK {cik}: {e}")
            continue

    if limit:
        results = results[:limit]

    logger.info(f"Found {len(results)} Form 4 filings for CIK {cik} (since {since_date})")
    return results


def parse_form4_xml(cik, accession_number, primary_doc):
    """
    Fetch and parse a Form 4 XML filing from www.sec.gov archives.
    Returns list of transaction dicts.
    """
    acc_no_dashes = accession_number.replace("-", "")
    xml_filename = primary_doc.split("/")[-1] if "/" in primary_doc else primary_doc
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{xml_filename}"
    
    try:
        resp = _get(url)
        content = resp.text
    except Exception as e:
        logger.error(f"Failed to fetch Form 4 XML {url}: {e}")
        return []
    
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML from {url}: {e}")
        return []
    
    def strip_ns(tag):
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag
    
    def find(el, path):
        parts = path.split(".")
        current = el
        for part in parts:
            found = None
            for child in current:
                if strip_ns(child.tag) == part:
                    found = child
                    break
            if found is None:
                return None
            current = found
        return current
    
    def find_text(el, path, default=None):
        node = find(el, path)
        if node is not None and node.text:
            return node.text.strip()
        return default
    
    def find_all(el, tag):
        results = []
        for child in el:
            if strip_ns(child.tag) == tag:
                results.append(child)
        return results
    
    def find_all_recursive(el, tag):
        results = []
        for child in el.iter():
            if strip_ns(child.tag) == tag:
                results.append(child)
        return results
    
    owners = find_all_recursive(root, "reportingOwner")
    owner_name = ""
    owner_cik = ""
    is_director = False
    is_officer = False
    is_ten_pct = False
    officer_title = ""
    
    if owners:
        owner = owners[0]
        owner_name = find_text(owner, "reportingOwnerId.rptOwnerName", "")
        owner_cik = find_text(owner, "reportingOwnerId.rptOwnerCik", "")
        rel = find(owner, "reportingOwnerRelationship")
        if rel is not None:
            is_director = find_text(rel, "isDirector", "0") == "1" or find_text(rel, "isDirector", "false").lower() == "true"
            is_officer = find_text(rel, "isOfficer", "0") == "1" or find_text(rel, "isOfficer", "false").lower() == "true"
            is_ten_pct = find_text(rel, "isTenPercentOwner", "0") == "1" or find_text(rel, "isTenPercentOwner", "false").lower() == "true"
            officer_title = find_text(rel, "officerTitle", "")
    
    roles = []
    if is_officer:
        roles.append(f"Officer ({officer_title})" if officer_title else "Officer")
    if is_director:
        roles.append("Director")
    if is_ten_pct:
        roles.append("10% Owner")
    relationship = ", ".join(roles) if roles else "Unknown"
    
    transactions = find_all_recursive(root, "nonDerivativeTransaction")
    results = []
    
    for txn in transactions:
        try:
            txn_date = find_text(txn, "transactionDate.value", "")
            txn_code = find_text(txn, "transactionCoding.transactionCode", "")
            
            amounts = find(txn, "transactionAmounts")
            shares = None
            price = None
            if amounts is not None:
                shares_str = find_text(amounts, "transactionShares.value")
                price_str = find_text(amounts, "transactionPricePerShare.value")
                shares = float(shares_str) if shares_str else None
                price = float(price_str) if price_str else None
            
            post_el = find(txn, "postTransactionAmounts")
            shares_after = None
            if post_el is not None:
                sa_str = find_text(post_el, "sharesOwnedFollowingTransaction.value")
                shares_after = float(sa_str) if sa_str else None
            
            total_value = (shares * price) if (shares and price) else None
            acq_disp = find_text(txn, "transactionAmounts.transactionAcquiredDisposedCode.value", "")
            
            results.append({
                "insider_name": owner_name,
                "insider_cik": owner_cik,
                "relationship": relationship,
                "transaction_code": txn_code,
                "transaction_date": txn_date,
                "shares": shares,
                "price": price,
                "total_value": total_value,
                "shares_owned_after": shares_after,
                "acq_disp": acq_disp,
            })
        except Exception as e:
            logger.warning(f"Error parsing transaction in {url}: {e}")
            continue
    
    if not results:
        logger.debug(f"No non-derivative transactions found in {url}")
    
    return results


def test_connection():
    logger.info("Testing connection to SEC EDGAR...")
    tickers = fetch_company_tickers()
    if tickers:
        aapl_cik = tickers.get("AAPL")
        print(f"Connection Successful. AAPL CIK: {aapl_cik}")
    else:
        print("Connection Failed.")


if __name__ == "__main__":
    test_connection()
