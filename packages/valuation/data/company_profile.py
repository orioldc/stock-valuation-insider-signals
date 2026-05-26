"""Fetch company profile data from yfinance (primary) and FMP (secondary)."""

import json
import os
import time
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "company"
CACHE_TTL_DAYS = 7


def _cache_path(ticker: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{ticker}_profile.json"


def _is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    return (time.time() - path.stat().st_mtime) > CACHE_TTL_DAYS * 86400


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


def get_profile(ticker: str, use_cache: bool = True) -> dict:
    """
    Fetch company profile. Returns a standardized dict with all fields
    needed by valuation modules.

    Args:
        ticker: Stock symbol (e.g. 'AAPL')
        use_cache: Use cached profile if fresh (< 7 days old)

    Returns dict with:
        ticker, name, sector, industry, description,
        market_cap, current_price, shares_outstanding,
        beta, enterprise_value,
        pe_ratio, pb_ratio, ev_ebitda, ps_ratio,
        dividend_yield, float_shares,
        country, exchange
    """
    cache = _cache_path(ticker)
    if use_cache and not _is_stale(cache):
        return json.loads(cache.read_text())

    profile = {"ticker": ticker.upper()}

    # Primary: yfinance
    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info

        profile["name"] = info.get("longName") or info.get("shortName") or ticker
        profile["sector"] = _normalize_sector(info.get("sector", ""), info.get("industry", ""))
        profile["industry"] = info.get("industry", "")
        profile["description"] = (info.get("longBusinessSummary") or "")[:500]
        profile["country"] = info.get("country", "US")
        profile["exchange"] = info.get("exchange", "")
        profile["market_cap"] = info.get("marketCap") or 0
        profile["current_price"] = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
            or 0
        )
        profile["shares_outstanding"] = info.get("sharesOutstanding") or 0
        profile["float_shares"] = info.get("floatShares") or 0
        profile["beta"] = info.get("beta") or 1.0
        profile["enterprise_value"] = info.get("enterpriseValue") or 0
        profile["pe_ratio"] = info.get("trailingPE") or info.get("forwardPE") or None
        profile["pb_ratio"] = info.get("priceToBook") or None
        profile["book_value"] = info.get("bookValue") or 0
        profile["ev_ebitda"] = info.get("enterpriseToEbitda") or None
        profile["ps_ratio"] = info.get("priceToSalesTrailing12Months") or None
        profile["dividend_yield"] = info.get("dividendYield") or 0.0
        profile["payout_ratio"] = info.get("payoutRatio") or 0.0
        profile["institutional_pct"] = info.get("heldPercentInstitutions") or 0.0
        profile["52w_high"] = info.get("fiftyTwoWeekHigh") or 0
        profile["52w_low"] = info.get("fiftyTwoWeekLow") or 0

    except Exception as e:
        print(f"[company_profile] yfinance failed for {ticker}: {e}")
        if not profile.get("name"):
            profile.setdefault("name", ticker)
            profile.setdefault("sector", "Unknown")
            profile.setdefault("industry", "")
            profile.setdefault("market_cap", 0)
            profile.setdefault("current_price", 0)
            profile.setdefault("shares_outstanding", 0)
            profile.setdefault("beta", 1.0)

    # Supplement with FMP for any missing critical fields
    try:
        fmp = _fmp_profile(ticker)
        if fmp:
            if not profile.get("beta") or profile["beta"] == 1.0:
                profile["beta"] = fmp.get("beta") or profile["beta"]
            if not profile.get("market_cap"):
                profile["market_cap"] = fmp.get("mktCap") or 0
            if not profile.get("description"):
                profile["description"] = (fmp.get("description") or "")[:500]
            # FMP sector classification as fallback
            if profile.get("sector") == "Unknown" and fmp.get("sector"):
                profile["sector"] = _normalize_sector(fmp["sector"])
                profile["industry"] = fmp.get("industry", "")
    except Exception:
        pass

    # Derived fields
    profile["market_cap_label"] = _mcap_label(profile.get("market_cap", 0))

    # Cache
    cache.write_text(json.dumps(profile, indent=2))
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
