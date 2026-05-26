"""
Decision Tree — implements Damodaran Figure 34.1 logic.

Routes each company to the appropriate valuation method(s) based on
firm characteristics. Deterministic; no ML.

Reference: knowledge/decision_tree_rules.md and knowledge/damodaran_principles.md

Verification vs Figure 34.8 (p.934) — three branches:

Branch 1 (Cash flows + leverage):
  - Financial firms → FCFE (can't define debt per Ch 21) ✅
  - Non-financials → FCFF by default (safe for unstable leverage) ✅
  - DDM not implemented (very rare for public companies) ⚠️ acceptable

Branch 2 (Earnings normalization):
  - Positive normal earnings → use current ✅
  - Cyclical temporary negative → normalize ✅
  - Persistent negative + distress + high debt → contingent claims ✅
  - Persistent negative + survivable → relative + dcf_normalized ✅
  - Not survivable + low debt → liquidation: not separately detected ⚠️
    (hard to determine "survivability" programmatically; distress heuristic
    covers the high-debt path; low-debt-but-dying is very rare for public cos)

Branch 3 (Growth model selection):
  - Code uses two-stage with linear decay universally
  - Linear decay approximates three-stage model ✅
  - Market-cap-aware growth caps naturally handle stable-growth companies ✅
  - Does not distinguish legal-barrier vs general-advantage growth sources ⚠️
    (would require qualitative judgment; acceptable simplification)
"""

FINANCIAL_SECTORS = {"Financial Services", "Banking", "Insurance"}
FINANCIAL_KEYWORDS = ["bank", "insurance", "financial", "brokerage", "asset management"]
BDC_KEYWORDS = ["business development company"]
ASSET_HEAVY_SECTORS = {"Real Estate", "Energy", "Basic Materials"}
ASSET_HEAVY_KEYWORDS = ["reit", "oil", "gas", "mining", "mineral", "gold", "silver", "coal"]
CYCLICAL_SECTORS = {"Basic Materials", "Industrials", "Consumer Cyclical", "Energy"}

# Distress thresholds (from dcf_formulas.md)
DISTRESS_ND_EBITDA = 4.0      # net debt / EBITDA
DISTRESS_INTEREST_COVER = 1.5  # EBIT / interest expense


def classify_company(profile: dict, financials: dict) -> dict:
    """
    Apply the Damodaran decision tree to determine which valuation method(s) to use.

    Args:
        profile: from company_profile.get_profile()
        financials: from financials.get_ttm_financials()

    Returns dict with:
        primary_method: str
        secondary_methods: list[str]
        earnings_status: str
        normalization_required: bool
        distress_risk: bool
        is_financial: bool
        is_asset_heavy: bool
        is_cyclical: bool
        rationale: str
        notes: list[str]
    """
    sector = profile.get("sector", "")
    industry = profile.get("industry", "").lower()
    market_cap = profile.get("market_cap", 0)

    revenue = financials.get("revenue_ttm", 0)
    ebit = financials.get("ebit_ttm", 0)
    ebitda = financials.get("ebitda_ttm", 0)
    net_income = financials.get("net_income_ttm", 0)
    net_debt = financials.get("net_debt", 0)
    interest = financials.get("interest_expense_ttm", 0)
    total_debt = financials.get("total_debt", 0)
    total_equity = financials.get("total_equity", 0)

    notes = []

    # ── Derived flags ────────────────────────────────────────────────────────
    is_financial = _is_financial(sector, industry)
    is_asset_heavy = _is_asset_heavy(sector, industry)
    is_cyclical = sector in CYCLICAL_SECTORS
    is_reit = "real estate" in sector.lower() or "reit" in industry
    is_bdc = any(k in profile.get("description", "").lower() for k in BDC_KEYWORDS)

    has_revenue = revenue > 1_000_000  # > $1M revenue threshold
    has_positive_ebit = ebit > 0
    has_positive_ni = net_income > 0

    # Earnings status label
    if not has_revenue:
        earnings_status = "pre_revenue"
    elif not has_positive_ebit:
        earnings_status = "negative"
    else:
        earnings_status = "positive"

    # Distress checks
    # Per Figure 34.8 (p.932): distress matters when the firm has a LOT of debt.
    # Negative interest coverage alone doesn't signal distress if debt is trivial
    # relative to firm size. Require meaningful debt for distress classification.
    nd_ebitda = net_debt / ebitda if ebitda > 0 else float("inf")
    interest_cover = ebit / interest if interest > 0 else float("inf")
    has_meaningful_debt = total_debt > 0 and (
        market_cap == 0 or total_debt / max(market_cap, 1) > 0.10
    )
    # Ch 21: Financial firms are structurally leveraged (debt is raw material).
    # ND/EBITDA and EBIT/Interest are meaningless for banks/BDCs — skip distress check.
    distress_risk = (not is_financial) and (not is_bdc) and has_meaningful_debt and (
        (nd_ebitda > DISTRESS_ND_EBITDA) or (interest_cover < DISTRESS_INTEREST_COVER)
    )

    # Override distress flag for companies in a temporary earnings trough:
    # - Revenue growing significantly (acquisition/expansion phase)
    # - Trading at reasonable forward PE (analysts expect profitability)
    # - Trading below book value (asset floor exists)
    # These are signs of an integration/transition period, not structural distress.
    if distress_risk:
        revenue_5yr = financials.get("revenue_5yr", [])
        revenue_growing = (
            len(revenue_5yr) >= 2
            and revenue_5yr[0] > revenue_5yr[-1] * 1.15  # >15% revenue growth over period
        )
        price_to_book = market_cap / total_equity if total_equity > 0 else float("inf")
        has_asset_floor = price_to_book < 1.0  # trading below book value
        forward_pe = financials.get("forward_pe", 0)
        analysts_expect_profit = 0 < forward_pe < 30

        if revenue_growing and (has_asset_floor or analysts_expect_profit):
            distress_risk = False
            notes.append(
                f"Distress override: revenue growing ({revenue_5yr[-1]/1e6:.0f}M → {revenue_5yr[0]/1e6:.0f}M), "
                + (f"P/B={price_to_book:.2f}x (<1.0), " if has_asset_floor else "")
                + (f"forward PE={forward_pe:.1f}x. " if analysts_expect_profit else "")
                + "Likely temporary earnings trough (acquisition/integration), not structural distress. "
                "Using normalized earnings approach instead of contingent claims."
            )

    if distress_risk:
        if nd_ebitda > DISTRESS_ND_EBITDA:
            notes.append(f"High leverage: Net Debt/EBITDA = {nd_ebitda:.1f}x (threshold: {DISTRESS_ND_EBITDA}x)")
        if interest_cover < DISTRESS_INTEREST_COVER:
            notes.append(f"Low interest coverage: EBIT/Interest = {interest_cover:.1f}x (threshold: {DISTRESS_INTEREST_COVER}x)")

    # Normalization required for cyclical companies with sufficient history
    normalization_required = (
        is_cyclical
        and has_positive_ebit
        and len(financials.get("ebit_5yr", [])) >= 3
    )
    if normalization_required:
        notes.append("Cyclical sector: earnings will be normalized over 5-year cycle.")

    # ── Decision tree (priority order) ────────────────────────────────────────

    # PRIORITY 1: Pre-revenue — no usable cash flow or earnings
    if not has_revenue:
        return _result(
            primary="contingent_claims",
            secondary=[],
            earnings_status="pre_revenue",
            normalization_required=False,
            distress_risk=distress_risk,
            is_financial=is_financial,
            is_asset_heavy=is_asset_heavy,
            is_cyclical=is_cyclical,
            rationale=(
                "Pre-revenue firm: no earnings or revenue to discount. "
                "Equity value is best modeled as a call option on future success. "
                "Standard DCF is not applicable."
            ),
            notes=notes,
        )

    # PRIORITY 2: BDC — Business Development Company (before generic financial)
    # BDCs are RICs: must distribute 90%+ of income. Structural leverage like banks.
    # DDM primary, P/NAV relative, reported NAV as asset-based cross-check.
    if is_bdc:
        notes.append(
            "BDC (RIC status): must distribute 90%+ of taxable income. "
            "Leverage is structural (debt funds lending portfolio). "
            "Using DDM (dividends as cash flow), P/NAV as primary relative multiple, "
            "reported NAV as asset-based cross-check."
        )
        return _result(
            primary="dcf_fcfe",
            secondary=["relative", "asset_based"],
            earnings_status=earnings_status,
            normalization_required=False,
            distress_risk=False,
            is_financial=False,
            is_asset_heavy=False,
            is_cyclical=False,
            is_bdc=True,
            rationale=(
                f"Business Development Company (sector: {sector}): regulated investment company "
                "that lends to/invests in private companies. 90%+ dividend distribution required "
                "(RIC status) — dividends approximate true cash flow to equity. "
                "DDM primary, P/NAV relative, reported NAV as cross-check."
            ),
            notes=notes,
        )

    # PRIORITY 3: Financial institution
    if is_financial:
        return _result(
            primary="dcf_fcfe",
            secondary=["relative"],
            earnings_status=earnings_status,
            normalization_required=False,
            distress_risk=distress_risk,
            is_financial=True,
            is_asset_heavy=False,
            is_cyclical=False,
            rationale=(
                f"Financial institution (sector: {sector}): debt is an operational input, not financing. "
                "WACC is not applicable. Using FCFE DCF discounted at cost of equity. "
                "Primary relative multiple: P/B (justified P/B = ROE / Ke)."
            ),
            notes=notes,
        )

    # PRIORITY 3: Asset-heavy — separable, marketable assets
    if is_asset_heavy and not is_reit:
        secondary = ["dcf_fcff"] if has_positive_ebit else ["relative"]
        return _result(
            primary="asset_based",
            secondary=secondary,
            earnings_status=earnings_status,
            normalization_required=normalization_required,
            distress_risk=distress_risk,
            is_financial=False,
            is_asset_heavy=True,
            is_cyclical=is_cyclical,
            rationale=(
                f"Asset-heavy sector ({sector}): firm value driven by owned assets "
                "(reserves, properties, commodities). Asset-based valuation is primary. "
                "DCF used as secondary if positive earnings exist."
            ),
            notes=notes,
        )

    # PRIORITY 4: REIT — Damodaran Ch 26, pp.764-768
    # REITs must distribute 95% of taxable income → DDM is the natural model
    # (same logic as banks: dividends ≈ true cash flow to equity)
    if is_reit:
        notes.append(
            "REIT (Ch 26): Depreciation is a legal fiction for real estate — "
            "property values typically appreciate. Using DDM (dividends as cash flow) "
            "since REITs must distribute 95%+ of taxable income. "
            "P/FFO is the primary relative multiple. NAV as asset-based cross-check."
        )
        return _result(
            primary="dcf_fcfe",
            secondary=["relative", "asset_based"],
            earnings_status=earnings_status,
            normalization_required=False,
            distress_risk=distress_risk,
            is_financial=False,
            is_asset_heavy=True,
            is_cyclical=False,
            is_reit=True,
            rationale=(
                "REIT (Damodaran Ch 26): net income is distorted by non-economic depreciation. "
                "REITs must distribute 95%+ of taxable income, making dividends the appropriate "
                "cash flow measure. DDM primary, P/FFO relative, NAV as cross-check."
            ),
            notes=notes,
        )

    # PRIORITY 5: Distress (non-financial, non-asset-heavy)
    if distress_risk:
        secondary = ["dcf_fcff"] if has_positive_ebit else []
        return _result(
            primary="contingent_claims",
            secondary=secondary,
            earnings_status=earnings_status,
            normalization_required=False,
            distress_risk=True,
            is_financial=False,
            is_asset_heavy=is_asset_heavy,
            is_cyclical=is_cyclical,
            rationale=(
                f"Financial distress detected (ND/EBITDA={nd_ebitda:.1f}x, "
                f"interest_cover={interest_cover:.1f}x). "
                "Standard DCF undervalues equity in distress because option value is positive "
                "even when out of the money. Merton model applied."
            ),
            notes=notes,
        )

    # PRIORITY 6: Negative earnings, positive revenue
    if not has_positive_ebit:
        # Figure 34.8 Branch 2: Is the firm likely to survive?
        # Heuristic: declining revenue + negative NI + low debt → likely not survivable
        # → route to asset_based (liquidation value) per book p.932
        revenue_5yr = financials.get("revenue_5yr", [])
        declining_revenue = (
            len(revenue_5yr) >= 3
            and revenue_5yr[0] < revenue_5yr[-1] * 0.85  # revenue down >15% over period
        )
        low_debt = total_debt == 0 or (ebitda != 0 and abs(net_debt / ebitda) < 1.0)

        if declining_revenue and not has_positive_ni and low_debt:
            notes.append(
                "Negative earnings + declining revenue + low debt: firm may not survive. "
                "Per Damodaran Figure 34.8 (p.932): estimate liquidation value."
            )
            return _result(
                primary="asset_based",
                secondary=["relative"],
                earnings_status="negative",
                normalization_required=False,
                distress_risk=False,
                is_financial=False,
                is_asset_heavy=False,
                is_cyclical=is_cyclical,
                rationale=(
                    "Negative EBIT with declining revenue and low debt: the firm may not survive "
                    "but lacks sufficient debt to trigger contingent claims. "
                    "Asset-based (liquidation) valuation is primary per Damodaran p.932."
                ),
                notes=notes,
            )

        return _result(
            primary="relative",
            secondary=["dcf_normalized"],
            earnings_status="negative",
            normalization_required=True,
            distress_risk=False,
            is_financial=False,
            is_asset_heavy=False,
            is_cyclical=is_cyclical,
            rationale=(
                "Negative EBIT: standard DCF using current cash flows would yield "
                "negative intrinsic value. Using relative valuation (EV/Sales) as primary. "
                "DCF run on normalized earnings (sector-average margin applied to revenue)."
            ),
            notes=notes,
        )

    # PRIORITY 7: Small-cap positive earnings
    if 0 < market_cap < 300_000_000:
        notes.append(f"Micro/small cap: size premium of 2.5% applied to cost of equity.")

    # PRIORITY 8: Default — profitable operating company
    return _result(
        primary="dcf_fcff",
        secondary=["relative"],
        earnings_status="positive",
        normalization_required=normalization_required,
        distress_risk=False,
        is_financial=False,
        is_asset_heavy=False,
        is_cyclical=is_cyclical,
        rationale=(
            f"Standard operating company with positive earnings (EBIT margin: "
            f"{ebit/revenue:.1%} if revenue > 0 else 'N/A'). "
            "Two-stage FCFF DCF is primary. Relative valuation (EV/EBITDA, P/E) "
            "used as cross-check."
        ),
        notes=notes,
    )


def check_red_flags(profile: dict, financials: dict, insider_signal: dict | None) -> list[str]:
    """
    Check all red flags from knowledge/red_flags.md.
    Returns a list of warning strings to include in the report.
    """
    flags = []

    revenue = financials.get("revenue_ttm", 0)
    ebit = financials.get("ebit_ttm", 0)
    ebitda = financials.get("ebitda_ttm", 0)
    net_income = financials.get("net_income_ttm", 0)
    dna = financials.get("d_and_a_ttm", 0)
    capex = financials.get("capex_ttm", 0)
    net_debt = financials.get("net_debt", 0)
    interest = financials.get("interest_expense_ttm", 0)
    revenue_5yr = financials.get("revenue_5yr", [])

    # Revenue >> cash flow (earnings quality)
    if revenue > 0 and ebitda > 0:
        # Rough check: if revenue growth is much higher than EBITDA growth historically
        if len(revenue_5yr) >= 3:
            rev_growth = (revenue_5yr[0] / revenue_5yr[-1]) ** (1 / len(revenue_5yr)) - 1 if revenue_5yr[-1] > 0 else 0
            if rev_growth > 0.20 and ebitda / revenue < 0.05:
                flags.append(
                    f"Revenue growing at {rev_growth:.0%}/yr but EBITDA margin is only {ebitda/revenue:.1%}. "
                    "Check for revenue recognition or working capital issues."
                )

    # Capex << D&A (underinvestment)
    if dna > 0 and capex > 0:
        capex_dna_ratio = capex / dna
        if capex_dna_ratio < 0.6:
            flags.append(
                f"Capex/D&A = {capex_dna_ratio:.2f} — company may be underinvesting. "
                "Future earnings power could be impaired."
            )

    # High leverage
    if ebitda > 0:
        nd_ebitda = net_debt / ebitda
        if nd_ebitda > 4:
            flags.append(f"Net Debt/EBITDA = {nd_ebitda:.1f}x — high leverage territory.")
        elif nd_ebitda > 2.5:
            flags.append(f"Net Debt/EBITDA = {nd_ebitda:.1f}x — moderate leverage; watch debt maturity schedule.")

    # Interest coverage
    if interest > 0 and ebit != 0:
        cover = ebit / interest
        if cover < 1.5:
            flags.append(f"Interest coverage = {cover:.1f}x — minimal cushion against earnings decline.")
        elif cover < 3.0:
            flags.append(f"Interest coverage = {cover:.1f}x — adequate but monitor.")

    # Negative FCF despite positive net income (earnings quality)
    from data.financials import compute_fcff
    fcff = compute_fcff(financials)
    if net_income > 0 and fcff < 0:
        flags.append(
            f"Positive net income (${net_income/1e6:.0f}M) but negative FCFF (${fcff/1e6:.0f}M). "
            "Accrual earnings are not converting to cash. Investigate capex and working capital."
        )

    # Insider signal cross-reference
    if insider_signal:
        if insider_signal.get("share_delta_4q", 0) > 5:
            flags.append(
                f"Share count has grown {insider_signal['share_delta_4q']:.1f}% in trailing 4Q — "
                "dilution partially offsets insider buying signal."
            )

    return flags


def _is_financial(sector: str, industry: str) -> bool:
    if sector in FINANCIAL_SECTORS:
        return True
    return any(k in sector.lower() for k in FINANCIAL_KEYWORDS) or \
           any(k in industry for k in FINANCIAL_KEYWORDS)


def _is_asset_heavy(sector: str, industry: str) -> bool:
    if sector in ASSET_HEAVY_SECTORS:
        return True
    return any(k in industry for k in ASSET_HEAVY_KEYWORDS)


def _result(
    primary, secondary, earnings_status, normalization_required,
    distress_risk, is_financial, is_asset_heavy, is_cyclical, rationale, notes,
    is_reit=False, is_bdc=False,
) -> dict:
    return {
        "primary_method": primary,
        "secondary_methods": secondary,
        "earnings_status": earnings_status,
        "normalization_required": normalization_required,
        "distress_risk": distress_risk,
        "is_financial": is_financial,
        "is_asset_heavy": is_asset_heavy,
        "is_cyclical": is_cyclical,
        "is_reit": is_reit,
        "is_bdc": is_bdc,
        "rationale": rationale,
        "notes": notes,
    }
