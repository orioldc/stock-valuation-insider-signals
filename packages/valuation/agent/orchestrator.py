"""
Orchestrator — main valuation pipeline.

Calls all modules in sequence and returns a consolidated results dict
and saves the markdown report.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Ensure imports work when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.company_profile import get_profile
from data.financials import get_ttm_financials
from data.damodaran_data import get_sector_data
from data.insider_signals import get_signal_for_ticker
from agent.decision_tree import classify_company, check_red_flags
from agent.report_generator import generate_report, generate_telegram_summary


def run_valuation(
    ticker: str,
    output_dir: str = None,
    telegram_format: bool = False,
    no_cache: bool = False,
) -> dict:
    """
    Run a full Damodaran-style valuation for a ticker.

    Args:
        ticker: Stock symbol (e.g. 'AAPL')
        output_dir: Directory to save report (default: ./output/reports/)
        telegram_format: If True, also generate condensed Telegram summary
        no_cache: If True, bypass all caches and fetch fresh data

    Returns dict with:
        ticker, report_path, summary (if telegram_format),
        intrinsic_value, current_price, upside_pct,
        method_used, verdict, all_results
    """
    ticker = ticker.upper()
    print(f"\n{'='*60}")
    print(f"  Damodaran Valuation Agent — {ticker}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    results = {"ticker": ticker, "errors": []}

    # ── Step 1: Company Profile ───────────────────────────────────────────────
    print(f"\n[1/6] Fetching company profile...")
    try:
        profile = get_profile(ticker, use_cache=not no_cache)
        results["profile"] = profile
        print(f"      {profile['name']} | {profile['sector']} | {profile.get('market_cap_label','')}")
    except Exception as e:
        print(f"      ERROR: {e}")
        results["errors"].append(f"Profile fetch failed: {e}")
        return results

    # ── Step 2: Financial Data ────────────────────────────────────────────────
    print(f"\n[2/6] Fetching financial data...")
    try:
        financials = get_ttm_financials(ticker)
        results["financials"] = financials
        rev = financials.get("revenue_ttm", 0)
        ebit = financials.get("ebit_ttm", 0)
        print(f"      Revenue: ${rev/1e9:.2f}B | EBIT: ${ebit/1e6:.0f}M | Source: {financials.get('source','?')}")
    except Exception as e:
        print(f"      ERROR: {e}")
        results["errors"].append(f"Financials fetch failed: {e}")
        return results

    # ── Step 3: Damodaran Sector Data ─────────────────────────────────────────
    print(f"\n[3/6] Fetching Damodaran sector benchmarks...")
    try:
        sector_data = get_sector_data(
            profile.get("sector", ""),
            profile.get("industry", ""),
            force_refresh=no_cache,
        )
        results["sector_data"] = sector_data
        print(f"      Sector WACC: {sector_data.get('wacc', 'N/A')} | "
              f"RF: {sector_data.get('rf', 'N/A'):.2%} | "
              f"ERP: {sector_data.get('erp', 'N/A'):.2%}")
    except Exception as e:
        print(f"      WARNING: Sector data fetch failed ({e}), using defaults")
        sector_data = {
            "wacc": None, "beta_unlevered": 1.0,
            "pe_sector": None, "ev_ebitda_sector": None,
            "ev_sales_sector": None, "roe_sector": None,
            "net_margin_sector": None,
            "erp": 0.055, "rf": 0.043,
        }
        results["sector_data"] = sector_data
        results["errors"].append(f"Sector data degraded: {e}")

    # ── Step 4: Insider Signal ────────────────────────────────────────────────
    print(f"\n[4/6] Reading insider tracker signals...")
    try:
        insider_signal = get_signal_for_ticker(ticker, use_cache=not no_cache)
        results["insider_signal"] = insider_signal
        if insider_signal:
            print(f"      Conviction: {insider_signal.get('conviction_score','N/A')} | "
                  f"Quality: {insider_signal.get('quality','N/A')} | "
                  f"Cluster: {insider_signal.get('cluster_detected', False)}")
        else:
            print(f"      {ticker} not in insider-tracker universe")
    except Exception as e:
        print(f"      WARNING: Insider signal fetch failed ({e})")
        insider_signal = None
        results["insider_signal"] = None

    # ── Step 5: Decision Tree + Valuation ─────────────────────────────────────
    print(f"\n[5/6] Running decision tree and valuation models...")
    try:
        decision = classify_company(profile, financials)
        results["decision"] = decision
        print(f"      Primary method: {decision['primary_method']}")
        print(f"      Secondary: {decision['secondary_methods']}")
        print(f"      Rationale: {decision['rationale'][:80]}...")

        # Red flags
        red_flags = check_red_flags(profile, financials, insider_signal)
        results["red_flags"] = red_flags

        # Run primary model
        dcf_result = None
        relative_result = None
        asset_result = None
        contingent_result = None

        primary = decision["primary_method"]
        secondary = decision["secondary_methods"]
        all_methods = [primary] + secondary

        for method in all_methods:
            if method in ("dcf_fcff", "dcf_fcfe", "dcf_normalized"):
                if dcf_result is None:
                    from valuation.dcf import run_dcf
                    fcf_method = "fcfe" if method == "dcf_fcfe" else "fcff"
                    normalization = decision.get("normalization_required", False) or method == "dcf_normalized"
                    dcf_result = run_dcf(
                        profile, financials, sector_data,
                        method=fcf_method,
                        normalization_required=normalization,
                    )
                    iv = dcf_result.get("intrinsic_value_per_share", 0)
                    print(f"      DCF ({fcf_method.upper()}): intrinsic = ${iv:.2f}")

            elif method == "relative":
                if relative_result is None:
                    from valuation.relative import run_relative_valuation
                    relative_result = run_relative_valuation(profile, financials, sector_data, decision)
                    comp = relative_result.get("composite_implied_price")
                    print(f"      Relative: composite = ${comp:.2f}" if comp else "      Relative: no multiples available")

            elif method == "asset_based":
                if asset_result is None:
                    from valuation.asset_based import run_asset_based
                    asset_result = run_asset_based(profile, financials, decision)
                    if asset_result.get("nav_per_share"):
                        nav = asset_result["nav_per_share"]
                        label = "BDC reported NAV" if asset_result.get("is_bdc") else "REIT cap rate approach"
                        print(f"      Asset-based: NAV = ${nav:.2f}/share ({label})")
                    else:
                        bv = asset_result.get("book_value_per_share", 0)
                        print(f"      Asset-based: book value = ${bv:.2f}/share")

            elif method == "contingent_claims":
                if contingent_result is None:
                    from valuation.contingent_claims import run_contingent_claims
                    contingent_result = run_contingent_claims(profile, financials)
                    ev = contingent_result.get("equity_value_per_share", 0)
                    pod = contingent_result.get("probability_of_default", 0)
                    print(f"      Contingent claims: equity = ${ev:.2f}/share | P(default)={pod:.1%}")

        results["dcf_result"] = dcf_result
        results["relative_result"] = relative_result
        results["asset_result"] = asset_result
        results["contingent_result"] = contingent_result

        # Damodaran Ch 12, p.319: Survival adjustment for distressed firms
        # Adjusted = DCF × (1 - P_distress) + Distressed_sale × P_distress
        # Ch 21: Do NOT apply to financial firms — their high leverage and
        # low EBIT/Interest are structural (debt is raw material, not financing).
        is_financial_firm = decision.get("is_financial", False)
        if decision.get("distress_risk") and dcf_result and not is_financial_firm:
            dcf_result = _apply_survival_adjustment(
                dcf_result, asset_result, financials, profile
            )
            results["dcf_result"] = dcf_result

        # Weighted synthesis
        synthesis = _synthesize(
            primary, dcf_result, relative_result, asset_result, contingent_result,
            profile.get("current_price", 0), decision=decision,
        )
        results["synthesis"] = synthesis
        print(f"\n      Synthesized intrinsic: ${synthesis['weighted_value']:.2f} | "
              f"Upside: {synthesis['upside_pct']:.1f}% | Verdict: {synthesis['verdict']}")

    except Exception as e:
        import traceback
        print(f"      ERROR in valuation: {e}")
        traceback.print_exc()
        results["errors"].append(f"Valuation failed: {e}")
        results.setdefault("decision", {})
        results.setdefault("red_flags", [])
        results.setdefault("synthesis", {"weighted_value": 0, "upside_pct": 0, "verdict": "ERROR"})

    # ── Step 6: Generate Report ───────────────────────────────────────────────
    print(f"\n[6/6] Generating report...")
    try:
        if output_dir is None:
            output_dir = str(Path(__file__).resolve().parent.parent / "output" / "reports")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        report_path = generate_report(results, output_dir)
        results["report_path"] = report_path
        print(f"      Report saved: {report_path}")

        if telegram_format:
            summary = generate_telegram_summary(results)
            results["telegram_summary"] = summary

    except Exception as e:
        print(f"      ERROR generating report: {e}")
        results["errors"].append(f"Report generation failed: {e}")

    print(f"\n{'='*60}")
    print(f"  Done. Report: {results.get('report_path', 'N/A')}")
    if results["errors"]:
        print(f"  Warnings/Errors: {len(results['errors'])}")
        for err in results["errors"]:
            print(f"    - {err}")
    print(f"{'='*60}\n")

    return results


def _synthesize(
    primary_method: str,
    dcf_result: dict | None,
    relative_result: dict | None,
    asset_result: dict | None,
    contingent_result: dict | None,
    current_price: float,
    decision: dict | None = None,
) -> dict:
    """
    Weighted average of available valuation results to get a final view.
    Weights depend on which method is primary.
    """
    values_and_weights = []

    def _pos(val) -> bool:
        """Return True only if val is a positive number (not None)."""
        return isinstance(val, (int, float)) and val > 0

    # Assign weights based on primary method
    # Special cases: BDCs and REITs with dcf_fcfe have 3 methods
    is_bdc = (decision or {}).get("is_bdc", False)
    is_reit_dcf = (
        primary_method == "dcf_fcfe"
        and asset_result is not None
        and asset_result.get("nav_per_share")
        and not is_bdc
    )

    if is_bdc and primary_method == "dcf_fcfe":
        # BDC 3-method synthesis: DDM 45%, Relative (P/NAV + P/E) 30%, Reported NAV 25%
        if dcf_result and _pos(dcf_result.get("intrinsic_value_per_share")):
            values_and_weights.append((dcf_result["intrinsic_value_per_share"], 0.45))
        if relative_result and _pos(relative_result.get("composite_implied_price")):
            values_and_weights.append((relative_result["composite_implied_price"], 0.30))
        if asset_result and _pos(asset_result.get("nav_per_share")):
            values_and_weights.append((asset_result["nav_per_share"], 0.25))
    elif is_reit_dcf:
        # REIT 3-method synthesis: DDM 40%, P/FFO 35%, NAV 25%
        if dcf_result and _pos(dcf_result.get("intrinsic_value_per_share")):
            values_and_weights.append((dcf_result["intrinsic_value_per_share"], 0.40))
        if relative_result and _pos(relative_result.get("composite_implied_price")):
            values_and_weights.append((relative_result["composite_implied_price"], 0.35))
        if _pos(asset_result.get("nav_per_share")):
            values_and_weights.append((asset_result["nav_per_share"], 0.25))
    elif primary_method in ("dcf_fcff", "dcf_fcfe", "dcf_normalized"):
        if dcf_result and _pos(dcf_result.get("intrinsic_value_per_share")):
            values_and_weights.append((dcf_result["intrinsic_value_per_share"], 0.60))
        if relative_result and _pos(relative_result.get("composite_implied_price")):
            values_and_weights.append((relative_result["composite_implied_price"], 0.40))

    elif primary_method == "relative":
        if relative_result and _pos(relative_result.get("composite_implied_price")):
            values_and_weights.append((relative_result["composite_implied_price"], 0.60))
        if dcf_result and _pos(dcf_result.get("intrinsic_value_per_share")):
            values_and_weights.append((dcf_result["intrinsic_value_per_share"], 0.40))

    elif primary_method == "asset_based":
        if asset_result and _pos(asset_result.get("book_value_per_share")):
            values_and_weights.append((asset_result["book_value_per_share"], 0.50))
        if dcf_result and _pos(dcf_result.get("intrinsic_value_per_share")):
            values_and_weights.append((dcf_result["intrinsic_value_per_share"], 0.30))
        if relative_result and _pos(relative_result.get("composite_implied_price")):
            values_and_weights.append((relative_result["composite_implied_price"], 0.20))

    elif primary_method == "contingent_claims":
        if contingent_result and _pos(contingent_result.get("equity_value_per_share")):
            values_and_weights.append((contingent_result["equity_value_per_share"], 0.70))
        if dcf_result and _pos(dcf_result.get("intrinsic_value_per_share")):
            values_and_weights.append((dcf_result["intrinsic_value_per_share"], 0.30))

    if not values_and_weights:
        return {"weighted_value": 0, "upside_pct": 0, "verdict": "INSUFFICIENT DATA"}

    # Normalize weights
    total_w = sum(w for _, w in values_and_weights)
    weighted_value = sum(v * (w / total_w) for v, w in values_and_weights)

    # Divergence check
    if len(values_and_weights) == 2:
        v1, v2 = values_and_weights[0][0], values_and_weights[1][0]
        divergence = abs(v1 - v2) / max(v1, v2, 1) * 100
    else:
        divergence = 0.0

    # DCF vs Relative divergence interpretation (Damodaran Ch 34, p.937)
    # The two methods answer different questions: DCF = absolute fundamental value,
    # Relative = value vs sector peers. Divergence reveals sector mispricing.
    divergence_interpretation = _interpret_divergence(
        dcf_result, relative_result, current_price
    )

    # Verdict
    if current_price > 0 and weighted_value > 0:
        upside_pct = (weighted_value - current_price) / current_price * 100
    else:
        upside_pct = 0.0

    if upside_pct > 20:
        verdict = "UNDERVALUED"
    elif upside_pct < -15:
        verdict = "OVERVALUED"
    else:
        verdict = "FAIRLY VALUED"

    return {
        "weighted_value": round(weighted_value, 2),
        "upside_pct": round(upside_pct, 1),
        "verdict": verdict,
        "divergence_pct": round(divergence, 1),
        "divergence_interpretation": divergence_interpretation,
        "components": values_and_weights,
    }


def _interpret_divergence(
    dcf_result: dict | None,
    relative_result: dict | None,
    current_price: float,
) -> dict:
    """
    Interpret DCF vs Relative divergence per Damodaran Ch 34, p.937.

    "Can a Firm Be Undervalued and Overvalued at the Same Time? Yes."
    DCF answers: is the stock undervalued on an absolute/fundamental basis?
    Relative answers: is the stock undervalued vs its sector peers?
    When they diverge, it reveals sector-level mispricing.
    """
    if not current_price or current_price <= 0:
        return {"signal": "insufficient_data", "explanation": "No current price available."}

    dcf_iv = (dcf_result or {}).get("intrinsic_value_per_share")
    rel_iv = (relative_result or {}).get("composite_implied_price")

    if not dcf_iv or not rel_iv or dcf_iv <= 0 or rel_iv <= 0:
        return {"signal": "insufficient_data", "explanation": "Need both DCF and Relative results to interpret divergence."}

    # Threshold: >15% above price = undervalued, >15% below = overvalued
    dcf_upside = (dcf_iv - current_price) / current_price
    rel_upside = (rel_iv - current_price) / current_price

    dcf_under = dcf_upside > 0.15
    dcf_over = dcf_upside < -0.15
    rel_under = rel_upside > 0.15
    rel_over = rel_upside < -0.15

    if dcf_under and rel_under:
        return {
            "signal": "strong_buy",
            "explanation": (
                "Undervalued on BOTH fundamentals (DCF) and relative to sector peers. "
                "Strongest buy signal — benefits from market corrections both across "
                "time (DCF) and across companies (relative). (Damodaran p.937)"
            ),
        }
    elif dcf_over and rel_over:
        return {
            "signal": "avoid",
            "explanation": (
                "Overvalued on BOTH fundamentals (DCF) and relative to sector peers. "
                "Avoid — no valuation support on either dimension. (Damodaran p.937)"
            ),
        }
    elif dcf_over and rel_under:
        return {
            "signal": "sector_overvalued",
            "explanation": (
                "DCF says overvalued but cheaper than sector peers. This suggests the "
                "entire sector is overvalued. The stock is overvalued on fundamentals "
                "but even more so than its peers. (Damodaran p.937)"
            ),
        }
    elif dcf_under and rel_over:
        return {
            "signal": "sector_undervalued",
            "explanation": (
                "DCF says undervalued but expensive vs sector peers. This suggests the "
                "sector as a whole is undervalued. The stock is cheap on fundamentals "
                "but its sector is even cheaper. (Damodaran p.937)"
            ),
        }
    else:
        # Both are in the "fairly valued" zone
        return {
            "signal": "neutral",
            "explanation": (
                "DCF and Relative valuations are broadly aligned near current price. "
                "No strong signal from divergence analysis."
            ),
        }


def _apply_survival_adjustment(
    dcf_result: dict,
    asset_result: dict | None,
    financials: dict,
    profile: dict,
) -> dict:
    """
    Damodaran Ch 12, p.319: Adjust DCF value for probability of distress.

    Adjusted = DCF_value × (1 - P_distress) + Distressed_sale_value × P_distress

    Uses a simple heuristic for P(distress) based on interest coverage and
    leverage. Distressed sale value is estimated as book value × liquidation discount.
    """
    iv = dcf_result.get("intrinsic_value_per_share", 0)
    if iv <= 0:
        return dcf_result

    # Estimate probability of distress using Damodaran Ch 15, Table 15.2
    # Uses synthetic rating from interest coverage → P(Default)
    from valuation.dcf import _synthetic_rating, _rating_to_p_default

    ebit = financials.get("ebit_ttm", 0)
    interest = financials.get("interest_expense_ttm", 0)

    interest_cover = ebit / interest if interest > 0 else float("inf")
    rating, _spread = _synthetic_rating(interest_cover)
    p_distress = _rating_to_p_default(rating)

    # Distressed sale value: book value × liquidation discount (typically 50-70%)
    if asset_result and asset_result.get("book_value_per_share", 0) > 0:
        distressed_sale = asset_result["book_value_per_share"] * 0.50
    else:
        # Rough fallback: 30% of DCF value
        distressed_sale = iv * 0.30

    adjusted_iv = iv * (1 - p_distress) + distressed_sale * p_distress

    # Update the DCF result with adjustment info
    adjusted_result = dcf_result.copy()
    adjusted_result["intrinsic_value_per_share"] = round(adjusted_iv, 2)
    adjusted_result["survival_adjustment"] = {
        "unadjusted_iv": round(iv, 2),
        "adjusted_iv": round(adjusted_iv, 2),
        "p_distress": round(p_distress, 2),
        "distressed_sale_value": round(distressed_sale, 2),
        "formula": "Adjusted = DCF × (1 - P_distress) + Distressed_sale × P_distress",
    }
    adjusted_result["warnings"] = dcf_result.get("warnings", []) + [
        f"Survival adjustment applied: P(distress)={p_distress:.0%}, "
        f"IV reduced from ${iv:.2f} to ${adjusted_iv:.2f} (Damodaran Ch 12, p.319)"
    ]
    print(f"      Survival adjustment: P(distress)={p_distress:.0%}, "
          f"IV: ${iv:.2f} → ${adjusted_iv:.2f}")

    return adjusted_result
