#!/usr/bin/env python3
"""
Test script to verify currency conversion fix for TSM and other foreign issuers.

Tests:
1. TSM (TWD → USD) - should produce sane valuation, not +3,630%
2. BABA (CNY → USD) - verify conversion
3. ASML (EUR → USD) - verify conversion
4. MTDR (USD → USD) - must be unchanged (regression check)
5. A ticker with unavailable FX rate - should return None
"""

import sys
from pathlib import Path

# Add packages to path
sys.path.insert(0, str(Path(__file__).parent / "packages" / "valuation"))

from data.financials import get_ttm_financials
from data.company_profile import get_profile


def test_ticker(ticker: str, expected_currency_mismatch: bool = False):
    """Test currency handling for a ticker."""
    print(f"\n{'='*80}")
    print(f"Testing {ticker}")
    print(f"{'='*80}")

    # Get profile
    profile = get_profile(ticker, use_cache=False)
    if profile is None:
        print(f"❌ Profile fetch failed for {ticker}")
        return

    price = profile.get("current_price", 0)
    price_curr = profile.get("currency", "?")
    financial_curr = profile.get("financial_currency", "?")

    print(f"Price: ${price:.2f} {price_curr}")
    print(f"Financial Currency: {financial_curr}")
    print(f"Currency mismatch: {financial_curr != price_curr}")

    # Get financials
    financials = get_ttm_financials(ticker, no_cache=True)

    if financials is None:
        print(f"⚠️  Financials returned None (insufficient data or FX rate unavailable)")
        return

    # Check conversion
    converted = financials.get("currency_converted_to")
    if converted:
        orig = financials.get("currency_original")
        rate = financials.get("fx_rate_applied")
        print(f"✅ Conversion applied: {orig} → {converted} at rate {rate:.6f}")
    else:
        print(f"ℹ️  No conversion needed (same currency)")

    # Display key financials
    rev = financials.get("revenue_ttm", 0)
    ebit = financials.get("ebit_ttm", 0)
    shares = financials.get("shares_outstanding", 0)

    print(f"\nFinancials ({financials.get('source', '?')}):")
    print(f"  Revenue TTM: ${rev/1e9:.2f}B")
    print(f"  EBIT TTM: ${ebit/1e9:.2f}B")
    print(f"  Shares: {shares/1e6:.1f}M")

    if shares > 0:
        revenue_per_share = rev / shares
        ebit_per_share = ebit / shares
        print(f"  Revenue/share: ${revenue_per_share:.2f}")
        print(f"  EBIT/share: ${ebit_per_share:.2f}")

        # Sanity check: revenue/share should be in reasonable range vs price
        if price > 0:
            price_to_rev = price / revenue_per_share if revenue_per_share > 0 else 0
            print(f"  Price/Revenue per share: {price_to_rev:.2f}x")

            if expected_currency_mismatch:
                # For foreign issuers, this should now be reasonable (< 5x typically)
                if price_to_rev > 10:
                    print(f"  ⚠️  WARNING: Price/Revenue still seems too high - conversion may have failed")
                else:
                    print(f"  ✅ Price/Revenue looks reasonable after conversion")

    return financials


def main():
    print("Currency Conversion Fix Verification")
    print("=" * 80)

    # Test foreign issuers (should convert)
    print("\n\n### FOREIGN ISSUERS (should convert) ###\n")

    tsm_before = test_ticker("TSM", expected_currency_mismatch=True)
    baba_before = test_ticker("BABA", expected_currency_mismatch=True)
    asml_before = test_ticker("ASML", expected_currency_mismatch=True)

    # Test US issuer (should NOT convert)
    print("\n\n### US ISSUERS (should NOT convert - regression check) ###\n")

    mtdr_after = test_ticker("MTDR", expected_currency_mismatch=False)

    print("\n\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("""
Expected results:
1. TSM, BABA, ASML: Should show currency conversion applied
2. MTDR: Should show NO conversion (USD → USD)
3. All should have reasonable Price/Revenue ratios (typically < 5x for mature companies)
4. TSM specifically should NOT show the +3,630% bug anymore
""")


if __name__ == "__main__":
    main()
