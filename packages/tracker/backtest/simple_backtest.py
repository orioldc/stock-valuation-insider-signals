"""
Phase 3: Backtest Framework for Insider Signal Tracker.

Fetches historical prices via yfinance, then backtests:
  1. Insider buying clusters → forward excess returns vs SPY
  2. Share buyback quintiles → next-quarter returns
  3. Composite signal deciles → forward returns
"""

import sqlite3
import os
import json
import time
import math
import warnings
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats as scipy_stats

warnings.filterwarnings("ignore", category=FutureWarning)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")

# ──────────────────────────────────────────────
# Price data management
# ──────────────────────────────────────────────

def _ensure_prices_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()


def fetch_prices(tickers, period="5y"):
    """Download/refresh daily prices for tickers + SPY into SQLite (incremental top-up).

    New tickers (no prior rows) fetch the full period; existing tickers fetch only
    from their last stored date forward. INSERT OR IGNORE dedupes overlap.
    """
    conn = sqlite3.connect(DB_PATH)
    _ensure_prices_table(conn)

    all_tickers = sorted(set(tickers) | {"SPY"})

    last_dates = {}
    for t, mx in conn.execute("SELECT ticker, MAX(date) FROM prices GROUP BY ticker"):
        last_dates[t] = mx

    new_tickers = [t for t in all_tickers if last_dates.get(t) is None]
    existing_tickers = [t for t in all_tickers if last_dates.get(t) is not None]
    print(f"  Refreshing prices: {len(existing_tickers)} existing (incremental), "
          f"{len(new_tickers)} new (full {period})...")

    batch_size = 20
    total_rows = 0

    def _store(batch, data):
        nonlocal total_rows
        rows = []
        for t in batch:
            try:
                closes = data["Close"].dropna() if len(batch) == 1 else data[t]["Close"].dropna()
                for dt, price in closes.items():
                    if pd.notna(price) and price > 0:
                        rows.append((t, dt.strftime("%Y-%m-%d"), float(price)))
            except Exception:
                print(f"    Warning: no data for {t}")
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO prices (ticker, date, close) VALUES (?, ?, ?)", rows
            )
            conn.commit()
            total_rows += len(rows)

    # New tickers: full history
    for i in range(0, len(new_tickers), batch_size):
        batch = new_tickers[i:i + batch_size]
        try:
            data = yf.download(" ".join(batch), period=period, interval="1d",
                               group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"    Error fetching new batch: {e}")
            continue
        _store(batch, data)
        time.sleep(0.5)

    # Existing tickers: incremental from the oldest last-stored date in each batch
    for i in range(0, len(existing_tickers), batch_size):
        batch = existing_tickers[i:i + batch_size]
        start = min(last_dates[t] for t in batch)
        try:
            data = yf.download(" ".join(batch), start=start, interval="1d",
                               group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"    Error fetching incremental batch: {e}")
            continue
        _store(batch, data)
        time.sleep(0.5)

    conn.close()
    print(f"  Price refresh complete: {total_rows} rows added.")


def _load_prices_df():
    """Load all prices into a pivot DataFrame: date × ticker."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT ticker, date, close FROM prices ORDER BY date", conn)
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="ticker", values="close")


# ──────────────────────────────────────────────
# 1. Insider Cluster Backtest
# ──────────────────────────────────────────────

def _find_all_cluster_events(lookback_years=5, window_days=30):
    """
    Scan all historical insider purchase transactions and find cluster events.
    A cluster = ≥2 distinct insiders buying within window_days.
    Returns list of (ticker, cluster_date, details).
    """
    conn = sqlite3.connect(DB_PATH)
    cutoff = (datetime.now() - timedelta(days=lookback_years * 365)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT c.ticker, it.transaction_date, it.reporting_cik, it.reporting_name,
               it.shares_transacted, it.price, it.filing_date
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.transaction_date >= ?
        ORDER BY c.ticker, it.transaction_date
    """, (cutoff,)).fetchall()
    conn.close()

    # Group by ticker
    by_ticker = defaultdict(list)
    for ticker, tx_date, cik, name, shares, price, filing_date in rows:
        by_ticker[ticker].append({
            "date": tx_date,
            "cik": cik,
            "name": name,
            "shares": shares or 0,
            "price": price or 0,
            "filing_date": filing_date,
        })

    events = []
    for ticker, trades in by_ticker.items():
        trades.sort(key=lambda x: x["date"])
        used_dates = set()  # avoid double-counting overlapping clusters

        for i, t in enumerate(trades):
            t_date = datetime.strptime(t["date"], "%Y-%m-%d")
            window_end = t_date + timedelta(days=window_days)

            cluster_trades = [t]
            for j, t2 in enumerate(trades):
                if i == j:
                    continue
                t2_date = datetime.strptime(t2["date"], "%Y-%m-%d")
                if t_date <= t2_date <= window_end:
                    cluster_trades.append(t2)

            distinct = set(tr["cik"] for tr in cluster_trades)
            if len(distinct) < 2:
                continue

            # Use the last trade date as the cluster signal date
            cluster_end = max(tr["date"] for tr in cluster_trades)
            # Use the latest filing date as the "known" date (avoid look-ahead bias)
            filing_dates = [tr["filing_date"] for tr in cluster_trades if tr["filing_date"]]
            signal_date = max(filing_dates) if filing_dates else cluster_end

            # Deduplicate: skip if we already have a cluster within 30 days for this ticker
            sig_key = f"{ticker}_{signal_date[:7]}"
            if sig_key in used_dates:
                continue
            used_dates.add(sig_key)

            events.append({
                "ticker": ticker,
                "cluster_date": cluster_end,
                "signal_date": signal_date,  # date info became available
                "n_insiders": len(distinct),
                "n_trades": len(cluster_trades),
                "total_value": sum(tr["shares"] * tr["price"] for tr in cluster_trades),
            })

    return events


def backtest_insider_clusters(lookback_years=5):
    """Backtest insider cluster events: measure forward returns vs SPY."""
    print("\n" + "=" * 60)
    print("BACKTEST 1: Insider Buying Clusters")
    print("=" * 60)

    events = _find_all_cluster_events(lookback_years=lookback_years)
    print(f"  Found {len(events)} cluster events across {len(set(e['ticker'] for e in events))} tickers")

    if not events:
        print("  No cluster events found — skipping.")
        return {"n_events": 0, "horizons": {}}

    prices = _load_prices_df()
    if prices.empty:
        print("  No price data — skipping.")
        return {"n_events": 0, "horizons": {}}

    horizons = {
        "1m": 21, "3m": 63, "6m": 126, "12m": 252
    }

    results_by_horizon = {}
    for label, days in horizons.items():
        excess_returns = []
        for ev in events:
            ticker = ev["ticker"]
            sig_date = pd.Timestamp(ev["signal_date"])

            if ticker not in prices.columns or "SPY" not in prices.columns:
                continue

            # Find the next trading day on or after signal_date
            valid_dates = prices.index[prices.index >= sig_date]
            if len(valid_dates) < days + 1:
                continue

            entry_date = valid_dates[0]
            exit_idx = min(days, len(valid_dates) - 1)
            exit_date = valid_dates[exit_idx]

            p_entry = prices.loc[entry_date, ticker]
            p_exit = prices.loc[exit_date, ticker]
            spy_entry = prices.loc[entry_date, "SPY"]
            spy_exit = prices.loc[exit_date, "SPY"]

            if pd.isna(p_entry) or pd.isna(p_exit) or pd.isna(spy_entry) or pd.isna(spy_exit):
                continue
            if p_entry <= 0 or spy_entry <= 0:
                continue

            stock_ret = (p_exit / p_entry) - 1
            spy_ret = (spy_exit / spy_entry) - 1
            excess = stock_ret - spy_ret
            excess_returns.append(excess)

        if excess_returns:
            arr = np.array(excess_returns)
            t_stat, p_val = scipy_stats.ttest_1samp(arr, 0) if len(arr) > 1 else (0, 1)
            results_by_horizon[label] = {
                "n": len(arr),
                "hit_rate": float(np.mean(arr > 0)),
                "avg_excess": float(np.mean(arr)),
                "median_excess": float(np.median(arr)),
                "t_stat": float(t_stat),
                "p_value": float(p_val),
            }
            print(f"  {label}: n={len(arr)}, hit_rate={np.mean(arr>0):.1%}, "
                  f"avg_excess={np.mean(arr):.2%}, median={np.median(arr):.2%}, "
                  f"t={t_stat:.2f}, p={p_val:.3f}")
        else:
            results_by_horizon[label] = {"n": 0}
            print(f"  {label}: no valid observations")

    return {
        "n_events": len(events),
        "events": events,
        "horizons": results_by_horizon,
    }


# ──────────────────────────────────────────────
# 2. Share Buyback Quintile Backtest
# ──────────────────────────────────────────────

def _get_quarterly_share_changes(lookback_years=5):
    """
    For each quarter, compute share count change (%) for each company.
    Returns DataFrame with columns: ticker, quarter, share_change_pct, available_date.
    """
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT c.ticker, so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        ORDER BY c.ticker, so.date
    """, conn)
    conn.close()

    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"])
    # Assign quarters
    df["quarter"] = df["date"].dt.to_period("Q")

    # Take the last observation per ticker per quarter
    df = df.sort_values("date").groupby(["ticker", "quarter"]).last().reset_index()

    records = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("quarter")
        for i in range(1, len(grp)):
            prev = grp.iloc[i - 1]
            curr = grp.iloc[i]
            if prev["shares"] > 0:
                change_pct = (curr["shares"] - prev["shares"]) / prev["shares"]
                # Available date: filing date ≈ quarter end + 40 days
                q_end = curr["quarter"].end_time
                available = q_end + timedelta(days=45)
                records.append({
                    "ticker": ticker,
                    "quarter": str(curr["quarter"]),
                    "share_change_pct": change_pct,
                    "available_date": available.strftime("%Y-%m-%d"),
                    "q_end": q_end.strftime("%Y-%m-%d"),
                })

    return pd.DataFrame(records)


def backtest_share_buybacks(lookback_years=5):
    """Backtest buyback quintile strategy: sort by share change, form quintiles."""
    print("\n" + "=" * 60)
    print("BACKTEST 2: Share Buyback Quintiles")
    print("=" * 60)

    changes = _get_quarterly_share_changes(lookback_years)
    if changes.empty:
        print("  No share change data.")
        return {}

    prices = _load_prices_df()
    if prices.empty:
        print("  No price data.")
        return {}

    # For each quarter, form quintile portfolios and measure next-quarter returns
    quarters = sorted(changes["quarter"].unique())
    print(f"  {len(quarters)} quarters of share change data")

    quintile_returns = defaultdict(list)  # quintile (1-5) → list of returns

    for q in quarters:
        q_data = changes[changes["quarter"] == q].copy()
        if len(q_data) < 10:
            continue

        # Assign quintiles (1 = most buyback / most negative change)
        q_data["quintile"] = pd.qcut(q_data["share_change_pct"], 5, labels=[1, 2, 3, 4, 5])

        # Entry date: available_date (accounts for filing lag)
        entry_date_str = q_data["available_date"].max()  # use latest available
        entry_date = pd.Timestamp(entry_date_str)

        # Exit: ~3 months later
        exit_date = entry_date + timedelta(days=90)

        valid_entry = prices.index[prices.index >= entry_date]
        valid_exit = prices.index[prices.index >= exit_date]
        if len(valid_entry) == 0 or len(valid_exit) == 0:
            continue

        actual_entry = valid_entry[0]
        actual_exit = valid_exit[0]

        for quintile in range(1, 6):
            q_tickers = q_data[q_data["quintile"] == quintile]["ticker"].tolist()
            rets = []
            for t in q_tickers:
                if t not in prices.columns:
                    continue
                p0 = prices.loc[actual_entry, t] if actual_entry in prices.index else np.nan
                p1 = prices.loc[actual_exit, t] if actual_exit in prices.index else np.nan
                if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    rets.append((p1 / p0) - 1)
            if rets:
                quintile_returns[int(quintile)].append(np.mean(rets))

    # Summarize
    summary = {}
    for q in range(1, 6):
        rets = quintile_returns[q]
        if rets:
            arr = np.array(rets)
            summary[q] = {
                "n_quarters": len(arr),
                "avg_return": float(np.mean(arr)),
                "median_return": float(np.median(arr)),
            }
            label = {1: "Most Buyback", 5: "Least Buyback"}.get(q, f"Q{q}")
            print(f"  Quintile {q} ({label}): n={len(arr)} quarters, "
                  f"avg={np.mean(arr):.2%}, median={np.median(arr):.2%}")

    # Spread
    if 1 in summary and 5 in summary:
        spread = summary[1]["avg_return"] - summary[5]["avg_return"]
        q1_arr = np.array(quintile_returns[1])
        q5_arr = np.array(quintile_returns[5])
        min_len = min(len(q1_arr), len(q5_arr))
        if min_len > 1:
            t_stat, p_val = scipy_stats.ttest_ind(q1_arr[:min_len], q5_arr[:min_len])
        else:
            t_stat, p_val = 0, 1
        summary["spread"] = {
            "avg": float(spread),
            "t_stat": float(t_stat),
            "p_value": float(p_val),
        }
        print(f"  Q1-Q5 Spread: {spread:.2%} (t={t_stat:.2f}, p={p_val:.3f})")

    return summary


# ──────────────────────────────────────────────
# 3. Composite Signal Backtest
# ──────────────────────────────────────────────

def backtest_composite(lookback_years=3):
    """
    Backtest composite signal: each quarter, rank by composite score,
    form top/bottom decile portfolios, measure forward returns.
    
    Since our signals table only has the latest snapshot, we reconstruct
    a simplified composite from raw data each quarter.
    """
    print("\n" + "=" * 60)
    print("BACKTEST 3: Composite Signal (Quarterly)")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    # Get all insider purchases
    purchases = pd.read_sql("""
        SELECT c.ticker, it.transaction_date, it.reporting_cik, it.filing_date,
               it.shares_transacted, it.price
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
        ORDER BY it.transaction_date
    """, conn)

    # Get share changes
    shares = pd.read_sql("""
        SELECT c.ticker, so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        ORDER BY c.ticker, so.date
    """, conn)

    tickers = pd.read_sql("SELECT ticker FROM companies", conn)["ticker"].tolist()
    conn.close()

    prices = _load_prices_df()
    if prices.empty:
        print("  No price data.")
        return {}

    shares["date"] = pd.to_datetime(shares["date"])
    purchases["transaction_date"] = pd.to_datetime(purchases["transaction_date"])
    purchases["filing_date"] = pd.to_datetime(purchases["filing_date"])

    cutoff = datetime.now() - timedelta(days=lookback_years * 365)

    # Generate quarterly rebalance dates
    rebal_dates = pd.date_range(
        start=max(cutoff, pd.Timestamp("2021-06-01")),
        end=datetime.now() - timedelta(days=90),
        freq="QS"  # quarter start
    )

    results = {"3m": [], "6m": []}

    for rebal in rebal_dates:
        # 1) Cluster score: count distinct insiders buying in trailing 90 days
        # Use filing_date to avoid look-ahead
        trail_start = rebal - timedelta(days=90)
        p_window = purchases[
            (purchases["filing_date"] >= trail_start) &
            (purchases["filing_date"] <= rebal)
        ]

        cluster_scores = {}
        for ticker in tickers:
            tp = p_window[p_window["ticker"] == ticker]
            n_insiders = tp["reporting_cik"].nunique()
            total_val = (tp["shares_transacted"].fillna(0) * tp["price"].fillna(0)).sum()
            cluster_scores[ticker] = n_insiders * math.log(max(total_val, 1)) if n_insiders >= 1 else 0

        # 2) Buyback score: most recent QoQ share change available before rebal
        avail_cutoff = rebal - timedelta(days=45)  # filing lag
        buyback_scores = {}
        for ticker in tickers:
            ts = shares[shares["ticker"] == ticker].sort_values("date")
            ts = ts[ts["date"] <= avail_cutoff]
            if len(ts) >= 2:
                last = ts.iloc[-1]["shares"]
                prev = ts.iloc[-2]["shares"]
                if prev > 0:
                    change = (last - prev) / prev
                    buyback_scores[ticker] = -change  # negative change = positive score
                else:
                    buyback_scores[ticker] = 0
            else:
                buyback_scores[ticker] = 0

        # 3) Composite: 0.6 × cluster_norm + 0.4 × buyback_norm
        df_scores = pd.DataFrame({
            "ticker": tickers,
            "cluster": [cluster_scores.get(t, 0) for t in tickers],
            "buyback": [buyback_scores.get(t, 0) for t in tickers],
        })

        max_c = df_scores["cluster"].max()
        max_b = df_scores["buyback"].max()
        df_scores["c_norm"] = df_scores["cluster"] / max_c if max_c > 0 else 0
        df_scores["b_norm"] = df_scores["buyback"] / max_b if max_b > 0 else 0
        df_scores["composite"] = 0.6 * df_scores["c_norm"] + 0.4 * df_scores["b_norm"]
        df_scores = df_scores.sort_values("composite", ascending=False)

        n = len(df_scores)
        decile_size = max(n // 10, 1)
        top = df_scores.head(decile_size)["ticker"].tolist()
        bottom = df_scores.tail(decile_size)["ticker"].tolist()

        # Measure forward returns
        for label, fwd_days in [("3m", 63), ("6m", 126)]:
            entry_dates = prices.index[prices.index >= rebal]
            if len(entry_dates) < fwd_days + 1:
                continue
            entry = entry_dates[0]
            exit_d = entry_dates[min(fwd_days, len(entry_dates) - 1)]

            def portfolio_return(ticker_list):
                rets = []
                for t in ticker_list:
                    if t in prices.columns:
                        p0 = prices.loc[entry, t]
                        p1 = prices.loc[exit_d, t]
                        if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                            rets.append((p1 / p0) - 1)
                return np.mean(rets) if rets else np.nan

            top_ret = portfolio_return(top)
            bot_ret = portfolio_return(bottom)
            spy_ret = portfolio_return(["SPY"])

            if not np.isnan(top_ret) and not np.isnan(bot_ret):
                results[label].append({
                    "date": rebal.strftime("%Y-%m-%d"),
                    "top_return": top_ret,
                    "bottom_return": bot_ret,
                    "spy_return": spy_ret if not np.isnan(spy_ret) else 0,
                    "spread": top_ret - bot_ret,
                    "top_excess": top_ret - (spy_ret if not np.isnan(spy_ret) else 0),
                })

    # Summarize
    summary = {}
    for label in ["3m", "6m"]:
        if results[label]:
            df_r = pd.DataFrame(results[label])
            spreads = df_r["spread"].values
            top_exc = df_r["top_excess"].values
            t_sp, p_sp = scipy_stats.ttest_1samp(spreads, 0) if len(spreads) > 1 else (0, 1)
            t_ex, p_ex = scipy_stats.ttest_1samp(top_exc, 0) if len(top_exc) > 1 else (0, 1)

            summary[label] = {
                "n_periods": len(df_r),
                "avg_top": float(df_r["top_return"].mean()),
                "avg_bottom": float(df_r["bottom_return"].mean()),
                "avg_spy": float(df_r["spy_return"].mean()),
                "avg_spread": float(np.mean(spreads)),
                "avg_top_excess": float(np.mean(top_exc)),
                "spread_t": float(t_sp),
                "spread_p": float(p_sp),
                "excess_t": float(t_ex),
                "excess_p": float(p_ex),
                "periods": results[label],
            }
            print(f"  {label}: n={len(df_r)} periods")
            print(f"    Top decile avg: {df_r['top_return'].mean():.2%}")
            print(f"    Bottom decile avg: {df_r['bottom_return'].mean():.2%}")
            print(f"    Spread: {np.mean(spreads):.2%} (t={t_sp:.2f}, p={p_sp:.3f})")
            print(f"    Top excess vs SPY: {np.mean(top_exc):.2%} (t={t_ex:.2f}, p={p_ex:.3f})")

    return summary


# ──────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────

def run_all_backtests():
    """Run all backtests and return combined results."""
    conn = sqlite3.connect(DB_PATH)
    tickers = [r[0] for r in conn.execute("SELECT ticker FROM companies").fetchall()]
    conn.close()

    print("=" * 60)
    print("INSIDER SIGNAL TRACKER — BACKTEST SUITE")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Universe: {len(tickers)} companies")
    print("=" * 60)

    # Step 1: Fetch prices
    print("\nStep 1: Fetching historical prices...")
    fetch_prices(tickers, period="5y")

    # Step 2: Run backtests
    cluster_results = backtest_insider_clusters(lookback_years=5)
    buyback_results = backtest_share_buybacks(lookback_years=5)
    composite_results = backtest_composite(lookback_years=3)

    return {
        "cluster": cluster_results,
        "buyback": buyback_results,
        "composite": composite_results,
        "run_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_tickers": len(tickers),
    }


if __name__ == "__main__":
    results = run_all_backtests()
    
    # Generate report
    from backtest.report_generator import generate_report
    generate_report(results)
    print("\n✅ Backtest complete. Report saved to output/backtest_report.md")
