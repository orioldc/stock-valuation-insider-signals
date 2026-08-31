"""Detect share count changes (buybacks) from shares outstanding data.

Discriminates genuine buybacks from reverse splits and data errors using:
1. Authoritative split history from yfinance (stored in split_events table)
2. Market cap continuity check (shares × price preserved across corporate actions)
3. Hard magnitude ceilings (per-quarter and 4Q aggregate)
4. Recency check (stale data produces no signal)
"""

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.environ.get("INSIDER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db"))

# Magnitude thresholds
MAX_QOQ_REDUCTION = -50  # -50% per quarter (catches data errors)
MAX_QOQ_DILUTION = 200   # +200% per quarter (catches basis switches)
MAX_4Q_BUYBACK = -25     # -25% trailing 4Q (most aggressive real programs are -10% to -15%)

# Data recency threshold
MAX_STALENESS_DAYS = 365  # Shares data older than 1 year is stale

# Market cap continuity tolerance
MCAP_CONTINUITY_TOLERANCE = 0.25  # ±25% tolerance for market moves


def _load_splits(conn, ticker):
    """Load split events for ticker. Returns dict {date_str: ratio}."""
    rows = conn.execute("""
        SELECT date, ratio
        FROM split_events
        WHERE ticker = ?
        ORDER BY date
    """, (ticker,)).fetchall()
    return {date_str: ratio for date_str, ratio in rows}


def _apply_splits_to_shares(quarterly_data, splits):
    """
    Apply split adjustments to share counts to normalize the series.

    Args:
        quarterly_data: dict {(year, quarter): (shares, price, date_str)}
        splits: dict {date_str: ratio} where ratio > 1.0 is forward split, < 1.0 is reverse

    Returns:
        dict {(year, quarter): (adjusted_shares, price, date_str, split_adjusted: bool)}

    Split adjustment works forward: if a 2:1 split happens on date D, all share counts
    BEFORE D are doubled to normalize to the post-split basis. This makes quarter-over-
    quarter comparisons valid for buyback detection.
    """
    if not splits:
        return {q: (*data, False) for q, data in quarterly_data.items()}

    adjusted = {}
    sorted_quarters = sorted(quarterly_data.keys())

    for q in sorted_quarters:
        shares, price, date_str = quarterly_data[q]

        # Find all splits that occurred AFTER this quarter
        # Each split multiplies historical shares by its ratio
        cumulative_ratio = 1.0
        for split_date, split_ratio in splits.items():
            if split_date > date_str:
                cumulative_ratio *= split_ratio

        adjusted_shares = shares * cumulative_ratio
        was_adjusted = (cumulative_ratio != 1.0)
        adjusted[q] = (adjusted_shares, price, date_str, was_adjusted)

    return adjusted


def compute_share_delta(ticker):
    """
    Compute share count changes for a ticker, discriminating genuine buybacks
    from reverse splits and data errors.

    Returns dict with:
        delta_qoq: float (latest QoQ % change, on valid data only)
        delta_4q: float (trailing 4-quarter cumulative % change, on valid data only)
        trend: str ('buyback', 'dilution', 'stable', 'data_error', 'insufficient_data')
        score: float (higher = more buyback, 0 if data is unreliable)
        data_points: int (total quarters available)
        valid_points: int (quarters used after filtering)
        split_adjusted: bool (whether split normalization was applied)
        data_quality: str ('clean', 'split_adjusted', 'suspect', 'unusable')
    """
    conn = sqlite3.connect(DB_PATH)

    # Fetch shares and prices together
    rows = conn.execute("""
        SELECT so.date, so.shares, p.close
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        LEFT JOIN prices p ON c.ticker = p.ticker AND so.date = p.date
        WHERE c.ticker = ?
        ORDER BY so.date
    """, (ticker,)).fetchall()

    # Load split history
    splits = _load_splits(conn, ticker)
    conn.close()

    if len(rows) < 2:
        return {
            "delta_qoq": 0, "delta_4q": 0, "trend": "insufficient_data",
            "score": 0, "data_points": len(rows), "valid_points": len(rows),
            "split_adjusted": False, "data_quality": "insufficient_data"
        }

    # Deduplicate by keeping last value per approximate quarter
    # Group by year-quarter, keeping shares, price, and date
    quarterly = {}
    for date_str, shares, price in rows:
        try:
            year = date_str[:4]
            month = int(date_str[5:7])
            q = (int(year), (month - 1) // 3 + 1)
            quarterly[q] = (shares, price, date_str)  # last value wins
        except (ValueError, IndexError):
            continue

    if len(quarterly) < 2:
        return {
            "delta_qoq": 0, "delta_4q": 0, "trend": "insufficient_data",
            "score": 0, "data_points": len(quarterly), "valid_points": len(quarterly),
            "split_adjusted": False, "data_quality": "insufficient_data"
        }

    # Apply split adjustments to normalize share counts
    adjusted = _apply_splits_to_shares(quarterly, splits)
    split_adjusted = any(was_adj for _, _, _, was_adj in adjusted.values())

    # Validate quarters and find the latest contiguous clean segment
    # Strategy: mark invalid quarters, then take the longest contiguous segment
    # ending at the most recent quarter. This handles basis switches correctly.
    sorted_quarters = sorted(adjusted.keys())
    is_valid = [False] * len(sorted_quarters)
    data_quality_issues = []

    # First pass: mark each quarter as valid or invalid
    for i, q in enumerate(sorted_quarters):
        shares, price, date_str, was_split_adj = adjusted[q]

        # First quarter is always valid (no prior to compare)
        if i == 0:
            is_valid[i] = True
            continue

        prev_q = sorted_quarters[i - 1]
        prev_shares, prev_price, prev_date, _ = adjusted[prev_q]

        # Compute QoQ ratio
        if prev_shares == 0:
            data_quality_issues.append(f"{date_str}: prev_shares=0")
            is_valid[i] = False
            continue

        share_ratio = shares / prev_shares
        share_change_pct = (share_ratio - 1.0) * 100

        # Check 1: Plausible change magnitude ceiling
        # No company repurchases >50% in a quarter, nor dilutes by >3x
        # (Splits are already normalized, so large changes are data errors)
        if share_change_pct < MAX_QOQ_REDUCTION or share_change_pct > MAX_QOQ_DILUTION:
            data_quality_issues.append(f"{date_str}: {share_change_pct:+.1f}% (exceeds plausible range)")
            is_valid[i] = False
            continue

        # Check 2: Market cap continuity for large unexplained changes
        # If share count changed >10% but no split explains it, verify mcap preserved
        if abs(share_change_pct) > 10 and price and prev_price and prev_price > 0:
            price_ratio = price / prev_price
            combined_ratio = share_ratio * price_ratio

            # If combined_ratio is near 1.0, it's an unexplained corporate action
            # (split not in our database, or reporting basis switch that happens to preserve mcap)
            if 0.8 < combined_ratio < 1.2:
                data_quality_issues.append(
                    f"{date_str}: mcap preserved despite {share_change_pct:+.1f}% share change "
                    f"(combined_ratio={combined_ratio:.3f})"
                )
                is_valid[i] = False
                continue

        # Quarter passes validation
        is_valid[i] = True

    # Second pass: find the longest contiguous valid segment ending at the most recent quarter
    # This ensures we don't compute deltas across basis switches
    valid_segment_end = len(sorted_quarters) - 1
    valid_segment_start = valid_segment_end

    # Walk backwards from the end to find the longest contiguous valid segment
    for i in range(len(sorted_quarters) - 1, -1, -1):
        if is_valid[i]:
            valid_segment_start = i
        else:
            # Hit an invalid quarter — stop here
            break

    valid_quarters = sorted_quarters[valid_segment_start:valid_segment_end + 1]
    invalid_count = len(sorted_quarters) - len(valid_quarters)

    # Check recency: shares data must be reasonably fresh
    # Get the date of the most recent valid quarter
    if valid_quarters:
        latest_date_str = adjusted[valid_quarters[-1]][2]  # date_str is third element
        try:
            latest_date = datetime.strptime(latest_date_str, "%Y-%m-%d")
            days_stale = (datetime.now() - latest_date).days

            if days_stale > MAX_STALENESS_DAYS:
                # Data is stale — mark as such and don't score
                return {
                    "delta_qoq": 0, "delta_4q": 0, "trend": "stale",
                    "score": 0, "data_points": len(quarterly), "valid_points": len(valid_quarters),
                    "split_adjusted": split_adjusted,
                    "data_quality": f"stale ({days_stale} days old, latest: {latest_date_str})"
                }
        except (ValueError, IndexError):
            # Can't parse date, treat as suspect
            pass

    # Assess data quality
    invalid_ratio = invalid_count / len(sorted_quarters) if sorted_quarters else 0

    if invalid_count == 0:
        if split_adjusted:
            data_quality = "split_adjusted"
        else:
            data_quality = "clean"
    elif invalid_ratio > 0.15 or len(valid_quarters) < 4:
        # More than 15% invalid, or fewer than 4 valid quarters → unusable
        # (A series with basis switches will have ~17-50% invalid quarters)
        data_quality = "unusable"
    elif invalid_count > 0:
        data_quality = "suspect"  # Some bad quarters, but enough good ones remain
    else:
        data_quality = "clean"

    # Compute deltas on valid quarters only
    if len(valid_quarters) < 2:
        return {
            "delta_qoq": 0, "delta_4q": 0, "trend": "data_error",
            "score": 0, "data_points": len(quarterly), "valid_points": len(valid_quarters),
            "split_adjusted": split_adjusted, "data_quality": data_quality
        }

    valid_values = [adjusted[q][0] for q in valid_quarters]  # shares only

    # QoQ change (latest valid)
    delta_qoq = (valid_values[-1] - valid_values[-2]) / valid_values[-2] * 100 if valid_values[-2] != 0 else 0

    # Trailing 4-quarter change
    if len(valid_values) >= 5:
        delta_4q = (valid_values[-1] - valid_values[-5]) / valid_values[-5] * 100 if valid_values[-5] != 0 else 0
    else:
        delta_4q = (valid_values[-1] - valid_values[0]) / valid_values[0] * 100 if valid_values[0] != 0 else 0

    # Check 4Q delta magnitude ceiling
    # Even the most aggressive buyback programs (Meta, AAPL at peak) retire at most
    # 10-15% annually. Anything beyond -25% is a corporate action or data error.
    if delta_4q < MAX_4Q_BUYBACK:
        # Implausibly large 4Q reduction — likely undocumented corporate action or data error
        return {
            "delta_qoq": round(delta_qoq, 4),
            "delta_4q": round(delta_4q, 4),
            "trend": "data_error",
            "score": 0,
            "data_points": len(quarterly),
            "valid_points": len(valid_quarters),
            "split_adjusted": split_adjusted,
            "data_quality": f"implausible_4q_delta ({delta_4q:.1f}% exceeds {MAX_4Q_BUYBACK}% ceiling)"
        }

    # Trend (based on 4Q delta for consistency with score)
    if delta_4q <= -1:
        trend = "buyback"
    elif delta_4q >= 1:
        trend = "dilution"
    else:
        trend = "stable"

    # Score: normalize negative delta. More negative = higher score.
    # Use 4q delta, cap at [-20%, 0%] range, map to [0, 1]
    # If data quality is suspect, reduce score confidence
    raw = min(0, delta_4q)  # only negative counts
    base_score = min(abs(raw) / 20.0, 1.0)  # -20% maps to 1.0

    # Penalize suspect data quality
    if data_quality == "suspect":
        score = base_score * 0.5  # 50% confidence penalty
    elif data_quality == "unusable":
        score = 0.0
    else:
        score = base_score

    return {
        "delta_qoq": round(delta_qoq, 4),
        "delta_4q": round(delta_4q, 4),
        "trend": trend,
        "score": round(score, 4),
        "data_points": len(quarterly),
        "valid_points": len(valid_quarters),
        "split_adjusted": split_adjusted,
        "data_quality": data_quality,
    }
