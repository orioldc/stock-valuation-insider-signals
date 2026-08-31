"""Fetch company profile data from yfinance (primary) and FMP (secondary)."""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "company"
CACHE_TTL_STABLE_DAYS = 7
CACHE_TTL_VOLATILE_HOURS = 4


def _cache_path_stable(ticker: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{ticker}_profile_stable.json"


def _cache_path_volatile(ticker: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{ticker}_profile_volatile.json"


def _is_stale(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return True
    return (time.time() - path.stat().st_mtime) > ttl_seconds


def _normalize_sector(sector: str, industry: str = "") -> str:
    """
    Map yfinance sector strings to cleaner canonical names used across the agent.
    Returns a standardized sector string.
    """
    mapping = {
        "Technology": "Technology",
        "Financial Services": "Financial Services",
        "Healthcare": "Healthcare",
        "Consumer Cyclical": "Consumer Cyclical",
        "Consumer Defensive": "Consumer Defensive",
        "Industrials": "Industrials",
        "Basic Materials": "Basic Materials",
        "Energy": "Energy",
        "Real Estate": "Real Estate",
        "Utilities": "Utilities",
        "Communication Services": "Communication Services",
    }
    return mapping.get(sector, sector or "Unknown")


def _fmp_profile(ticker: str) -> dict:
    """Get company profile from FMP as supplementary data."""
    if not FMP_API_KEY:
        return {}
    try:
        url = f"{FMP_BASE}/api/v3/profile/{ticker}"
        resp = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list):
            return data[0]
    except Exception:
        pass
    return {}


def _fetch_stable_profile(ticker: str) -> dict:
    """Fetch stable company fields (name, sector, etc.) from yfinance + FMP.

    These fields change rarely and are cached for 7 days.
    """
    profile = {"ticker": ticker}

    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info

        profile["name"] = info.get("longName") or info.get("shortName") or ticker
        profile["sector"] = _normalize_sector(info.get("sector", ""), info.get("industry", ""))
        profile["industry"] = info.get("industry", "")
        profile["description"] = (info.get("longBusinessSummary") or "")[:500]
        profile["country"] = info.get("country", "US")
        profile["exchange"] = info.get("exchange", "")
        profile["shares_outstanding"] = info.get("sharesOutstanding") or 0
        profile["float_shares"] = info.get("floatShares") or 0
        profile["beta"] = info.get("beta") or 1.0
        profile["book_value"] = info.get("bookValue") or 0
        profile["payout_ratio"] = info.get("payoutRatio") or 0.0
        profile["institutional_pct"] = info.get("heldPercentInstitutions") or 0.0

    except Exception as e:
        print(f"[company_profile] yfinance failed for {ticker} (stable): {e}")
        profile.setdefault("name", ticker)
        profile.setdefault("sector", "Unknown")
        profile.setdefault("industry", "")
        profile.setdefault("shares_outstanding", 0)
        profile.setdefault("beta", 1.0)

    # Supplement with FMP for missing fields
    try:
        fmp = _fmp_profile(ticker)
        if fmp:
            if not profile.get("beta") or profile["beta"] == 1.0:
                profile["beta"] = fmp.get("beta") or profile["beta"]
            if not profile.get("description"):
                profile["description"] = (fmp.get("description") or "")[:500]
            if profile.get("sector") == "Unknown" and fmp.get("sector"):
                profile["sector"] = _normalize_sector(fmp["sector"])
                profile["industry"] = fmp.get("industry", "")
    except Exception:
        pass

    return profile


def _fetch_volatile_profile(ticker: str) -> dict:
    """Fetch volatile price-dependent fields from yfinance.

    These fields change daily and are cached for 4 hours with fallback on failure.
    Returns price_date representing the trading date of the price.
    """
    profile = {}

    yf_ticker = yf.Ticker(ticker)
    info = yf_ticker.info

    # Price fields
    profile["current_price"] = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
        or 0
    )

    # Capture price date from regularMarketTime (Unix timestamp)
    price_date = None
    price_fetch_time = datetime.now().isoformat()
    if info.get("regularMarketTime"):
        try:
            price_dt = datetime.fromtimestamp(info["regularMarketTime"])
            price_date = price_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    # If no regularMarketTime, infer from current date (assume last trading day)
    if not price_date:
        # Simple heuristic: if today is weekend, use Friday; otherwise use today
        now = datetime.now()
        weekday = now.weekday()
        if weekday == 5:  # Saturday
            price_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        elif weekday == 6:  # Sunday
            price_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        else:
            price_date = now.strftime("%Y-%m-%d")

    profile["price_date"] = price_date
    profile["price_fetch_time"] = price_fetch_time

    # Price-dependent fields
    profile["market_cap"] = info.get("marketCap") or 0
    profile["enterprise_value"] = info.get("enterpriseValue") or 0
    profile["pe_ratio"] = info.get("trailingPE") or info.get("forwardPE") or None
    profile["pb_ratio"] = info.get("priceToBook") or None
    profile["ev_ebitda"] = info.get("enterpriseToEbitda") or None
    profile["ps_ratio"] = info.get("priceToSalesTrailing12Months") or None
    profile["dividend_yield"] = info.get("dividendYield") or 0.0
    profile["52w_high"] = info.get("fiftyTwoWeekHigh") or 0
    profile["52w_low"] = info.get("fiftyTwoWeekLow") or 0

    # FMP supplement for market cap if missing
    try:
        fmp = _fmp_profile(ticker)
        if fmp and not profile.get("market_cap"):
            profile["market_cap"] = fmp.get("mktCap") or 0
    except Exception:
        pass

    return profile


def get_profile(ticker: str, use_cache: bool = True) -> dict:
    """
    Fetch company profile. Returns a standardized dict with all fields
    needed by valuation modules.

    Stable fields (name, sector, etc.) are cached for 7 days.
    Volatile fields (current_price and price-dependent ratios) are cached
    for 4 hours and fetched fresh with fallback to stale cache on failure.

    Args:
        ticker: Stock symbol (e.g. 'AAPL')
        use_cache: Use cached profile if fresh

    Returns dict with:
        ticker, name, sector, industry, description,
        market_cap, current_price, price_date, shares_outstanding,
        beta, enterprise_value,
        pe_ratio, pb_ratio, ev_ebitda, ps_ratio,
        dividend_yield, float_shares,
        country, exchange
    """
    ticker = ticker.upper()
    stable_cache = _cache_path_stable(ticker)
    volatile_cache = _cache_path_volatile(ticker)

    # Load stable profile (7-day cache)
    stable_ttl = CACHE_TTL_STABLE_DAYS * 86400
    if use_cache and not _is_stale(stable_cache, stable_ttl):
        stable = json.loads(stable_cache.read_text())
    else:
        stable = _fetch_stable_profile(ticker)
        stable_cache.write_text(json.dumps(stable, indent=2))

    # Try volatile cache first (4-hour TTL)
    volatile_ttl = CACHE_TTL_VOLATILE_HOURS * 3600
    volatile = None
    if use_cache and not _is_stale(volatile_cache, volatile_ttl):
        volatile = json.loads(volatile_cache.read_text())

    # If volatile stale or missing, fetch fresh with fallback
    if volatile is None:
        try:
            volatile = _fetch_volatile_profile(ticker)
            volatile_cache.write_text(json.dumps(volatile, indent=2))
        except Exception as e:
            # Fallback to stale cache if available
            if volatile_cache.exists():
                print(f"[company_profile] Fresh price fetch failed for {ticker}, using cached data: {e}")
                volatile = json.loads(volatile_cache.read_text())
            else:
                print(f"[company_profile] Fresh price fetch failed for {ticker}, no cache available: {e}")
                volatile = {
                    "current_price": 0,
                    "price_date": None,
                    "price_fetch_time": None,
                    "market_cap": 0,
                    "enterprise_value": 0,
                    "pe_ratio": None,
                    "pb_ratio": None,
                    "ev_ebitda": None,
                    "ps_ratio": None,
                    "dividend_yield": 0.0,
                    "52w_high": 0,
                    "52w_low": 0,
                }

    # Merge stable and volatile
    profile = {**stable, **volatile}

    # Add derived fields
    profile["market_cap_label"] = _mcap_label(profile.get("market_cap", 0))

    return profile


def _mcap_label(mc: float) -> str:
    if mc >= 200e9:
        return "Mega Cap"
    if mc >= 10e9:
        return "Large Cap"
    if mc >= 2e9:
        return "Mid Cap"
    if mc >= 300e6:
        return "Small Cap"
    return "Micro Cap"


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    p = get_profile(ticker)
    for k, v in p.items():
        if k != "description":
            print(f"  {k}: {v}")
