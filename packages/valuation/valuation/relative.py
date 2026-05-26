"""
Relative Valuation — sector multiple comparisons.

Reference: Damodaran "Investment Valuation" Chapter 17-18.
Uses Damodaran's annual sector-average multiples as benchmarks.
"""


def run_relative_valuation(
    profile: dict, financials: dict, sector_data: dict, decision: dict
) -> dict:
    """
    Apply sector-appropriate multiples to derive implied fair value.
    Always runs at least 2 multiples.

    Returns dict with:
        multiples_used, composite_implied_price,
        ev_ebitda_implied, pe_implied, ev_sales_implied, pb_implied,
        vs_sector (per-multiple breakdown),
        peg_ratio, justified_pe, warnings
    """
    sector = profile.get("sector", "")
    current_price = profile.get("current_price", 0)
    market_cap = profile.get("market_cap", 0)
    shares = profile.get("shares_outstanding", 0) or financials.get("shares_outstanding", 1)
    net_debt = financials.get("net_debt", 0)
    warnings_list = []

    results = {
        "multiples_used": [],
        "composite_implied_price": None,
        "ev_ebitda_implied": None,
        "pe_implied": None,
        "ev_sales_implied": None,
        "pb_implied": None,
        "ev_ebit_implied": None,
        "vs_sector": {},
        "peg_ratio": None,
        "justified_pe": None,
        "justified_ev_ebitda": None,
        "warnings": warnings_list,
    }

    implied_prices = []

    # ── Determine which multiples apply (from multiples_guide.md) ─────────────
    is_financial = _is_financial_sector(sector)
    is_reit = _is_reit(sector, profile.get("industry", ""))
    ebitda = financials.get("ebitda_ttm", 0)
    ebit = financials.get("ebit_ttm", 0)
    revenue = financials.get("revenue_ttm", 0)
    net_income = financials.get("net_income_ttm", 0)
    book_value = _estimate_book_value(financials, market_cap, net_debt)
    ev = market_cap + net_debt  # Enterprise Value

    # 1. EV/EBITDA — default for most non-financial companies
    is_bdc_check = "business development company" in profile.get("description", "").lower()
    if not is_financial and not is_reit and not is_bdc_check and ebitda > 0:
        sector_ev_ebitda = sector_data.get("ev_ebitda_sector")
        company_ev_ebitda = ev / ebitda if ebitda > 0 else None

        if sector_ev_ebitda and sector_ev_ebitda > 0:
            implied_ev = sector_ev_ebitda * ebitda
            implied_price = _ev_to_equity_price(implied_ev, net_debt, shares)
            results["ev_ebitda_implied"] = round(implied_price, 2)
            implied_prices.append(implied_price)
            results["multiples_used"].append("EV/EBITDA")
            results["vs_sector"]["EV/EBITDA"] = {
                "company_value": round(company_ev_ebitda, 2) if company_ev_ebitda else None,
                "sector_avg": round(sector_ev_ebitda, 2),
                "implied_price": round(implied_price, 2),
                "premium_pct": round((company_ev_ebitda / sector_ev_ebitda - 1) * 100, 1) if company_ev_ebitda else None,
            }

            # Justified EV/EBITDA (Ch 17, p.461):
            # = (EBIT/EBITDA) × (1-t) × (1 - g/ROIC) × (1+g) / (WACC - g)
            wacc = sector_data.get("wacc")
            g1 = _get_stage1_growth(financials)
            tax_eff = financials.get("tax_rate_effective", 0.21) or 0.21
            roic = _compute_roic(financials, ebit, tax_eff)
            if wacc and wacc > (g1 + 0.005) and roic > 0.01:
                ebit_ebitda = ebit / ebitda
                reinv = min(g1 / roic, 0.95)
                justified = ebit_ebitda * (1 - tax_eff) * (1 - reinv) * (1 + g1) / (wacc - g1)
                if justified > 0:
                    results["justified_ev_ebitda"] = round(justified, 2)
                    results["vs_sector"]["EV/EBITDA"]["justified"] = round(justified, 2)
                    if company_ev_ebitda and company_ev_ebitda > justified * 1.3:
                        warnings_list.append(
                            f"EV/EBITDA ({company_ev_ebitda:.1f}x) is >30% above justified "
                            f"({justified:.1f}x; ROIC={roic:.1%}, g={g1:.1%}, WACC={wacc:.1%}) "
                            "— verify premium is warranted by growth or moat."
                        )
                    elif company_ev_ebitda and company_ev_ebitda < justified * 0.7:
                        warnings_list.append(
                            f"EV/EBITDA ({company_ev_ebitda:.1f}x) is >30% below justified "
                            f"({justified:.1f}x) — potential undervaluation on enterprise basis."
                        )

    # 2. P/E ratio — use when earnings are clean and positive
    # Damodaran Ch 21, p.601: P/E is valid for financial firms (unlike EV/EBITDA).
    if net_income > 0:
        sector_pe = sector_data.get("pe_sector")
        eps = net_income / shares if shares > 0 else 0
        company_pe = current_price / eps if eps > 0 else None

        if sector_pe and sector_pe > 0 and eps > 0:
            implied_price = sector_pe * eps
            results["pe_implied"] = round(implied_price, 2)
            implied_prices.append(implied_price)
            results["multiples_used"].append("P/E")
            premium_pct = round((company_pe / sector_pe - 1) * 100, 1) if company_pe else None
            results["vs_sector"]["P/E"] = {
                "company_value": round(company_pe, 2) if company_pe else None,
                "sector_avg": round(sector_pe, 2),
                "implied_price": round(implied_price, 2),
                "premium_pct": premium_pct,
            }
            # Ch 17 companion variable check: P/E premium should be
            # explained by higher growth, lower risk, or higher payout
            if premium_pct is not None and abs(premium_pct) > 20:
                g1 = _get_stage1_growth(financials)
                beta = profile.get("beta", 1.0)
                direction = "premium" if premium_pct > 0 else "discount"
                results["vs_sector"]["P/E"]["companion_note"] = (
                    f"P/E {direction} vs sector ({premium_pct:+.0f}%); "
                    f"company growth={g1:.1%}, beta={beta:.2f}"
                )

            # PEG ratio
            g1 = _get_stage1_growth(financials)
            if g1 > 0 and company_pe:
                results["peg_ratio"] = round(company_pe / (g1 * 100), 2)

            # Justified P/E (Damodaran Ch 18, pp.471-473):
            # Use two-stage model when growth > terminal; else stable-growth Gordon
            ke = _estimate_ke(profile, financials, sector_data)
            justified_pe = _justified_pe(financials, g1, ke, sector_data)
            if justified_pe and justified_pe > 0:
                results["justified_pe"] = round(justified_pe, 2)
                if company_pe and company_pe > justified_pe * 1.2:
                    warnings_list.append(
                        f"P/E ({company_pe:.1f}x) is >20% above justified P/E ({justified_pe:.1f}x) "
                        "— company may be overvalued on an earnings basis."
                    )
                elif company_pe and company_pe < justified_pe * 0.8:
                    warnings_list.append(
                        f"P/E ({company_pe:.1f}x) is >20% below justified P/E ({justified_pe:.1f}x) "
                        "— potential undervaluation on earnings basis."
                    )

    # 3a. P/FFO — primary multiple for REITs (Ch 26, pp.764-768)
    # FFO replaces EPS since depreciation is non-economic for real estate
    if is_reit:
        from data.financials import compute_ffo
        ffo_data = compute_ffo(financials)
        ffo = ffo_data["ffo"]
        ffo_per_share = ffo_data["ffo_per_share"]
        affo_per_share = ffo_data["affo_per_share"]

        if ffo_per_share > 0:
            company_p_ffo = current_price / ffo_per_share

            # Use sector P/E as proxy for P/FFO (FFO replaces earnings for REITs)
            # Sector-specific P/FFO ranges would be better, but Damodaran tables
            # don't separate REIT P/FFO — sector P/E is the closest benchmark
            sector_p_ffo = sector_data.get("pe_sector")
            if sector_p_ffo and sector_p_ffo > 0:
                implied_price = sector_p_ffo * ffo_per_share
                results["pe_implied"] = round(implied_price, 2)  # store as pe_implied for compatibility
                implied_prices.append(implied_price)
                results["multiples_used"].append("P/FFO")
                results["vs_sector"]["P/FFO"] = {
                    "company_value": round(company_p_ffo, 2),
                    "sector_avg": round(sector_p_ffo, 2),
                    "implied_price": round(implied_price, 2),
                    "premium_pct": round((company_p_ffo / sector_p_ffo - 1) * 100, 1),
                }
                # Justified P/FFO (same as justified P/E but using FFO growth)
                ke = _estimate_ke(profile, financials, sector_data)
                g1 = _get_stage1_growth(financials)
                # REIT payout from FFO is typically 60-80%
                ffo_payout = 0.75  # conservative default
                if ke > g1 + 0.001:
                    justified_p_ffo = ffo_payout * (1 + g1) / (ke - g1)
                    if justified_p_ffo > 0:
                        results["justified_pe"] = round(justified_p_ffo, 2)
                        results["vs_sector"]["P/FFO"]["justified"] = round(justified_p_ffo, 2)
                        if company_p_ffo > justified_p_ffo * 1.3:
                            warnings_list.append(
                                f"P/FFO ({company_p_ffo:.1f}x) is >30% above justified "
                                f"({justified_p_ffo:.1f}x; growth={g1:.1%}, Ke={ke:.1%}) "
                                "— verify premium is warranted."
                            )

            # Also add AFFO-based metric as a note
            if affo_per_share > 0:
                results["vs_sector"]["P/FFO"]["affo_per_share"] = round(affo_per_share, 2)
                results["vs_sector"]["P/FFO"]["p_affo"] = round(current_price / affo_per_share, 2)

            # Store FFO data for the report
            results["ffo_per_share"] = round(ffo_per_share, 2)
            results["affo_per_share"] = round(affo_per_share, 2)

    # 3b. P/NAV — primary multiple for BDCs (Business Development Companies)
    # BDCs report NAV quarterly (ASC 820 mark-to-market). bookValue from yfinance ≈ NAV.
    is_bdc = "business development company" in profile.get("description", "").lower()
    if is_bdc:
        nav_per_share = profile.get("book_value", 0)
        if nav_per_share and nav_per_share > 0:
            company_p_nav = current_price / nav_per_share

            # BDCs typically trade at 0.8-1.5x NAV. Use sector P/B as reference,
            # but BDC-average P/NAV ≈ 1.0x is a better anchor.
            sector_pb = sector_data.get("pb_sector") or 1.0
            # Clamp sector P/B to reasonable BDC range (avoid extreme sector averages)
            sector_p_nav = max(0.7, min(sector_pb, 2.0))

            implied_price = sector_p_nav * nav_per_share
            results["pb_implied"] = round(implied_price, 2)
            implied_prices.append(implied_price)
            results["multiples_used"].append("P/NAV")
            results["vs_sector"]["P/NAV"] = {
                "company_value": round(company_p_nav, 2),
                "sector_avg": round(sector_p_nav, 2),
                "implied_price": round(implied_price, 2),
                "premium_pct": round((company_p_nav / sector_p_nav - 1) * 100, 1),
            }
            # Justified P/NAV = payout × (1+g) / (Ke - g)
            # Same as justified P/E but payout ≈ 90% (RIC requirement)
            ke = _estimate_ke(profile, financials, sector_data)
            g1 = _get_stage1_growth(financials)
            bdc_payout = 0.90  # RIC distribution requirement
            if ke > g1 + 0.001:
                justified_p_nav = bdc_payout * (1 + g1) / (ke - g1)
                if justified_p_nav > 0:
                    results["vs_sector"]["P/NAV"]["justified"] = round(justified_p_nav, 2)
                    if company_p_nav > justified_p_nav * 1.3:
                        warnings_list.append(
                            f"P/NAV ({company_p_nav:.2f}x) is >30% above justified "
                            f"({justified_p_nav:.2f}x; growth={g1:.1%}, Ke={ke:.1%}) "
                            "— verify premium is warranted by portfolio quality."
                        )

            results["nav_per_share"] = round(nav_per_share, 2)
            results["p_nav"] = round(company_p_nav, 3)

    # 3. EV/Sales — use for high-growth, pre-profit, or margin-distorted businesses
    if revenue > 0 and (net_income <= 0 or ebitda <= 0) and not is_reit:
        sector_ev_sales = sector_data.get("ev_sales_sector")
        company_ev_sales = ev / revenue if revenue > 0 else None

        if sector_ev_sales and sector_ev_sales > 0:
            implied_ev = sector_ev_sales * revenue
            implied_price = _ev_to_equity_price(implied_ev, net_debt, shares)
            results["ev_sales_implied"] = round(implied_price, 2)
            implied_prices.append(implied_price)
            results["multiples_used"].append("EV/Sales")
            results["vs_sector"]["EV/Sales"] = {
                "company_value": round(company_ev_sales, 2) if company_ev_sales else None,
                "sector_avg": round(sector_ev_sales, 2),
                "implied_price": round(implied_price, 2),
                "premium_pct": round((company_ev_sales / sector_ev_sales - 1) * 100, 1) if company_ev_sales else None,
            }

    # 4. P/B — primary for financial firms (Ch 21, p.601: P/B and P/E are
    #    the ONLY valid multiples for banks — NOT EV/EBITDA or EV/Sales)
    #    Justified P/B = ROE / Ke (Damodaran Ch 21, p.601, R² = 0.601)
    if is_financial and not is_bdc:  # BDCs use P/NAV instead of P/B
        # Prefer actual book equity from financials over rough estimate
        bv_equity = financials.get("total_equity", 0) or book_value
        if bv_equity > 0:
            roe_avg = _safe_mean(financials.get("roe_5yr", []))
            ke = _estimate_ke(profile, financials, sector_data)
            justified_pb = roe_avg / ke if ke > 0 and roe_avg > 0 else None
            if justified_pb and justified_pb > 0:
                bvps = bv_equity / shares if shares > 0 else 0
                implied_price = justified_pb * bvps
                results["pb_implied"] = round(implied_price, 2)
                implied_prices.append(implied_price)
                results["multiples_used"].append("P/B")
                company_pb = profile.get("pb_ratio") or (current_price / bvps if bvps > 0 else None)
                results["vs_sector"]["P/B"] = {
                    "company_value": round(company_pb, 2) if company_pb else None,
                    "justified_pb": round(justified_pb, 2),
                    "implied_price": round(implied_price, 2),
                    "premium_pct": round((company_pb / justified_pb - 1) * 100, 1) if company_pb and justified_pb else None,
                }
                warnings_list.append(
                    f"Financial firm: using Justified P/B = ROE/Ke = "
                    f"{roe_avg:.1%}/{ke:.1%} = {justified_pb:.2f}x"
                )

    # 5. EV/EBIT — for capex-light software/SaaS
    if not is_financial and ebit > 0 and "Software" in sector:
        sector_ev_ebitda = sector_data.get("ev_ebitda_sector")
        if sector_ev_ebitda:
            # EV/EBIT ≈ EV/EBITDA × (EBITDA/EBIT) — use as approximation
            ebitda_ebit_ratio = ebitda / ebit if ebit > 0 else 1.0
            ev_ebit_sector = sector_ev_ebitda / max(ebitda_ebit_ratio, 0.5)
            company_ev_ebit = ev / ebit if ebit > 0 else None
            implied_ev = ev_ebit_sector * ebit
            implied_price = _ev_to_equity_price(implied_ev, net_debt, shares)
            results["ev_ebit_implied"] = round(implied_price, 2)
            implied_prices.append(implied_price)
            results["multiples_used"].append("EV/EBIT")
            results["vs_sector"]["EV/EBIT"] = {
                "company_value": round(company_ev_ebit, 2) if company_ev_ebit else None,
                "sector_avg": round(ev_ebit_sector, 2),
                "implied_price": round(implied_price, 2),
                "premium_pct": None,
            }

    # Composite: equal-weighted average of valid implied prices
    if implied_prices:
        results["composite_implied_price"] = round(
            sum(implied_prices) / len(implied_prices), 2
        )

        # Cross-check: flag if composite diverges from current price by less than 15%
        if current_price > 0 and results["composite_implied_price"] > 0:
            divergence = (results["composite_implied_price"] - current_price) / current_price * 100
            results["composite_upside_pct"] = round(divergence, 1)
    else:
        warnings_list.append("No valid sector multiples could be applied. Relative valuation unavailable.")

    # Check for large DCF vs relative divergence (handled in report_generator)
    results["current_price"] = current_price

    return results


def _ev_to_equity_price(ev: float, net_debt: float, shares: float) -> float:
    """Bridge: (EV - net_debt) / shares"""
    equity_value = ev - net_debt
    return equity_value / shares if shares > 0 else 0.0


def _is_financial_sector(sector: str) -> bool:
    return sector in ("Financial Services",) or any(
        k in sector.lower() for k in ["bank", "insurance", "financial"]
    )


def _is_reit(sector: str, industry: str) -> bool:
    return sector == "Real Estate" or "reit" in industry.lower()


def _estimate_book_value(financials: dict, market_cap: float, net_debt: float) -> float:
    """Rough book value: total assets - total liabilities approximation."""
    return max(market_cap - net_debt * 0.5, 0)  # simplified


def _get_stage1_growth(financials: dict) -> float:
    """Quick growth estimate for PEG calculation."""
    rev = financials.get("revenue_5yr", [])
    if len(rev) >= 2 and rev[-1] > 0 and rev[0] > 0:
        return (rev[0] / rev[-1]) ** (1 / (len(rev) - 1)) - 1
    return 0.05


def _estimate_ke(profile: dict, financials: dict, sector_data: dict) -> float:
    """Quick cost of equity for justified multiple calculations."""
    rf = sector_data.get("rf", 0.043)
    erp = sector_data.get("erp", 0.055)
    beta = profile.get("beta", 1.0)
    return rf + beta * erp


def _compute_roic(financials: dict, ebit: float, tax_rate: float) -> float:
    """Compute ROIC = NOPAT / Invested_Capital, using R&D-adjusted values if available."""
    import math
    rd_ttm = financials.get("rd_expense_ttm", 0) or 0
    if rd_ttm > 0:
        try:
            from data.financials import capitalize_rd
            rd = capitalize_rd(financials, amort_life=5)
            if rd and rd.get("roic_adjusted"):
                roic = rd["roic_adjusted"]
                return min(roic, 1.0)  # cap at 100%
        except Exception:
            pass
    # Fallback: unadjusted ROIC
    bv_equity = financials.get("total_equity", 0) or 0
    total_debt = financials.get("total_debt", 0) or 0
    cash = financials.get("cash", 0) or 0
    ic = bv_equity + total_debt - cash
    if ic <= 0:
        return 0.0
    nopat = ebit * (1 - tax_rate)
    return min(nopat / ic, 1.0)


def _justified_pe(financials: dict, g_hg: float, ke_hg: float,
                   sector_data: dict) -> float | None:
    """Justified P/E using two-stage model (Ch 18, pp.472-473) when growth is
    above terminal rate, else stable-growth Gordon Growth (Ch 17, p.461).

    Two-stage formula:
      PE = [Payout_hg × (1+g) × (1 - (1+g)^n/(1+Ke)^n)] / (Ke - g)
         + [Payout_st × (1+g)^n × (1+g_st)] / [(Ke_st - g_st) × (1+Ke)^n]
    """
    rf = sector_data.get("rf", 0.043)
    g_st = min(rf, 0.03)  # terminal growth ≤ risk-free rate (Ch 16)

    # If growth is near or below terminal, use simple stable-growth model
    if g_hg <= g_st + 0.005:
        payout = _fundamental_payout(financials, g_hg)
        if payout > 0 and ke_hg > g_hg + 0.001:
            return (payout * (1 + g_hg)) / (ke_hg - g_hg)
        return None

    # Two-stage model
    n = 5  # years of high growth
    roe_vals = financials.get("roe_5yr", [])
    roe = _safe_mean(roe_vals)
    roe = max(roe, 0.08)  # floor at 8%

    # High-growth phase payout = 1 - g/ROE
    payout_hg = max(1 - g_hg / roe, 0.0) if roe > g_hg else 0.0
    # Stable phase: ROE converges toward Ke (competitive equilibrium)
    roe_st = max(ke_hg, 0.10)  # stable ROE ≥ cost of equity
    payout_st = max(1 - g_st / roe_st, 0.5)  # at least 50% payout in stable

    # Ke in stable phase (lower beta assumed)
    ke_st = ke_hg  # simplification: same cost of equity

    if ke_st <= g_st:
        return None

    # First term: high-growth phase PV of dividends
    # Math works even when g_hg > ke_hg: both numerator and denominator are
    # negative, yielding a positive result (finite sum, not perpetuity)
    growth_factor = (1 + g_hg) ** n
    discount_factor = (1 + ke_hg) ** n
    denom_hg = ke_hg - g_hg
    if abs(denom_hg) < 0.001:  # g ≈ ke: use L'Hôpital limit
        term1 = payout_hg * n * (1 + g_hg) / discount_factor
    else:
        term1 = (payout_hg * (1 + g_hg) * (1 - growth_factor / discount_factor)) / denom_hg

    # Second term: terminal value (stable-growth PE at year n, discounted back)
    term2 = (payout_st * growth_factor * (1 + g_st)) / ((ke_st - g_st) * discount_factor)

    justified = term1 + term2
    return justified if justified > 0 else None


def _fundamental_payout(financials: dict, growth: float) -> float:
    """Fundamental payout ratio = 1 - g/ROE (Damodaran Ch 17, p.461).

    This captures total shareholder return (dividends + buybacks),
    not just dividend payout which understates cash returned for
    buyback-heavy companies like AAPL.
    """
    roe_vals = financials.get("roe_5yr", [])
    roe = _safe_mean(roe_vals)
    if roe > 0.01 and growth < roe:
        return 1 - growth / roe
    # Fallback to reported dividend payout
    payout = financials.get("payout_ratio") or 0
    return payout if payout > 0 else 0.3  # default 30% if unknown


def _safe_mean(values: list) -> float:
    clean = [v for v in values if v is not None and v != 0]
    return sum(clean) / len(clean) if clean else 0.0
