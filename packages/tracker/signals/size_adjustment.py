"""Size-adjust raw signal scores so the scanner doesn't mechanically favor micro-caps.

Two layers:
  1. Bucket tickers by market cap into [micro, small, mid, large, mega].
  2. Within each bucket, percentile-rank the raw score so we surface the
     "best of class" rather than the largest absolute %.
  3. Apply a tier weight that down-weights micro-cap and slightly under-weights
     mega-cap, emphasizing the mid-cap "sweet spot" found in the insider-trading
     literature (Lakonishok-Lee, Cohen-Malloy-Pomorski).
"""

from __future__ import annotations
from typing import Iterable, Mapping, Optional

# Boundaries are the upper edges of each tier (exclusive).
CAP_BUCKETS: list[tuple[str, float]] = [
    ("micro",  300_000_000),       # < $300M
    ("small",  2_000_000_000),     # $300M – $2B
    ("mid",    10_000_000_000),    # $2B – $10B
    ("large",  200_000_000_000),   # $10B – $200B
    ("mega",   float("inf")),      # $200B+
]

TIER_WEIGHTS: dict[str, float] = {
    "micro": 0.60,
    "small": 0.85,
    "mid":   1.00,
    "large": 1.00,
    "mega":  0.90,
    "unknown": 0.0,
}

TIER_ORDER = ["micro", "small", "mid", "large", "mega"]


def get_tier(mcap: Optional[float]) -> str:
    """Bucket label for a given market cap in USD. Returns 'unknown' if missing."""
    if mcap is None or mcap <= 0:
        return "unknown"
    for label, upper in CAP_BUCKETS:
        if mcap < upper:
            return label
    return "mega"


def bucket_percentile_rank(values_by_tier: Mapping[str, Iterable[float]], tier: str, value: float) -> float:
    """Percentile rank of `value` within `tier`'s distribution. 0.0 if tier unknown or empty.

    Uses fraction of strictly-lower peers (excludes ties from numerator) so a
    score equal to the max in its bucket maps to (n-1)/n, never artificially 1.0
    when the universe is tiny.
    """
    if tier == "unknown":
        return 0.0
    peers = list(values_by_tier.get(tier, []))
    if not peers:
        return 0.0
    lower = sum(1 for v in peers if v < value)
    return lower / len(peers)


def size_adjusted_score(raw: float, tier: str, percentile: float) -> float:
    """Combine bucket percentile with tier weight. 0..1 range."""
    if tier == "unknown" or raw <= 0:
        return 0.0
    weight = TIER_WEIGHTS.get(tier, 0.0)
    return round(percentile * weight, 4)


def explain_tier_thresholds() -> str:
    """Human-readable tier definition for tool descriptions and UI."""
    parts = []
    prev = 0.0
    for label, upper in CAP_BUCKETS:
        if upper == float("inf"):
            parts.append(f"{label} ≥${prev/1e9:.0f}B")
        else:
            parts.append(f"{label} ${prev/1e9:.1f}B–${upper/1e9:.1f}B")
        prev = upper
    return ", ".join(parts)
