"""Bridge to tracker and valuation packages.

Path resolution: defaults assume this file lives at packages/mcp/api/bridge.py
inside the monorepo, so the sibling packages are at ../../tracker and
../../valuation. Power users can override via env vars.

Data files (DB + scanner CSV) default to the repo's data/ directory so that
the install script can populate them from a release artifact.
"""

import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]  # packages/mcp/api/bridge.py → repo root

INSIDER_TRACKER = os.environ.get(
    "INSIDER_TRACKER_PATH",
    str(_REPO_ROOT / "packages" / "tracker"),
)
VALUATION_AGENT = os.environ.get(
    "VALUATION_AGENT_PATH",
    str(_REPO_ROOT / "packages" / "valuation"),
)
DB_PATH = os.environ.get(
    "INSIDER_DB_PATH",
    str(_REPO_ROOT / "data" / "insider_signals.db"),
)
SIGNALS_CSV = os.environ.get(
    "SIGNALS_CSV_PATH",
    str(_REPO_ROOT / "data" / "latest_signals.csv"),
)

sys.path.insert(0, INSIDER_TRACKER)
sys.path.insert(0, VALUATION_AGENT)

from signals.size_adjustment import (
    get_tier, bucket_percentile_rank, size_adjusted_score,
    TIER_WEIGHTS, TIER_ORDER, explain_tier_thresholds,
)

# Cache for universe-wide raw scores keyed by tier (used to compute percentile ranks).
# Refreshed when the underlying CSV mtime changes.
_UNIVERSE_CACHE: dict = {"loaded_at": 0.0, "csv_mtime": 0.0, "by_tier": None, "by_ticker": None}


def _load_universe() -> dict:
    """Load (ticker → {mcap, tier, raw_cluster, raw_share}) + per-tier score arrays.

    Memoized; reloads only when latest_signals.csv mtime changes.
    """
    if not os.path.exists(SIGNALS_CSV):
        return {"by_ticker": {}, "by_tier_cluster": {}, "by_tier_share": {}}

    mtime = os.path.getmtime(SIGNALS_CSV)
    if _UNIVERSE_CACHE.get("csv_mtime") == mtime and _UNIVERSE_CACHE.get("by_tier") is not None:
        return _UNIVERSE_CACHE["by_tier"]

    # Read CSV: ticker → raw scores
    with open(SIGNALS_CSV) as f:
        rows = list(csv.DictReader(f))

    tickers = [r["ticker"] for r in rows]
    mcap_by_ticker: dict[str, float] = {}
    if tickers:
        conn = get_db()
        placeholders = ",".join("?" * len(tickers))
        for r in conn.execute(
            f"SELECT ticker, market_cap FROM companies WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall():
            if r["market_cap"]:
                mcap_by_ticker[r["ticker"]] = float(r["market_cap"])
        conn.close()

    by_ticker: dict[str, dict] = {}
    by_tier_cluster: dict[str, list[float]] = {t: [] for t in TIER_ORDER}
    by_tier_share: dict[str, list[float]] = {t: [] for t in TIER_ORDER}

    for r in rows:
        ticker = r["ticker"]
        mcap = mcap_by_ticker.get(ticker)
        tier = get_tier(mcap)
        try:
            raw_cluster = float(r.get("cluster_score_raw") or 0)
        except ValueError:
            raw_cluster = 0.0
        try:
            raw_share = float(r.get("share_score_raw") or 0)
        except ValueError:
            raw_share = 0.0
        by_ticker[ticker] = {
            "mcap": mcap, "tier": tier,
            "raw_cluster": raw_cluster, "raw_share": raw_share,
        }
        if tier in by_tier_cluster:
            by_tier_cluster[tier].append(raw_cluster)
            by_tier_share[tier].append(raw_share)

    bundle = {
        "by_ticker": by_ticker,
        "by_tier_cluster": by_tier_cluster,
        "by_tier_share": by_tier_share,
    }
    _UNIVERSE_CACHE["loaded_at"] = time.time()
    _UNIVERSE_CACHE["csv_mtime"] = mtime
    _UNIVERSE_CACHE["by_tier"] = bundle
    return bundle


def _size_adjust(ticker: str, raw_cluster: float, raw_share: float, mcap=None) -> dict:
    """Bucket-percentile + tier-weight a ticker's raw signal scores.

    Always percentile-ranks the supplied raw scores against the tier's
    distribution from the scanner CSV. If the ticker is in the universe
    we use its tier from there; otherwise infer tier from the supplied mcap.
    """
    universe = _load_universe()
    info = universe["by_ticker"].get(ticker)
    tier = info["tier"] if info is not None else get_tier(mcap)
    pct_cluster = bucket_percentile_rank(universe["by_tier_cluster"], tier, raw_cluster)
    pct_share = bucket_percentile_rank(universe["by_tier_share"], tier, raw_share)

    weight = TIER_WEIGHTS.get(tier, 0.0)
    return {
        "tier": tier,
        "tier_weight": weight,
        "cluster_percentile": round(pct_cluster, 4),
        "share_percentile": round(pct_share, 4),
        "cluster_adjusted": size_adjusted_score(raw_cluster, tier, pct_cluster),
        "share_adjusted": size_adjusted_score(raw_share, tier, pct_share),
        "tier_thresholds": explain_tier_thresholds(),
    }


def _to_native(val):
    """Coerce numpy / non-JSON-safe scalars to native Python."""
    if val is None:
        return None
    if hasattr(val, "item"):
        try:
            return val.item()
        except Exception:
            pass
    if isinstance(val, (int, float, str, bool)):
        return val
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_signals(limit=50, min_score=0, sector=None, cluster_only=False):
    """Read latest_signals.csv, join with companies table, re-rank by size-adjusted composite.

    The CSV's raw `composite` column max-normalizes across the whole universe and
    so mechanically favors micro-caps. We compute a size-adjusted composite here
    (bucket-percentile × tier weight, 0.6/0.4 insider/buyback blend) and rank by
    that instead. Raw scores stay accessible for transparency.
    """
    if not os.path.exists(SIGNALS_CSV):
        return []

    # Read CSV
    with open(SIGNALS_CSV) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    tickers = [r["ticker"] for r in rows]
    if not tickers:
        return []

    # Get company info from DB
    conn = get_db()
    placeholders = ",".join("?" * len(tickers))
    companies = {}
    for r in conn.execute(
        f"SELECT ticker, name, sector, market_cap FROM companies WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall():
        companies[r["ticker"]] = dict(r)
    conn.close()

    results = []
    for r in rows:
        cluster_detected = r.get("cluster_detected", "").lower() == "true"
        if cluster_only and not cluster_detected:
            continue

        co = companies.get(r["ticker"], {})
        if sector and co.get("sector", "").lower() != sector.lower():
            continue

        cluster_details = r.get("cluster_details", "0")
        try:
            num_insiders = int(cluster_details) if cluster_details else 0
        except ValueError:
            num_insiders = 0

        raw_cluster = float(r.get("cluster_score_raw", 0) or 0)
        raw_share = float(r.get("share_score_raw", 0) or 0)
        raw_composite = float(r.get("composite", 0) or 0)
        sa = _size_adjust(r["ticker"], raw_cluster, raw_share, mcap=co.get("market_cap"))
        composite_adj = round(0.6 * sa["cluster_adjusted"] + 0.4 * sa["share_adjusted"], 4)

        if composite_adj < min_score:
            continue

        results.append({
            "ticker": r["ticker"],
            "name": co.get("name"),
            "sector": co.get("sector"),
            "market_cap": co.get("market_cap"),
            "tier": sa["tier"],
            "composite_score": composite_adj,
            "composite_score_raw": raw_composite,
            "cluster_score": raw_cluster,
            "cluster_adjusted": sa["cluster_adjusted"],
            "buyback_score": raw_share,
            "buyback_adjusted": sa["share_adjusted"],
            "cluster_detected": cluster_detected,
            "num_insiders": num_insiders,
            "total_insider_value": None,
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    return results[:limit]


def get_buyback_status(ticker):
    """Buyback status for a ticker, independent of scanner intensity gate.

    "is_buyback" = trailing 4-quarter share count decline (<0%).
    Adds market-cap tier and a size-adjusted relevance score (bucket-percentile
    × tier weight) so the same -3% buyback is read differently for mega- vs.
    micro-cap. Returns None fields if no data.
    """
    from signals.share_count_change import compute_share_delta
    delta = compute_share_delta(ticker)

    latest_shares = None
    latest_date = None
    mcap = None
    conn = get_db()
    row = conn.execute("""
        SELECT so.date, so.shares
        FROM shares_outstanding so
        JOIN companies c ON so.company_id = c.id
        WHERE c.ticker = ?
        ORDER BY so.date DESC
        LIMIT 1
    """, (ticker,)).fetchone()
    if row:
        latest_date = row["date"]
        latest_shares = row["shares"]
    mcap_row = conn.execute(
        "SELECT market_cap FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    if mcap_row and mcap_row["market_cap"]:
        mcap = float(mcap_row["market_cap"])
    conn.close()

    delta_4q = delta.get("delta_4q")
    is_buyback = isinstance(delta_4q, (int, float)) and delta_4q < 0

    raw_share = float(delta.get("score") or 0)
    sa = _size_adjust(ticker, raw_cluster=0.0, raw_share=raw_share, mcap=mcap)

    return {
        "is_buyback": bool(is_buyback),
        "trend": delta.get("trend"),
        "delta_qoq": delta.get("delta_qoq"),
        "delta_4q": delta_4q,
        "intensity_score_raw": raw_share,
        "relevance_score": sa["share_adjusted"],
        "tier": sa["tier"],
        "tier_weight": sa["tier_weight"],
        "tier_percentile": sa["share_percentile"],
        "tier_thresholds": sa["tier_thresholds"],
        "market_cap": mcap,
        "data_points": delta.get("data_points"),
        "latest_shares": latest_shares,
        "latest_date": latest_date,
    }


def get_cluster(ticker):
    """Get cluster + buyback detail for a ticker."""
    from signals.insider_clusters import detect_clusters
    result = detect_clusters(ticker)

    # Also get recent purchases from DB
    conn = get_db()
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    trades = conn.execute("""
        SELECT it.transaction_date as date, it.reporting_name as name,
               it.shares_transacted as shares, it.price, it.raw_json
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE c.ticker = ? AND it.transaction_type = 'P'
          AND it.transaction_date >= ?
        ORDER BY it.transaction_date DESC
    """, (ticker, cutoff)).fetchall()
    conn.close()

    trade_list = []
    for t in trades:
        raw = json.loads(t["raw_json"]) if t["raw_json"] else {}
        price = t["price"] or 0
        shares = t["shares"] or 0
        trade_list.append({
            "date": t["date"],
            "name": t["name"],
            "shares": shares,
            "price": price,
            "value": round(price * shares, 2),
            "relationship": raw.get("relationship", ""),
        })

    # Size-adjust the cluster score (and reuse buyback's already-adjusted share value).
    bb = get_buyback_status(ticker)
    raw_cluster = float(result.get("score") or 0)
    mcap = bb.get("market_cap")
    sa = _size_adjust(ticker, raw_cluster=raw_cluster, raw_share=0.0, mcap=mcap)

    return {
        "ticker": ticker,
        "cluster_detected": result["cluster_detected"],
        "score": result["score"],
        "score_raw": raw_cluster,
        "relevance_score": sa["cluster_adjusted"],
        "tier": sa["tier"],
        "tier_weight": sa["tier_weight"],
        "tier_percentile": sa["cluster_percentile"],
        "tier_thresholds": sa["tier_thresholds"],
        "market_cap": mcap,
        "trades": trade_list,
        "buyback": bb,
    }


def get_insider_activity(ticker):
    """Full insider purchase history for a ticker."""
    conn = get_db()
    rows = conn.execute("""
        SELECT it.filing_date, it.transaction_date, it.reporting_name as name,
               it.shares_transacted as shares, it.price, it.raw_json
        FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE c.ticker = ? AND it.transaction_type = 'P'
        ORDER BY it.transaction_date DESC
    """, (ticker,)).fetchall()
    conn.close()

    purchases = []
    total_value = 0
    insiders = set()
    dates = []

    for r in rows:
        price = r["price"] or 0
        shares = r["shares"] or 0
        value = round(price * shares, 2)
        total_value += value
        insiders.add(r["name"])
        if r["transaction_date"]:
            dates.append(r["transaction_date"])
        purchases.append({
            "filing_date": r["filing_date"],
            "transaction_date": r["transaction_date"],
            "name": r["name"],
            "shares": shares,
            "price": price,
            "value": value,
        })

    summary = {
        "total_purchases": len(purchases),
        "total_value": round(total_value, 2),
        "unique_insiders": len(insiders),
        "date_range": {"earliest": min(dates) if dates else None, "latest": max(dates) if dates else None},
    }

    return {"ticker": ticker, "purchases": purchases, "summary": summary}


def _pct(x, digits=1):
    if x is None:
        return "n/a"
    try:
        return f"{x * 100:.{digits}f}%"
    except Exception:
        return "n/a"


def _money(x, digits=2):
    if x is None:
        return "n/a"
    try:
        return f"${x:,.{digits}f}"
    except Exception:
        return "n/a"


def _build_summary_text(p):
    """Build a multi-section text summary for the LLM chat context."""
    lines = []
    name = p.get("company_name") or p["ticker"]
    sector = p.get("sector") or "n/a"
    cap_lbl = p.get("market_cap_label") or ""
    cap = p.get("market_cap")
    cap_str = f"${cap/1e9:.1f}B" if cap else "n/a"

    lines.append(f"# {p['ticker']} — {name}")
    lines.append(f"Sector: {sector} ({cap_lbl}, market cap {cap_str})")
    lines.append("")
    lines.append("## Verdict")
    lines.append(
        f"{p.get('verdict','n/a')} — Intrinsic {_money(p.get('intrinsic_value'))} vs "
        f"Current {_money(p.get('current_price'))} ({_pct((p.get('upside_pct') or 0)/100)} upside)"
    )
    lines.append("")
    lines.append("## Method Outputs")
    lines.append(f"- DCF ({p.get('dcf_method') or 'fcff'}): {_money(p.get('dcf_value'))}")
    lines.append(f"- Relative (composite): {_money(p.get('relative_value'))}")
    if p.get("ev_ebitda_implied") is not None:
        lines.append(f"  - EV/EBITDA implied: {_money(p.get('ev_ebitda_implied'))}")
    if p.get("pe_implied") is not None:
        lines.append(f"  - P/E implied: {_money(p.get('pe_implied'))}")
    if p.get("multiples_used"):
        lines.append(f"  - Multiples used: {', '.join(p['multiples_used'])}")
    lines.append(f"- Synthesized: {_money(p.get('intrinsic_value'))}")
    lines.append("")
    lines.append("## DCF Assumptions")
    lines.append(f"- Year 1-5 (stage 1) growth: {_pct(p.get('stage1_growth'))}")
    lines.append(f"- Year 6-10 (stage 2) growth: {_pct(p.get('stage2_growth'))}")
    lines.append(f"- Terminal growth: {_pct(p.get('terminal_growth_rate'))}")
    lines.append(f"- WACC: {_pct(p.get('wacc'))}  |  Ke: {_pct(p.get('ke'))}  |  Kd: {_pct(p.get('kd'))}")
    lines.append(
        f"- Risk-free rate: {_pct(p.get('risk_free_rate'))}  |  ERP: {_pct(p.get('equity_risk_premium'))}  |  "
        f"Beta (relevered): {p.get('beta_relevered') if p.get('beta_relevered') is not None else 'n/a'}"
    )
    lines.append(f"- Effective tax rate: {_pct(p.get('tax_rate'))}")
    if p.get("base_fcf") is not None:
        lines.append(f"- Base FCF: ${p['base_fcf']/1e9:.2f}B")
    if p.get("terminal_value_pct") is not None:
        lines.append(f"- Terminal value as % of total: {p['terminal_value_pct']:.0f}%")
    if p.get("growth_rationale"):
        lines.append(f"- Growth rationale: {p['growth_rationale']}")
    lines.append("")
    lines.append("## Decision Tree")
    lines.append(f"Primary method: {p.get('decision_method') or 'n/a'}")
    if p.get("decision_rationale"):
        lines.append(f"Rationale: {p['decision_rationale']}")
    if p.get("risk_flags"):
        lines.append("")
        lines.append("## Risk Flags")
        for rf in p["risk_flags"]:
            lines.append(f"- {rf}")
    insider = p.get("insider_signal") or {}
    if insider:
        lines.append("")
        lines.append("## Insider Signal")
        lines.append(
            f"Cluster detected: {insider.get('cluster_detected')}  |  "
            f"Score: {insider.get('cluster_score')}  |  Insiders: {insider.get('insider_count')}"
        )
    if p.get("errors"):
        lines.append("")
        lines.append("## Warnings")
        for e in p["errors"]:
            lines.append(f"- {e}")
    return "\n".join(lines)


def run_valuation(ticker):
    """Call agent.orchestrator directly and map to the card's expected schema."""
    from agent.orchestrator import run_valuation as orchestrator_run

    result = orchestrator_run(ticker)

    profile = result.get("profile") or {}
    dcf = result.get("dcf_result") or {}
    rel = result.get("relative_result") or {}
    syn = result.get("synthesis") or {}
    insider = result.get("insider_signal") or {}
    decision = result.get("decision") or {}
    dcf_assumptions = dcf.get("assumptions") or {}

    payload = {
        "ticker": result.get("ticker", ticker.upper()),
        "company_name": profile.get("name"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "market_cap": _to_native(profile.get("market_cap")),
        "market_cap_label": profile.get("market_cap_label"),
        "current_price": _to_native(profile.get("current_price")),
        "intrinsic_value": _to_native(syn.get("weighted_value")),
        "synthesized_value": _to_native(syn.get("weighted_value")),
        "upside_pct": _to_native(syn.get("upside_pct")),
        "dcf_value": _to_native(dcf.get("intrinsic_value_per_share")),
        "relative_value": _to_native(rel.get("composite_implied_price")),
        "ev_ebitda_implied": _to_native(rel.get("ev_ebitda_implied")),
        "pe_implied": _to_native(rel.get("pe_implied")),
        "multiples_used": rel.get("multiples_used") or [],
        "wacc": _to_native(dcf.get("wacc_used")),
        "ke": _to_native(dcf.get("ke")),
        "kd": _to_native(dcf.get("kd")),
        "beta_relevered": _to_native(dcf.get("beta_relevered")),
        "risk_free_rate": _to_native(dcf_assumptions.get("rf")),
        "equity_risk_premium": _to_native(dcf_assumptions.get("erp")),
        "tax_rate": _to_native(dcf_assumptions.get("tax_rate_effective")),
        "dcf_method": dcf_assumptions.get("method"),
        "growth_rate": _to_native(dcf.get("stage1_growth")),
        "stage1_growth": _to_native(dcf.get("stage1_growth")),
        "stage2_growth": _to_native(dcf.get("stage2_growth")),
        "terminal_growth_rate": _to_native(dcf.get("terminal_growth")),
        "base_fcf": _to_native(dcf.get("base_fcf")),
        "terminal_value_pct": _to_native(dcf.get("terminal_value_pct")),
        "growth_rationale": dcf.get("growth_rationale"),
        "decision_method": decision.get("primary_method"),
        "decision_rationale": decision.get("rationale"),
        "verdict": syn.get("verdict"),
        "risk_flags": result.get("red_flags") or [],
        "report_path": result.get("report_path"),
        "errors": result.get("errors") or [],
    }

    if insider:
        payload["insider_signal"] = {
            "cluster_detected": bool(insider.get("cluster_detected")),
            "cluster_score": _to_native(insider.get("cluster_score") or insider.get("score")),
            "insider_count": _to_native(insider.get("num_insiders") or insider.get("unique_insiders")),
        }

    payload["summary_text"] = _build_summary_text(payload)
    return payload


def get_combos():
    """Get combo analysis from signals + backtest data."""
    # Try backtest results first
    backtest_path = os.path.join(INSIDER_TRACKER, "output", "backtest_results.json")
    if os.path.exists(backtest_path):
        with open(backtest_path) as f:
            return json.load(f)

    # Fallback: derive from signals CSV
    signals = get_signals(limit=9999)
    combos = {}
    for s in signals:
        key = (s["sector"] or "Unknown", s["buyback_score"] > 0, s["cluster_detected"])
        if key not in combos:
            combos[key] = {"scores": [], "count": 0}
        combos[key]["scores"].append(s["composite_score"])
        combos[key]["count"] += 1

    result = []
    for (sector, has_buyback, is_cluster), data in combos.items():
        avg = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        result.append({
            "sector": sector,
            "has_buyback": has_buyback,
            "is_sweet_spot": has_buyback and is_cluster,
            "hit_rate": None,
            "avg_composite_score": round(avg, 4),
            "sample_size": data["count"],
        })
    return result


def get_health():
    """DB stats for health check."""
    conn = get_db()
    stats = {}
    stats["total_companies"] = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    stats["total_transactions"] = conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0]
    stats["total_clusters"] = conn.execute(
        "SELECT COUNT(DISTINCT ticker) FROM companies c JOIN insider_transactions it ON it.company_id = c.id WHERE it.transaction_type = 'P'"
    ).fetchone()[0]
    stats["last_transaction_date"] = conn.execute(
        "SELECT MAX(transaction_date) FROM insider_transactions"
    ).fetchone()[0]
    stats["universe_size"] = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE market_cap IS NOT NULL"
    ).fetchone()[0]

    # Check signals CSV freshness
    if os.path.exists(SIGNALS_CSV):
        mtime = os.path.getmtime(SIGNALS_CSV)
        stats["signals_last_updated"] = datetime.fromtimestamp(mtime).isoformat()
        with open(SIGNALS_CSV) as f:
            stats["signals_count"] = sum(1 for _ in f) - 1
    conn.close()
    return stats
