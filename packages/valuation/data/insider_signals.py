"""Read insider signal data — dispatch between frozen snapshot and live EDGAR fetch.

Strategy:
  1. Try live SEC EDGAR fetch first (via insider_fetcher.py), with 7-day disk cache.
  2. Fall back to frozen snapshot if live fetch fails or returns nothing.
  3. The frozen file path can be overridden via INSIDER_FROZEN_DATA env var.

Why this order: The frozen file contains 3,037 tickers from May 2026. If we check
it first, those tickers never get updated. Inverting the order gives live data
precedence while keeping the frozen file as a robust offline fallback.
"""

import gzip
import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FROZEN_PATH = Path(
    os.environ.get(
        "INSIDER_FROZEN_DATA",
        Path(__file__).resolve().parent / "insider_frozen.json.gz",
    )
)

# Lazy-loaded frozen data
_frozen_data: dict | None = None


def _load_frozen() -> dict:
    """Load and cache the entire frozen data file."""
    global _frozen_data
    if _frozen_data is not None:
        return _frozen_data
    if not FROZEN_PATH.exists():
        return {}
    try:
        with gzip.open(FROZEN_PATH, "rt") as f:
            _frozen_data = json.load(f)
        logger.info(f"Loaded {len(_frozen_data)} tickers from frozen insider data")
    except Exception as e:
        logger.warning(f"Failed to load frozen insider data: {e}")
        _frozen_data = {}
    return _frozen_data


def get_signal_for_ticker(ticker: str, use_cache: bool = True) -> dict | None:
    """Return insider signal data for a ticker.

    Tries live SEC EDGAR fetch first, then falls back to frozen snapshot.

    Returns dict with:
        ticker, in_universe, conviction_score, quality, cluster_detected,
        n_insiders, total_value, share_delta_4q, share_delta_qoq, share_trend,
        latest_transaction_date, insider_summary,
        source ("live_edgar" | "frozen_snapshot"),
        as_of (ISO date),
        cluster_window_days (90),
        count_window_days (120)
    or None if ticker is not found.
    """
    ticker = ticker.upper()

    # 1. Try live EDGAR fetch first (has built-in 7-day disk cache)
    try:
        from data.insider_fetcher import fetch_insider_data

        result = fetch_insider_data(ticker, use_cache=use_cache)
        if result is not None:
            # fetch_insider_data already includes provenance fields
            return result
    except Exception as e:
        logger.warning(f"Live insider fetch failed for {ticker}: {e}")
        # Fall through to frozen snapshot

    # 2. Fall back to frozen snapshot
    frozen = _load_frozen()
    if frozen:
        entry = frozen.get(ticker)
        if entry:
            entry["ticker"] = ticker
            # Augment with provenance metadata
            entry["source"] = "frozen_snapshot"
            # Use frozen file mtime as approximation of build date
            if FROZEN_PATH.exists():
                entry["as_of"] = datetime.fromtimestamp(FROZEN_PATH.stat().st_mtime).strftime("%Y-%m-%d")
            else:
                entry["as_of"] = "unknown"
            entry["cluster_window_days"] = 90
            entry["count_window_days"] = 120  # Frozen file was built with 120-day count window
            return entry

    return None
