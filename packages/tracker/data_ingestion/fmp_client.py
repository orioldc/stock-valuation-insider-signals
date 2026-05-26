"""FMP (Financial Modeling Prep) client for insider trading data."""
import os
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com"


def get_insider_trades(ticker: str, page: int = 0) -> list[dict]:
    """Fetch insider trades for a ticker from FMP."""
    url = f"{BASE_URL}/api/v4/insider-trading"
    params = {"symbol": ticker, "page": page, "apikey": API_KEY}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def test_connection():
    """Quick test: fetch AAPL insider trades and print the first result."""
    try:
        trades = get_insider_trades("AAPL")
        print(f"FMP OK — Got {len(trades)} trades for AAPL")
        if trades:
            t = trades[0]
            print(f"First trade: {t.get('reportingName','')} | {t.get('transactionType','')} | "
                  f"{t.get('securitiesTransacted','')} shares @ ${t.get('price','')} on {t.get('transactionDate','')}")
        return trades
    except requests.exceptions.HTTPError as e:
        print(f"FMP ERROR: {e.response.status_code} — {e.response.text[:200]}")
        print("Note: insider-trading endpoint requires a paid FMP plan.")
        return None


if __name__ == "__main__":
    test_connection()
