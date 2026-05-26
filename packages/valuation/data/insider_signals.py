"""Read insider signal data — dispatch between frozen snapshot and live EDGAR fetch.

Strategy:
  1. If a frozen data file exists (data/insider_frozen.json.gz), read from it (fast, no network).
  2. Otherwise, fall back to live SEC EDGAR fetch via insider_fetcher.py.
  3. The frozen file path can be overridden via INSIDER_FROZEN_DATA env var.
"""

import gzip
import json
import logging
import os
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

    Tries frozen data first, then falls back to live SEC EDGAR fetch.

    Returns the same dict structure:
        ticker, in_universe, conviction_score, quality, cluster_detected,
        n_insiders, total_value, share_delta_4q, share_delta_qoq, share_trend,
        latest_transaction_date, insider_summary
    or None if ticker is not found.
    """
    ticker = ticker.upper()

    # Try frozen data first
    frozen = _load_frozen()
    if frozen:
        entry = frozen.get(ticker)
        if entry:
            entry["ticker"] = ticker
            return entry
        # Tick explicitly in frozen data as not present
        if ticker not in frozen:
            pass  # fall through to live fetch

    # Fall back to live EDGAR fetch
    try:
        from data.insider_fetcher import fetch_insider_data

        return fetch_insider_data(ticker, use_cache=use_cache)
    except Exception as e:
        logger.warning(f"Live insider fetch failed for {ticker}: {e}")
        return None
