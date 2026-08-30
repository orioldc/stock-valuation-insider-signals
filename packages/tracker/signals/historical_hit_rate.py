"""Historical Hit Rate — per-ticker insider accuracy from historical clusters."""

import pandas as pd
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Winsorization cap for excess returns (±200%).
# The distribution of excess_ret_12m is heavily right-skewed: 13 rows exceed +1000%
# (WGS +4307%, SEZL +3560%, GME +3116%, etc.), and those extreme tails otherwise
# dominate sector-level means. Capping at ±200% keeps the observations in the sample
# but prevents multi-thousand-percent outliers from setting whole sectors' favorability bands.
EXCESS_RETURN_CAP = 200.0

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]  # packages/tracker/signals/historical_hit_rate.py → repo root

# Path candidates (resolved at call time to avoid staleness)
_DATA_PATH = _REPO_ROOT / "data" / "historical_clusters.csv"
_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "historical_clusters.csv")


def _resolve_csv_path() -> str:
    """Resolve CSV path at call time: env override → data/ (runtime) → output/ (local pipeline)."""
    if "HISTORICAL_CSV_PATH" in os.environ:
        return os.environ["HISTORICAL_CSV_PATH"]
    if _DATA_PATH.exists():
        return str(_DATA_PATH)
    return _OUTPUT_PATH


# Backwards compatibility for external imports
HIST_CSV = _OUTPUT_PATH  # Fallback; callers should use the functions, not this directly


def compute_hit_rates(csv_path=None):
    """Compute per-ticker historical insider accuracy.

    Returns dict keyed by ticker:
    {
        "AAPL": {
            "n_clusters": 5,
            "wins": 3,
            "win_rate": 0.6,
            "avg_excess_12m": 8.5,
            "best_excess_12m": 25.0,
            "worst_excess_12m": -10.0,
            "summary": "Insiders at AAPL have been right 3/5 times (60% hit rate)"
        }
    }
    """
    path = csv_path or _resolve_csv_path()
    try:
        df = pd.read_csv(path)
        df = df.dropna(subset=["excess_ret_12m"])

        result = {}
        for ticker, group in df.groupby("ticker"):
            n = len(group)
            wins = int((group["excess_ret_12m"] > 0).sum())
            win_rate = wins / n if n > 0 else 0

            # Winsorize excess returns for avg/best/worst calculations
            # (win_rate is a sign test and remains untouched)
            winsorized = group["excess_ret_12m"].clip(-EXCESS_RETURN_CAP, EXCESS_RETURN_CAP)
            avg_excess = winsorized.mean()
            best = winsorized.max()
            worst = winsorized.min()

            result[ticker] = {
                "n_clusters": n,
                "wins": wins,
                "win_rate": win_rate,
                "avg_excess_12m": round(avg_excess, 2),
                "best_excess_12m": round(best, 2),
                "worst_excess_12m": round(worst, 2),
                "summary": f"Insiders at {ticker} have been right {wins}/{n} times ({win_rate*100:.0f}% hit rate)"
            }

        return result
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Failed to load or parse historical clusters CSV at {path}: {e}. Degrading to empty hit rates.")
        return {}


def get_sector_hit_rates(csv_path=None):
    """Compute per-sector average excess returns for sector favorability scoring."""
    path = csv_path or _resolve_csv_path()
    try:
        df = pd.read_csv(path)
        df = df.dropna(subset=["excess_ret_12m"])

        result = {}
        for sector, group in df.groupby("sector"):
            # Winsorize excess returns for avg calculation (win_rate is a sign test and remains untouched)
            winsorized = group["excess_ret_12m"].clip(-EXCESS_RETURN_CAP, EXCESS_RETURN_CAP)
            result[sector] = {
                "avg_excess_12m": round(winsorized.mean(), 2),
                "n_clusters": len(group),
                "win_rate": round((group["excess_ret_12m"] > 0).mean(), 3)
            }

        return result
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Failed to load or parse historical clusters CSV at {path}: {e}. Degrading to empty sector hit rates.")
        return {}
