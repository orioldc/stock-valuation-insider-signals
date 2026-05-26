"""SEC EDGAR API client — rate-limited Form 4 filing fetcher and XML parser.

Portions ported from insider-signal-tracker (https://github.com/fuertesito91/insider-signal-tracker).
"""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# SEC requires a valid User-Agent: "Company/App Contact@email.com"
# https://www.sec.gov/os/accessing-edgar-data
USER_AGENTS = [
    "DamodaranValuationAgent admin@fuertesito.dev",
]
_ua_index = 0
_current_delay = 0.2
_base_delay = 0.2
_max_delay = 5.0
_consecutive_503s = 0
_total_503s = 0
_last_request_time = 0
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
TICKER_CIK_CACHE = CACHE_DIR / "ticker_cik.json"
TICKER_CIK_TTL_DAYS = 30


def _get_headers():
    return {"User-Agent": USER_AGENTS[_ua_index % len(USER_AGENTS)]}


def _rotate_ua():
    global _ua_index
    _ua_index += 1
    logger.info(f"Rotated User-Agent to index {_ua_index % len(USER_AGENTS)}")


def _rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _current_delay:
        time.sleep(_current_delay - elapsed)
    _last_request_time = time.time()


def _on_503():
    global _current_delay, _consecutive_503s, _total_503s
    _consecutive_503s += 1
    _total_503s += 1
    _current_delay = min(_current_delay * 2, _max_delay)
    logger.info(f"503 #{_total_503s} — global delay now {_current_delay:.1f}s")


def _on_success():
    global _current_delay, _consecutive_503s
    _consecutive_503s = 0
    if _current_delay > _base_delay:
        _current_delay = max(_current_delay * 0.95, _base_delay)


def _get_proxies():
    proxy = os.environ.get("SOCKS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        return {"http": proxy, "https": proxy}
    return None


def _get(url, max_retries=6):
    """GET with adaptive rate limiting, UA rotation, and exponential backoff."""
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
        except requests.exceptions.ConnectionError as e:
            wait = 15 * (attempt + 1)
            logger.warning(f"Connection error on {url}: {e}, waiting {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 503:
            _on_503()
            _rotate_ua()
            wait = min(15 * (2 ** attempt), 120)
            logger.warning(f"SEC 503 on {url} — attempt {attempt+1}/{max_retries}, waiting {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 403:
            _rotate_ua()
            wait = min(30 * (2 ** attempt), 180)
            logger.warning(f"SEC 403 on {url} — attempt {attempt+1}/{max_retries}, waiting {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            wait = min(30 * (2 ** attempt), 180)
            logger.warning(f"SEC 429 on {url} — waiting {wait}s")
            time.sleep(wait)
            continue

        _on_success()
        resp.raise_for_status()
        return resp

    raise requests.exceptions.HTTPError(f"Failed after {max_retries} retries: {url}")


def get_rate_stats() -> dict:
    return {
        "current_delay": _current_delay,
        "total_503s": _total_503s,
        "consecutive_503s": _consecutive_503s,
    }


def fetch_company_tickers(force_refresh: bool = False) -> dict | None:
    """Fetch ticker -> CIK mapping from SEC, cached locally."""
    if not force_refresh and TICKER_CIK_CACHE.exists():
        age = time.time() - TICKER_CIK_CACHE.stat().st_mtime
        if age < TICKER_CIK_TTL_DAYS * 86400:
            with open(TICKER_CIK_CACHE) as f:
                return json.load(f)

    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = _get(url)
        data = resp.json()
        ticker_map = {}
        for key, value in data.items():
            ticker_map[value["ticker"]] = value["cik_str"]
        logger.info(f"Fetched {len(ticker_map)} tickers from SEC.")
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(TICKER_CIK_CACHE, "w") as f:
            json.dump(ticker_map, f)
        return ticker_map
    except Exception as e:
        logger.warning(f"Failed to fetch tickers from SEC: {e}")
        if TICKER_CIK_CACHE.exists():
            with open(TICKER_CIK_CACHE) as f:
                return json.load(f)
        return None


def fetch_form4_filings(cik: int, limit: int = 50, since_date: str = "2024-01-01") -> list[dict]:
    """Fetch Form 4 filing metadata for a CIK from data.sec.gov submissions API."""
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


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find(el, path: str):
    parts = path.split(".")
    current = el
    for part in parts:
        found = None
        for child in current:
            if _strip_ns(child.tag) == part:
                found = child
                break
        if found is None:
            return None
        current = found
    return current


def _find_text(el, path: str, default=None):
    node = _find(el, path)
    if node is not None and node.text:
        return node.text.strip()
    return default


def _find_all_recursive(el, tag: str) -> list:
    return [child for child in el.iter() if _strip_ns(child.tag) == tag]


def parse_form4_xml(cik: str, accession_number: str, primary_doc: str) -> list[dict]:
    """Fetch and parse a Form 4 XML filing, returning purchase transactions only."""
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

    # Extract reporting owner
    owners = _find_all_recursive(root, "reportingOwner")
    owner_name = ""
    owner_cik = ""
    is_director = False
    is_officer = False
    is_ten_pct = False
    officer_title = ""

    if owners:
        owner = owners[0]
        owner_name = _find_text(owner, "reportingOwnerId.rptOwnerName", "")
        owner_cik = _find_text(owner, "reportingOwnerId.rptOwnerCik", "")
        rel = _find(owner, "reportingOwnerRelationship")
        if rel is not None:
            is_director = (
                _find_text(rel, "isDirector", "0") == "1"
                or _find_text(rel, "isDirector", "false").lower() == "true"
            )
            is_officer = (
                _find_text(rel, "isOfficer", "0") == "1"
                or _find_text(rel, "isOfficer", "false").lower() == "true"
            )
            is_ten_pct = (
                _find_text(rel, "isTenPercentOwner", "0") == "1"
                or _find_text(rel, "isTenPercentOwner", "false").lower() == "true"
            )
            officer_title = _find_text(rel, "officerTitle", "")

    roles = []
    if is_officer:
        roles.append(f"Officer ({officer_title})" if officer_title else "Officer")
    if is_director:
        roles.append("Director")
    if is_ten_pct:
        roles.append("10% Owner")
    relationship = ", ".join(roles) if roles else "Unknown"

    transactions = _find_all_recursive(root, "nonDerivativeTransaction")
    results = []

    for txn in transactions:
        try:
            txn_date = _find_text(txn, "transactionDate.value", "")
            txn_code = _find_text(txn, "transactionCoding.transactionCode", "")

            amounts = _find(txn, "transactionAmounts")
            shares = None
            price = None
            if amounts is not None:
                shares_str = _find_text(amounts, "transactionShares.value")
                price_str = _find_text(amounts, "transactionPricePerShare.value")
                shares = float(shares_str) if shares_str else None
                price = float(price_str) if price_str else None

            shares_after = None
            post_el = _find(txn, "postTransactionAmounts")
            if post_el is not None:
                sa_str = _find_text(post_el, "sharesOwnedFollowingTransaction.value")
                shares_after = float(sa_str) if sa_str else None

            total_value = (shares * price) if (shares and price) else None
            acq_disp = _find_text(txn, "transactionAmounts.transactionAcquiredDisposedCode.value", "")

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

    return results
