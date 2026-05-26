"""Generate markdown backtest report."""

import os
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")


def generate_report(results):
    """Generate output/backtest_report.md from backtest results dict."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    lines = []
    w = lines.append

    w("# Insider Signal Tracker — Backtest Report")
    w(f"\n**Generated:** {results.get('run_date', datetime.now().strftime('%Y-%m-%d %H:%M'))}")
    w(f"**Universe:** {results.get('n_tickers', '?')} companies")
    w("")

    # ── Cluster backtest ──
    w("## 1. Insider Buying Cluster Signal")
    w("")
    cl = results.get("cluster", {})
    n_events = cl.get("n_events", 0)
    w(f"**Cluster events found:** {n_events}")
    w("")

    if n_events > 0 and cl.get("horizons"):
        w("| Horizon | N | Hit Rate | Avg Excess | Median Excess | t-stat | p-value |")
        w("|---------|---|----------|------------|---------------|--------|---------|")
        for h in ["1m", "3m", "6m", "12m"]:
            d = cl["horizons"].get(h, {})
            if d.get("n", 0) > 0:
                w(f"| {h} | {d['n']} | {d['hit_rate']:.1%} | {d['avg_excess']:.2%} | "
                  f"{d['median_excess']:.2%} | {d['t_stat']:.2f} | {d['p_value']:.3f} |")
            else:
                w(f"| {h} | 0 | — | — | — | — | — |")
        w("")

        # List events
        events = cl.get("events", [])
        if events:
            w("### Cluster Events Detail")
            w("")
            w("| Ticker | Cluster Date | Signal Date | # Insiders | Total Value |")
            w("|--------|-------------|-------------|------------|-------------|")
            for ev in sorted(events, key=lambda x: x["signal_date"]):
                val = f"${ev['total_value']:,.0f}" if ev["total_value"] > 0 else "N/A"
                w(f"| {ev['ticker']} | {ev['cluster_date']} | {ev['signal_date']} | "
                  f"{ev['n_insiders']} | {val} |")
            w("")
    else:
        w("*No cluster events detected in the lookback period.*")
        w("")

    # ── Buyback backtest ──
    w("## 2. Share Buyback Quintile Strategy")
    w("")
    bb = results.get("buyback", {})
    if bb and any(k for k in bb if isinstance(k, int)):
        w("| Quintile | Label | N Quarters | Avg Return | Median Return |")
        w("|----------|-------|------------|------------|---------------|")
        for q in range(1, 6):
            d = bb.get(q, {})
            if d:
                label = {1: "Most Buyback", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Least Buyback"}.get(q, f"Q{q}")
                w(f"| {q} | {label} | {d['n_quarters']} | {d['avg_return']:.2%} | {d['median_return']:.2%} |")
        w("")
        sp = bb.get("spread", {})
        if sp:
            w(f"**Q1-Q5 Spread:** {sp['avg']:.2%} (t-stat: {sp['t_stat']:.2f}, p-value: {sp['p_value']:.3f})")
            w("")
    else:
        w("*Insufficient data for buyback quintile backtest.*")
        w("")

    # ── Composite backtest ──
    w("## 3. Composite Signal (Top vs Bottom Decile)")
    w("")
    comp = results.get("composite", {})
    if comp:
        w("| Horizon | N Periods | Top Decile | Bottom Decile | SPY | Spread | Top Excess vs SPY |")
        w("|---------|-----------|------------|---------------|-----|--------|-------------------|")
        for h in ["3m", "6m"]:
            d = comp.get(h, {})
            if d:
                w(f"| {h} | {d['n_periods']} | {d['avg_top']:.2%} | {d['avg_bottom']:.2%} | "
                  f"{d['avg_spy']:.2%} | {d['avg_spread']:.2%} | {d['avg_top_excess']:.2%} |")
        w("")
        for h in ["3m", "6m"]:
            d = comp.get(h, {})
            if d:
                w(f"**{h} Spread t-stat:** {d['spread_t']:.2f} (p={d['spread_p']:.3f}) | "
                  f"**Top excess t-stat:** {d['excess_t']:.2f} (p={d['excess_p']:.3f})")
        w("")
    else:
        w("*Insufficient data for composite backtest.*")
        w("")

    # ── Caveats ──
    w("## Caveats & Limitations")
    w("")
    w("1. **Small sample size:** The universe is ~100 large-cap companies. Insider buying clusters "
      "are rare events (often <20 total). Statistical significance should be interpreted with caution.")
    w("2. **Survivorship bias:** The universe is today's large-cap stocks. Companies that declined "
      "or were delisted are not included, which biases results upward.")
    w("3. **Look-ahead bias mitigation:** We use SEC filing dates (not transaction dates) as signal "
      "dates, and add a 45-day lag for shares outstanding data. However, some residual bias may exist.")
    w("4. **Transaction costs:** No trading costs, slippage, or market impact are modeled.")
    w("5. **Data coverage:** Insider transaction data is from SEC EDGAR Form 4 filings. "
      "Shares outstanding from XBRL filings. Coverage may be incomplete for some periods.")
    w("6. **Time period:** Results cover a specific ~5-year window and may not generalize.")
    w("")
    w("---")
    w(f"*Report generated by Insider Signal Tracker backtest suite on {datetime.now().strftime('%Y-%m-%d')}.*")

    path = os.path.join(OUTPUT_DIR, "backtest_report.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Report written to {path}")
    return path
