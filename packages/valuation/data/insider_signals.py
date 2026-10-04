"""Insider buying and share count change for one ticker, for the valuation tool.

Reads the cleaned insider database (data/insider_signals.db, downloaded and
kept current by scripts/install.sh and scripts/start.sh), using the same code
as the scanner (packages/tracker/signals/ticker_summary.py). Only when the
database is missing does it fall back to the frozen snapshot file.

There is deliberately no live SEC lookup here: the database has been through
the cleanup and the correctness audit (amendments, duplicates, joint filings,
companies' own investments in other issuers), and a separate live parser would
skip all of that.

Overrides: INSIDER_DB_PATH for the database, INSIDER_FROZEN_DATA for the
frozen file.
"""

import gzip
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]  # packages/valuation/data/insider_signals.py → repo root
_TRACKER_DIR = _REPO_ROOT / "packages" / "tracker"

# Lazy-loaded frozen data with mtime tracking
_frozen_data: dict | None = None
_frozen_mtime: float | None = None
_frozen_path: Path | None = None


def _db_path() -> Path:
    return Path(os.environ.get("INSIDER_DB_PATH", _REPO_ROOT / "data" / "insider_signals.db"))


def _resolve_frozen_path() -> Path:
    """Resolve frozen path at call time: env override → data/ (runtime) → committed fallback."""
    if "INSIDER_FROZEN_DATA" in os.environ:
        return Path(os.environ["INSIDER_FROZEN_DATA"])

    # Prefer downloaded data/ copy (from install.sh release asset)
    data_path = _REPO_ROOT / "data" / "insider_frozen.json.gz"
    if data_path.exists():
        return data_path

    # Fall back to committed copy
    return Path(__file__).resolve().parent / "insider_frozen.json.gz"


def _load_frozen() -> dict:
    """Load and cache the entire frozen data file, invalidating on mtime change."""
    global _frozen_data, _frozen_mtime, _frozen_path

    current_path = _resolve_frozen_path()

    if not current_path.exists():
        _frozen_data = {}
        _frozen_mtime = None
        _frozen_path = current_path
        return {}

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


def _from_database(ticker: str) -> dict | None:
    if str(_TRACKER_DIR) not in sys.path:
        # Appended, not prepended, so the valuation package's own modules win.
        sys.path.append(str(_TRACKER_DIR))
    from signals.ticker_summary import summarize_ticker

    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    try:
        result = summarize_ticker(conn, ticker)
    finally:
        conn.close()
    if result is not None:
        result["source"] = "database"
    return result


def get_signal_for_ticker(ticker: str, use_cache: bool = True) -> dict | None:
    """Return insider signal data for a ticker, or None if it is not known.

    The fields are those of signals/ticker_summary.py, plus "source"
    ("database" or "frozen_snapshot"). "data_through" is the newest filing
    included, so a reader can tell how current the figures are.

    use_cache is kept for callers; the database needs no cache.
    """
    ticker = ticker.upper()

    if _db_path().exists():
        try:
            return _from_database(ticker)
        except Exception as e:
            logger.warning(f"Reading insider data for {ticker} from the database failed: {e}")
            # Fall through to the frozen snapshot

    entry = _load_frozen().get(ticker)
    if not entry:
        return None
    entry = dict(entry, ticker=ticker, source="frozen_snapshot")
    # Snapshots made before ticker_summary.py have no build date of their own,
    # and the file's modification time is the download or clone date, not the
    # build date, so the date is left unknown rather than guessed.
    entry.setdefault("as_of", "unknown")
    entry.setdefault("data_through", "unknown")
    entry.setdefault("cluster_window_days", 90)
    entry.setdefault("count_window_days", 120)
    entry.pop("conviction_score", None)
    entry.pop("conviction", None)
    return entry
