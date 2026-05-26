"""Report generator — assembles the full markdown valuation report and Telegram summary."""

from datetime import datetime
from pathlib import Path


def generate_report(results: dict, output_dir: str) -> str:
    """
    Write the full valuation report to {output_dir}/{TICKER}_{date}.md.
    Returns the file path.
    """
    ticker = results["ticker"]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{ticker}_{date_str}.md"
    path = Path(output_dir) / filename

    path.write_text(_build_full_report(results))
    return str(path)


def generate_telegram_summary(results: dict) -> str:
    """Return a condensed ~500-word Telegram-friendly summary."""
    return _build_telegram_summary(results)


# ── Full Report ──────────────────────────────────────────────────────────────

def _build_full_report(r: dict) -> str:
    ticker = r["ticker"]
    profile = r.get("profile", {})
    financials = r.get("financials", {})
    sector_data = r.get("sector_data", {})
    decision = r.get("decision", {})
    dcf = r.get("dcf_result")
    rel = r.get("relative_result")
    asset = r.get("asset_result")
    contingent = r.get("contingent_result")
    insider = r.get("insider_signal")
    synthesis = r.get("synthesis", {})
    red_flags = r.get("red_flags", [])

    name = profile.get("name", ticker)
    sector = profile.get("sector", "N/A")
    industry = profile.get("industry", "N/A")
    mc = profile.get("market_cap", 0)
    price = profile.get("current_price", 0)
    date = datetime.now().strftime("%Y-%m-%d")

    verdict = synthesis.get("verdict", "N/A")
    weighted = synthesis.get("weighted_value", 0)
    upside = synthesis.get("upside_pct", 0)

    verdict_emoji = {"UNDERVALUED": "🟢", "OVERVALUED": "🔴", "FAIRLY VALUED": "🟡"}.get(verdict, "⚪")

    lines = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(f"# Valuation Report: {name} ({ticker})")
    lines.append(f"**Date:** {date} | **Model:** Damodaran Investment Valuation Agent v1.0")
    lines.append(f"**Current Price:** ${price:.2f} | **Market Cap:** ${mc/1e9:.2f}B")
    lines.append(f"**Sector:** {sector} | **Industry:** {industry}")
    lines.append("")

    # ── Executive Summary ─────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"**{verdict_emoji} Verdict: {verdict}**")
    lines.append(f"**Weighted Intrinsic Value:** ${weighted:.2f} per share")
    lines.append(f"**Upside / (Downside):** {upside:+.1f}% at current price of ${price:.2f}")
    lines.append("")

    if dcf and dcf.get("intrinsic_value_per_share"):
        dcf_val = dcf["intrinsic_value_per_share"]
        lines.append(f"The primary DCF analysis ({decision.get('primary_method','').upper()}) "
                     f"yields an intrinsic value of **${dcf_val:.2f}** per share, "
                     f"implying {(dcf_val - price) / price * 100:+.1f}% "
                     f"{'upside' if dcf_val > price else 'downside'} to the current price.")

    if rel and rel.get("composite_implied_price"):
        rel_val = rel["composite_implied_price"]
        lines.append(f"Relative valuation (sector multiples) implies **${rel_val:.2f}**, "
                     f"a {(rel_val - price) / price * 100:+.1f}% "
                     f"{'premium' if rel_val > price else 'discount'} to current price.")

    div = synthesis.get("divergence_pct", 0)
    if div > 30 and dcf and rel:
        lines.append(f"\n> ⚠️ **DCF and relative valuation diverge by {div:.0f}%.** "
                     "This divergence is discussed in the Valuation Synthesis section.")
    lines.append("")

    # ── Decision Tree ─────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 2. Valuation Method Selection (Damodaran Decision Tree)")
    lines.append("")
    lines.append(f"**Primary Method:** `{decision.get('primary_method', 'N/A')}`")
    lines.append(f"**Secondary Methods:** {', '.join(decision.get('secondary_methods', [])) or 'None'}")
    lines.append(f"**Earnings Status:** {decision.get('earnings_status', 'N/A')}")
    lines.append(f"**Normalization Applied:** {decision.get('normalization_required', False)}")
    lines.append(f"**Distress Risk:** {'⚠️ YES' if decision.get('distress_risk') else 'No'}")
    lines.append("")
    lines.append("**Rationale:**")
    lines.append(f"> {decision.get('rationale', 'N/A')}")
    if decision.get("notes"):
        lines.append("")
        lines.append("**Decision Notes:**")
        for note in decision["notes"]:
            lines.append(f"- {note}")
    lines.append("")

    # ── Company Fundamentals ──────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 3. Company Fundamentals")
    lines.append("")
    rev = financials.get("revenue_ttm", 0)
    ebit = financials.get("ebit_ttm", 0)
    ebitda = financials.get("ebitda_ttm", 0)
    ni = financials.get("net_income_ttm", 0)
    nd = financials.get("net_debt", 0)
    t = financials.get("tax_rate_effective", 0)
    nd_ebitda = nd / ebitda if ebitda > 0 else None
    ebit_margin = ebit / rev if rev > 0 else None
    net_margin = ni / rev if rev > 0 else None

    lines.append("| Metric | Company | Sector Avg |")
    lines.append("|--------|---------|------------|")
    lines.append(f"| Revenue (TTM) | ${rev/1e6:,.0f}M | — |")
    lines.append(f"| EBIT Margin | {ebit_margin:.1%} | {sector_data.get('net_margin_sector', 0) or 0:.1%} |" if ebit_margin is not None else "| EBIT Margin | N/A | — |")
    lines.append(f"| EBITDA (TTM) | ${ebitda/1e6:,.0f}M | — |")
    lines.append(f"| Net Income (TTM) | ${ni/1e6:,.0f}M | — |")
    lines.append(f"| Net Debt | ${nd/1e6:,.0f}M | — |")
    if nd_ebitda is not None:
        lines.append(f"| Net Debt / EBITDA | {nd_ebitda:.1f}x | — |")
    lines.append(f"| Effective Tax Rate | {t:.1%} | — |")
    lines.append(f"| Beta (profile) | {profile.get('beta', 'N/A')} | {sector_data.get('beta_unlevered', 'N/A')} (unlevered) |")
    lines.append(f"| Market Cap | ${mc/1e9:.2f}B ({profile.get('market_cap_label','')}) | — |")
    lines.append("")

    # ── DCF ───────────────────────────────────────────────────────────────────
    if dcf:
        lines.append("---")
        lines.append("")
        lines.append("## 4. DCF Valuation")
        lines.append("")

        # WACC components
        lines.append("### 4.1 WACC / Cost of Capital")
        lines.append("")
        wc = dcf.get("wacc_components", {})
        lines.append("| Component | Value | Notes |")
        lines.append("|-----------|-------|-------|")
        lines.append(f"| Risk-Free Rate (10yr Treasury) | {dcf['assumptions'].get('rf', 0):.2%} | Live as of {date} |")
        lines.append(f"| Equity Risk Premium | {dcf['assumptions'].get('erp', 0):.2%} | Damodaran implied ERP |")
        lines.append(f"| Unlevered Beta (sector) | {dcf['assumptions'].get('beta_unlevered', 1.0):.3f} | {sector} sector |")
        lines.append(f"| Relevered Beta | {dcf.get('beta_relevered', 'N/A')} | D/E = {wc.get('d_e_ratio', 0):.2f}x |")
        lines.append(f"| Size Premium | {dcf['assumptions'].get('size_premium', 0):.2%} | {'Applied' if dcf['assumptions'].get('size_premium', 0) > 0 else 'None'} |")
        lines.append(f"| Cost of Equity (Ke) | {dcf.get('ke', 0):.2%} | CAPM |")
        lines.append(f"| Cost of Debt (after-tax, Kd) | {dcf.get('kd', 0):.2%} | |")
        lines.append(f"| **WACC** | **{dcf.get('wacc_used', 0):.2%}** | |")
        if wc.get("sector_wacc_reference"):
            lines.append(f"| Sector WACC (reference) | {wc['sector_wacc_reference']:.2%} | Damodaran sector avg |")
        lines.append("")

        # Growth assumptions
        lines.append("### 4.2 Growth Assumptions")
        lines.append("")
        lines.append("| Stage | Years | Growth Rate | Basis |")
        lines.append("|-------|-------|-------------|-------|")
        lines.append(f"| Stage 1 | 1–5 | {dcf.get('stage1_growth', 0):.1%} | {dcf.get('growth_rationale', '')[:60]}... |")
        lines.append(f"| Stage 2 | 6–10 | {dcf.get('stage2_growth', 0):.1%} | Linear decay to terminal |")
        lines.append(f"| Terminal | 10+ | {dcf.get('terminal_growth', 0):.1%} | Capped at RF rate |")
        lines.append("")

        # Value breakdown
        lines.append("### 4.3 Valuation Summary")
        lines.append("")
        lines.append("| | Amount | % of EV |")
        lines.append("|-|--------|---------|")
        ev = dcf.get("total_ev", 0)
        pv1 = dcf.get("pv_fcf_stage1", 0)
        pv2 = dcf.get("pv_fcf_stage2", 0)
        pvtv = dcf.get("pv_terminal_value", 0)
        pct1 = pv1 / ev * 100 if ev else 0
        pct2 = pv2 / ev * 100 if ev else 0
        pcttv = pvtv / ev * 100 if ev else 0
        lines.append(f"| PV of Stage 1 FCFs (yrs 1–5) | ${pv1/1e6:,.0f}M | {pct1:.0f}% |")
        lines.append(f"| PV of Stage 2 FCFs (yrs 6–10) | ${pv2/1e6:,.0f}M | {pct2:.0f}% |")
        lines.append(f"| PV of Terminal Value | ${pvtv/1e6:,.0f}M | {pcttv:.0f}% |")
        lines.append(f"| **Enterprise Value** | **${ev/1e6:,.0f}M** | 100% |")
        lines.append(f"| Less: Net Debt | (${dcf.get('net_debt', 0)/1e6:,.0f}M) | — |")
        lines.append(f"| Equity Value | ${dcf.get('equity_value', 0)/1e6:,.0f}M | — |")
        lines.append(f"| Shares Outstanding | {dcf.get('shares', 0)/1e6:,.0f}M | — |")
        lines.append(f"| **Intrinsic Value per Share** | **${dcf.get('intrinsic_value_per_share', 0):.2f}** | — |")
        lines.append("")

        for w in dcf.get("warnings", []):
            lines.append(f"> ⚠️ {w}")
        if dcf.get("warnings"):
            lines.append("")

        # Sensitivity table
        sens = dcf.get("sensitivity_table", {})
        if sens:
            lines.append("### 4.4 Sensitivity Analysis (Intrinsic Value per Share)")
            lines.append("")
            # Build header from first row keys
            first_row = next(iter(sens.values()), {})
            g_cols = list(first_row.keys())
            header = "| WACC \\ Terminal g |" + "".join(f" {c} |" for c in g_cols)
            separator = "|---|" + "---|" * len(g_cols)
            lines.append(header)
            lines.append(separator)
            for wacc_delta, row in sens.items():
                label = f"WACC {wacc_delta}"
                row_str = "| " + label + " |"
                for g_delta in g_cols:
                    v = row.get(g_delta, "—")
                    val_str = f"${v:.0f}" if isinstance(v, (int, float)) else str(v)
                    row_str += f" {val_str} |"
                lines.append(row_str)
            lines.append("")

    # ── Relative Valuation ───────────────────────────────────────────────────
    if rel:
        lines.append("---")
        lines.append("")
        lines.append("## 5. Relative Valuation")
        lines.append("")

        vs = rel.get("vs_sector", {})
        if vs:
            lines.append("| Multiple | Company | Sector Avg | Implied Price | Premium/(Disc) |")
            lines.append("|----------|---------|------------|---------------|----------------|")
            for mult, data in vs.items():
                co = f"{data.get('company_value', 'N/A'):.1f}x" if isinstance(data.get('company_value'), float) else "N/A"
                sect = f"{data.get('sector_avg', data.get('justified_pb', 'N/A')):.1f}x" if isinstance(data.get('sector_avg', data.get('justified_pb')), float) else "N/A"
                ip = f"${data.get('implied_price', 0):.2f}"
                prem = f"{data.get('premium_pct', 0):+.1f}%" if isinstance(data.get('premium_pct'), float) else "—"
                lines.append(f"| {mult} | {co} | {sect} | {ip} | {prem} |")
        lines.append("")

        if rel.get("composite_implied_price"):
            lines.append(f"**Composite Relative Value:** ${rel['composite_implied_price']:.2f} "
                         f"(equal-weighted across {len(rel.get('multiples_used', []))} multiples)")
        if rel.get("peg_ratio"):
            lines.append(f"**PEG Ratio:** {rel['peg_ratio']:.2f}x")
        if rel.get("justified_pe"):
            lines.append(f"**Justified P/E:** {rel['justified_pe']:.1f}x (based on payout, growth, and Ke)")
        if rel.get("justified_ev_ebitda"):
            lines.append(f"**Justified EV/EBITDA:** {rel['justified_ev_ebitda']:.1f}x (based on ROIC, growth, and WACC)")
        if rel.get("ffo_per_share"):
            lines.append(f"**FFO per Share:** ${rel['ffo_per_share']:.2f}")
            lines.append(f"**AFFO per Share:** ${rel.get('affo_per_share', 0):.2f}")
        if rel.get("nav_per_share"):
            lines.append(f"**NAV per Share:** ${rel['nav_per_share']:.2f}")
            if rel.get("p_nav"):
                lines.append(f"**Price/NAV:** {rel['p_nav']:.2f}x")
        lines.append("")
        for w in rel.get("warnings", []):
            lines.append(f"> {w}")
        if rel.get("warnings"):
            lines.append("")

    # ── Asset-Based ───────────────────────────────────────────────────────────
    if asset:
        lines.append("---")
        lines.append("")
        lines.append("## 6. Asset-Based Valuation")
        lines.append("")
        if asset.get("nav_per_share") and asset.get("is_bdc"):
            # BDC-specific NAV display
            lines.append("### BDC Net Asset Value (Reported)")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| **NAV per Share** | **${asset['nav_per_share']:.2f}** |")
            if asset.get("p_nav"):
                lines.append(f"| Price/NAV | {asset['p_nav']:.2f}x |")
                p_nav = asset['p_nav']
                if p_nav > 1.05:
                    lines.append(f"| Premium to NAV | {(p_nav - 1) * 100:.1f}% |")
                elif p_nav < 0.95:
                    lines.append(f"| Discount to NAV | {(1 - p_nav) * 100:.1f}% |")
            lines.append("")
            lines.append("_BDCs report NAV quarterly (ASC 820 mark-to-market). "
                         "Premium to NAV reflects market confidence in management and portfolio quality._")
        elif asset.get("nav_per_share"):
            # REIT-specific NAV display
            lines.append("### REIT Net Asset Value (Damodaran Ch 26)")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| NOI (EBIT + D&A) | ${asset.get('noi', 0)/1e6:,.0f}M |")
            lines.append(f"| Estimated Cap Rate | {asset.get('estimated_cap_rate', 0):.2%} |")
            lines.append(f"| Implied Property Value | ${asset.get('property_value', 0)/1e6:,.0f}M |")
            lines.append(f"| Less: Net Debt | (${financials.get('net_debt', 0)/1e6:,.0f}M) |")
            lines.append(f"| **NAV per Share** | **${asset['nav_per_share']:.2f}** |")
            if asset.get("p_nav"):
                lines.append(f"| Price/NAV | {asset['p_nav']:.2f}x |")
            lines.append("")
            lines.append(f"**Book Value per Share:** ${asset.get('book_value_per_share', 0):.2f}")
        else:
            lines.append(f"**Book Value per Share:** ${asset.get('book_value_per_share', 0):.2f}")
            lines.append(f"**Liquidation Value per Share:** ${asset.get('liquidation_value_per_share', 0):.2f}")
        if asset.get("price_to_book"):
            lines.append(f"**Price-to-Book:** {asset['price_to_book']:.2f}x")
        if asset.get("replacement_note"):
            lines.append(f"\n> {asset['replacement_note']}")
        lines.append(f"\n_{asset.get('method_note', '')}_")
        lines.append("")

    # ── Contingent Claims ─────────────────────────────────────────────────────
    if contingent:
        lines.append("---")
        lines.append("")
        lines.append("## 7. Contingent Claims Valuation (Merton Model)")
        lines.append("")
        lines.append(f"**Equity Value (option model):** ${contingent.get('equity_value_per_share', 0):.2f}/share")
        lines.append(f"**Probability of Default:** {contingent.get('probability_of_default', 0):.1%}")
        lines.append(f"**Distance to Default:** {contingent.get('distance_to_default', 0):.2f}")
        inputs = contingent.get("model_inputs", {})
        if inputs:
            lines.append(f"\nModel inputs: Firm Value=${inputs.get('firm_value_S', 0)/1e6:,.0f}M, "
                         f"Debt={inputs.get('debt_face_value_K', 0)/1e6:,.0f}M, "
                         f"Maturity={inputs.get('debt_maturity_T', 0):.0f}yr, "
                         f"Asset Vol={inputs.get('asset_volatility_sigma', 0):.1%}")
        lines.append(f"\n> ⚠️ {contingent.get('caveat', '')}")
        lines.append("")

    # ── Insider Tracker Integration ───────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 8. Insider Tracker Integration")
    lines.append("")
    if insider:
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| In Universe | ✅ Yes |")
        if insider.get("conviction_score") is not None:
            lines.append(f"| Conviction Score | {insider['conviction_score']}/100 |")
        if insider.get("quality"):
            lines.append(f"| Signal Quality | {insider['quality']} |")
        lines.append(f"| Insider Cluster Detected | {'✅ Yes' if insider.get('cluster_detected') else 'No'} |")
        if insider.get("n_insiders"):
            lines.append(f"| Insiders in Cluster | {insider['n_insiders']} |")
        if insider.get("total_value"):
            lines.append(f"| Total Buy Value | ${insider['total_value']:,.0f} |")
        if insider.get("share_delta_4q") is not None:
            lines.append(f"| Share Buyback (4Q) | {insider['share_delta_4q']:.1f}% |")
        if insider.get("latest_transaction_date"):
            lines.append(f"| Last Transaction | {insider['latest_transaction_date']} |")
        if insider.get("insider_summary"):
            lines.append(f"\n_{insider['insider_summary']}_")
        lines.append("")
        # Interpretation
        if insider.get("cluster_detected") and synthesis.get("verdict") == "UNDERVALUED":
            lines.append("**Signal Alignment:** Insider buying is consistent with DCF undervaluation — strengthens the thesis. ✅")
        elif insider.get("cluster_detected") and synthesis.get("verdict") == "OVERVALUED":
            lines.append("**Signal Conflict:** Insider buying present despite DCF showing overvaluation. ⚠️ "
                         "Insiders may have information not reflected in public data, or the DCF assumptions may be conservative.")
        elif not insider.get("cluster_detected") and synthesis.get("verdict") == "UNDERVALUED":
            lines.append("**Signal Gap:** DCF shows undervaluation but no insider cluster detected. "
                         "Consider this a DCF-only thesis without the insider catalyst.")
    else:
        lines.append(f"**{ticker} is not currently tracked in the Insider Tracker universe** "
                     "(not in the ~488-company coverage list).")
    lines.append("")

    # ── Synthesis ─────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 9. Valuation Synthesis")
    lines.append("")
    if synthesis.get("components"):
        lines.append("| Method | Implied Value | Weight |")
        lines.append("|--------|--------------|--------|")
        method_labels = {
            "dcf_fcff": "DCF (FCFF)",
            "dcf_fcfe": "DCF (FCFE)",
            "relative": "Relative Valuation",
            "asset_based": "Asset-Based",
            "contingent_claims": "Contingent Claims",
        }
        all_vals = synthesis["components"]
        total_w = sum(w for _, w in all_vals)
        for val, wt in all_vals:
            pct = wt / total_w * 100
            label = "Value"
            lines.append(f"| {label} | ${val:.2f} | {pct:.0f}% |")
    lines.append("")
    lines.append(f"**Weighted Average Intrinsic Value:** ${synthesis.get('weighted_value', 0):.2f}")
    lines.append(f"**Current Price:** ${price:.2f}")
    lines.append(f"**Upside / (Downside):** {upside:+.1f}%")
    lines.append("")
    # Divergence interpretation (Damodaran Ch 34, p.937)
    div_interp = synthesis.get("divergence_interpretation", {})
    div_signal = div_interp.get("signal", "")
    if div_signal and div_signal != "insufficient_data":
        signal_labels = {
            "strong_buy": "🟢 **Strong Buy Signal**",
            "avoid": "🔴 **Avoid**",
            "sector_overvalued": "⚠️ **Sector Overvalued**",
            "sector_undervalued": "⚠️ **Sector Undervalued**",
            "neutral": "🟡 **Neutral**",
        }
        lines.append(f"**DCF vs Relative Divergence:** {signal_labels.get(div_signal, div_signal)}")
        lines.append(f"> {div_interp.get('explanation', '')}")
        lines.append("")

    if synthesis.get("divergence_pct", 0) > 30:
        lines.append(f"> ⚠️ DCF and relative valuation diverge by {synthesis['divergence_pct']:.0f}%. "
                     "Investigate model inputs before acting on synthesis value.")
    lines.append("")

    # ── Red Flags ─────────────────────────────────────────────────────────────
    if red_flags:
        lines.append("---")
        lines.append("")
        lines.append("## 10. Risk Flags")
        lines.append("")
        for flag in red_flags:
            lines.append(f"- ⚠️ {flag}")
        lines.append("")

    # ── Assumptions & Caveats ─────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 11. Key Assumptions & Caveats")
    lines.append("")
    rf = sector_data.get("rf", 0.043)
    erp = sector_data.get("erp", 0.055)
    lines.append(f"1. WACC uses Damodaran sector beta and implied ERP ({erp:.2%}) as of {date}")
    lines.append(f"2. Risk-free rate: {rf:.2%} (10Y US Treasury)")
    if dcf:
        lines.append(f"3. Terminal growth rate: {dcf.get('terminal_growth', 0):.2%} — highly sensitive assumption")
        lines.append(f"4. Base FCF: ${dcf.get('base_fcf', 0)/1e6:,.0f}M — verify against reported cash flow statement")
    lines.append("5. This model does not capture qualitative factors: management quality, "
                 "competitive moat, regulatory risk, ESG considerations")
    lines.append("6. Financial data sourced from FMP/yfinance and may contain errors — "
                 "always cross-check against primary filings")
    lines.append("7. **This is NOT investment advice.** For informational purposes only.")
    lines.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append(f"*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append(f"*Data sources: FMP, yfinance, SEC EDGAR, Damodaran NYU datasets*")
    lines.append(f"*Methodology: Damodaran \"Investment Valuation\" (3rd Ed.)*")

    return "\n".join(lines)


# ── Telegram Summary ─────────────────────────────────────────────────────────

def _build_telegram_summary(r: dict) -> str:
    ticker = r["ticker"]
    profile = r.get("profile", {})
    dcf = r.get("dcf_result")
    rel = r.get("relative_result")
    insider = r.get("insider_signal")
    synthesis = r.get("synthesis", {})
    decision = r.get("decision", {})
    red_flags = r.get("red_flags", [])

    name = profile.get("name", ticker)
    price = profile.get("current_price", 0)
    sector = profile.get("sector", "N/A")
    verdict = synthesis.get("verdict", "N/A")
    weighted = synthesis.get("weighted_value", 0)
    upside = synthesis.get("upside_pct", 0)
    method = decision.get("primary_method", "N/A")

    verdict_emoji = {"UNDERVALUED": "🟢", "OVERVALUED": "🔴", "FAIRLY VALUED": "🟡"}.get(verdict, "⚪")

    lines = []
    lines.append(f"📊 *Valuation Report: {name} ({ticker})*")
    lines.append(f"Current: ${price:.2f} | Sector: {sector}")
    lines.append("")
    lines.append(f"{verdict_emoji} *Verdict: {verdict}*")
    lines.append(f"Intrinsic Value: *${weighted:.2f}* ({upside:+.1f}% vs current price)")
    lines.append(f"Method: {method}")
    lines.append("")

    if dcf:
        lines.append(f"📉 *DCF ({method.upper()}):* ${dcf.get('intrinsic_value_per_share', 0):.2f}/share")
        lines.append(f"WACC: {dcf.get('wacc_used', 0):.1%} | Stage 1 growth: {dcf.get('stage1_growth', 0):.1%} | Terminal: {dcf.get('terminal_growth', 0):.1%}")
        if dcf.get("terminal_value_pct", 0) > 80:
            lines.append(f"⚠️ Terminal value = {dcf.get('terminal_value_pct', 0):.0f}% of total EV (high sensitivity)")

    if rel and rel.get("composite_implied_price"):
        lines.append(f"\n📊 *Relative Valuation:* ${rel['composite_implied_price']:.2f}/share")
        mults = rel.get("multiples_used", [])
        if mults:
            lines.append(f"Multiples used: {', '.join(mults)}")

    if insider:
        lines.append(f"\n🔍 *Insider Tracker:*")
        if insider.get("conviction_score") is not None:
            lines.append(f"Conviction: {insider['conviction_score']}/100 ({insider.get('quality', 'N/A')})")
        if insider.get("cluster_detected"):
            lines.append(f"✅ Insider cluster: {insider.get('n_insiders', 0)} insiders, ${insider.get('total_value', 0):,.0f} bought")
        if insider.get("share_delta_4q", 0) < -1:
            lines.append(f"📉 Buyback: {insider['share_delta_4q']:.1f}% share reduction (4Q)")
    else:
        lines.append(f"\n🔍 Not in Insider Tracker universe")

    # Divergence interpretation
    div_interp = synthesis.get("divergence_interpretation", {})
    div_signal = div_interp.get("signal", "")
    if div_signal and div_signal not in ("insufficient_data", "neutral"):
        signal_short = {
            "strong_buy": "🟢 Both DCF & sector say undervalued — strongest signal",
            "avoid": "🔴 Both DCF & sector say overvalued — avoid",
            "sector_overvalued": "⚠️ Sector appears overvalued (DCF↑ vs Relative↓)",
            "sector_undervalued": "⚠️ Sector appears undervalued (DCF↓ vs Relative↑)",
        }
        lines.append(f"\n{signal_short.get(div_signal, '')}")

    if red_flags:
        lines.append(f"\n⚠️ *Risk Flags ({len(red_flags)}):*")
        for flag in red_flags[:3]:
            lines.append(f"• {flag[:80]}{'...' if len(flag) > 80 else ''}")

    lines.append(f"\n📄 Full report saved to: output/reports/{ticker}_{datetime.now().strftime('%Y-%m-%d')}.md")
    lines.append(f"\n_Not investment advice. Run: python run_valuation.py {ticker}_")

    return "\n".join(lines)
