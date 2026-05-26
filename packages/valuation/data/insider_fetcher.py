"""Live per-ticker insider data fetcher from SEC EDGAR.

Ported from insider-signal-tracker (https://github.com/fuertesito91/insider-signal-tracker).
"""

import json
import logging
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from data.edgar_client import (
    _get,
    fetch_company_tickers,
    fetch_form4_filings,
    parse_form4_xml,
)

logger = logging.getLogger(__name__)

INSIDER_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "insider"
INSIDER_CACHE_TTL_DAYS = 7


def _get_seniority_weight(relationship: str) -> float:
    r = relationship.upper()
    if any(k in r for k in ["CEO", "CFO", "COO", "CHIEF", "PRESIDENT"]):
        return 3.0
    if any(k in r for k in ["VP", "SVP", "EVP", "OFFICER"]):
        return 2.0
    if "DIRECTOR" in r:
        return 1.5
    if "10%" in r or "OWNER" in r:
        return 1.0
    return 1.0


def _detect_clusters(trades: list[dict], lookback_days=90, window_days=30) -> dict:
    """Detect insider buying clusters from a list of transaction dicts."""
    if not trades:
        return {"cluster_detected": False, "score": 0.0, "details": []}

    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    recent = [t for t in trades if t.get("transaction_date", "") >= cutoff]
    if not recent:
        return {"cluster_detected": False, "score": 0.0, "details": []}

    enriched = []
    for t in recent:
        price = t.get("price") or 0
        shares = t.get("shares") or 0
        enriched.append({
            "date": t.get("transaction_date", ""),
            "name": t.get("insider_name", ""),
            "cik": t.get("insider_cik", ""),
            "shares": shares,
            "price": price,
            "value": t.get("total_value") or (price * shares),
            "relationship": t.get("relationship", ""),
            "seniority_weight": _get_seniority_weight(t.get("relationship", "")),
        })

    best_cluster = []
    best_score = 0.0

    for i, t in enumerate(enriched):
        t_date = datetime.strptime(t["date"][:10], "%Y-%m-%d")
        window_end = t_date + timedelta(days=window_days)

        cluster = [t]
        for j, t2 in enumerate(enriched):
            if i == j:
                continue
            t2_date = datetime.strptime(t2["date"][:10], "%Y-%m-%d")
            if t_date <= t2_date <= window_end:
                cluster.append(t2)

        distinct_insiders = set(tr["cik"] for tr in cluster)
        if len(distinct_insiders) < 2:
            continue

        cluster_size = len(distinct_insiders)
        total_value = sum(tr["value"] for tr in cluster)
        avg_seniority = sum(tr["seniority_weight"] for tr in cluster) / len(cluster)
        log_value = math.log(max(total_value, 1))
        score = cluster_size * log_value * avg_seniority

        if score > best_score:
            best_score = score
            best_cluster = cluster

    return {
        "cluster_detected": best_score > 0,
        "score": round(best_score, 4),
        "details": best_cluster,
    }


def _fetch_shares_outstanding(cik: int) -> list[dict]:
    """Fetch shares outstanding time series from SEC Company Facts API."""
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json"
    try:
        resp = _get(url)
        data = resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch company facts for CIK {cik}: {e}")
        return []

    facts = data.get("facts", {})
    dei = facts.get("dei", {}) if "dei" in facts else facts.get("us-gaap", {})
    entity_info = dei

    # Try both common labels for shares outstanding
    shares_units = None
    for label in ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding", "CommonStockSharesIssued"):
        if label in facts.get("dei", {}):
            shares_units = facts["dei"][label].get("units", {})
            break
        if label in facts.get("us-gaap", {}):
            shares_units = facts["us-gaap"][label].get("units", {})
            break
    if "shares" in facts.get("dei", {}) and label not in facts.get("dei", {}):
        shares_units = facts["dei"]["shares"].get("units", {})

    if not shares_units:
        logger.warning(f"No shares outstanding data for CIK {cik}")
        return []

    # Prefer "shares" unit
    entries = shares_units.get("shares", []) or []
    if not entries:
        for unit_name, unit_entries in shares_units.items():
            entries = unit_entries
            break

    results = []
    for entry in entries:
        date = entry.get("fp", "") and entry.get("end", "")
        if not date:
            date = entry.get("end", "")
        results.append({
            "date": date,
            "shares": entry.get("val", 0),
        })
    return results


def _compute_share_delta(shares_data: list[dict]) -> dict:
    """Compute QoQ and 4Q share count changes from time series data."""
    if len(shares_data) < 2:
        return {"delta_qoq": 0, "delta_4q": 0, "trend": "insufficient_data", "score": 0, "data_points": len(shares_data)}

    quarterly = {}
    for record in shares_data:
        date_str = record.get("date", "")
        shares = record.get("shares", 0)
        if not date_str or not shares:
            continue
        try:
            year = date_str[:4]
            month = int(date_str[5:7])
            q = (int(year), (month - 1) // 3 + 1)
            quarterly[q] = shares  # last value wins
        except (ValueError, IndexError):
            continue

    if len(quarterly) < 2:
        return {"delta_qoq": 0, "delta_4q": 0, "trend": "insufficient_data", "score": 0, "data_points": len(quarterly)}

    sorted_quarters = sorted(quarterly.keys())
    values = [quarterly[q] for q in sorted_quarters]

    delta_qoq = (values[-1] - values[-2]) / values[-2] * 100 if values[-2] != 0 else 0

    if len(values) >= 5:
        delta_4q = (values[-1] - values[-5]) / values[-5] * 100 if values[-5] != 0 else 0
    else:
        delta_4q = (values[-1] - values[0]) / values[0] * 100 if values[0] != 0 else 0

    if delta_qoq <= -1:
        trend = "buyback"
    elif delta_qoq >= 1:
        trend = "dilution"
    else:
        trend = "stable"

    raw = min(0, delta_4q)
    score = min(abs(raw) / 20.0, 1.0)

    return {
        "delta_qoq": round(delta_qoq, 4),
        "delta_4q": round(delta_4q, 4),
        "trend": trend,
        "score": round(score, 4),
        "data_points": len(quarterly),
    }


def _load_cache(ticker: str) -> dict | None:
    """Load cached insider data for a ticker if fresh."""
    cache_file = INSIDER_CACHE_DIR / f"{ticker.upper()}.json"
    if not cache_file.exists():
        return None
    age = time.time() - cache_file.stat().st_mtime
    if age > INSIDER_CACHE_TTL_DAYS * 86400:
        return None
    try:
        with open(cache_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(ticker: str, data: dict):
    """Save insider data to per-ticker cache."""
    INSIDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = INSIDER_CACHE_DIR / f"{ticker.upper()}.json"
    try:
        with open(cache_file, "w") as f:
            json.dump(data, f)
    except OSError as e:
        logger.warning(f"Failed to write insider cache for {ticker}: {e}")


def fetch_insider_data(ticker: str, use_cache: bool = True) -> dict | None:
    """Fetch insider trading data for a single ticker from SEC EDGAR.

    Returns the same dict structure as the frozen data file, or None.
    """
    ticker = ticker.upper()
    if use_cache:
        cached = _load_cache(ticker)
        if cached is not None:
            return cached

    # Get CIK for ticker
    ticker_map = fetch_company_tickers()
    if ticker_map is None:
        logger.warning("No ticker->CIK mapping available")
        return None

    cik = ticker_map.get(ticker)
    if cik is None:
        logger.info(f"{ticker} not found in SEC ticker list")
        return None

    # Fetch Form 4 filings
    filings = fetch_form4_filings(cik, limit=50, since_date="2024-01-01")
    if not filings:
        logger.info(f"No Form 4 filings found for {ticker}")
        return None

    # Parse all filings and extract purchase transactions
    all_purchases = []
    latest_txn_date = None
    for filing in filings:
        txns = parse_form4_xml(filing["cik"], filing["accession_number"], filing["primary_doc"])
        for txn in txns:
            if txn.get("transaction_code") == "P" and txn.get("shares", 0) > 0:
                all_purchases.append(txn)
                txn_date = txn.get("transaction_date", "")
                if txn_date and (latest_txn_date is None or txn_date > latest_txn_date):
                    latest_txn_date = txn_date

    if not all_purchases:
        logger.info(f"No purchase transactions found for {ticker}")
        return None

    # Build insider summary
    recent_purchases = [
        t for t in all_purchases
        if t.get("transaction_date", "") >= (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    ]

    n_insiders = 0
    total_value = 0.0
    insider_summary = None
    if recent_purchases:
        unique_names = set()
        for t in recent_purchases:
            unique_names.add(t.get("insider_name", ""))
            tv = t.get("total_value") or 0
            total_value += tv
        n_insiders = len(unique_names)
        names = "; ".join(list(unique_names)[:3])
        insider_summary = f"{n_insiders} insider(s) bought ${total_value:,.0f} in last 120 days ({names})"

    # Cluster detection
    cluster = _detect_clusters(all_purchases)

    # Share count change
    shares_data = _fetch_shares_outstanding(cik)
    share_delta = _compute_share_delta(shares_data)

    result = {
        "ticker": ticker,
        "in_universe": True,
        "conviction_score": None,
        "quality": None,
        "cluster_detected": cluster["cluster_detected"],
        "n_insiders": n_insiders,
        "total_value": total_value,
        "share_delta_4q": share_delta["delta_4q"],
        "share_delta_qoq": share_delta["delta_qoq"],
        "share_trend": share_delta["trend"],
        "latest_transaction_date": latest_txn_date,
        "insider_summary": insider_summary,
    }

    _save_cache(ticker, result)
    return result
