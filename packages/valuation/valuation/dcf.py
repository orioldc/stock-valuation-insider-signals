"""
DCF Valuation Model — Damodaran FCFF / FCFE approach.

Reference: Damodaran "Investment Valuation" Chapters 12-16.
All formulas are from dcf_formulas.md in the knowledge directory.
"""

import math
from scipy.stats import norm


def run_dcf(
    profile: dict,
    financials: dict,
    sector_data: dict,
    method: str = "fcff",
    normalization_required: bool = False,
) -> dict:
    """
    Run a two-stage DCF valuation.

    Args:
        profile: from company_profile.get_profile()
        financials: from financials.get_ttm_financials()
        sector_data: from damodaran_data.get_sector_data()
        method: 'fcff' (default) or 'fcfe' (financial firms)
        normalization_required: normalize earnings over cycle first

    Returns dict with:
        intrinsic_value_per_share, wacc_used, ke, kd,
        stage1_growth, stage2_growth, terminal_growth,
        pv_fcf_stage1, pv_terminal_value, total_ev, equity_value,
        terminal_value_pct, margin_of_safety, upside_pct,
        sensitivity_table, projections, assumptions, warnings
    """
    warnings_list = []

    if normalization_required:
        from data.financials import normalize_earnings
        financials = normalize_earnings(financials, sector_data=sector_data)
        if financials.get("sector_margin_normalized"):
            op_margin = sector_data.get("operating_margin_sector", 0)
            warnings_list.append(
                f"EBIT persistently negative — normalized using sector operating margin "
                f"({op_margin:.1%}) applied to revenue (Damodaran Ch 34)."
            )
        else:
            warnings_list.append("Earnings normalized over 5-year cycle (cyclical company).")

    # ── 1. WACC / Cost of equity ──────────────────────────────────────────────
    wacc_result = _estimate_wacc(profile, financials, sector_data)
    wacc = wacc_result["wacc"]
    ke = wacc_result["ke"]

    # ── 2. Growth rates ───────────────────────────────────────────────────────
    growth = _estimate_growth_rates(profile, financials, sector_data, ke, method=method)
    g1 = growth["stage1"]
    g2 = growth["stage2"]
    gt = growth["terminal"]

    # ── 3. Base free cash flow ────────────────────────────────────────────────
    is_financial = profile.get("sector", "") in ("Financial Services",) or \
        any(k in profile.get("sector", "").lower() for k in ["bank", "insurance", "financial"])
    is_reit = profile.get("sector", "") == "Real Estate" or \
        "reit" in profile.get("industry", "").lower()
    is_bdc = "business development company" in profile.get("description", "").lower()

    if method == "fcff":
        from data.financials import compute_fcff
        base_fcf = compute_fcff(financials)
        discount_rate = wacc
    elif is_reit:
        # Damodaran Ch 26, pp.764-768: REITs must distribute 95% of taxable income.
        # Dividends are NOT discretionary — they ARE the cash flow to equity.
        # Use DDM approach identical to banks (Ch 21).
        # FFO (not net income) is the right earnings basis since depreciation
        # is non-economic for real estate.
        from data.financials import compute_ffo
        ffo_data = compute_ffo(financials)
        ffo = ffo_data["ffo"]

        # Get actual dividends paid (augmented with buybacks if any)
        aug_div = _get_augmented_dividends(profile.get("ticker", ""), ffo)
        if aug_div and aug_div["total"] > 0:
            base_fcf = aug_div["total"]
            payout_of_ffo = aug_div["total"] / ffo if ffo > 0 else 0.95
            warnings_list.append(
                f"REIT DDM (Ch 26): FFO ${ffo/1e9:.2f}B. "
                f"Dividends ${aug_div['dividends']/1e9:.2f}B + "
                f"Buybacks ${aug_div['buybacks']/1e9:.2f}B = "
                f"${base_fcf/1e9:.2f}B ({payout_of_ffo:.0%} of FFO)"
            )
        else:
            # Fallback: REITs pay ~80% of FFO as dividends (Ch 26 norm)
            payout = 0.80
            base_fcf = ffo * payout
            warnings_list.append(
                f"REIT DDM (Ch 26): FFO ${ffo/1e6:.0f}M × {payout:.0%} payout = "
                f"${base_fcf/1e6:.0f}M estimated dividends"
            )
        discount_rate = ke
    elif is_bdc:
        # BDC (RIC status): must distribute 90%+ of taxable income.
        # Like banks, use augmented dividends (dividends + buybacks).
        # Unlike REITs, use net income (not FFO) as the earnings basis.
        ni = financials.get("net_income_ttm", 0)
        augmented_div = _get_augmented_dividends(profile.get("ticker", ""), ni)
        if augmented_div and augmented_div["total"] > 0:
            base_fcf = augmented_div["total"]
            aug_payout = augmented_div["total"] / ni if ni > 0 else 0.90
            warnings_list.append(
                f"BDC DDM (RIC, 90%+ payout): "
                f"Dividends ${augmented_div['dividends']/1e9:.2f}B + "
                f"Buybacks ${augmented_div['buybacks']/1e9:.2f}B = "
                f"${base_fcf/1e9:.2f}B ({aug_payout:.0%} of NI)"
            )
        else:
            payout = 0.90  # RIC distribution requirement
            base_fcf = ni * payout
            warnings_list.append(
                f"BDC DDM (RIC): NI ${ni/1e6:.0f}M × {payout:.0%} payout = "
                f"${base_fcf/1e6:.0f}M estimated dividends"
            )
        discount_rate = ke
    elif is_financial:
        # Damodaran Ch 21, p.584: For financial firms, use DIVIDENDS as cash flow.
        # For banks with significant buybacks, use AUGMENTED dividends
        # (dividends + buybacks) to capture total cash returned to equity.
        ni = financials.get("net_income_ttm", 0)

        # Try to get augmented dividends (dividends + buybacks) from yfinance
        augmented_div = _get_augmented_dividends(profile.get("ticker", ""), ni)
        if augmented_div and augmented_div["total"] > 0:
            base_fcf = augmented_div["total"]
            aug_payout = augmented_div["total"] / ni if ni > 0 else 0.50
            warnings_list.append(
                f"Financial firm: Augmented DDM (Ch 21). "
                f"Dividends ${augmented_div['dividends']/1e9:.1f}B + "
                f"Buybacks ${augmented_div['buybacks']/1e9:.1f}B = "
                f"${base_fcf/1e9:.1f}B ({aug_payout:.0%} of NI)"
            )
        else:
            # Fallback: simple dividend payout
            payout = financials.get("payout_ratio", 0) or profile.get("payout_ratio", 0)
            if payout <= 0 or payout > 1.5:
                roe = _safe_mean(financials.get("roe_5yr", [])) or 0.10
                payout = max(0.10, 1 - g1 / roe) if roe > 0 else 0.50
            base_fcf = ni * payout
            warnings_list.append(
                f"Financial firm: DDM (Ch 21). "
                f"Base dividends = NI × payout = ${ni/1e6:.0f}M × {payout:.0%} = ${base_fcf/1e6:.0f}M"
            )
        discount_rate = ke
    else:
        from data.financials import compute_fcfe
        base_fcf = compute_fcfe(financials)
        discount_rate = ke

    if base_fcf <= 0:
        warnings_list.append(
            f"Base {'FCFF' if method == 'fcff' else 'FCFE/Dividends'} is negative (${base_fcf:,.0f}). "
            "DCF reliability is low — treat result as directional only."
        )

    # ── 4. Project cash flows ─────────────────────────────────────────────────
    projections = []
    pv_sum = 0.0
    fcf = base_fcf

    # Stage 1: years 1-5
    for yr in range(1, 6):
        fcf = fcf * (1 + g1)
        pv_factor = 1 / (1 + discount_rate) ** yr
        pv = fcf * pv_factor
        projections.append({"year": yr, "stage": 1, "fcf": fcf, "pv_factor": pv_factor, "pv": pv})
        pv_sum += pv

    pv_stage1 = pv_sum

    # Stage 2: years 6-10 (linear decay from g1 to gt)
    for yr in range(6, 11):
        # Interpolate: year 6 = g1 decaying toward gt by year 10
        frac = (yr - 5) / 5  # 0.2 to 1.0
        g_yr = g1 + frac * (gt - g1)
        fcf = fcf * (1 + g_yr)
        pv_factor = 1 / (1 + discount_rate) ** yr
        pv = fcf * pv_factor
        projections.append({"year": yr, "stage": 2, "fcf": fcf, "pv_factor": pv_factor, "pv": pv})
        pv_sum += pv

    pv_stage2 = pv_sum - pv_stage1

    # ── 5. Terminal value ─────────────────────────────────────────────────────
    # Damodaran Ch 12, p.314: TV = NOPAT_{n+1} × (1 - g/ROC) / (WACC - g)
    # This links terminal reinvestment to growth — when ROC = WACC, growth
    # has no effect on value. Falls back to simple Gordon Growth if ROC unavailable.
    if method == "fcff":
        # Try to use the explicit reinvestment formula for FCFF
        roc_stable = _get_stable_roc(financials, sector_data, discount_rate)
        if roc_stable and roc_stable > 0 and abs(gt) < roc_stable:
            # Terminal reinvestment rate = g / ROC (Damodaran p.312-313)
            terminal_reinvestment = gt / roc_stable
            # Damodaran Ch 10, p.250: terminal value MUST use MARGINAL tax rate
            # (effective rate converges to marginal over projection period)
            t_marginal = financials.get("marginal_tax_rate", 0.21)
            nopat_y10 = financials.get("ebit_ttm", 0) * (1 - t_marginal)
            # Grow NOPAT through stages 1 and 2 the same way we grew FCF
            nopat_n1 = nopat_y10
            for yr in range(1, 6):
                nopat_n1 *= (1 + g1)
            for yr in range(6, 11):
                frac_yr = (yr - 5) / 5
                g_yr_tv = g1 + frac_yr * (gt - g1)
                nopat_n1 *= (1 + g_yr_tv)
            nopat_n1 *= (1 + gt)  # year 11
            fcf_n1 = nopat_n1 * (1 - terminal_reinvestment)
        else:
            # Fallback: simple Gordon Growth on projected FCF
            fcf_n1 = fcf * (1 + gt)
    else:
        # FCFE: simple Gordon Growth on projected FCF
        fcf_n1 = fcf * (1 + gt)

    if discount_rate <= gt:
        warnings_list.append("Terminal growth >= discount rate — using discount_rate + 1% as floor.")
        terminal_denom = max(discount_rate - gt, 0.01)
    else:
        terminal_denom = discount_rate - gt

    terminal_value = fcf_n1 / terminal_denom
    pv_terminal = terminal_value / (1 + discount_rate) ** 10
    pv_sum += pv_terminal

    total_ev = pv_sum
    terminal_pct = pv_terminal / total_ev * 100 if total_ev > 0 else 0

    if terminal_pct > 80:
        warnings_list.append(
            f"WARNING: {terminal_pct:.0f}% of firm value is in the terminal value. "
            "This DCF is highly sensitive to long-run growth and discount rate assumptions. "
            "Treat intrinsic value as a range, not a point estimate."
        )

    # ── 6. Bridge to equity value ─────────────────────────────────────────────
    if method == "fcff":
        net_debt = financials.get("net_debt", 0)
        equity_value = total_ev - net_debt
    else:
        equity_value = total_ev  # FCFE already equity-level

    shares = profile.get("shares_outstanding") or financials.get("shares_outstanding") or 1
    if shares <= 0:
        shares = 1
        warnings_list.append("Shares outstanding not found — using 1 share (equity value not per-share).")

    intrinsic_per_share = equity_value / shares
    current_price = profile.get("current_price", 0)

    margin_of_safety = 0.0
    upside_pct = 0.0
    if intrinsic_per_share > 0 and current_price > 0:
        margin_of_safety = (intrinsic_per_share - current_price) / intrinsic_per_share
        upside_pct = (intrinsic_per_share - current_price) / current_price * 100

    # ── 7. Sensitivity table ──────────────────────────────────────────────────
    sensitivity = _build_sensitivity_table(
        financials, profile, discount_rate, gt, g1, method, base_fcf, shares
    )

    return {
        "intrinsic_value_per_share": round(intrinsic_per_share, 2),
        "current_price": round(current_price, 2),
        "upside_pct": round(upside_pct, 1),
        "margin_of_safety": round(margin_of_safety, 3),
        "wacc_used": round(wacc, 4),
        "ke": round(ke, 4),
        "kd": round(wacc_result["kd"], 4),
        "beta_relevered": round(wacc_result["beta_relevered"], 3),
        "stage1_growth": round(g1, 4),
        "stage2_growth": round((g1 + gt) / 2, 4),
        "terminal_growth": round(gt, 4),
        "base_fcf": round(base_fcf, 0),
        "pv_fcf_stage1": round(pv_stage1, 0),
        "pv_fcf_stage2": round(pv_stage2, 0),
        "pv_terminal_value": round(pv_terminal, 0),
        "total_ev": round(total_ev, 0),
        "net_debt": round(financials.get("net_debt", 0), 0),
        "equity_value": round(equity_value, 0),
        "shares": int(shares),
        "terminal_value_pct": round(terminal_pct, 1),
        "projections": projections,
        "sensitivity_table": sensitivity,
        "wacc_components": wacc_result,
        "growth_rationale": growth["rationale"],
        "warnings": warnings_list,
        "assumptions": {
            "method": method,
            "rf": round(sector_data.get("rf", 0.043), 4),
            "erp": round(sector_data.get("erp", 0.055), 4),
            "beta_unlevered": round(wacc_result.get("beta_unlevered", 1.0), 3),
            "size_premium": round(wacc_result.get("size_premium", 0), 4),
            "tax_rate_effective": round(financials.get("tax_rate_effective", 0.21), 3),
            "tax_rate_marginal": round(financials.get("marginal_tax_rate", 0.21), 3),
            "normalization_applied": normalization_required,
            "rd_capitalized": financials.get("rd_expense_ttm", 0) > 0,
        },
    }


def _estimate_wacc(profile: dict, financials: dict, sector_data: dict) -> dict:
    """
    WACC = (E/V)*Ke + (D/V)*Kd*(1-t)
    Ke = Rf + beta_relevered * ERP [+ size_premium if market_cap < $2B]
    beta_relevered = beta_unlevered * (1 + (1-t) * (D/E))

    Exception: Financial firms (Ch 21, p.586) — use market levered beta directly.
    Banks are homogeneously leveraged, so unlevering/relevering is meaningless.
    """
    rf = sector_data.get("rf", 0.043)
    erp = sector_data.get("erp", 0.055)
    t = financials.get("tax_rate_effective", 0.21)
    market_cap = profile.get("market_cap", 0)
    total_debt = financials.get("total_debt", 0)

    # Damodaran Ch 21, p.586: For financial firms, use the market (levered)
    # beta directly. Don't unlever/relever — banks are ALL highly leveraged
    # by nature (D/E of 5-10x is normal), so the relevering formula produces
    # absurd betas. The market beta already reflects bank-level leverage.
    # Ch 26, p.744: REITs similarly — use REIT-derived (market) betas as
    # reasonable proxies. REITs have structural leverage like banks.
    is_financial = profile.get("sector", "") in ("Financial Services",) or \
        any(k in profile.get("sector", "").lower() for k in ["bank", "insurance", "financial"])
    is_reit = profile.get("sector", "") == "Real Estate" or \
        "reit" in profile.get("industry", "").lower()
    is_bdc = "business development company" in profile.get("description", "").lower()

    cash = financials.get("cash", 0)

    if is_financial or is_reit or is_bdc:
        beta_r = profile.get("beta", 1.0) or 1.0
        beta_u = beta_r  # store as-is (no unlevering for banks)
        d_e_ratio = total_debt / market_cap if market_cap > 0 else 0.0
    else:
        # Non-financial: Damodaran sector unlevered beta (corrected for cash),
        # relevered at company NET D/E (Ch 8, p.200).
        # Using cash-corrected beta + net D/E is internally consistent:
        # the beta already strips out cash drag, so D/E must also net out cash.
        beta_u = sector_data.get("beta_unlevered") or profile.get("beta") or 1.0
        net_debt = max(0, total_debt - cash)
        d_e_ratio = net_debt / market_cap if market_cap > 0 else 0.0
        beta_r = beta_u * (1 + (1 - t) * d_e_ratio)

    # Note: Damodaran Ch 12, p.311 says stable period betas should be 0.8-1.2.
    # Our model uses a single discount rate; ideally terminal value would use
    # a lower beta for mature firms. For now, cap extreme betas as a sanity check.
    beta_r = max(0.5, min(beta_r, 3.0))

    # Small-cap premium for companies < $2B
    size_premium = 0.025 if 0 < market_cap < 2e9 else 0.0

    # Damodaran Ch 7, p.172: Country risk premium for non-US companies
    # Ke = Rf + β × ERP + size_premium + λ × CRP
    # λ = 1.0 (full exposure) for companies domiciled in that country
    country = profile.get("country", "")
    crp = 0.0
    crp_data = {}
    if country and country not in ("United States", ""):
        try:
            from data.damodaran_data import get_country_risk_premium
            crp_data = get_country_risk_premium(country)
            crp = crp_data.get("country_risk_premium", 0.0)
        except Exception:
            pass

    ke = rf + beta_r * erp + size_premium + crp

    # Cost of debt — Damodaran Ch 15, p.407: synthetic rating approach
    # Step 1: Try actual interest rate from financials
    # Step 2: Fall back to synthetic rating (interest coverage → spread)
    interest_expense = financials.get("interest_expense_ttm", 0)
    if interest_expense is None or (isinstance(interest_expense, float) and math.isnan(interest_expense)):
        interest_expense = 0
    ebit = financials.get("ebit_ttm", 0)

    if total_debt > 0 and interest_expense > 0:
        kd_actual = interest_expense / total_debt
        kd_actual = min(kd_actual, 0.25)  # cap at 25% (data quality)

        # Also compute synthetic rating for cross-check and reporting
        coverage_ratio = ebit / interest_expense if interest_expense > 0 else 0
        rating, spread = _synthetic_rating(coverage_ratio, market_cap)
        kd_synthetic = rf + spread

        # Use actual Kd if reasonable (within 2x of synthetic), otherwise synthetic
        # Data quirks (e.g., total_debt not capturing all obligations) can inflate actual Kd
        if kd_actual > kd_synthetic * 2.5:
            kd_pretax = kd_synthetic  # actual looks inflated by data issue
        else:
            kd_pretax = kd_actual
    else:
        # No interest expense data — estimate coverage from available info
        if total_debt > 0 and ebit > 0:
            # Estimate interest: total_debt × (rf + 1.5%) as rough proxy
            est_interest = total_debt * (rf + 0.015)
            coverage_ratio = ebit / est_interest if est_interest > 0 else 999
        elif total_debt > 0:
            coverage_ratio = 0  # debt but no EBIT → worst case
        else:
            coverage_ratio = 999  # no debt → AAA equivalent
        rating, spread = _synthetic_rating(coverage_ratio, market_cap)
        kd_pretax = rf + spread

    # Damodaran Ch 8, p.216: For non-US firms, add country default spread to Kd.
    # Kd = Rf + company_spread + country_default_spread
    country_default_spread = 0.0
    if crp > 0 and crp_data.get("rating"):
        # Use the sovereign default spread as a proxy for local corporate debt premium.
        # CRP = default_spread × (σ_equity/σ_bond), so default_spread ≈ CRP / 1.5 roughly.
        # But more precisely, we can back it out from the rating's typical spread.
        from data.damodaran_data import get_country_risk_premium as _get_crp
        # Country default spread ≈ CRP × 0.6 (Damodaran's σ_bond/σ_equity ratio is ~1.5,
        # so default_spread ≈ CRP / 1.5 ≈ CRP × 0.67; we use 0.6 to be conservative)
        country_default_spread = crp * 0.6
        kd_pretax += country_default_spread

    # Damodaran Ch 15, p.410: Tax rate adjustment at high leverage
    # When Interest > EBIT, firm cannot fully deduct interest
    if interest_expense > 0 and ebit > 0 and interest_expense > ebit:
        t_adj = min(t, (ebit / interest_expense) * t)
    else:
        t_adj = t

    kd_at = kd_pretax * (1 - t_adj)  # after-tax with adjusted rate

    # Weights at market value
    total_capital = market_cap + total_debt
    w_e = market_cap / total_capital if total_capital > 0 else 1.0
    w_d = total_debt / total_capital if total_capital > 0 else 0.0

    wacc = w_e * ke + w_d * kd_at

    # Sanity check: Damodaran sector WACC as anchor
    sector_wacc = sector_data.get("wacc")
    if sector_wacc and abs(wacc - sector_wacc) > 0.05:
        print(f"[dcf] Computed WACC {wacc:.1%} diverges from sector avg {sector_wacc:.1%} by >{5:.0f}pp")

    return {
        "wacc": wacc,
        "ke": ke,
        "kd": kd_at,
        "kd_pretax": kd_pretax,
        "beta_unlevered": beta_u,
        "beta_relevered": beta_r,
        "size_premium": size_premium,
        "w_equity": w_e,
        "w_debt": w_d,
        "d_e_ratio": d_e_ratio,
        "country_risk_premium": round(crp, 4) if crp > 0 else None,
        "country_default_spread_on_kd": round(country_default_spread, 4) if country_default_spread > 0 else None,
        "sovereign_rating": crp_data.get("rating") if crp > 0 else None,
        "synthetic_rating": rating,
        "interest_coverage": round(coverage_ratio, 2) if coverage_ratio < 900 else None,
        "tax_rate_adjusted": round(t_adj, 4) if t_adj != t else None,
        "sector_wacc_reference": sector_wacc,
    }


def _estimate_growth_rates(
    profile: dict, financials: dict, sector_data: dict, ke: float,
    method: str = "fcff",
) -> dict:
    """
    Stage 1 (yrs 1-5): median of fundamental, historical, and analyst growth.
    Terminal: min(stage1/2, risk-free rate), capped at 2.5%.

    Per Damodaran Ch 11 (pp.280-300):
      - FCFF models: g_EBIT = Reinvestment rate × ROIC
      - FCFE models: g_NI = Equity reinvestment rate × ROE (or simple: retention × ROE)
    """
    import numpy as np

    growth_inputs = []
    rationale_parts = []

    # Detect financial firms (Ch 21: growth = retention × ROE, not reinvestment × ROIC)
    is_financial = profile.get("sector", "") in ("Financial Services",) or \
        any(k in profile.get("sector", "").lower() for k in ["bank", "insurance", "financial"])
    # Detect REITs (Ch 26: growth = (1 - FFO_payout) × ROE, like banks)
    is_reit = profile.get("sector", "") == "Real Estate" or \
        "reit" in profile.get("industry", "").lower()
    # Detect BDCs (RIC: 90%+ payout, growth = retention × ROE like banks)
    is_bdc = "business development company" in profile.get("description", "").lower()

    # 1. Fundamental growth — method-appropriate formula (Ch 11, p.280-282)
    if is_bdc:
        # BDCs: g = ROE × augmented retention (like banks, Ch 21 analog)
        # BDCs must distribute 90%+, so retention is very low (5-15%).
        # Growth is typically 2-8% from portfolio expansion.
        roe_avg = _safe_mean(financials.get("roe_5yr", []))
        ni = financials.get("net_income_ttm", 0)
        aug_div = _get_augmented_dividends(profile.get("ticker", ""), ni)
        if aug_div and ni > 0 and roe_avg > 0:
            aug_payout = aug_div["total"] / ni
            aug_retention = max(0.05, 1 - aug_payout)  # floor at 5%
            g_fund_val = roe_avg * aug_retention
            growth_inputs.append(g_fund_val)
            rationale_parts.append(
                f"fundamental BDC DDM ({g_fund_val:.1%} = ROE {roe_avg:.1%} × "
                f"augmented retention {aug_retention:.0%}, Ch 21 analog)"
            )
        elif roe_avg > 0:
            ret_avg = _safe_mean(financials.get("retention_5yr", []))
            if 0 < ret_avg < 1:
                g_fund_val = roe_avg * ret_avg
                growth_inputs.append(g_fund_val)
                rationale_parts.append(
                    f"fundamental BDC DDM ({g_fund_val:.1%} = ROE {roe_avg:.1%} × retention {ret_avg:.1%})"
                )
    elif is_reit:
        # Damodaran Ch 26: REIT growth is inherently low (2-6% typical)
        # because REITs must distribute 95%+ of taxable income.
        # WARNING: ROE × retention is NOT reliable for REITs because book
        # equity is distorted by non-economic depreciation (accumulated
        # depreciation depletes BV, inflating ROE to 50-100%+).
        # Instead, use NOI/revenue growth as the fundamental growth rate.
        #
        # For REITs, same-store NOI growth (2-5%) is the true organic driver.
        # We approximate with revenue CAGR, capped at 8% (max for a REIT).
        pass  # skip fundamental growth; rely on historical + analyst
    elif is_financial:
        # Damodaran Ch 21, p.588: g = Retention ratio × ROE
        # Standard reinvestment-based growth is meaningless for banks.
        # Use AUGMENTED retention (1 - total_payout) when buyback data is available,
        # so growth is consistent with augmented dividends in cash flow estimation.
        roe_avg = _safe_mean(financials.get("roe_5yr", []))
        ni = financials.get("net_income_ttm", 0)
        aug_div = _get_augmented_dividends(profile.get("ticker", ""), ni)
        if aug_div and ni > 0 and roe_avg > 0:
            aug_payout = aug_div["total"] / ni
            aug_retention = max(0.05, 1 - aug_payout)  # floor at 5%
            g_fund_val = roe_avg * aug_retention
            growth_inputs.append(g_fund_val)
            rationale_parts.append(
                f"fundamental bank DDM ({g_fund_val:.1%} = ROE {roe_avg:.1%} × "
                f"augmented retention {aug_retention:.0%}, Ch 21 p.588)"
            )
        elif roe_avg > 0:
            ret_avg = _safe_mean(financials.get("retention_5yr", []))
            if 0 < ret_avg < 1:
                g_fund_val = roe_avg * ret_avg
                growth_inputs.append(g_fund_val)
                rationale_parts.append(
                    f"fundamental bank DDM ({g_fund_val:.1%} = ROE {roe_avg:.1%} × retention {ret_avg:.1%}, Ch 21 p.588)"
                )
            else:
                g_fund_val = roe_avg * 0.40
                growth_inputs.append(g_fund_val)
                rationale_parts.append(
                    f"fundamental bank DDM ({g_fund_val:.1%} = ROE {roe_avg:.1%} × assumed 40% retention)"
                )
    elif method == "fcff":
        g_fund = _fundamental_growth_fcff(financials, sector_data)
        if g_fund is not None:
            growth_inputs.append(g_fund["growth"])
            rationale_parts.append(g_fund["rationale"])
    else:
        g_fund = _fundamental_growth_fcfe(financials)
        if g_fund is not None:
            growth_inputs.append(g_fund["growth"])
            rationale_parts.append(g_fund["rationale"])

    # 2. Historical revenue CAGR (5yr) — filter NaN/None values
    rev_raw = financials.get("revenue_5yr", [])
    rev = [v for v in rev_raw if v is not None and not math.isnan(v) and v > 0]
    if len(rev) >= 2:
        g_hist = (rev[0] / rev[-1]) ** (1 / (len(rev) - 1)) - 1
        growth_inputs.append(g_hist)
        rationale_parts.append(f"historical revenue CAGR ({g_hist:.1%})")

    # 3. Analyst/recent growth from yfinance (if available)
    # Per Damodaran Ch 11, p.284: analyst EPS growth ≠ operating income growth.
    # For FCFF models, prefer revenue growth over earnings growth as it's closer
    # to operating income growth and avoids leverage/buyback distortions.
    try:
        import yfinance as yf
        yf_ticker = yf.Ticker(profile["ticker"])
        if method == "fcfe":
            # FCFE: earnings growth is appropriate (equity perspective)
            growth_est = yf_ticker.info.get("earningsGrowth") or yf_ticker.info.get("revenueGrowth")
        else:
            # FCFF: prefer revenue growth (closer to operating income growth)
            growth_est = yf_ticker.info.get("revenueGrowth") or yf_ticker.info.get("earningsGrowth")
        if growth_est and -0.5 < growth_est < 2.0:
            growth_inputs.append(float(growth_est))
            rationale_parts.append(f"analyst/recent growth ({float(growth_est):.1%})")
    except Exception:
        pass

    if growth_inputs:
        g1 = float(np.median(growth_inputs))
    else:
        # Fallback: 5% nominal growth (conservative)
        g1 = 0.05
        rationale_parts.append("default 5% (no data available)")

    # Cap stage 1 based on market cap (larger = harder to sustain high growth)
    # Damodaran Ch 11, p.285: "rate of growth in revenues will decrease as revenues increase"
    market_cap = profile.get("market_cap", 0) or 0
    if is_bdc:
        # BDCs distribute 90%+ of income (RIC); growth from portfolio expansion
        # Typical BDC growth: 2-6% from new loans, rate increases
        # With 90%+ payout, retention is only 5-10% → g = ROE × retention ≈ 1-2%
        growth_cap = 0.06  # 6% max for BDCs (very limited retained earnings)
    elif is_reit:
        # REITs are structurally limited in growth (95% payout, mature properties)
        # Same-store NOI growth is typically 2-5%, acquisitions add 1-3%
        growth_cap = 0.08  # 8% max for any REIT
    elif market_cap >= 200e9:      # Megacap: >$200B
        growth_cap = 0.12
    elif market_cap >= 50e9:     # Large cap: $50B–$200B
        growth_cap = 0.18
    elif market_cap >= 10e9:     # Mid cap: $10B–$50B
        growth_cap = 0.25
    else:                        # Small/micro cap
        growth_cap = 0.35
    g1 = max(-0.10, min(g1, growth_cap))

    rf = sector_data.get("rf", 0.043)
    # Terminal growth: cannot exceed risk-free rate sustainably (Ch 12, p.307)
    gt = min(g1 / 2, rf, 0.025)
    # Ch 12, p.308: negative terminal growth IS allowed for declining industries
    # but floor at -5% to prevent extreme values
    gt = max(gt, -0.05)

    return {
        "stage1": g1,
        "stage2": (g1 + gt) / 2,
        "terminal": gt,
        "rationale": f"Stage 1 = median of: {', '.join(rationale_parts)}; terminal capped at {gt:.1%}",
    }


def _get_stable_roc(financials: dict, sector_data: dict, wacc: float) -> float | None:
    """
    Estimate the return on capital in stable growth (Damodaran Ch 12, p.311-313).

    In stable growth, ROC should converge toward WACC (no excess returns) or
    toward industry average. We use a blend: move current ROC toward WACC,
    but allow some residual excess returns for competitive advantages.
    """
    ebit = financials.get("ebit_ttm", 0)
    t = financials.get("tax_rate_effective", 0.21)
    total_equity = financials.get("total_equity", 0)
    total_debt = financials.get("total_debt", 0)
    cash = financials.get("cash", 0)

    invested_capital = total_equity + total_debt - cash
    if invested_capital <= 0 or ebit <= 0:
        return None

    current_roic = ebit * (1 - t) / invested_capital

    # Use R&D-adjusted ROIC if available (Ch 9, pp.232-236)
    if financials.get("rd_expense_ttm", 0) > 0:
        from data.financials import capitalize_rd
        rd_adj = capitalize_rd(financials, amort_life=5)
        if rd_adj and rd_adj.get("roic_adjusted") and 0 < rd_adj["roic_adjusted"] < 1.0:
            current_roic = rd_adj["roic_adjusted"]

    if current_roic <= 0 or current_roic > 1.0:
        return None

    # Per Ch 12 p.311: move ROC toward cost of capital in stable growth,
    # but allow some residual excess returns.
    # Blend: 60% toward WACC, 40% current ROIC (preserves some competitive advantage)
    stable_roc = 0.6 * wacc + 0.4 * current_roic
    # Floor at WACC (don't assume value destruction in perpetuity)
    stable_roc = max(stable_roc, wacc)

    return stable_roc


def _fundamental_growth_fcff(financials: dict, sector_data: dict) -> dict | None:
    """
    Damodaran Ch 11 (p.280): g_EBIT = Reinvestment rate × ROIC

    Reinvestment rate = (Capex - D&A + Δ Noncash WC) / NOPAT
    ROIC = NOPAT / Invested Capital
    Invested Capital = BV Equity + BV Debt - Cash

    Uses 3-5 year average when available (p.282: "more sustainable value").
    Falls back to ROE × retention if ROIC data is unavailable or produces
    nonsensical results (negative reinvestment for capital-light firms, p.283).
    """
    ebit = financials.get("ebit_ttm", 0)
    tax = financials.get("tax_rate_effective", 0.21)
    nopat = ebit * (1 - tax)

    capex = financials.get("capex_ttm", 0)
    dna = financials.get("d_and_a_ttm", 0)
    delta_wc = financials.get("delta_wc_ttm", 0)

    # Invested capital: BV equity + BV debt - Cash (p.280)
    bv_equity = financials.get("total_equity", 0)
    total_debt = financials.get("total_debt", 0)
    cash = financials.get("cash", 0)
    invested_capital = bv_equity + total_debt - cash

    # Compute ROIC — use R&D-adjusted if available (Ch 9, pp.232-236)
    # R&D capitalization materially changes ROIC for tech/pharma firms
    # (Amgen example: unadjusted 25.4% → adjusted 20.5%)
    roic = None
    rd_adjustment = None
    if invested_capital > 0 and nopat > 0:
        roic = nopat / invested_capital

        # Try R&D-adjusted ROIC (Ch 9)
        if financials.get("rd_expense_ttm", 0) > 0:
            from data.financials import capitalize_rd
            # Use 5 years for tech, but we don't know sector here — default 5
            rd_adjustment = capitalize_rd(financials, amort_life=5)
            if rd_adjustment and rd_adjustment.get("roic_adjusted"):
                roic_raw = roic
                roic = rd_adjustment["roic_adjusted"]
                # Cap at 100% (sanity)
                roic = min(roic, 1.0)

    # Compute reinvestment rate
    # Per p.281: use average over 3-5 years if available (current year is too volatile)
    reinvestment_rate = None
    if nopat > 0:
        net_capex = capex - dna
        # Ch 10, p.261: When R&D is capitalized, adjust net capex
        # Adjusted net capex = Capex + R&D - D&A - R&D_amortization
        if rd_adjustment:
            net_capex = capex + rd_adjustment["rd_current"] - dna - rd_adjustment["rd_amortization"]
            reinvestment = net_capex + delta_wc
            # Use adjusted NOPAT for the rate
            adj_nopat = rd_adjustment["adjusted_ebit"] * (1 - tax)
            reinvestment_rate = reinvestment / adj_nopat if adj_nopat > 0 else None
        else:
            reinvestment = net_capex + delta_wc
            reinvestment_rate = reinvestment / nopat

    # Only use ROIC × reinvestment if both are sensible positive numbers.
    # Per p.283: negative reinvestment rate in capital-light firms (D&A ≈ capex, declining WC)
    # doesn't reflect true growth dynamics — fall back to ROE × retention.
    if (roic is not None
        and reinvestment_rate is not None
        and 0.01 < reinvestment_rate < 1.50
        and 0.01 < roic < 1.0):

        rd_note = ""
        if rd_adjustment and rd_adjustment.get("roic_adjusted"):
            rd_note = f", R&D-adjusted from {roic_raw:.1%}"

        # If current ROIC >> sector average, forecast lower (p.281: competition erodes)
        sector_roe = sector_data.get("roe_sector")
        if (sector_roe and isinstance(sector_roe, (int, float))
                and 0.01 < sector_roe < 1.0
                and roic > sector_roe * 2.0):
            adj_roic = (roic + sector_roe) / 2
            g = reinvestment_rate * adj_roic
            return {
                "growth": g,
                "rationale": (
                    f"fundamental FCFF ({g:.1%} = reinvest {reinvestment_rate:.0%} × "
                    f"adj ROIC {adj_roic:.1%}, blended from {roic:.1%}{rd_note} toward sector {sector_roe:.1%})"
                ),
            }
        else:
            g = reinvestment_rate * roic
            return {
                "growth": g,
                "rationale": (
                    f"fundamental FCFF ({g:.1%} = reinvest {reinvestment_rate:.0%} × ROIC {roic:.1%}{rd_note})"
                ),
            }

    # Fallback: use ROE × retention (less accurate for FCFF but appropriate for
    # capital-light firms where ROIC × reinvestment is not meaningful, p.283)
    roe_avg = _safe_mean(financials.get("roe_5yr", []))
    ret_avg = _safe_mean(financials.get("retention_5yr", []))
    if roe_avg > 0 and 0 < ret_avg < 1:
        g_fund = roe_avg * ret_avg
        return {
            "growth": g_fund,
            "rationale": f"fundamental ({g_fund:.1%} = ROE {roe_avg:.1%} × retention {ret_avg:.1%})",
        }

    return None


def _fundamental_growth_fcfe(financials: dict) -> dict | None:
    """
    Damodaran Ch 11 (p.286-287): g_NI = Equity reinvestment rate × ROE
    Simple fallback: g_EPS = ROE × retention ratio
    """
    # Try equity reinvestment rate × ROE first (p.287: more accurate)
    ni = financials.get("net_income_ttm", 0)
    capex = financials.get("capex_ttm", 0)
    dna = financials.get("d_and_a_ttm", 0)
    delta_wc = financials.get("delta_wc_ttm", 0)
    # Net borrowing not tracked separately; approximate as 0 for stable firms (p.287)
    net_borrowing = 0

    if ni > 0:
        equity_reinvested = (capex - dna) + delta_wc - net_borrowing
        eq_reinvest_rate = equity_reinvested / ni
        # Cap at reasonable range
        eq_reinvest_rate = max(-0.50, min(eq_reinvest_rate, 1.50))

        roe_5yr = financials.get("roe_5yr", [])
        roe = roe_5yr[0] if roe_5yr else _safe_mean(roe_5yr)
        if roe > 0:
            g = eq_reinvest_rate * roe
            return {
                "growth": g,
                "rationale": (
                    f"fundamental FCFE ({g:.1%} = eq reinvest {eq_reinvest_rate:.0%} × ROE {roe:.1%})"
                ),
            }

    # Fallback: simple retention × ROE (p.286)
    roe_avg = _safe_mean(financials.get("roe_5yr", []))
    ret_avg = _safe_mean(financials.get("retention_5yr", []))
    if roe_avg > 0 and 0 < ret_avg < 1:
        g_fund = roe_avg * ret_avg
        return {
            "growth": g_fund,
            "rationale": f"fundamental FCFE ({g_fund:.1%} = ROE {roe_avg:.1%} × retention {ret_avg:.1%})",
        }

    return None


def _build_sensitivity_table(
    financials, profile, discount_rate, gt, g1, method, base_fcf, shares
) -> dict:
    """
    5×5 sensitivity: WACC ± 1.5% (steps of 0.5%) × terminal growth ± 1% (steps of 0.5%).
    Returns nested dict: {wacc_delta: {g_delta: intrinsic_value}}.
    """
    table = {}
    wacc_deltas = [-0.015, -0.005, 0, 0.005, 0.015]
    g_deltas = [-0.010, -0.005, 0, 0.005, 0.010]

    net_debt = financials.get("net_debt", 0) if method == "fcff" else 0

    for wd in wacc_deltas:
        row = {}
        dr = discount_rate + wd
        if dr <= 0:
            dr = 0.01
        for gd in g_deltas:
            gt_adj = gt + gd
            if dr <= gt_adj:
                gt_adj = dr - 0.005

            # Simplified projection (same structure as main DCF but inline)
            pv = 0.0
            fcf = base_fcf
            for yr in range(1, 6):
                fcf = fcf * (1 + g1)
                pv += fcf / (1 + dr) ** yr
            for yr in range(6, 11):
                frac = (yr - 5) / 5
                g_yr = g1 + frac * (gt_adj - g1)
                fcf = fcf * (1 + g_yr)
                pv += fcf / (1 + dr) ** yr

            fcf_n1 = fcf * (1 + gt_adj)
            tv = (fcf_n1 / (dr - gt_adj)) / (1 + dr) ** 10
            ev = pv + tv
            eq = (ev - net_debt) / shares if shares > 0 else 0
            row[f"{gd:+.1%}"] = round(eq, 2)

        table[f"{wd:+.1%}"] = row

    return table


def _get_augmented_dividends(ticker: str, net_income: float) -> dict | None:
    """
    Get augmented dividends (dividends + net buybacks) from yfinance.
    Damodaran Ch 21: for banks with significant buybacks, total cash
    returned to equity is more relevant than dividends alone.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cf = t.cashflow
        if cf is None or cf.empty:
            return None

        # Most recent year's cash flow
        dividends = 0
        buybacks = 0

        if "Cash Dividends Paid" in cf.index:
            dividends = abs(float(cf.loc["Cash Dividends Paid"].iloc[0]))

        if "Repurchase Of Capital Stock" in cf.index:
            buybacks = abs(float(cf.loc["Repurchase Of Capital Stock"].iloc[0]))

        # Subtract any issuance (net buybacks)
        issuance = 0
        if "Issuance Of Capital Stock" in cf.index:
            raw_issuance = cf.loc["Issuance Of Capital Stock"].iloc[0]
            if raw_issuance is not None and not math.isnan(float(raw_issuance)):
                issuance = abs(float(raw_issuance))
        net_buybacks = max(buybacks - issuance, 0)

        total = dividends + net_buybacks
        if total <= 0 or (net_income > 0 and total / net_income > 1.5):
            # Payout > 150% of NI is unsustainable — fall back to dividends only
            if dividends > 0:
                return {"dividends": dividends, "buybacks": 0, "total": dividends}
            return None

        return {"dividends": dividends, "buybacks": net_buybacks, "total": total}
    except Exception:
        return None


def _synthetic_rating(coverage_ratio: float, market_cap: float = 0) -> tuple[str, float]:
    """
    Damodaran Ch 8, pp.212-213: Map interest coverage ratio to synthetic bond
    rating and default spread.  Market-cap-dependent tables.

    Table 8.1 (large cap, market cap > $5B): tighter coverage thresholds, lower spreads.
    Table 8.2 (small cap, market cap ≤ $5B): looser thresholds, higher spreads.

    Returns (rating, default_spread) tuple.
    """
    # Table 8.1 — Large-cap firms (>$5B market cap)
    _LARGE_CAP_TABLE = [
        (12.50, "AAA",  0.0050),
        (9.50,  "AA",   0.0065),
        (7.50,  "A+",   0.0085),
        (6.00,  "A",    0.0100),
        (4.25,  "A-",   0.0125),
        (3.00,  "BBB",  0.0175),
        (2.50,  "BB+",  0.0250),
        (2.25,  "BB",   0.0325),
        (2.00,  "B+",   0.0400),
        (1.50,  "B",    0.0500),
        (1.25,  "B-",   0.0600),
        (0.80,  "CCC",  0.0700),
        (0.65,  "CC",   0.0850),
        (0.20,  "C",    0.1100),
        (-999,  "D",    0.1500),
    ]

    # Table 8.2 — Small-cap firms (≤$5B market cap)
    _SMALL_CAP_TABLE = [
        (8.50,  "AAA",  0.0050),
        (6.50,  "AA",   0.0065),
        (5.50,  "A+",   0.0085),
        (4.25,  "A",    0.0100),
        (3.00,  "A-",   0.0125),
        (2.50,  "BBB",  0.0175),
        (2.25,  "BB+",  0.0250),
        (2.00,  "BB",   0.0325),
        (1.75,  "B+",   0.0400),
        (1.50,  "B",    0.0500),
        (1.25,  "B-",   0.0600),
        (0.80,  "CCC",  0.0700),
        (0.65,  "CC",   0.0850),
        (0.20,  "C",    0.1100),
        (-999,  "D",    0.1400),
    ]

    table = _LARGE_CAP_TABLE if market_cap > 5e9 else _SMALL_CAP_TABLE

    for min_cov, rating, spread in table:
        if coverage_ratio >= min_cov:
            return rating, spread
    return "D", table[-1][2]


def _rating_to_p_default(rating: str) -> float:
    """
    Damodaran Ch 15, Table 15.2 (p.399): Bond rating to cumulative
    probability of default over 10 years.

    Source: Altman (compiled at NYU Stern).
    """
    _P_DEFAULT = {
        "AAA": 0.0007,
        "AA":  0.0051,
        "A+":  0.0060,
        "A":   0.0066,
        "A-":  0.0250,
        "BBB": 0.0754,
        "BB+": 0.1000,
        "BB":  0.1663,
        "B+":  0.2500,
        "B":   0.3680,
        "B-":  0.4500,
        "CCC": 0.5901,
        "CC":  0.7000,
        "C":   0.8500,
        "D":   1.0000,
    }
    return _P_DEFAULT.get(rating, 0.10)


def _safe_mean(values: list) -> float:
    clean = [v for v in values if v is not None and not math.isnan(v) and v != 0]
    return sum(clean) / len(clean) if clean else 0.0
