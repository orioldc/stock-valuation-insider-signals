#!/usr/bin/env python3
"""
Verify the TSM bug is fixed by comparing implied valuation metrics.

Before fix: TSM showed revenue_ttm = 4,440B TWD (unconverted) vs price = $439 USD
This produced an implied revenue/share of $856 (4440B / 5.19B shares), completely wrong.

After fix: Should show ~$141B revenue (4400B TWD * 0.032) and reasonable per-share metrics.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "packages" / "valuation"))

from data.financials import get_ttm_financials
from data.company_profile import get_profile


def main():
    ticker = "TSM"
    print(f"Verifying TSM Currency Fix")
    print("=" * 80)

    profile = get_profile(ticker, use_cache=False)
    financials = get_ttm_financials(ticker, no_cache=True)

    if not profile or not financials:
        print("ERROR: Could not fetch data")
        return

    price = profile["current_price"]
    shares = financials["shares_outstanding"]
    revenue_ttm = financials["revenue_ttm"]
    ebit_ttm = financials["ebit_ttm"]

    print(f"\nProfile:")
    print(f"  Price: ${price:.2f}")
    print(f"  Currency: {profile.get('currency')}")
    print(f"  Financial Currency: {profile.get('financial_currency')}")

    print(f"\nFinancials:")
    print(f"  Revenue TTM: ${revenue_ttm/1e9:.2f}B")
    print(f"  EBIT TTM: ${ebit_ttm/1e9:.2f}B")
    print(f"  Shares Outstanding: {shares/1e6:.1f}M")

    if financials.get("currency_converted_to"):
        orig = financials["currency_original"]
        target = financials["currency_converted_to"]
        rate = financials["fx_rate_applied"]
        print(f"\n✅ Currency Conversion Applied:")
        print(f"  {orig} → {target} at rate {rate:.6f}")
        print(f"  Original revenue (TWD): ~${revenue_ttm / rate / 1e9:.0f}B TWD")

    # Calculate per-share metrics
    revenue_per_share = revenue_ttm / shares
    ebit_per_share = ebit_ttm / shares

    print(f"\nPer-Share Metrics:")
    print(f"  Revenue/share: ${revenue_per_share:.2f}")
    print(f"  EBIT/share: ${ebit_per_share:.2f}")

    # Original bug check
    print(f"\nBug Verification:")
    print(f"  Current price: ${price:.2f}")
    print(f"  Revenue/share: ${revenue_per_share:.2f}")

    # The original bug had revenue/share = $856 (because 4440B / 5.19B with no conversion)
    # After fix, should be ~$27 (141B / 5.19B)
    if revenue_per_share > 100:
        print(f"  ❌ BUG STILL PRESENT: Revenue/share ${revenue_per_share:.2f} is way too high")
        print(f"     This suggests currency conversion didn't work correctly")
    else:
        print(f"  ✅ BUG FIXED: Revenue/share ${revenue_per_share:.2f} is in reasonable range")

    # Implied P/S ratio
    ps_ratio = price / revenue_per_share if revenue_per_share > 0 else 0
    print(f"\nImplied P/S ratio: {ps_ratio:.2f}x")

    # For reference, TSM's actual P/S is around 8-10x, so anything in that ballpark is correct
    # Anything > 50x would indicate the bug is still present
    if ps_ratio > 50:
        print(f"  ❌ P/S ratio {ps_ratio:.2f}x is too high - currency bug likely present")
    elif ps_ratio > 20:
        print(f"  ⚠️  P/S ratio {ps_ratio:.2f}x is higher than typical but could be legitimate")
    else:
        print(f"  ✅ P/S ratio {ps_ratio:.2f}x is in reasonable range")

    print("\n" + "=" * 80)
    print("VERDICT:")
    if revenue_per_share < 100 and ps_ratio < 50:
        print("✅ The currency bug is FIXED. TSM now shows converted financials.")
    else:
        print("❌ The currency bug may still be present. Review the conversion logic.")


if __name__ == "__main__":
    main()
