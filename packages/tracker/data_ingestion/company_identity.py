"""
Which company a trade belongs to.

A company is identified by its SEC number (CIK), never by its ticker. Tickers
are reused: BOX was also reported by BOXABL, CART by Carolina Trust before
Maplebear, GRAF by Velodyne before Graf Global. Matching on ticker booked one
issuer's trades under another, so tickers are only labels, set at the end of a
rebuild from SEC's current ticker list.

The one exception is a company that re-registers under a new CIK while staying
the same business (BlackRock in 2024, DraftKings in 2022). Its older CIK is
folded into the new one when both reported the same ticker, their names match
once suffixes like "Inc" are removed, and the new CIK's first filing falls
within 90 days of the old one's last. That timing rule is what keeps out the
lookalikes: an acquirer that takes the target's name already has years of
filings (Central Valley becoming Community West Bancshares), and a spin-off or
re-listing starts months after the old company stopped (Aaron's, Instructure,
Xperi). Anything less certain stays separate: a missing history is visible, a
wrong one is not.
"""

import re
from datetime import date

from form4_rules import normalize_cik

# Filers type these into the ticker field when the stock has no listing.
PLACEHOLDER_TICKERS = {"", "NONE", "N/A", "NA", "NULL", "TBD", "-", "--", "NOTICKER"}
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Name words that change when a company re-registers but the business doesn't.
_NAME_NOISE = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "sa", "nv", "ag", "se", "lp", "llc", "the", "de", "md", "nv", "new", "old",
    "holdings", "holding", "group",
}

# A re-registration swaps one CIK for the other within weeks; the old one may
# file a few last forms (e.g. for the share swap itself) after the new starts.
_HANDOVER_DAYS = 90


def usable_ticker(ticker) -> bool:
    t = (ticker or "").strip().upper()
    return t not in PLACEHOLDER_TICKERS and bool(_TICKER_RE.match(t))


def normalize_name(name) -> str:
    """'EXXON MOBIL CORP' and 'ExxonMobil Holdings Corp' both become 'exxonmobil'."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace(".", "")).split()
    return "".join(w for w in words if w not in _NAME_NOISE and len(w) > 1)


def company_for_cik(conn, cik, name=None) -> int:
    """Return the company row for an issuer CIK, creating it if needed.

    A new row gets the placeholder ticker 'CIK<number>'; assign_tickers gives
    it a real one once the whole history is loaded.
    """
    cik = normalize_cik(cik)
    if not cik:
        raise ValueError("issuer CIK is required")
    row = conn.execute("SELECT MIN(id) FROM companies WHERE cik = ?", (int(cik),)).fetchone()
    if row[0] is not None:
        return row[0]
    cur = conn.execute("INSERT INTO companies (ticker, cik, name) VALUES (?, ?, ?)",
                       (f"CIK{cik}", int(cik), name))
    return cur.lastrowid


def record_issuer_tickers(conn, seen):
    """Remember what each issuer called itself and when.

    seen: {(cik, ticker): (name, first_filing, last_filing)} for one batch.
    """
    conn.executemany("""
        INSERT INTO issuer_tickers (cik, ticker, name, first_filing, last_filing)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(cik, ticker) DO UPDATE SET
            name = CASE WHEN excluded.last_filing >= last_filing THEN excluded.name ELSE name END,
            first_filing = MIN(first_filing, excluded.first_filing),
            last_filing = MAX(last_filing, excluded.last_filing)
    """, [(int(c), t, n, f, l) for (c, t), (n, f, l) in seen.items()])


def _latest_names(conn):
    names = {}
    for cik, name in conn.execute(
            "SELECT cik, name FROM issuer_tickers ORDER BY last_filing"):
        names[cik] = name
    return names


def merge_predecessors(conn, sec_map) -> list:
    """Fold re-registered companies' old CIKs into their successors.

    sec_map: {ticker: cik} from SEC's current company_tickers.json.
    Moves the old CIK's trades onto the successor's company row and records the
    link in company_ciks. Returns [(old_cik, new_cik, ticker)].
    """
    names = _latest_names(conn)
    current_ciks = {int(c) for c in sec_map.values()}
    spans = {cik: (first, last) for cik, first, last in conn.execute(
        "SELECT cik, MIN(first_filing), MAX(last_filing) FROM issuer_tickers GROUP BY cik")}
    by_ticker = {}
    for cik, ticker, last in conn.execute("SELECT cik, ticker, last_filing FROM issuer_tickers"):
        if usable_ticker(ticker):
            by_ticker.setdefault(ticker, []).append((cik, last))

    def handed_over(old_cik, new_cik):
        old_last = date.fromisoformat(spans[old_cik][1])
        new_first = date.fromisoformat(spans[new_cik][0])
        return abs((new_first - old_last).days) <= _HANDOVER_DAYS

    merges = []
    for ticker, users in by_ticker.items():
        if len(users) < 2:
            continue
        if ticker in sec_map:
            new_cik = int(sec_map[ticker])
        else:
            new_cik = max(users, key=lambda u: u[1])[0]
        if new_cik not in spans:
            continue
        new_name = normalize_name(names.get(new_cik))
        for old_cik, _ in users:
            if (old_cik != new_cik and old_cik not in current_ciks and new_name
                    and normalize_name(names.get(old_cik)) == new_name
                    and handed_over(old_cik, new_cik)):
                merges.append((old_cik, new_cik, ticker))

    for old_cik, new_cik, _ in merges:
        new_id = company_for_cik(conn, new_cik, names.get(new_cik))
        old_id = company_for_cik(conn, old_cik, names.get(old_cik))
        conn.execute("UPDATE insider_transactions SET company_id = ? WHERE company_id = ?",
                     (new_id, old_id))
        conn.execute("INSERT OR REPLACE INTO company_ciks (cik, company_id) VALUES (?, ?)",
                     (old_cik, new_id))
    conn.commit()
    return merges


def assign_tickers(conn, sec_map, sec_titles=None) -> int:
    """Give every company row the ticker and name it should carry today.

    1. A CIK on SEC's current list gets its SEC ticker (keeping the one it
       already has if SEC lists several, e.g. BRK-B rather than BRK-A).
    2. Otherwise the ticker it (or a folded-in predecessor) last reported, if
       no later issuer reported it and SEC doesn't list it for another company
       with trades. If SEC lists it for a company without trades, the names
       must match: Exxon's new holding company owns XOM on SEC's list before
       it has filed anything, and until it does XOM stays on the CIK holding
       Exxon's history; Barrick owns B but files no insider forms, and the
       B that Barnes Group reported until 2025 is not Barrick.
       A listed CIK with no trades then gets its SEC ticker if still free.
    3. Otherwise it keeps its ticker if nobody above claimed it, else falls
       back to 'CIK<number>'.

    Names come from SEC's list, else the latest filing. Returns rows changed.
    """
    sec_titles = sec_titles or {}
    sec_by_cik = {}
    for ticker, cik in sec_map.items():
        sec_by_cik.setdefault(int(cik), []).append(ticker)
    names = _latest_names(conn)
    owner_of = {}
    for cik, company_id in conn.execute("SELECT cik, company_id FROM company_ciks"):
        owner_of[cik] = company_id

    rows = conn.execute("SELECT id, ticker, cik, name FROM companies").fetchall()
    id_by_cik = {cik: cid for cid, _, cik, _ in rows if cik is not None}
    with_trades = {cid for (cid,) in conn.execute(
        "SELECT DISTINCT company_id FROM insider_transactions")}
    waiting = {t: int(c) for t, c in sec_map.items() if id_by_cik.get(int(c)) not in with_trades}

    def may_report(ticker, cik):
        if ticker not in sec_map:
            return True
        return (ticker in waiting and normalize_name(names.get(cik))
                and normalize_name(names.get(cik)) == normalize_name(sec_titles.get(waiting[ticker])))

    # Latest reporter of each ticker, counting a predecessor's reports as its successor's.
    latest = {}
    for cik, ticker, last in conn.execute(
            "SELECT cik, ticker, last_filing FROM issuer_tickers"):
        cid = owner_of.get(cik) or id_by_cik.get(cik)
        if cid is not None and usable_ticker(ticker) and may_report(ticker, cik):
            if ticker not in latest or last > latest[ticker][1]:
                latest[ticker] = (cid, last)

    # Each company's most recent such ticker.
    reported_last = {}
    for ticker, (cid, last) in latest.items():
        if cid not in reported_last or last > reported_last[cid][1]:
            reported_last[cid] = (ticker, last)

    claims = {}  # ticker -> company_id

    def claim(ticker, cid):
        if ticker not in claims and cid not in placed:
            claims[ticker] = cid
            placed.add(cid)

    placed = set()
    for cid, ticker, cik, _ in rows:                       # rule 1
        options = [t for t in sec_by_cik.get(cik, []) if t not in waiting]
        if options:
            claim(ticker if ticker in options else options[0], cid)
    for cid, _, _, _ in rows:                              # rule 2
        if cid in reported_last:
            claim(reported_last[cid][0], cid)
    for cid, ticker, cik, _ in rows:                       # rule 1, no trades yet
        options = sec_by_cik.get(cik, [])
        if options:
            claim(ticker if ticker in options else options[0], cid)
    for cid, ticker, cik, _ in rows:                       # rule 3
        if usable_ticker(ticker) and ticker not in sec_map and ticker != f"CIK{cik}":
            claim(ticker, cid)

    final = {cid: f"CIK{cik}" if cik is not None else f"ID{cid}" for cid, _, cik, _ in rows}
    for ticker, cid in claims.items():
        final[cid] = ticker

    changed = [(cid, ticker, final[cid], sec_titles.get(cik) or names.get(cik) or name)
               for cid, ticker, cik, name in rows]
    changed = [c for c, r in zip(changed, rows) if c[2] != r[1] or c[3] != r[3]]
    # Park changed rows on unique temporary tickers first so swaps don't collide.
    conn.executemany("UPDATE companies SET ticker = ? WHERE id = ?",
                     [(f"__{cid}", cid) for cid, _, _, _ in changed])
    conn.executemany("UPDATE companies SET ticker = ?, name = ? WHERE id = ?",
                     [(ticker, name, cid) for cid, _, ticker, name in changed])
    conn.commit()
    return len(changed)
