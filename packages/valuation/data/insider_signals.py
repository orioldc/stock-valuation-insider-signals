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

# Lazy-loaded frozen data with mtime tracking
_frozen_data: dict | None = None
_frozen_mtime: float | None = None
_frozen_path: Path | None = None


def _resolve_frozen_path() -> Path:
    """Resolve frozen path at call time: env override → data/ (runtime) → committed fallback."""
    if "INSIDER_FROZEN_DATA" in os.environ:
        return Path(os.environ["INSIDER_FROZEN_DATA"])

    # Prefer downloaded data/ copy (from install.sh release asset)
    repo_root = Path(__file__).resolve().parents[3]  # packages/valuation/data/insider_signals.py → repo root
    data_path = repo_root / "data" / "insider_frozen.json.gz"
    if data_path.exists():
        return data_path

    # Fall back to committed copy
    return Path(__file__).resolve().parent / "insider_frozen.json.gz"


def _load_frozen() -> dict:
    """Load and cache the entire frozen data file, invalidating on mtime change."""
    global _frozen_data, _frozen_mtime, _frozen_path

    current_path = _resolve_frozen_path()

    # Check if file exists
    if not current_path.exists():
        _frozen_data = {}
        _frozen_mtime = None
        _frozen_path = current_path
        return {}

    # Check if we need to reload (different path, no cache, or mtime changed)
    current_mtime = current_path.stat().st_mtime
    if (_frozen_data is not None
        and _frozen_path == current_path
        and _frozen_mtime == current_mtime):
        return _frozen_data

    try:
        with gzip.open(current_path, "rt") as f:
            _frozen_data = json.load(f)
        _frozen_mtime = current_mtime
        _frozen_path = current_path
        logger.info(f"Loaded {len(_frozen_data)} tickers from frozen insider data ({current_path})")
    except Exception as e:
        logger.warning(f"Failed to load frozen insider data: {e}")
        _frozen_data = {}
        _frozen_mtime = None
        _frozen_path = current_path
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
            frozen_path = _resolve_frozen_path()
            if frozen_path.exists():
                entry["as_of"] = datetime.fromtimestamp(frozen_path.stat().st_mtime).strftime("%Y-%m-%d")
            else:
                entry["as_of"] = "unknown"
            entry["cluster_window_days"] = 90
            entry["count_window_days"] = 120  # Frozen file was built with 120-day count window
            return entry

    return None
