"""Bridge to tracker and valuation packages.

Path resolution: defaults assume this file lives at packages/mcp/api/bridge.py
inside the monorepo, so the sibling packages are at ../../tracker and
../../valuation. Power users can override via env vars.

Data files (DB + scanner CSV) default to the repo's data/ directory so that
the install script can populate them from a release artifact.
"""

import csv
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

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

# GitHub release cache (memoized for ~6 hours to avoid hammering the API)
# Maps {tag_name: published_at_date} for all data-* releases, plus latest_tag
_GITHUB_RELEASE_CACHE: dict = {"fetched_at": 0.0, "latest_tag": None, "tag_dates": {}}


def _fetch_github_releases() -> None:
    """Fetch data-* releases from GitHub and cache {tag_name: published_at_date}.

    Updates the global cache with latest_tag and tag_dates map.
    Success cache: 6 hours. Failure cache: 5 minutes.
    """
    now = time.time()
    # Check cache (6 hours = 21600 seconds on success, 5 min = 300s on failure)
    cached_at = _GITHUB_RELEASE_CACHE.get("fetched_at", 0)
    if now - cached_at < 21600 and _GITHUB_RELEASE_CACHE.get("latest_tag") is not None:
        return  # Fresh success cache
    if now - cached_at < 300:
        return  # Recent failure, don't retry yet

    try:
        import urllib.request
        url = "https://api.github.com/repos/orioldc/stock-valuation-insider-signals/releases?per_page=30"
        req = urllib.request.Request(url, headers={"User-Agent": "insider-signal-mcp"})
        with urllib.request.urlopen(req, timeout=3) as response:
            releases = json.loads(response.read())

        # Build {tag_name: YYYY-MM-DD} map for all data-* releases
        tag_dates = {}
        data_tags = []
        for r in releases:
            tag = r.get("tag_name", "")
            if tag.startswith("data-"):
                data_tags.append(tag)
                # published_at is ISO timestamp, extract date portion
                published = r.get("published_at", "")
                if published:
                    tag_dates[tag] = published[:10]  # "YYYY-MM-DD"

        latest = data_tags[0] if data_tags else None
        _GITHUB_RELEASE_CACHE["fetched_at"] = now
        _GITHUB_RELEASE_CACHE["latest_tag"] = latest
        _GITHUB_RELEASE_CACHE["tag_dates"] = tag_dates
    except Exception:
        # Negative-cache failures for 5 minutes
        _GITHUB_RELEASE_CACHE["fetched_at"] = now


def _snapshot_metadata(release_tag: str = None) -> dict:
    """Return snapshot metadata: as_of (YYYY-MM-DD), age_days, as_of_source.

    Prefers the release tag's published_at from GitHub API (cached).
    Falls back to CSV file mtime when tag or API result unavailable.
    Returns None fields if both are unavailable.
    """
    # Try release-published date first
    _fetch_github_releases()
    tag_dates = _GITHUB_RELEASE_CACHE.get("tag_dates", {})

    if release_tag and release_tag in tag_dates:
        as_of_str = tag_dates[release_tag]
        as_of_dt = datetime.strptime(as_of_str, "%Y-%m-%d")
        age_days = (datetime.now() - as_of_dt).days
        return {
            "release": release_tag,
            "as_of": as_of_str,
            "as_of_source": "release_published_at",
            "age_days": age_days,
        }

    # Fall back to file mtime
    if os.path.exists(SIGNALS_CSV):
        mtime = os.path.getmtime(SIGNALS_CSV)
        as_of_dt = datetime.fromtimestamp(mtime)
        age_days = (datetime.now() - as_of_dt).days
        return {
            "release": release_tag,
            "as_of": as_of_dt.strftime("%Y-%m-%d"),
            "as_of_source": "file_mtime",
            "age_days": age_days,
        }

    # No data available
    return {
        "release": release_tag,
        "as_of": None,
        "as_of_source": "unavailable",
        "age_days": None,
    }


def _get_installed_release() -> str | None:
    """Read installed release tag from data/.data_release. Returns None if missing."""
    release_file = os.path.join(_REPO_ROOT, "data", ".data_release")
    if os.path.exists(release_file):
        with open(release_file) as f:
            return f.read().strip()
    return None


def _load_universe() -> dict:
    """Load (ticker → {mcap, tier, raw_cluster, raw_share}) + per-tier score arrays.

    Memoized; reloads only when latest_signals.csv mtime changes.

    Market cap is derived from (latest price × latest shares) when both are available,
    falling back to companies.market_cap otherwise. This ensures tier assignment
    uses fresh data even when the stored value is stale.
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
    mcap_by_ticker: dict[str, dict] = {}  # ticker → {mcap, asof, source}
    if tickers:
        conn = get_db()
        placeholders = ",".join("?" * len(tickers))

        # Check if market_cap_asof column exists (tolerate old DBs)
        cur = conn.execute("PRAGMA table_info(companies)")
        columns = [col[1] for col in cur.fetchall()]
        has_asof_column = "market_cap_asof" in columns

        # Get stored market cap
        stored_mcap = {}
        if has_asof_column:
            for r in conn.execute(
                f"SELECT ticker, market_cap, market_cap_asof FROM companies WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall():
                stored_mcap[r["ticker"]] = {
                    "mcap": float(r["market_cap"]) if r["market_cap"] else None,
                    "asof": r["market_cap_asof"],
                }
        else:
            for r in conn.execute(
                f"SELECT ticker, market_cap FROM companies WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall():
                stored_mcap[r["ticker"]] = {
                    "mcap": float(r["market_cap"]) if r["market_cap"] else None,
                    "asof": None,
                }

        # Derive market cap from price × shares
        for r in conn.execute(f"""
            SELECT c.ticker, p.date as price_date, p.close, so.shares
            FROM companies c
            LEFT JOIN (
                SELECT ticker, date, close,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) as rn
                FROM prices
            ) p ON c.ticker = p.ticker AND p.rn = 1
            LEFT JOIN (
                SELECT company_id, shares,
                       ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY date DESC) as rn
                FROM shares_outstanding
            ) so ON c.id = so.company_id AND so.rn = 1
            WHERE c.ticker IN ({placeholders})
        """, tickers).fetchall():
            ticker = r["ticker"]
            if r["close"] and r["shares"]:
                # Derived value available
                derived_mcap = float(r["close"]) * float(r["shares"])
                mcap_by_ticker[ticker] = {
                    "mcap": derived_mcap,
                    "asof": r["price_date"],
                    "source": "derived_price_x_shares",
                }
            elif ticker in stored_mcap and stored_mcap[ticker]["mcap"]:
                # Fall back to stored value
                mcap_by_ticker[ticker] = {
                    "mcap": stored_mcap[ticker]["mcap"],
                    "asof": stored_mcap[ticker]["asof"],
                    "source": "companies_table",
                }
            else:
                # No data available
                mcap_by_ticker[ticker] = {
                    "mcap": None,
                    "asof": None,
                    "source": "unavailable",
                }

        conn.close()

    by_ticker: dict[str, dict] = {}
    by_tier_cluster: dict[str, list[float]] = {t: [] for t in TIER_ORDER}
    by_tier_share: dict[str, list[float]] = {t: [] for t in TIER_ORDER}

    for r in rows:
        ticker = r["ticker"]
        mcap_info = mcap_by_ticker.get(ticker, {"mcap": None, "asof": None, "source": "unavailable"})
        mcap = mcap_info["mcap"]
        tier = get_tier(mcap)
        try:
            raw_cluster = float(r.get("cluster_score_raw") or 0)
        except ValueError:
            raw_cluster = 0.0
        try:
            raw_share = float(r.get("share_score_raw") or 0)
        except ValueError:
            raw_share = 0.0
        cluster_detected_str = r.get("cluster_detected", "").lower()
        cluster_detected = cluster_detected_str == "true"
        try:
            cluster_adj = float(r.get("cluster_adj") or 0)
        except ValueError:
            cluster_adj = 0.0
        by_ticker[ticker] = {
            "mcap": mcap,
            "mcap_asof": mcap_info["asof"],
            "mcap_source": mcap_info["source"],
            "tier": tier,
            "raw_cluster": raw_cluster,
            "raw_share": raw_share,
            "cluster_detected": cluster_detected,
            "cluster_adj": cluster_adj,
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

    # Get snapshot metadata (prefer release published_at over file mtime)
    release_tag = _get_installed_release()
    snapshot_meta = _snapshot_metadata(release_tag)

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

        # cluster_details = trade count; cluster_insiders = distinct CIK count
        cluster_details = r.get("cluster_details", "0")
        try:
            num_trades = int(cluster_details) if cluster_details else 0
        except ValueError:
            num_trades = 0

        cluster_insiders = r.get("cluster_insiders")
        if cluster_insiders:
            try:
                num_insiders = int(cluster_insiders)
            except ValueError:
                num_insiders = None
        else:
            # Column absent (all pre-2026-08 releases): return None, not the trade count
            num_insiders = None

        raw_cluster = float(r.get("cluster_score_raw", 0) or 0)
        raw_share = float(r.get("share_score_raw", 0) or 0)
        raw_composite = float(r.get("composite", 0) or 0)

        # Get enriched market cap from universe (derived or fallback)
        universe = _load_universe()
        ticker_info = universe["by_ticker"].get(r["ticker"], {})
        mcap = ticker_info.get("mcap") or co.get("market_cap")
        mcap_asof = ticker_info.get("mcap_asof")
        mcap_source = ticker_info.get("mcap_source", "companies_table")

        sa = _size_adjust(r["ticker"], raw_cluster, raw_share, mcap=mcap)
        composite_adj = round(0.6 * sa["cluster_adjusted"] + 0.4 * sa["share_adjusted"], 4)

        if composite_adj < min_score:
            continue

        results.append({
            "ticker": r["ticker"],
            "name": co.get("name"),
            "sector": co.get("sector"),
            "market_cap": mcap,
            "market_cap_asof": mcap_asof,
            "market_cap_source": mcap_source,
            "tier": sa["tier"],
            "composite_score": composite_adj,
            "composite_score_raw": raw_composite,
            "cluster_score": raw_cluster,
            "cluster_adjusted": sa["cluster_adjusted"],
            "buyback_score": raw_share,
            "buyback_adjusted": sa["share_adjusted"],
            "cluster_detected": cluster_detected,
            "num_trades": num_trades,
            "num_insiders": num_insiders,
            "total_insider_value": None,
            "release": snapshot_meta["release"],
            "as_of": snapshot_meta["as_of"],
            "as_of_source": snapshot_meta["as_of_source"],
            "age_days": snapshot_meta["age_days"],
        })

    # Batch-compute base rates for all unique (sector, tier) combinations
    # to avoid N DB round trips (there are ~40 combinations across 500 rows)
    try:
        from scoring.base_rates import score_cluster

        # Collect unique (sector, tier) pairs
        segment_keys = set()
        for result in results:
            segment_keys.add((result["sector"], result["tier"]))

        # Query base rates for all segments
        base_rate_cache = {}
        for sector_val, tier_val in segment_keys:
            try:
                base_rate_cache[(sector_val, tier_val)] = score_cluster(DB_PATH, sector_val, tier_val)
            except Exception as e:
                logger.exception(f"Failed to score segment ({sector_val}, {tier_val})")
                base_rate_cache[(sector_val, tier_val)] = {
                    "base_rate": None,
                    "ci_lower": None,
                    "ci_upper": None,
                    "n_samples": 0,
                    "level_used": None,
                    "suppressed": True,
                    "reason": f"scoring failed: {type(e).__name__}",
                    "spy_beat_rate": None,
                    "qqq_beat_rate": None,
                }

        # Attach base rates to each result
        for result in results:
            segment_key = (result["sector"], result["tier"])
            conviction = base_rate_cache.get(segment_key, {})
            result["base_rate"] = conviction.get("base_rate")
            result["base_rate_ci_lower"] = conviction.get("ci_lower")
            result["base_rate_ci_upper"] = conviction.get("ci_upper")
            result["base_rate_n_samples"] = conviction.get("n_samples")
            result["base_rate_level_used"] = conviction.get("level_used")
            result["base_rate_suppressed"] = conviction.get("suppressed")
            result["base_rate_reason"] = conviction.get("reason")
            result["spy_beat_rate"] = conviction.get("spy_beat_rate")
            result["qqq_beat_rate"] = conviction.get("qqq_beat_rate")
    except Exception as e:
        logger.exception("Failed to batch-compute base rates for scanner")
        # If batch computation fails, populate all results with error fields
        for result in results:
            result["base_rate"] = None
            result["base_rate_ci_lower"] = None
            result["base_rate_ci_upper"] = None
            result["base_rate_n_samples"] = 0
            result["base_rate_level_used"] = None
            result["base_rate_suppressed"] = True
            result["base_rate_reason"] = f"batch scoring failed: {type(e).__name__}"
            result["spy_beat_rate"] = None
            result["qqq_beat_rate"] = None

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
    mcap_asof = None
    mcap_source = "unavailable"

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

    # Derive market cap from price × shares (prefer fresh data)
    derived_row = conn.execute("""
        SELECT p.date as price_date, p.close, so.shares, c.market_cap
        FROM companies c
        LEFT JOIN (
            SELECT ticker, date, close
            FROM prices
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT 1
        ) p ON c.ticker = p.ticker
        LEFT JOIN (
            SELECT company_id, shares
            FROM shares_outstanding
            WHERE company_id = (SELECT id FROM companies WHERE ticker = ?)
            ORDER BY date DESC
            LIMIT 1
        ) so ON c.id = so.company_id
        WHERE c.ticker = ?
    """, (ticker, ticker, ticker)).fetchone()

    if derived_row:
        if derived_row["close"] and derived_row["shares"]:
            # Derived value available
            mcap = float(derived_row["close"]) * float(derived_row["shares"])
            mcap_asof = derived_row["price_date"]
            mcap_source = "derived_price_x_shares"
        elif derived_row["market_cap"]:
            # Fall back to stored value
            mcap = float(derived_row["market_cap"])
            mcap_asof = None
            mcap_source = "companies_table"

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
        "market_cap_asof": mcap_asof,
        "market_cap_source": mcap_source,
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

    # Build frozen snapshot block from the CSV data loaded by _load_universe
    universe = _load_universe()
    frozen_entry = universe["by_ticker"].get(ticker)
    release_tag = _get_installed_release()
    snapshot_meta = _snapshot_metadata(release_tag)

    if frozen_entry:
        frozen = {
            "in_snapshot": True,
            "cluster_detected": frozen_entry.get("cluster_detected"),
            "score_raw": frozen_entry.get("raw_cluster"),
            "cluster_adjusted": frozen_entry.get("cluster_adj"),
            "release": snapshot_meta["release"],
            "as_of": snapshot_meta["as_of"],
            "as_of_source": snapshot_meta["as_of_source"],
            "age_days": snapshot_meta["age_days"],
        }
    else:
        frozen = {
            "in_snapshot": False,
            "cluster_detected": None,
            "score_raw": None,
            "cluster_adjusted": None,
            "release": snapshot_meta["release"],
            "as_of": snapshot_meta["as_of"],
            "as_of_source": snapshot_meta["as_of_source"],
            "age_days": snapshot_meta["age_days"],
        }

    # Compute base-rate conviction for live signal
    conviction_data = _compute_conviction_live(ticker, result, release_tag)

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
        "market_cap_asof": bb.get("market_cap_asof"),
        "market_cap_source": bb.get("market_cap_source", "unavailable"),
        "trades": trade_list,
        "buyback": bb,
        "frozen": frozen,
        # Base-rate conviction fields
        "base_rate": conviction_data.get("base_rate"),
        "base_rate_ci_lower": conviction_data.get("base_rate_ci_lower"),
        "base_rate_ci_upper": conviction_data.get("base_rate_ci_upper"),
        "base_rate_n_samples": conviction_data.get("base_rate_n_samples"),
        "base_rate_level_used": conviction_data.get("base_rate_level_used"),
        "base_rate_suppressed": conviction_data.get("base_rate_suppressed"),
        "base_rate_reason": conviction_data.get("base_rate_reason"),
        "spy_beat_rate": conviction_data.get("spy_beat_rate"),
        "qqq_beat_rate": conviction_data.get("qqq_beat_rate"),
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


def _compute_conviction_live(ticker: str, insider: dict, hit_rates_release: str | None) -> dict:
    """Compute base-rate conviction for a live insider signal.

    Returns dict with base_rate, ci_lower, ci_upper, n_samples, level_used, suppressed, reason,
    spy_beat_rate, qqq_beat_rate, conviction_score (deprecated). Never returns empty dict;
    always includes base_rate_reason describing any failure.
    """
    try:
        from scoring.base_rates import score_cluster
        from signals.size_adjustment import get_tier
    except Exception as e:
        logger.exception("Failed to import scoring modules")
        return {
            "base_rate": None,
            "base_rate_ci_lower": None,
            "base_rate_ci_upper": None,
            "base_rate_n_samples": 0,
            "base_rate_level_used": None,
            "base_rate_suppressed": True,
            "base_rate_reason": f"import failed: {type(e).__name__}",
            "spy_beat_rate": None,
            "qqq_beat_rate": None,
            "conviction_score": None,
            "conviction_source": "base_rate_model",
            "conviction_deprecated": True,
            "conviction_deprecation_note": "Use base_rate fields instead; conviction_score will be removed in next release",
        }

    # Get sector and market cap for tier derivation
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT sector, market_cap FROM companies WHERE ticker = ?", (ticker,)
        ).fetchone()
        if not row:
            conn.close()
            return {
                "base_rate": None,
                "base_rate_ci_lower": None,
                "base_rate_ci_upper": None,
                "base_rate_n_samples": 0,
                "base_rate_level_used": None,
                "base_rate_suppressed": True,
                "base_rate_reason": f"ticker {ticker} not found in database",
                "spy_beat_rate": None,
                "qqq_beat_rate": None,
                "conviction_score": None,
                "conviction_source": "base_rate_model",
                "conviction_deprecated": True,
                "conviction_deprecation_note": "Use base_rate fields instead; conviction_score will be removed in next release",
            }
        sector = row["sector"] if row["sector"] else None
        market_cap = float(row["market_cap"]) if row["market_cap"] else None
        conn.close()
    except Exception as e:
        logger.exception(f"DB lookup failed for {ticker}")
        return {
            "base_rate": None,
            "base_rate_ci_lower": None,
            "base_rate_ci_upper": None,
            "base_rate_n_samples": 0,
            "base_rate_level_used": None,
            "base_rate_suppressed": True,
            "base_rate_reason": f"database lookup failed: {type(e).__name__}",
            "spy_beat_rate": None,
            "qqq_beat_rate": None,
            "conviction_score": None,
            "conviction_source": "base_rate_model",
            "conviction_deprecated": True,
            "conviction_deprecation_note": "Use base_rate fields instead; conviction_score will be removed in next release",
        }

    # Require market cap for tier assignment, but allow NULL sector (tier fallback)
    if not market_cap:
        return {
            "base_rate": None,
            "base_rate_ci_lower": None,
            "base_rate_ci_upper": None,
            "base_rate_n_samples": 0,
            "base_rate_level_used": None,
            "base_rate_suppressed": True,
            "base_rate_reason": f"market_cap missing for {ticker}",
            "spy_beat_rate": None,
            "qqq_beat_rate": None,
            "conviction_score": None,
            "conviction_source": "base_rate_model",
            "conviction_deprecated": True,
            "conviction_deprecation_note": "Use base_rate fields instead; conviction_score will be removed in next release",
        }

    # Derive size tier from market cap
    size_tier = get_tier(market_cap)

    # Score using base-rate model (handles NULL sector with tier fallback)
    try:
        result = score_cluster(DB_PATH, sector, size_tier)
    except Exception as e:
        logger.exception(f"score_cluster failed for {ticker}")
        return {
            "base_rate": None,
            "base_rate_ci_lower": None,
            "base_rate_ci_upper": None,
            "base_rate_n_samples": 0,
            "base_rate_level_used": None,
            "base_rate_suppressed": True,
            "base_rate_reason": f"scoring failed: {type(e).__name__}",
            "spy_beat_rate": None,
            "qqq_beat_rate": None,
            "conviction_score": None,
            "conviction_source": "base_rate_model",
            "conviction_deprecated": True,
            "conviction_deprecation_note": "Use base_rate fields instead; conviction_score will be removed in next release",
        }

    # Legacy conviction_score (deprecated): map base_rate to 0-100 scale
    # This maintains API compatibility for one release cycle
    if result['base_rate'] is not None:
        # Simple linear mapping: 50% base_rate = 50 points, 100% = 100 points, 0% = 0 points
        conviction_score_deprecated = result['base_rate'] * 100
    else:
        conviction_score_deprecated = None

    return {
        # New base-rate fields (primary)
        "base_rate": result['base_rate'],
        "base_rate_ci_lower": result['ci_lower'],
        "base_rate_ci_upper": result['ci_upper'],
        "base_rate_n_samples": result['n_samples'],
        "base_rate_level_used": result['level_used'],
        "base_rate_suppressed": result['suppressed'],
        "base_rate_reason": result['reason'],
        "spy_beat_rate": result['spy_beat_rate'],
        "qqq_beat_rate": result['qqq_beat_rate'],
        # Legacy fields (deprecated, for backward compatibility)
        "conviction_score": conviction_score_deprecated,
        "conviction_source": "base_rate_model",
        "conviction_deprecated": True,
        "conviction_deprecation_note": "Use base_rate fields instead; conviction_score will be removed in next release",
    }


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

    # Build current price line with date
    price_str = _money(p.get('current_price'))
    price_date = p.get('price_date')
    if price_date:
        price_display = f"{price_str} (close {price_date})"
    else:
        price_display = price_str

    lines.append(
        f"{p.get('verdict','n/a')} — Intrinsic {_money(p.get('intrinsic_value'))} vs "
        f"Current {price_display} ({_pct((p.get('upside_pct') or 0)/100)} upside)"
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
        parts = [f"Cluster detected: {insider.get('cluster_detected')}"]

        # Show base-rate information if available
        if insider.get("base_rate") is not None:
            br = insider['base_rate']
            ci_lower = insider.get('base_rate_ci_lower')
            ci_upper = insider.get('base_rate_ci_upper')
            n_samples = insider.get('base_rate_n_samples')
            level_used = insider.get('base_rate_level_used')
            suppressed = insider.get('base_rate_suppressed')

            rate_str = f"Base rate: {br:.1%}"
            if ci_lower is not None and ci_upper is not None:
                rate_str += f" [{ci_lower:.1%}-{ci_upper:.1%}]"
            if n_samples:
                rate_str += f", n={n_samples}"
            if suppressed:
                rate_str += f" (tier fallback)"
            elif level_used == 'segment':
                rate_str += f" (sector+size)"
            parts.append(rate_str)

            # Show SPY/QQQ beat rates
            if insider.get("spy_beat_rate") is not None:
                parts.append(f"SPY beat: {insider['spy_beat_rate']:.1%}")
            if insider.get("qqq_beat_rate") is not None:
                parts.append(f"QQQ beat: {insider['qqq_beat_rate']:.1%}")
        elif insider.get("base_rate_reason"):
            # No base rate available, show reason
            parts.append(f"Base rate: {insider['base_rate_reason']}")
        elif insider.get("conviction_score") is not None:
            # Fallback to deprecated conviction_score for old snapshots
            max_ach = insider.get("conviction_max_achievable")
            if max_ach is None:
                conv_str = f"Conviction: {insider['conviction_score']:.1f} (deprecated, from snapshot)"
            else:
                conv_str = f"Conviction: {insider['conviction_score']:.1f}/{max_ach} (deprecated)"
                missing = insider.get("conviction_missing_components")
                if missing:
                    conv_str += f" ({', '.join(missing)} unavailable)"
            parts.append(conv_str)

        if insider.get("quality"):
            parts.append(f"Quality: {insider['quality']}")
        lines.append("  |  ".join(parts))

        # Count line with window
        count_parts = []
        if insider.get("insider_count") is not None:
            count_str = f"Insiders: {insider['insider_count']}"
            if insider.get("count_window_days"):
                count_str += f" ({insider['count_window_days']}d window)"
            count_parts.append(count_str)
        if insider.get("latest_transaction_date"):
            count_parts.append(f"Latest trade: {insider['latest_transaction_date']}")
        if count_parts:
            lines.append("  |  ".join(count_parts))

        # Provenance line
        prov_parts = []
        if insider.get("source"):
            src_str = f"Source: {insider['source']}"
            if insider.get("as_of"):
                src_str += f" (as of {insider['as_of']})"
            prov_parts.append(src_str)
        if insider.get("hit_rates_release"):
            prov_parts.append(f"Hit rates: {insider['hit_rates_release']} backtest")
        if prov_parts:
            lines.append("  |  ".join(prov_parts))
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

    # Get hit-rates release tag for provenance
    hit_rates_release = _get_installed_release()

    # Handle conviction source provenance
    if insider:
        if insider.get("source") == "live_edgar" and insider.get("conviction_score") is None:
            # Compute conviction for live signal
            computed = _compute_conviction_live(ticker, insider, hit_rates_release)
            if computed:
                insider.update(computed)
        elif insider.get("source") == "frozen_snapshot" and insider.get("conviction_score") is not None:
            # Mark frozen conviction with provenance
            # Scale unknown: the frozen file records only the score, not the components or max.
            # historical_clusters.csv was never committed and is not produced by any pipeline step,
            # so the May snapshot was likely also computed with historical_accuracy=0 (max=80),
            # but we cannot prove it. Treat as genuinely unknown to avoid cross-source inconsistency.
            insider["conviction_source"] = "frozen_snapshot"
            insider["hit_rates_release"] = hit_rates_release
            insider["conviction_max_achievable"] = None
            insider["conviction_missing_components"] = None

    payload = {
        "ticker": result.get("ticker", ticker.upper()),
        "company_name": profile.get("name"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "market_cap": _to_native(profile.get("market_cap")),
        "market_cap_label": profile.get("market_cap_label"),
        "current_price": _to_native(profile.get("current_price")),
        "price_date": profile.get("price_date"),
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
        # Frozen snapshot keys: conviction_score, n_insiders, quality, total_value, latest_transaction_date
        # Live fetcher fallback keys: cluster_score/score, num_insiders/unique_insiders
        # Use explicit is-not-None checks to avoid treating 0 as missing
        conviction = insider.get("conviction_score")
        if conviction is None:
            conviction = insider.get("cluster_score")
        if conviction is None:
            conviction = insider.get("score")
        conviction = _to_native(conviction)

        insider_count = insider.get("n_insiders")
        if insider_count is None:
            insider_count = insider.get("num_insiders")
        if insider_count is None:
            insider_count = insider.get("unique_insiders")
        insider_count = _to_native(insider_count)

        payload["insider_signal"] = {
            "cluster_detected": bool(insider.get("cluster_detected")),
            # Base-rate fields (primary)
            "base_rate": _to_native(insider.get("base_rate")),
            "base_rate_ci_lower": _to_native(insider.get("base_rate_ci_lower")),
            "base_rate_ci_upper": _to_native(insider.get("base_rate_ci_upper")),
            "base_rate_n_samples": _to_native(insider.get("base_rate_n_samples")),
            "base_rate_level_used": insider.get("base_rate_level_used"),
            "base_rate_suppressed": insider.get("base_rate_suppressed"),
            "base_rate_reason": insider.get("base_rate_reason"),
            "spy_beat_rate": _to_native(insider.get("spy_beat_rate")),
            "qqq_beat_rate": _to_native(insider.get("qqq_beat_rate")),
            # Legacy fields (deprecated)
            "conviction_score": conviction,
            "conviction_deprecated": insider.get("conviction_deprecated"),
            "conviction_deprecation_note": insider.get("conviction_deprecation_note"),
            "conviction_source": insider.get("conviction_source"),
            "conviction_max_achievable": insider.get("conviction_max_achievable"),
            "conviction_missing_components": insider.get("conviction_missing_components"),
            # Other fields
            "insider_count": insider_count,
            "quality": insider.get("quality"),
            "total_value": _to_native(insider.get("total_value")),
            "latest_transaction_date": insider.get("latest_transaction_date"),
            "source": insider.get("source"),
            "as_of": insider.get("as_of"),
            "count_window_days": insider.get("count_window_days"),
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

    # Check signals CSV row count
    if os.path.exists(SIGNALS_CSV):
        with open(SIGNALS_CSV) as f:
            stats["signals_count"] = sum(1 for _ in f) - 1
    conn.close()

    # Snapshot block: installed vs. latest release
    release_tag = _get_installed_release()
    meta = _snapshot_metadata(release_tag)

    # Align signals_last_updated with snapshot as_of (data vintage, not download time)
    # Preserves field for backward compatibility but uses release date when available
    if meta["as_of"]:
        stats["signals_last_updated"] = meta["as_of"]
    elif os.path.exists(SIGNALS_CSV):
        # Fallback to file mtime when release date unavailable
        mtime = os.path.getmtime(SIGNALS_CSV)
        stats["signals_last_updated"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

    # Fetch latest release from GitHub (cached, non-blocking)
    _fetch_github_releases()
    latest = _GITHUB_RELEASE_CACHE.get("latest_tag")

    # Compute releases_behind (months behind with monthly cadence; None if unparseable)
    releases_behind = None
    if release_tag and latest:
        # If identical tags, we're current
        if release_tag == latest:
            releases_behind = 0
        else:
            try:
                # Extract YYYY-MM from "data-YYYY-MM" tags, ignoring any suffix
                # (e.g., "data-2026-08-0830" → parse "data-2026-08")
                installed_parts = release_tag.split("-")
                latest_parts = latest.split("-")
                if len(installed_parts) >= 3 and len(latest_parts) >= 3:
                    installed_ym = (int(installed_parts[1]), int(installed_parts[2]))
                    latest_ym = (int(latest_parts[1]), int(latest_parts[2]))
                    # Rough month diff (ignores day precision)
                    months_behind = (latest_ym[0] - installed_ym[0]) * 12 + (latest_ym[1] - installed_ym[1])
                    releases_behind = max(0, months_behind)
            except (ValueError, IndexError):
                # Unparseable tag format; degrade gracefully
                pass

    snapshot = {
        "installed_release": release_tag,
        "as_of": meta["as_of"],
        "as_of_source": meta["as_of_source"],
        "age_days": meta["age_days"],
        "latest_release": latest,
        "releases_behind": releases_behind,
    }

    stats["snapshot"] = snapshot
    return stats
