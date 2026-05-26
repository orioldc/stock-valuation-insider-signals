"""Conviction Scorer — combines multiple signals into a 0-100 score."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signals.sweet_spot_filter import classify_cluster
from signals.historical_hit_rate import compute_hit_rates, get_sector_hit_rates

# Cache these at module level
_hit_rates = None
_sector_rates = None


def _get_hit_rates():
    global _hit_rates
    if _hit_rates is None:
        _hit_rates = compute_hit_rates()
    return _hit_rates


def _get_sector_rates():
    global _sector_rates
    if _sector_rates is None:
        _sector_rates = get_sector_hit_rates()
    return _sector_rates


def score_signal(row):
    """Score a signal row (merged latest_signals + company data).
    
    Returns dict with:
        total: 0-100
        cluster_quality: 0-30
        buyback_intensity: 0-20
        sector_favorability: 0-15
        historical_accuracy: 0-20
        seniority: 0-15
        breakdown: dict of component details
    """
    result = {"total": 0, "cluster_quality": 0, "buyback_intensity": 0,
              "sector_favorability": 0, "historical_accuracy": 0, "seniority": 0,
              "breakdown": {}}
    
    ticker = row.get("ticker", "")
    
    # 1. Cluster quality (0-30)
    quality, sweet_spot, reason = classify_cluster(row)
    if quality == "Premium":
        result["cluster_quality"] = 30
    elif quality == "Standard":
        result["cluster_quality"] = 18
    else:
        # Still give some points if cluster detected
        cluster_detected = row.get("cluster_detected", False)
        result["cluster_quality"] = 8 if cluster_detected else 0
    result["breakdown"]["quality"] = quality
    result["breakdown"]["sweet_spot"] = sweet_spot
    result["breakdown"]["sweet_spot_reason"] = reason
    
    # 2. Buyback intensity (0-20) — based on share_delta_4q
    delta_4q = row.get("share_delta_4q") or 0
    if delta_4q < -5:
        result["buyback_intensity"] = 20
    elif delta_4q < -3:
        result["buyback_intensity"] = 15
    elif delta_4q < -1:
        result["buyback_intensity"] = 10
    elif delta_4q < 0:
        result["buyback_intensity"] = 5
    else:
        result["buyback_intensity"] = 0
    
    # 3. Sector favorability (0-15)
    sector = row.get("sector", "Unknown")
    sector_rates = _get_sector_rates()
    if sector in sector_rates:
        avg_excess = sector_rates[sector]["avg_excess_12m"]
        if avg_excess > 10:
            result["sector_favorability"] = 15
        elif avg_excess > 5:
            result["sector_favorability"] = 12
        elif avg_excess > 0:
            result["sector_favorability"] = 8
        elif avg_excess > -5:
            result["sector_favorability"] = 4
        else:
            result["sector_favorability"] = 0
        result["breakdown"]["sector_avg_excess"] = avg_excess
    else:
        result["sector_favorability"] = 7  # neutral
    
    # 4. Historical insider accuracy (0-20)
    hit_rates = _get_hit_rates()
    if ticker in hit_rates:
        hr = hit_rates[ticker]
        result["breakdown"]["insider_history"] = hr["summary"]
        if hr["n_clusters"] >= 3:
            wr = hr["win_rate"]
            result["historical_accuracy"] = min(20, int(wr * 20))
        elif hr["n_clusters"] >= 1:
            result["historical_accuracy"] = min(10, int(hr["win_rate"] * 10))
    
    # 5. Seniority (0-15)
    has_ceo = bool(row.get("has_ceo", False))
    has_officer = bool(row.get("has_officer", False))
    if has_ceo:
        result["seniority"] = 15
    elif has_officer:
        result["seniority"] = 10
    else:
        result["seniority"] = 3  # director-only gets some credit
    
    result["total"] = (result["cluster_quality"] + result["buyback_intensity"] +
                       result["sector_favorability"] + result["historical_accuracy"] +
                       result["seniority"])
    
    return result


def score_dataframe(df):
    """Add conviction score columns to a DataFrame."""
    scores = df.apply(score_signal, axis=1)
    df = df.copy()
    df["conviction_score"] = scores.apply(lambda x: x["total"])
    df["conv_cluster_quality"] = scores.apply(lambda x: x["cluster_quality"])
    df["conv_buyback"] = scores.apply(lambda x: x["buyback_intensity"])
    df["conv_sector"] = scores.apply(lambda x: x["sector_favorability"])
    df["conv_history"] = scores.apply(lambda x: x["historical_accuracy"])
    df["conv_seniority"] = scores.apply(lambda x: x["seniority"])
    df["quality"] = scores.apply(lambda x: x["breakdown"].get("quality", ""))
    df["sweet_spot"] = scores.apply(lambda x: x["breakdown"].get("sweet_spot", False))
    df["sweet_spot_reason"] = scores.apply(lambda x: x["breakdown"].get("sweet_spot_reason", ""))
    df["insider_history_summary"] = scores.apply(lambda x: x["breakdown"].get("insider_history", ""))
    return df
