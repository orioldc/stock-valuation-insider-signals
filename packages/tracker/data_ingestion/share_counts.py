"""
How many shares a company has, and whether buybacks are shrinking that number
or new issuance is growing it.

The figures come from the numbers companies tag in their 10-Q and 10-K filings
(SEC "company facts"), read from SEC's nightly archive of every company.

Year-on-year change: every 10-Q and 10-K reports the average number of shares
for the period next to the same period a year earlier. Both numbers come from
the same filing, so they are on the same basis: a stock split is already
applied to the older figure, a filer who mistakenly tags a figure "in
thousands" makes the same mistake on both, and the figure covers every share
class (Meta, Comcast and Alphabet report no single share count anywhere
else). Comparing figures from different filings got all three wrong.

Quarter-on-quarter change: this quarter's average against the previous
quarter's, which comes from a different filing. It is only kept when both
filings provably count shares the way the filing a year earlier did: each
filing's "same quarter last year" figure must match what that earlier filing
reported. A split, a figure in the wrong units or a switch to counting one
share class in between breaks that match. Most companies report no separate
fourth quarter, so quarter-on-quarter changes come from the second- and
third-quarter reports (and the first, where the annual report gives the
fourth quarter).

Each change is dated by the day the filing reached SEC, so a backtest only
sees what the market could have known.

Companies that report shares only per class (Berkshire, Visa) or not at all
(closed-end funds, most SPACs) get no figures: a missing number is visible, a
wrong one is not.
"""

import json
import logging
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import date

import requests

logger = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

# Average basic shares, the share count used for earnings per share.
AVERAGE_SHARES = [
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
    ("ifrs-full", "WeightedAverageShares"),
]
# Shares outstanding on the balance sheet, used when no average is reported.
# Only 10-K pairs count: a 10-Q compares with the last year end, not a year ago.
YEAR_END_SHARES = ("us-gaap", "CommonStockSharesOutstanding")
# The share count on a filing's cover page, as of a date close to filing.
COVER_SHARES = ("dei", "EntityCommonStockSharesOutstanding")

# Net income and earnings per share: their ratio is the average share count,
# worked out from figures in dollars rather than shares.
NET_INCOME = [
    ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),
    ("us-gaap", "NetIncomeLoss"),
    ("us-gaap", "ProfitLoss"),
    ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
]
EPS_BASIC = [
    ("us-gaap", "EarningsPerShareBasic"),
    ("us-gaap", "EarningsPerShareBasicAndDiluted"),
    ("ifrs-full", "BasicEarningsLossPerShare"),
]
# EPS is rounded to the cent; below 10 cents that alone is more than 5% off.
MIN_EPS = 0.10

# Money or shares spent buying back stock, reported for the same period.
REPURCHASES = [
    ("us-gaap", "PaymentsForRepurchaseOfCommonStock"),
    ("us-gaap", "PaymentsForRepurchaseOfEquity"),
    ("us-gaap", "TreasuryStockValueAcquiredCostMethod"),
    ("us-gaap", "StockRepurchasedDuringPeriodValue"),
    ("us-gaap", "StockRepurchasedDuringPeriodShares"),
    ("us-gaap", "TreasuryStockSharesAcquired"),
    ("ifrs-full", "PaymentsToAcquireOrRedeemEntitysShares"),
]

PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# Two share counts from one filing further apart than this are counting
# different things (one share class, or a figure tagged in the wrong units).
AGREEMENT = 0.3

# Year-on-year change thresholds, in percent.
BUYBACK_PCT = -1         # a fall of at least 1% is a buyback
DILUTION_PCT = 1         # a rise of at least 1% is dilution
LARGE_DECLINE_PCT = -25  # beyond this, a buyback needs the filing to report repurchases

# For databases created before share_count_changes existed (same as init_db.py).
SHARE_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS share_count_changes (
    company_id INTEGER NOT NULL,
    accession_number TEXT NOT NULL,
    filed TEXT NOT NULL,
    period_end TEXT NOT NULL,
    basis TEXT NOT NULL,
    shares REAL NOT NULL,
    shares_year_ago REAL NOT NULL,
    repurchases INTEGER NOT NULL,
    shares_prev_quarter REAL,
    PRIMARY KEY (company_id, accession_number)
);
"""

# The same quarter as reported a year ago and as restated now must match this
# closely for two filings to count as being on the same basis.
SAME_BASIS = 0.005


def _days(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _entries(facts, concept, unit="shares"):
    taxonomy, name = concept
    units = facts.get(taxonomy, {}).get(name, {}).get("units", {})
    found = units.get(unit, []) if unit else [e for es in units.values() for e in es]
    return [e for e in found if e.get("form") in PERIODIC_FORMS
            and e.get("val") is not None and e.get("end")]


def _repurchase_periods(facts):
    """{(accession, period_end)} where the filing reports buying back stock."""
    return {(e["accn"], e["end"]) for concept in REPURCHASES
            for e in _entries(facts, concept, unit=None) if e["val"] > 0}


def _by_filing(entries):
    by = defaultdict(list)
    for e in entries:
        by[e["accn"]].append(e)
    return by


def _year_ago_pair(entries, duration_days=None):
    """The latest period in one filing and the same period a year earlier."""
    if duration_days:
        lo, hi = duration_days
        entries = [e for e in entries if e.get("start") and lo <= _days(e["start"], e["end"]) <= hi]
    else:
        entries = [e for e in entries if not e.get("start")]
    if not entries:
        return None
    current = max(entries, key=lambda e: e["end"])
    for e in entries:
        if 350 <= _days(e["end"], current["end"]) <= 380 and e["val"] > 0 and current["val"] > 0:
            return current, e
    return None


def share_changes(facts) -> list:
    """Year-on-year share count changes, one per filing.

    facts: the "facts" object of one company's companyfacts JSON.
    Returns dicts with accession_number, filed, period_end, basis, shares,
    shares_year_ago and repurchases (the filing reports buying back stock in
    the period). basis is 'average_3m' (average over the quarter),
    'average_12m' (average over the year) or 'year_end' (balance sheet).
    """
    repurchased = _repurchase_periods(facts)
    by_filing = defaultdict(dict)  # accession -> basis -> (current, year_ago)
    for concept in AVERAGE_SHARES:
        for accn, entries in _by_filing(_entries(facts, concept)).items():
            for basis, days in (("average_3m", (80, 100)), ("average_12m", (350, 380))):
                pair = _year_ago_pair(entries, days)
                if pair and basis not in by_filing[accn]:
                    by_filing[accn][basis] = pair
    for accn, entries in _by_filing(_entries(facts, YEAR_END_SHARES)).items():
        if not by_filing[accn] and entries[0]["form"].startswith("10-K"):
            pair = _year_ago_pair(entries)
            if pair:
                by_filing[accn]["year_end"] = pair

    changes = []
    for accn, pairs in by_filing.items():
        for basis in ("average_3m", "average_12m", "year_end"):
            if basis in pairs:
                current, year_ago = pairs[basis]
                changes.append({
                    "accession_number": accn,
                    "filed": current["filed"],
                    "period_end": current["end"],
                    "basis": basis,
                    "shares": float(current["val"]),
                    "shares_year_ago": float(year_ago["val"]),
                    "repurchases": (accn, current["end"]) in repurchased,
                })
                break
    return sorted(changes, key=lambda c: (c["filed"], c["accession_number"]))


def _same_basis(change, quarterly):
    """The filing a year before `change` exists and reported the same figure
    that `change` gives for that quarter."""
    year_before = [q for q in quarterly
                   if q["filed"] < change["filed"] and 350 <= _days(q["period_end"], change["period_end"]) <= 380]
    if not year_before:
        return False
    reported = max(year_before, key=lambda q: q["filed"])["shares"]
    return abs(change["shares_year_ago"] / reported - 1) <= SAME_BASIS


def add_previous_quarter(changes) -> list:
    """Set shares_prev_quarter on quarterly changes where it can be trusted.

    changes: one company's share_changes. The previous quarter's average comes
    from the latest earlier filing for that quarter, and is only used when
    both filings pass the same-basis check against the filing a year before
    them, and the two quarters are within a factor of two of each other.
    """
    quarterly = [c for c in changes if c["basis"] == "average_3m"]
    for change in changes:
        change["shares_prev_quarter"] = None
        if change["basis"] != "average_3m" or not _same_basis(change, quarterly):
            continue
        earlier = [q for q in quarterly
                   if q["filed"] < change["filed"] and 80 <= _days(q["period_end"], change["period_end"]) <= 100]
        if not earlier:
            continue
        prev = max(earlier, key=lambda q: q["filed"])
        if _same_basis(prev, quarterly) and 0.5 <= change["shares"] / prev["shares"] <= 2:
            change["shares_prev_quarter"] = prev["shares"]
    return changes


def _by_period(facts, concepts):
    """{(accession, start, end): value} for duration figures, first concept wins."""
    found = {}
    for concept in concepts:
        for e in _entries(facts, concept, unit=None):
            if e.get("start"):
                found.setdefault((e["accn"], e["start"], e["end"]), e["val"])
    return found


def _implied_averages(facts):
    """{accession: average shares} as net income / basic EPS for the filing's
    latest quarter, else its latest year."""
    income, eps = _by_period(facts, NET_INCOME), _by_period(facts, EPS_BASIC)
    best = {}  # accession -> (prefer quarter, latest end, value)
    for (accn, start, end), per_share in eps.items():
        days = _days(start, end)
        quarter = 80 <= days <= 100
        if not (quarter or 350 <= days <= 380) or abs(per_share) < MIN_EPS:
            continue
        value = income.get((accn, start, end), 0) / per_share
        if value > 0:
            best[accn] = max(best.get(accn, (False, "", 0)), (quarter, end, value))
    return {accn: value for accn, (_, _, value) in best.items()}


def _agree(a, b):
    return a is not None and b is not None and abs(a / b - 1) <= AGREEMENT


def share_counts(facts) -> list:
    """The share count each filing reports, dated when it became known.

    A filing's count is only kept when two of its own figures agree: the
    cover-page count, the average count for the period, the balance-sheet
    count at period end, and net income divided by earnings per share. One
    figure alone can be off by a factor of a thousand (tagged in the wrong
    units) and nothing would show it.

    Preference: the cover-page count (dated as of the cover page; the classes
    added up when the cover lists several), else the average count, else the
    balance-sheet count, both dated the day the filing reached SEC. Net
    income / EPS is only a check: it is rounded with the EPS.
    Returns dicts with date, shares, source.
    """
    averages = {}
    for concept in AVERAGE_SHARES:
        for accn, entries in _by_filing(_entries(facts, concept)).items():
            pair = _year_ago_pair(entries, (80, 100)) or _year_ago_pair(entries, (350, 380))
            latest = pair[0] if pair else max(
                (e for e in entries if e.get("start")), key=lambda e: e["end"], default=None)
            if latest and latest["val"] > 0:
                averages.setdefault(accn, latest)
    balances = {}
    for accn, entries in _by_filing(_entries(facts, YEAR_END_SHARES)).items():
        latest = max((e for e in entries if not e.get("start")), key=lambda e: e["end"], default=None)
        if latest and latest["val"] > 0:
            balances[accn] = latest
    covers = {}
    for accn, entries in _by_filing(_entries(facts, COVER_SHARES)).items():
        latest_end = max(e["end"] for e in entries)
        total = sum(e["val"] for e in entries if e["end"] == latest_end)
        if total > 0:
            covers[accn] = (latest_end, float(total))

    implied = _implied_averages(facts)
    counts = {}
    for accn in set(covers) | set(averages) | set(balances):
        average = float(averages[accn]["val"]) if accn in averages else None
        balance = float(balances[accn]["val"]) if accn in balances else None
        check = implied.get(accn)
        filed = (averages.get(accn) or balances.get(accn) or {}).get("filed")
        if accn in covers:
            cover_date, cover = covers[accn]
            if _agree(cover, average) or _agree(cover, balance) or _agree(cover, check):
                counts[cover_date] = (cover, "sec_cover")
                continue
        if not filed:
            continue
        if _agree(average, balance) or _agree(average, check):
            counts.setdefault(filed, (average, "sec_average"))
        elif _agree(balance, check):
            counts.setdefault(filed, (balance, "sec_balance"))
    return [{"date": d, "shares": s, "source": src} for d, (s, src) in sorted(counts.items())]


def change_pct(change) -> float:
    """% change in shares on a year earlier; negative = fewer shares."""
    return (change["shares"] / change["shares_year_ago"] - 1) * 100


def classify_change(change) -> str:
    """'buyback', 'dilution', 'stable' or 'unexplained_decline' for one change.

    A fall of more than 25% in a year only counts as a buyback when the filing
    also reports buying back stock; without that it is usually a restructuring
    or a share exchange.
    """
    pct = change_pct(change)
    if pct <= LARGE_DECLINE_PCT:
        return "buyback" if change["repurchases"] else "unexplained_decline"
    if pct <= BUYBACK_PCT:
        return "buyback"
    if pct >= DILUTION_PCT:
        return "dilution"
    return "stable"


def drop_spikes(counts) -> list:
    """Remove counts at least 5x away from both neighbours that agree with each other.

    Some filers tag both figures of one filing in the wrong units, so the
    same-filing check passes. A split moves the count for good; an error
    like that is gone again by the next filing.
    """
    kept = sorted(counts, key=lambda c: c["date"])
    i = 1
    while i < len(kept) - 1:
        before, now, after = (kept[j]["shares"] for j in (i - 1, i, i + 1))
        if (_agree(before, after)
                and max(now / before, before / now) >= 5 and max(now / after, after / now) >= 5):
            del kept[i]
            i = max(i - 1, 1)
        else:
            i += 1
    return kept


def download_companyfacts(dest_dir=None) -> str:
    """Download SEC's nightly archive of every company's facts (~1.3 GB)."""
    from data_ingestion.edgar_client import _get_headers
    dest_dir = dest_dir or tempfile.gettempdir()
    path = os.path.join(dest_dir, "companyfacts.zip")
    logger.info(f"Downloading {COMPANYFACTS_URL}")
    with requests.get(COMPANYFACTS_URL, headers=_get_headers(), stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return path


def _company_ciks(conn):
    """company_id -> its CIK plus the old CIKs of a re-registered company."""
    ciks = defaultdict(list)
    for company_id, cik in conn.execute("""
            SELECT DISTINCT c.id, c.cik FROM companies c
            JOIN insider_transactions t ON t.company_id = c.id
            WHERE c.cik IS NOT NULL"""):
        ciks[company_id].append(int(cik))
    for cik, company_id in conn.execute("SELECT cik, company_id FROM company_ciks"):
        if company_id in ciks:
            ciks[company_id].append(int(cik))
    return ciks


def rebuild_share_tables(conn, zip_path) -> dict:
    """Replace share_count_changes and shares_outstanding from the archive.

    Covers every company with insider trades. Both tables are rebuilt rather
    than added to, so no figure from an older method survives.
    """
    conn.executescript(SHARE_TABLES_DDL)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(share_count_changes)")}
    if "shares_prev_quarter" not in columns:  # table made before quarter-on-quarter changes
        conn.execute("ALTER TABLE share_count_changes ADD COLUMN shares_prev_quarter REAL")
    conn.execute("DELETE FROM share_count_changes")
    conn.execute("DELETE FROM shares_outstanding")
    stats = {"companies": 0, "with_changes": 0, "with_counts": 0, "changes": 0, "counts": 0}
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        for company_id, ciks in _company_ciks(conn).items():
            stats["companies"] += 1
            changes, counts = [], {}
            # The current CIK comes first. An old CIK's figures only count up
            # to the current one's first: after a re-registration the old
            # company often keeps filing as a subsidiary with a handful of
            # shares (Howard Hughes, 10 shares from 2023).
            cutoff = None
            for cik in ciks:
                name = f"CIK{cik:010d}.json"
                if name not in names:
                    continue
                facts = json.loads(archive.read(name)).get("facts", {})
                cik_changes, cik_counts = share_changes(facts), share_counts(facts)
                if cutoff:
                    cik_changes = [c for c in cik_changes if c["filed"] < cutoff]
                    cik_counts = [c for c in cik_counts if c["date"] < cutoff]
                elif cik_changes or cik_counts:
                    cutoff = min([c["filed"] for c in cik_changes] + [c["date"] for c in cik_counts])
                changes += cik_changes
                for c in cik_counts:
                    counts.setdefault(c["date"], c)
            add_previous_quarter(changes)
            conn.executemany("""
                INSERT OR IGNORE INTO share_count_changes
                    (company_id, accession_number, filed, period_end, basis, shares,
                     shares_year_ago, repurchases, shares_prev_quarter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(company_id, c["accession_number"], c["filed"], c["period_end"], c["basis"],
                  c["shares"], c["shares_year_ago"], c["repurchases"], c["shares_prev_quarter"])
                 for c in changes])
            counts = drop_spikes(counts.values())
            conn.executemany(
                "INSERT INTO shares_outstanding (company_id, date, shares, source) VALUES (?, ?, ?, ?)",
                [(company_id, c["date"], c["shares"], c["source"]) for c in counts])
            stats["with_changes"] += bool(changes)
            stats["with_counts"] += bool(counts)
            stats["changes"] += len(changes)
            stats["counts"] += len(counts)
    conn.commit()
    return stats


def refresh_share_tables(conn) -> dict:
    """Download the archive, rebuild both share tables, delete the download."""
    with tempfile.TemporaryDirectory() as tmp:
        return rebuild_share_tables(conn, download_companyfacts(tmp))


def share_change_as_of(conn, company_id, as_of=None):
    """The latest year-on-year share change known on a date (default today).

    Returns the share_count_changes row as a dict with extra change_pct and
    change_qoq_pct (negative = fewer shares; change_qoq_pct None when the
    filing has no trusted previous quarter), or None.
    """
    as_of = as_of or date.today().isoformat()
    row = conn.execute("""
        SELECT filed, period_end, basis, shares, shares_year_ago, repurchases, shares_prev_quarter
        FROM share_count_changes
        WHERE company_id = ? AND filed <= ?
        -- Newest period wins, so a late amendment for an older year cannot hide a
        -- newer quarter. For the same period the latest filing (the amendment) wins.
        ORDER BY period_end DESC, filed DESC, accession_number DESC
        LIMIT 1""", (company_id, as_of)).fetchone()
    if not row:
        return None
    filed, period_end, basis, shares, year_ago, repurchases, prev_quarter = row
    change = {"filed": filed, "period_end": period_end, "basis": basis, "shares": shares,
              "shares_year_ago": year_ago, "repurchases": bool(repurchases)}
    qoq = (shares / prev_quarter - 1) * 100 if prev_quarter else None
    return {**change, "change_pct": change_pct(change), "change_qoq_pct": qoq}
