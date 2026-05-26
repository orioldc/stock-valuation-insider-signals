"""Historical Hit Rate — per-ticker insider accuracy from historical clusters."""

import pandas as pd
import os

HIST_CSV = os.path.join(os.path.dirname(__file__), "..", "output", "historical_clusters.csv")


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
    path = csv_path or HIST_CSV
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        return {}
    
    df = df.dropna(subset=["excess_ret_12m"])
    
    result = {}
    for ticker, group in df.groupby("ticker"):
        n = len(group)
        wins = int((group["excess_ret_12m"] > 0).sum())
        win_rate = wins / n if n > 0 else 0
        avg_excess = group["excess_ret_12m"].mean()
        best = group["excess_ret_12m"].max()
        worst = group["excess_ret_12m"].min()
        
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


def get_sector_hit_rates(csv_path=None):
    """Compute per-sector average excess returns for sector favorability scoring."""
    path = csv_path or HIST_CSV
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        return {}
    
    df = df.dropna(subset=["excess_ret_12m"])
    
    result = {}
    for sector, group in df.groupby("sector"):
        result[sector] = {
            "avg_excess_12m": round(group["excess_ret_12m"].mean(), 2),
            "n_clusters": len(group),
            "win_rate": round((group["excess_ret_12m"] > 0).mean(), 3)
        }
    
    return result
