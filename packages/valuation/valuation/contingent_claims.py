"""
Contingent Claims Valuation — Black-Scholes equity option model.

Reference: Damodaran "Investment Valuation" Chapters 28-30.
Applied to:
  - Distressed firms (equity as call option on firm value)
  - Pre-revenue firms (option to expand)
"""

import math
from scipy.stats import norm


def run_contingent_claims(profile: dict, financials: dict) -> dict:
    """
    Model equity as a call option on the firm's assets (Merton model).

    For distressed firms:
        S = Current firm value (EV)
        K = Face value of total debt
        T = Average debt maturity (approximated)
        sigma = Asset volatility (estimated from equity volatility + leverage)
        r = risk-free rate

    Returns dict with:
        equity_value_per_share, probability_of_default,
        distance_to_default, model_inputs, caveat, warnings
    """
    warnings_list = []
    shares = profile.get("shares_outstanding") or financials.get("shares_outstanding", 1)
    current_price = profile.get("current_price", 0)
    market_cap = profile.get("market_cap", 0)
    net_debt = financials.get("net_debt", 0)
    total_debt = financials.get("total_debt", 0)

    # Firm value S = market cap + total debt (enterprise value)
    S = market_cap + total_debt
    K = total_debt  # strike = face value of debt

    if K <= 0:
        return {
            "equity_value_per_share": current_price,
            "probability_of_default": 0.0,
            "distance_to_default": float("inf"),
            "model_inputs": {},
            "caveat": "No debt outstanding — contingent claims model not applicable.",
            "warnings": ["No debt: standard DCF or relative valuation is more appropriate."],
        }

    if S <= 0:
        warnings_list.append("Firm value (EV) is zero or negative — extreme distress scenario.")
        S = max(S, 1)

    # Risk-free rate
    try:
        from data.damodaran_data import get_risk_free_rate
        r = get_risk_free_rate()
    except Exception:
        r = 0.043

    # Debt maturity: typical corporate average ~5 years; adjust if leverage is extreme
    if total_debt / max(S, 1) > 0.7:
        T = 3.0  # high leverage → shorter effective maturity
        warnings_list.append("High leverage: using 3-year effective debt maturity.")
    else:
        T = 5.0

    # Asset volatility: estimated from equity volatility + leverage (Leland approach)
    # sigma_equity ≈ from beta × market vol; sigma_asset < sigma_equity
    beta = profile.get("beta", 1.0)
    market_annual_vol = 0.18  # approximate S&P 500 annual vol
    sigma_equity = beta * market_annual_vol * 1.2  # company-specific premium
    # Asset vol = equity vol × (E/V) as approximation
    e_v_ratio = market_cap / S if S > 0 else 0.5
    sigma_asset = sigma_equity * e_v_ratio
    sigma_asset = max(sigma_asset, 0.10)  # floor at 10%
    sigma_asset = min(sigma_asset, 0.80)  # cap at 80%

    # Black-Scholes option pricing
    d1 = (math.log(S / K) + (r + 0.5 * sigma_asset ** 2) * T) / (sigma_asset * math.sqrt(T))
    d2 = d1 - sigma_asset * math.sqrt(T)

    equity_value = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    probability_of_default = 1 - norm.cdf(d2)
    distance_to_default = d2

    equity_per_share = equity_value / shares if shares > 0 else 0

    # Upside vs current price
    upside_pct = 0.0
    if current_price > 0 and equity_per_share > 0:
        upside_pct = (equity_per_share - current_price) / current_price * 100

    if probability_of_default > 0.30:
        warnings_list.append(
            f"Probability of default is high ({probability_of_default:.1%}). "
            "Standard valuation models are unreliable. Consider distressed analysis."
        )

    return {
        "equity_value_per_share": round(equity_per_share, 2),
        "current_price": round(current_price, 2),
        "upside_pct": round(upside_pct, 1),
        "probability_of_default": round(probability_of_default, 4),
        "distance_to_default": round(distance_to_default, 3),
        "model_inputs": {
            "firm_value_S": round(S, 0),
            "debt_face_value_K": round(K, 0),
            "debt_maturity_T": T,
            "asset_volatility_sigma": round(sigma_asset, 4),
            "risk_free_rate": round(r, 4),
        },
        "upside_pct": round(upside_pct, 1),
        "caveat": (
            "IMPORTANT: This is a structural (Merton) model for distressed equity. "
            "It is highly sensitive to asset volatility assumptions. "
            "Use as a cross-check only — not as a primary valuation. "
            "Seek specialist distressed debt analysis for investment decisions."
        ),
        "warnings": warnings_list,
    }
