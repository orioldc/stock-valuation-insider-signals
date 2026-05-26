"""Sweet Spot Filter — flags clusters matching historically proven winning patterns."""

import pandas as pd

EXCLUDED_SECTORS = {"Real Estate", "Utilities", "Consumer Defensive", "REITs"}

def classify_cluster(row):
    """Classify a cluster row (from historical_clusters or latest_signals merged with company data).
    
    Expects: num_insiders (or cluster_details), total_value, sector, has_officer, has_ceo
    Returns: (quality, sweet_spot, reason)
        quality: "Premium", "Standard", "Speculative"
        sweet_spot: bool
        reason: str explanation
    """
    reasons = []
    score = 0  # 0-5 scale for sweet spot matching
    
    n_insiders = row.get("num_insiders") or row.get("cluster_details") or 0
    total_value = row.get("total_value") or 0
    sector = row.get("sector") or "Unknown"
    has_officer = bool(row.get("has_officer", False))
    has_ceo = bool(row.get("has_ceo", False))
    
    # 1. Insider count: 2-3 is sweet spot
    if 2 <= n_insiders <= 3:
        score += 1
        reasons.append(f"{n_insiders} insiders (sweet spot)")
    elif n_insiders >= 2:
        score += 0.5
        reasons.append(f"{n_insiders} insiders (acceptable)")
    else:
        reasons.append(f"Only {n_insiders} insider(s)")
    
    # 2. Total value: $500K-2M sweet spot
    if 500_000 <= total_value <= 2_000_000:
        score += 1
        reasons.append(f"${total_value:,.0f} value (sweet spot)")
    elif 100_000 <= total_value <= 5_000_000:
        score += 0.5
        reasons.append(f"${total_value:,.0f} value (acceptable)")
    elif total_value > 0:
        reasons.append(f"${total_value:,.0f} value (outside sweet spot)")
    
    # 3. Sector exclusion
    if sector in EXCLUDED_SECTORS:
        score -= 1
        reasons.append(f"{sector} (historically underperforms)")
    else:
        score += 1
        reasons.append(f"{sector} sector OK")
    
    # 4. Officer/CEO involvement
    if has_ceo:
        score += 1.5
        reasons.append("CEO involved ✓")
    elif has_officer:
        score += 1
        reasons.append("Officer involved ✓")
    else:
        reasons.append("No officer/CEO")
    
    # Classify
    if score >= 3.5:
        quality = "Premium"
        sweet_spot = True
    elif score >= 2:
        quality = "Standard"
        sweet_spot = False
    else:
        quality = "Speculative"
        sweet_spot = False
    
    return quality, sweet_spot, "; ".join(reasons)


def classify_dataframe(df):
    """Add sweet_spot, quality, and sweet_spot_reason columns to a DataFrame."""
    results = df.apply(classify_cluster, axis=1)
    df = df.copy()
    df["quality"] = results.apply(lambda x: x[0])
    df["sweet_spot"] = results.apply(lambda x: x[1])
    df["sweet_spot_reason"] = results.apply(lambda x: x[2])
    return df
