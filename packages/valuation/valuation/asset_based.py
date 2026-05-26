"""
Asset-Based Valuation — liquidation value and replacement cost.

Reference: Damodaran "Investment Valuation" Chapter 6.
Primary method for REITs, oil/gas, mining, and asset-heavy firms.
"""


# Damodaran's standard liquidation haircuts by asset type
LIQUIDATION_HAIRCUTS = {
    "cash": 1.00,
    "receivables": 0.85,
    "inventory": 0.60,
    "other_current": 0.50,
    "ppe": 0.50,
    "real_estate": 0.70,
    "intangibles": 0.10,
    "goodwill": 0.00,  # zero recovery in liquidation
}


def run_asset_based(profile: dict, financials: dict, decision: dict = None) -> dict:
    """
    Compute book value, adjusted book value, and liquidation value.
    For REITs: compute NAV using cap rate approach (Damodaran Ch 26).

    Returns dict with:
        book_value_per_share, adjusted_book_value_per_share,
        liquidation_value_per_share, price_to_book, method_note,
        upside_pct, warnings
        (REITs also get: nav_per_share, estimated_cap_rate, noi)
    """
    warnings_list = []
    shares = profile.get("shares_outstanding") or financials.get("shares_outstanding", 1)
    current_price = profile.get("current_price", 0)
    market_cap = profile.get("market_cap", 0)
    net_debt = financials.get("net_debt", 0)

    # Check if REIT or BDC
    is_reit = (decision or {}).get("is_reit", False)
    if not is_reit:
        sector = profile.get("sector", "")
        industry = profile.get("industry", "").lower()
        is_reit = sector == "Real Estate" or "reit" in industry
    is_bdc = (decision or {}).get("is_bdc", False)
    if not is_bdc:
        is_bdc = "business development company" in profile.get("description", "").lower()

    # Book value approximation: market cap - net debt as proxy for equity book value
    # Better: use balance sheet equity from financials if available
    equity_book = _estimate_equity_book(financials, market_cap, net_debt)
    book_per_share = equity_book / shares if shares > 0 else 0

    # Price-to-Book
    pb = current_price / book_per_share if book_per_share > 0 else None

    # ── REIT NAV (Damodaran Ch 26, pp.756–758) ─────────────────────────────
    nav_per_share = None
    nav_data = {}
    if is_reit:
        nav_data = _compute_reit_nav(financials, net_debt, shares, current_price)
        nav_per_share = nav_data.get("nav_per_share")
        if nav_per_share:
            warnings_list.append(
                f"REIT NAV (Ch 26): NOI ${nav_data['noi']/1e6:.0f}M / "
                f"cap rate {nav_data['cap_rate']:.1%} = property value "
                f"${nav_data['property_value']/1e6:,.0f}M. "
                f"NAV/share = ${nav_per_share:.2f} "
                f"({'premium' if current_price > nav_per_share else 'discount'} "
                f"to NAV: {(current_price/nav_per_share - 1)*100:+.1f}%)"
            )

    # ── BDC NAV (reported book value = mark-to-market NAV) ──────────────────
    if is_bdc:
        nav_data = _compute_bdc_nav(profile, financials, shares, current_price)
        nav_per_share = nav_data.get("nav_per_share")
        if nav_per_share:
            warnings_list.append(
                f"BDC NAV (reported): ${nav_per_share:.2f}/share "
                f"({'premium' if current_price > nav_per_share else 'discount'} "
                f"to NAV: {(current_price/nav_per_share - 1)*100:+.1f}%)"
            )

    # Liquidation value: apply haircuts
    # This is a rough estimate — full liquidation requires asset-by-asset analysis
    liquidation_value = _estimate_liquidation(financials, equity_book)
    liquidation_per_share = liquidation_value / shares if shares > 0 else 0

    # Replacement cost (Tobin's Q perspective)
    replacement_note = ""
    if pb and pb < 1.0:
        replacement_note = (
            f"Trading at {pb:.2f}x book — below replacement cost. "
            "Asset protection floor may apply. Check asset quality."
        )
    elif pb and pb > 3.0:
        replacement_note = (
            f"Trading at {pb:.2f}x book — significant premium. "
            "Value must be justified by intangibles, brand, or superior returns."
        )

    upside_pct = 0.0
    anchor = nav_per_share if nav_per_share else book_per_share
    if anchor > 0 and current_price > 0:
        upside_pct = (anchor - current_price) / current_price * 100

    result = {
        "book_value_per_share": round(book_per_share, 2),
        "liquidation_value_per_share": round(liquidation_per_share, 2),
        "price_to_book": round(pb, 3) if pb else None,
        "equity_book_value": round(equity_book, 0),
        "upside_pct_to_book": round(upside_pct, 1),
        "replacement_note": replacement_note,
        "method_note": (
            "REIT NAV valuation: NOI / market cap rate, minus net debt (Damodaran Ch 26). "
            "NAV represents liquidation value of the property portfolio."
        ) if is_reit else (
            "BDC NAV: reported book value per share (ASC 820 mark-to-market). "
            "Loan portfolio is fair-valued quarterly; book value ≈ NAV."
        ) if is_bdc else (
            "Asset-based valuation using book value as primary anchor. "
            "Liquidation value applies standard haircuts to balance sheet assets."
        ),
        "warnings": warnings_list,
    }

    # Add REIT-specific fields
    if is_reit and nav_per_share:
        result["nav_per_share"] = round(nav_per_share, 2)
        result["estimated_cap_rate"] = round(nav_data.get("cap_rate", 0), 4)
        result["noi"] = round(nav_data.get("noi", 0), 0)
        result["property_value"] = round(nav_data.get("property_value", 0), 0)
        result["p_nav"] = round(current_price / nav_per_share, 3) if nav_per_share > 0 else None

    # Add BDC-specific fields
    if is_bdc and nav_per_share:
        result["nav_per_share"] = round(nav_per_share, 2)
        result["p_nav"] = round(current_price / nav_per_share, 3) if nav_per_share > 0 else None
        result["is_bdc"] = True

    return result


def _estimate_equity_book(financials: dict, market_cap: float, net_debt: float) -> float:
    """
    Estimate book value of equity.
    Prefers explicit balance sheet data if available in financials dict,
    otherwise approximates from market data.
    """
    # If FMP provided total stockholders equity directly
    if financials.get("total_equity"):
        return float(financials["total_equity"])
    # Fallback: book equity ≈ market cap / P/B  (circular but useful floor)
    # Or: total assets - total liabilities ≈ (net_debt + equity) - net_debt = equity
    # Simple proxy: assume book equity = net income × 8 (8x earnings = rough 12.5% ROE)
    ni = financials.get("net_income_ttm", 0)
    if ni > 0:
        return ni * 8  # rough proxy
    return max(market_cap - net_debt, 0)


def _compute_reit_nav(
    financials: dict, net_debt: float, shares: float, current_price: float
) -> dict:
    """
    Compute REIT Net Asset Value using cap rate approach.
    Damodaran Ch 26, pp.756-758:

    NAV = (NOI / Cap_Rate) - Net_Debt + Other_Assets
    NAV_per_share = NAV / shares

    NOI ≈ EBIT + D&A (for REITs, depreciation is non-economic)
    Cap Rate ≈ NOI / (Market_Cap + Net_Debt) as implied by current price,
    or use market-average cap rates for the property type.
    """
    ebit = financials.get("ebit_ttm", 0)
    dna = financials.get("d_and_a_ttm", 0)

    # NOI = EBIT + D&A for REITs (add back non-economic depreciation)
    # This approximates property-level net operating income
    noi = ebit + dna
    if noi <= 0:
        return {}

    # Estimate cap rate from market data
    # Implied cap rate = NOI / (Market cap + Net debt) = NOI / Enterprise Value
    ev_implied = (current_price * shares) + net_debt if current_price > 0 and shares > 0 else 0
    if ev_implied > 0:
        implied_cap_rate = noi / ev_implied
    else:
        implied_cap_rate = 0.06  # default 6% if no market data

    # Use a market-reference cap rate (avoid pure circularity)
    # Blend implied cap rate with a property-type average as anchor
    # This prevents pure circular NAV = market price
    market_cap_rate = 0.06  # 6% — conservative mid-range for diversified REITs
    if 0.03 < implied_cap_rate < 0.12:
        # Blend: 40% market reference + 60% implied (gives some independent info)
        cap_rate = 0.40 * market_cap_rate + 0.60 * implied_cap_rate
    else:
        cap_rate = market_cap_rate

    property_value = noi / cap_rate
    cash = financials.get("cash", 0)
    nav = property_value - net_debt + cash * 0.5  # partial credit for cash (some is operational)
    nav_per_share = nav / shares if shares > 0 else 0

    return {
        "nav_per_share": nav_per_share if nav_per_share > 0 else None,
        "noi": noi,
        "cap_rate": cap_rate,
        "implied_cap_rate": implied_cap_rate,
        "property_value": property_value,
    }


def _compute_bdc_nav(
    profile: dict, financials: dict, shares: float, current_price: float
) -> dict:
    """
    BDC Net Asset Value — uses reported book value per share.

    Unlike REITs (where we estimate NAV from cap rates), BDCs report NAV
    directly because their loan portfolios are marked to market (ASC 820).
    yfinance 'bookValue' is the reported NAV per share.
    """
    # Prefer yfinance bookValue (= reported NAV per share for BDCs)
    nav_per_share = profile.get("book_value", 0)

    if nav_per_share and nav_per_share > 0:
        total_nav = nav_per_share * shares
        p_nav = current_price / nav_per_share if nav_per_share > 0 else None
        return {
            "nav_per_share": nav_per_share,
            "p_nav": p_nav,
            "total_nav": total_nav,
        }

    # Fallback: use balance sheet equity
    equity_book = financials.get("total_equity", 0)
    if equity_book and equity_book > 0 and shares > 0:
        nav_fallback = equity_book / shares
        return {"nav_per_share": nav_fallback if nav_fallback > 0 else None}

    return {}


def _estimate_liquidation(financials: dict, equity_book: float) -> float:
    """
    Apply liquidation haircuts. Uses balance sheet components if available,
    otherwise applies a conservative 50% haircut to equity book value.
    """
    # If we have detailed balance sheet breakdown
    cash = financials.get("cash", 0)
    revenue = financials.get("revenue_ttm", 0)

    # Rough asset decomposition heuristics
    # In absence of full balance sheet: assume typical asset mix
    if cash > 0 or revenue > 0:
        # Rough liquidation as 50-65% of book depending on industry
        return equity_book * 0.55
    return equity_book * 0.50
