#!/usr/bin/env python3
"""
Regression test: Verify USD reporters are unchanged.

Test tickers: MTDR, FI, NGL, DKL, AMR
All should show NO currency conversion and values should be stable.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "packages" / "valuation"))

from data.financials import get_ttm_financials
from data.company_profile import get_profile


def test_usd_ticker(ticker: str):
    """Test that a USD ticker shows no conversion."""
    print(f"\n{'='*60}")
    print(f"Testing {ticker}")
    print(f"{'='*60}")

    try:
        profile = get_profile(ticker, use_cache=False)
        if not profile:
            print(f"  ⚠️  Profile unavailable")
            return

        financials = get_ttm_financials(ticker, no_cache=True)
        if not financials:
            print(f"  ⚠️  Financials unavailable")
            return

        price_curr = profile.get("currency", "?")
        fin_curr = profile.get("financial_currency", "?")
        converted = financials.get("currency_converted_to")

        print(f"  Price Currency: {price_curr}")
        print(f"  Financial Currency: {fin_curr}")
        print(f"  Conversion Applied: {'YES' if converted else 'NO'}")

        if price_curr == "USD" and fin_curr == "USD" and not converted:
            print(f"  ✅ PASS: No conversion for USD → USD")
        elif converted:
            print(f"  ❌ FAIL: Unexpected conversion applied for USD ticker")
        else:
            print(f"  ⚠️  WARNING: Non-USD currency detected")

        rev = financials.get("revenue_ttm", 0)
        shares = financials.get("shares_outstanding", 0)
        print(f"  Revenue: ${rev/1e6:.1f}M")
        print(f"  Shares: {shares/1e6:.1f}M")

        if shares > 0:
            rev_per_share = rev / shares
            price = profile.get("current_price", 0)
            if price > 0:
                ps = price / rev_per_share if rev_per_share > 0 else 0
                print(f"  Revenue/share: ${rev_per_share:.2f}")
                print(f"  Price: ${price:.2f}")
                print(f"  P/S: {ps:.2f}x")

    except Exception as e:
        print(f"  ❌ ERROR: {e}")


def main():
    print("USD Ticker Regression Test")
    print("=" * 60)
    print("Testing that USD reporters show NO currency conversion\n")

    tickers = ["MTDR", "FI", "NGL", "DKL", "AMR"]

    for ticker in tickers:
        test_usd_ticker(ticker)

    print("\n" + "=" * 60)
    print("SUMMARY: All USD tickers should show NO conversion")
    print("=" * 60)


if __name__ == "__main__":
    main()
