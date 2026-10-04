#!/usr/bin/env python3
"""
Check a freshly rebuilt database against SEC itself.

Comparing a rebuild with the last release only shows the two agree; a mistake
both contain (Berkshire's OXY purchases booked as BRK-B insider buying) passes.
This audit checks the data against its source instead:

1. Rules that must always hold: one owner per filing, no trade stored twice,
   no trade dated after its filing, every company identified by its CIK.
2. A random sample of purchases and sales re-read from the original filings
   on SEC: same company, insider, code, date, shares and price.
3. A random sample of filings from SEC's quarterly index: every trade in
   them must be in the database.
4. Known cases that went wrong before (Berkshire/OXY, BlackRock's and
   DraftKings' re-registrations, the reused ticker BOX).
5. Share counts: companies known to buy back or issue stock come out that
   way, share counts for large companies are in the right range, buybacks
   are backed by reported repurchases, and counts and changes agree.

Usage:
    python pipeline/correctness_audit.py --db db/insider_signals.db \\
        [--json report.json] [--sample 150] [--filings 100] [--seed N]

Exit codes: 0 all FAIL-level checks passed, 1 one or more failed, 2 error.
"""

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
from datetime import date, timedelta

TRACKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (TRACKER, os.path.join(TRACKER, "data_ingestion")):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_ingestion.edgar_client import _get, parse_form4_document  # noqa: E402
from data_ingestion.form4_rules import TRANSACTION_FORM_TYPES, repeated_across_owners  # noqa: E402
from data_ingestion.share_counts import classify_change, share_change_as_of  # noqa: E402

logger = logging.getLogger(__name__)

FAIL, WARN = "FAIL", "WARN"

# Bulk files round shares and prices to 2 decimals; the XML has full precision.
ROUNDING = 0.006

# More than this share of SEC requests failing means the sample says too little.
MAX_UNREACHABLE = 0.1


def _result(check_id, severity, passed, measured, problems=(), details=""):
    return {"id": check_id, "severity": severity, "passed": bool(passed),
            "measured": measured, "problems": list(problems)[:50], "details": details}


def _ciks_by_company(conn):
    """company_id -> [its CIK, then the old CIKs folded into it]."""
    ciks = {}
    for company_id, cik in conn.execute("SELECT id, cik FROM companies WHERE cik IS NOT NULL"):
        ciks[company_id] = [str(cik)]
    for cik, company_id in conn.execute("SELECT cik, company_id FROM company_ciks"):
        ciks.setdefault(company_id, []).append(str(cik))
    return ciks


def _fetch_filing(accession, dir_ciks):
    """SEC's full submission text for a filing, or None if no directory has it."""
    for cik in dir_ciks:
        if not cik:
            continue
        try:
            return _get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}.txt").text
        except Exception as e:  # 404 under this CIK, or SEC unreachable
            logger.debug(f"{accession} not under CIK {cik}: {e}")
    return None


def _close(a, b):
    return abs(a - b) <= max(ROUNDING, abs(b) * 1e-6)


# 1. Rules that must always hold

def check_invariants(conn):
    results = []

    def count(sql):
        return conn.execute(sql).fetchone()[0]

    rules = [
        ("invariant.one_owner_per_filing",
         "Filings whose trades are credited to more than one insider (a joint filing is one trade)",
         """SELECT COUNT(*) FROM (SELECT accession_number FROM insider_transactions
            GROUP BY accession_number HAVING COUNT(DISTINCT reporting_cik) > 1)"""),
        ("invariant.no_repeated_trades",
         "Same trade (company, insider, date, code, shares, price, holding after) in several filings",
         """SELECT COUNT(*) FROM (SELECT 1 FROM insider_transactions
            WHERE shares_transacted IS NOT NULL AND shares_owned_after IS NOT NULL
            GROUP BY company_id, reporting_cik, transaction_date, transaction_type,
                     shares_transacted, price, shares_owned_after
            HAVING COUNT(DISTINCT accession_number) > 1)"""),
        ("invariant.cik_format",
         "Insider CIKs stored with leading zeros (the same insider would look like two)",
         "SELECT COUNT(*) FROM insider_transactions WHERE reporting_cik LIKE '0%'"),
        ("invariant.company_has_cik",
         "Companies with trades but no SEC CIK",
         """SELECT COUNT(*) FROM companies c WHERE c.cik IS NULL
            AND EXISTS (SELECT 1 FROM insider_transactions t WHERE t.company_id = c.id)"""),
        ("invariant.no_self_reported",
         "Trades where the company is listed as its own insider (the real person is unknown)",
         """SELECT COUNT(*) FROM insider_transactions t JOIN companies c ON c.id = t.company_id
            WHERE t.reporting_cik = CAST(c.cik AS TEXT)
               OR t.reporting_cik IN (SELECT CAST(cik AS TEXT) FROM company_ciks
                                      WHERE company_id = t.company_id)"""),
        ("invariant.filing_not_in_future",
         "Trades filed after today",
         f"SELECT COUNT(*) FROM insider_transactions WHERE filing_date > '{date.today().isoformat()}'"),
    ]
    for check_id, description, sql in rules:
        n = count(sql)
        results.append(_result(check_id, FAIL, n == 0, {"violations": n}, details=description))
    n = len(repeated_across_owners(conn))
    results.append(_result(
        "invariant.no_repeated_across_owners", FAIL, n == 0, {"violations": n},
        details="Same trade filed separately by related owners (a fund and its manager, a couple) "
                "and stored more than once"))

    # Filers do mistype trade dates (a year late, say), and SEC's record is
    # what we store, so these are listed for a look rather than failed.
    late = conn.execute("""
        SELECT c.ticker, t.accession_number, t.line_number, t.transaction_date, t.filing_date
        FROM insider_transactions t JOIN companies c ON c.id = t.company_id
        WHERE t.transaction_type IN ('P', 'S') AND t.transaction_date > t.filing_date
        ORDER BY t.filing_date DESC""").fetchall()
    results.append(_result(
        "invariant.trade_not_after_filing", WARN, not late, {"rows": len(late)},
        [f"{t} {a} line {n}: traded {d}, filed {f}" for t, a, n, d, f in late],
        "Purchases or sales dated after the day they were filed (filer typos)"))
    return results


# 2. Stored trades re-read from the original filings

def check_rows_against_sec(conn, sample_size, rng):
    ciks = _ciks_by_company(conn)
    rows = []
    for code in ("P", "S"):
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM insider_transactions WHERE transaction_type = ?", (code,))]
        rows += rng.sample(ids, min(sample_size // 2, len(ids)))
    problems, warnings, unreachable, checked = [], [], 0, 0
    for row_id in rows:
        (company_id, ticker, accession, line, filed, code, txn_date, shares, price,
         owner_cik, raw_json) = conn.execute("""
            SELECT t.company_id, c.ticker, t.accession_number, t.line_number, t.filing_date,
                   t.transaction_type,
                   t.transaction_date, t.shares_transacted, t.price, t.reporting_cik, t.raw_json
            FROM insider_transactions t JOIN companies c ON c.id = t.company_id
            WHERE t.id = ?""", (row_id,)).fetchone()
        company_ciks = ciks.get(company_id, [])
        content = _fetch_filing(accession, company_ciks + [owner_cik])
        if content is None:
            unreachable += 1
            continue
        checked += 1
        lines = {t["line_number"]: t for t in parse_form4_document(content, None, accession, filed)}
        where = f"{ticker} {accession} line {line}"
        sec = lines.get(line)
        if sec is None:
            problems.append(f"{where}: no such trade line in the filing")
            continue
        if sec["issuer_cik"] not in company_ciks:
            problems.append(f"{where}: filed for issuer CIK {sec['issuer_cik']}, stored under {ticker}")
        if sec["transaction_code"] != code:
            problems.append(f"{where}: code {code}, SEC says {sec['transaction_code']}")
        if sec["transaction_date"] != txn_date:
            problems.append(f"{where}: date {txn_date}, SEC says {sec['transaction_date']}")
        if sec["insider_cik"] != owner_cik:
            problems.append(f"{where}: insider CIK {owner_cik}, SEC's main owner is {sec['insider_cik']}")
        if shares is None or sec["shares"] is None or not _close(shares, sec["shares"]):
            problems.append(f"{where}: shares {shares}, SEC says {sec['shares']}")
        if sec["price"] is None:
            if price is not None:
                problems.append(f"{where}: price {price}, SEC reports none")
        elif price is None:
            rejected = json.loads(raw_json or "{}").get("price_rejected")
            if sec["price"] < 0.01:
                warnings.append(f"{where}: price {sec['price']} lost to 2-decimal rounding")
            elif rejected:
                warnings.append(f"{where}: price {sec['price']} withheld as implausible ({rejected})")
            else:
                problems.append(f"{where}: price missing, SEC says {sec['price']}")
        elif not _close(price, sec["price"]):
            problems.append(f"{where}: price {price}, SEC says {sec['price']}")
    total = len(rows)
    measured = {"sampled": total, "checked": checked, "unreachable": unreachable,
                "mismatches": len(problems), "price_warnings": len(warnings)}
    return [
        _result("sec.rows_match_filings", FAIL,
                not problems and unreachable <= MAX_UNREACHABLE * total, measured, problems,
                "Random purchases and sales re-read from the original SEC filings"),
        _result("sec.prices_withheld", WARN, not warnings, {"rows": len(warnings)}, warnings,
                "Prices SEC reports that the database leaves empty"),
    ]


# 3. Filings from SEC's index must all be in the database

def _sample_index(year, quarter, latest_filed, n, rng):
    from backfill_quarter_index import parse_form_index
    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    filings = {f["accession_number"]: f for f in parse_form_index(_get(url).content.decode("latin-1"))
               if f["form_type"] in TRANSACTION_FORM_TYPES and f["filing_date"] <= latest_filed}
    return rng.sample(sorted(filings.values(), key=lambda f: f["accession_number"]),
                      min(n, len(filings)))


def check_filings_are_loaded(conn, sample_size, rng):
    latest_filed = conn.execute("SELECT MAX(filing_date) FROM insider_transactions").fetchone()[0]
    first_filed = conn.execute("SELECT MIN(filing_date) FROM insider_transactions").fetchone()[0]
    # Allow for the day the rebuild ran: the last day may be partly loaded.
    latest_filed = (date.fromisoformat(latest_filed) - timedelta(days=1)).isoformat()
    # The newest quarter comes from the per-filing step, older ones from the
    # bulk files; sample both.
    newest = date.fromisoformat(latest_filed)
    first_year = int(first_filed[:4])
    older_year = rng.randint(first_year, max(first_year, newest.year - 1))
    quarters = [(newest.year, (newest.month - 1) // 3 + 1), (older_year, rng.randint(1, 4))]

    company_by_cik = {str(cik): cid for cid, cik in conn.execute(
        "SELECT id, cik FROM companies WHERE cik IS NOT NULL")}
    company_by_cik.update({str(cik): cid for cik, cid in conn.execute(
        "SELECT cik, company_id FROM company_ciks")})

    problems, unreachable, filings_checked, trades = [], 0, 0, 0
    sampled = []
    for year, quarter in quarters:
        sampled += _sample_index(year, quarter, latest_filed, sample_size // len(quarters), rng)
    for filing in sampled:
        content = _fetch_filing(filing["accession_number"], [filing["dir_cik"]])
        if content is None:
            unreachable += 1
            continue
        filings_checked += 1
        for sec in parse_form4_document(content, None, filing["accession_number"], filing["filing_date"]):
            trades += 1
            where = f"{filing['accession_number']} line {sec['line_number']} ({sec['issuer_ticker']})"
            company_id = company_by_cik.get(sec["issuer_cik"])
            if company_id is None:
                problems.append(f"{where}: issuer CIK {sec['issuer_cik']} not in the database")
                continue
            if company_by_cik.get(sec["insider_cik"]) == company_id:
                continue  # filed under the company's own name: dropped on purpose
            # Matched on the trade, not the filing: the same trade may be
            # stored from another filing that reported it first.
            candidates = conn.execute("""
                SELECT shares_transacted, document_type FROM insider_transactions
                WHERE company_id = ? AND reporting_cik = ? AND transaction_date = ?
                  AND transaction_type = ?""",
                (company_id, sec["insider_cik"], sec["transaction_date"],
                 sec["transaction_code"])).fetchall()
            if any(s is not None and sec["shares"] is not None and _close(s, sec["shares"])
                   for s, _ in candidates):
                continue
            if any(doc in ("4/A", "5/A") for _, doc in candidates):
                continue  # corrected by a later amendment
            # Filed separately by related owners: stored once, under the main
            # owner, with this owner's name in its owner list.
            merged = conn.execute("""
                SELECT COUNT(*) FROM insider_transactions t, json_each(t.raw_json, '$.all_owners') o
                WHERE t.company_id = ? AND t.transaction_date = ? AND t.transaction_type = ?
                  AND t.shares_transacted = ? AND o.value = ?""",
                (company_id, sec["transaction_date"], sec["transaction_code"],
                 sec["shares"], sec["insider_name"])).fetchone()[0]
            if merged:
                continue
            problems.append(f"{where}: {sec['transaction_code']} {sec['shares']} shares on "
                            f"{sec['transaction_date']} by CIK {sec['insider_cik']} not in the database")
    total = len(sampled)
    measured = {"filings_sampled": total, "filings_checked": filings_checked,
                "unreachable": unreachable, "trades_checked": trades, "missing": len(problems),
                "quarters": [f"{y}q{q}" for y, q in quarters]}
    return [_result("sec.filings_loaded", FAIL,
                    not problems and unreachable <= MAX_UNREACHABLE * total, measured, problems,
                    "Every trade in a random sample of SEC's filing index is in the database")]


# 4. Cases that went wrong before

def check_known_cases(conn):
    def one(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    def company(ticker):
        return conn.execute("SELECT id, cik FROM companies WHERE ticker = ?", (ticker,)).fetchone()

    def trades(ticker, where="1", *args):
        c = company(ticker)
        return one(f"SELECT COUNT(*) FROM insider_transactions WHERE company_id = ? AND {where}",
                   c[0], *args) if c else 0

    berkshire = ("315090", "1067983")  # Warren Buffett, Berkshire Hathaway
    cases = [
        ("known.oxy_keeps_berkshire_buys",
         "Berkshire's 2022 OXY purchases are filed under OXY",
         trades("OXY", "transaction_type = 'P' AND filing_date LIKE '2022%' AND reporting_cik IN (?, ?)",
                *berkshire) >= 50),
        ("known.brk_has_no_investment_buys",
         "BRK-B shows none of Berkshire's purchases of other companies' stock",
         trades("BRK-B", "transaction_type = 'P' AND reporting_cik IN (?, ?)", *berkshire) == 0),
        ("known.blackrock_history_kept",
         "BlackRock's trades from before its 2024 re-registration are under BLK (CIK 2012383)",
         (company("BLK") or (0, 0))[1] == 2012383 and trades("BLK", "filing_date < '2024-09-01'") >= 500),
        ("known.draftkings_history_kept",
         "DraftKings' trades from before its 2022 re-registration are under DKNG (CIK 1883685)",
         (company("DKNG") or (0, 0))[1] == 1883685 and trades("DKNG", "filing_date < '2022-01-01'") >= 500),
        ("known.box_is_box_inc",
         "BOX is Box Inc (CIK 1372612), not BOXABL, which reported the same ticker",
         (company("BOX") or (0, 0))[1] == 1372612),
    ]
    return [_result(cid, FAIL, ok, {"passed": bool(ok)}, [] if ok else [desc], desc)
            for cid, desc, ok in cases]


# 5. Share counts

# Long-running, large buyback and issuance programmes. If one of these stops,
# check the filing before changing the list.
KNOWN_BUYBACKS = ["AAPL", "AZO", "NVR", "ORLY", "WFC"]
KNOWN_DILUTERS = ["MSTR", "RIVN", "SOFI"]

# Current share counts (all classes) in a range that only a large real change
# would leave. Update the range after checking the company's latest 10-Q.
KNOWN_LEVELS = {
    "AAPL": (13.5e9, 16e9),
    "MSFT": (7.0e9, 7.8e9),
    "GOOGL": (11e9, 13e9),
    "META": (2.3e9, 2.75e9),
    "MCD": (0.66e9, 0.75e9),
    "NVDA": (23e9, 25.5e9),
}
# Report shares per class only, so they must have no current figure rather
# than a figure for one class.
NO_CURRENT_LEVEL = ["BRK-B", "V"]


def check_share_counts(conn):
    today = date.today().isoformat()
    year_ago = (date.today() - timedelta(days=400)).isoformat()

    def company_id(ticker):
        row = conn.execute("SELECT id FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        return row[0] if row else None

    def trend(ticker):
        cid = company_id(ticker)
        change = share_change_as_of(conn, cid, today) if cid else None
        if not change or change["period_end"] < year_ago:
            return "no current figure"
        return classify_change(change)

    def level(ticker):
        cid = company_id(ticker)
        row = conn.execute("""SELECT shares FROM shares_outstanding
                              WHERE company_id = ? AND date >= ? ORDER BY date DESC LIMIT 1""",
                           (cid, year_ago)).fetchone() if cid else None
        return row[0] if row else None

    results = []
    problems = [f"{t}: {trend(t)}, expected buyback" for t in KNOWN_BUYBACKS if trend(t) != "buyback"]
    problems += [f"{t}: {trend(t)}, expected dilution" for t in KNOWN_DILUTERS if trend(t) != "dilution"]
    results.append(_result("shares.known_trends", FAIL, not problems,
                           {"companies": len(KNOWN_BUYBACKS) + len(KNOWN_DILUTERS), "wrong": len(problems)},
                           problems, "Companies with long-running buybacks or issuance are classified so"))

    problems = []
    for ticker, (lo, hi) in KNOWN_LEVELS.items():
        shares = level(ticker)
        if shares is None or not lo <= shares <= hi:
            problems.append(f"{ticker}: {shares}, expected {lo:.3g}-{hi:.3g}")
    problems += [f"{t}: has a current figure ({level(t)}) though it reports shares per class only"
                 for t in NO_CURRENT_LEVEL if level(t) is not None]
    results.append(_result("shares.known_levels", FAIL, not problems,
                           {"companies": len(KNOWN_LEVELS) + len(NO_CURRENT_LEVEL), "wrong": len(problems)},
                           problems, "Current share counts of large companies are in range"))

    # Current changes only: the latest per company, for a period in the last year.
    latest = """
        SELECT * FROM (
            SELECT s.*, ROW_NUMBER() OVER (PARTITION BY company_id
                                           ORDER BY filed DESC, period_end DESC) AS rn
            FROM share_count_changes s)
        WHERE rn = 1 AND period_end >= ?"""
    n, backed = conn.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(repurchases), 0) FROM ({latest})
        WHERE (shares / shares_year_ago - 1) * 100 BETWEEN -25 AND -1""", (year_ago,)).fetchone()
    pct = 100 * backed / n if n else 0
    results.append(_result("shares.buybacks_backed_by_repurchases", WARN, pct >= 80,
                           {"buybacks": n, "with_repurchases": backed, "pct": round(pct, 1)},
                           details="Share of current buybacks whose filing also reports buying back "
                                   "stock (89% when set; a fall means counts may be misread)"))

    n, agree = conn.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(ABS(so.shares / l.shares - 1) <= 0.3), 0)
        FROM ({latest}) l
        JOIN shares_outstanding so ON so.company_id = l.company_id
         AND so.date = (SELECT MAX(date) FROM shares_outstanding x WHERE x.company_id = l.company_id)
        WHERE so.date >= ?""", (year_ago, year_ago)).fetchone()
    pct = 100 * agree / n if n else 0
    results.append(_result("shares.levels_agree_with_changes", FAIL, pct >= 90,
                           {"companies": n, "within_30pct": agree, "pct": round(pct, 1)},
                           details="Current share count within 30% of the count in the latest "
                                   "year-on-year change (95.5% when set; the rest are real "
                                   "changes since the period ended)"))
    return results


def run_audit(db_path, sample_size=150, filings=100, seed=None):
    seed = seed if seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    conn = sqlite3.connect(db_path)
    try:
        results = check_invariants(conn)
        results += check_known_cases(conn)
        results += check_share_counts(conn)
        results += check_rows_against_sec(conn, sample_size, rng)
        results += check_filings_are_loaded(conn, filings, rng)
    finally:
        conn.close()
    failed = [r for r in results if not r["passed"] and r["severity"] == FAIL]
    return {"database_path": db_path, "seed": seed, "run_date": date.today().isoformat(),
            "checks": results,
            "summary": {"total": len(results), "failed": len(failed),
                        "warnings": sum(not r["passed"] and r["severity"] == WARN for r in results)}}


def format_report(report):
    lines = [f"Correctness audit of {report['database_path']} (seed {report['seed']})", ""]
    for r in report["checks"]:
        status = "PASS" if r["passed"] else r["severity"]
        lines.append(f"[{status}] {r['id']}: {r['details']}")
        lines.append(f"       {json.dumps(r['measured'])}")
        for p in r["problems"]:
            lines.append(f"       - {p}")
    s = report["summary"]
    lines += ["", f"{s['total']} checks, {s['failed']} failed, {s['warnings']} warnings"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.path.join(TRACKER, "db", "insider_signals.db"))
    parser.add_argument("--json", help="write the full report here")
    parser.add_argument("--sample", type=int, default=150, help="stored trades to re-read from SEC")
    parser.add_argument("--filings", type=int, default=100, help="index filings to look for")
    parser.add_argument("--seed", type=int, help="repeat an earlier run's samples")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        report = run_audit(args.db, args.sample, args.filings, args.seed)
    except Exception:
        logger.exception("Audit could not run")
        return 2
    print(format_report(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
