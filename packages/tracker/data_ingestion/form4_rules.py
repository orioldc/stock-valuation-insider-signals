"""
Rules shared by every path that writes insider_transactions.

The bulk SEC data sets and the per-filing Form 4 XML describe the same filings
in different shapes. These helpers turn both into one shape, so a transaction
looks identical whichever path loaded it:

- CIKs are stored without leading zeros ("0001169896" and "1169896" are the
  same insider; storing both made the duplicate check miss ~39k repeats).
- One relationship format: "Officer (Chief Executive Officer), Director, 10% Owner".
- Joint filings (a fund and its manager filing one form together) are one
  trade, attributed to one main owner chosen by a fixed rule, with every
  owner's name kept alongside.
- A price of zero or less means "not reported" and is stored as NULL.
"""

import json
import re
from collections import Counter
from typing import Iterable, Optional

AMENDMENT_TYPES = ("4/A", "5/A")
TRANSACTION_FORM_TYPES = ("4", "4/A", "5", "5/A")


def normalize_cik(cik) -> str:
    """Return a CIK as a plain number string with no leading zeros ('' if missing)."""
    if cik is None:
        return ""
    s = str(cik).strip().lstrip("0")
    return s


def normalize_price(price: Optional[float]) -> Optional[float]:
    """Zero or negative prices are 'not reported', not free shares."""
    if price is None or price <= 0:
        return None
    return price


def format_relationship(is_officer: bool, is_director: bool, is_ten_pct: bool,
                        is_other: bool = False, title: str = "") -> str:
    roles = []
    if is_officer:
        roles.append(f"Officer ({title})" if title else "Officer")
    if is_director:
        roles.append("Director")
    if is_ten_pct:
        roles.append("10% Owner")
    if is_other and not roles:
        roles.append(f"Other ({title})" if title else "Other")
    return ", ".join(roles) if roles else "Unknown"


def relationship_from_bulk(relationship: str, title: str) -> str:
    """Convert the bulk data set's 'Director,Officer,TenPercentOwner' style."""
    parts = {p.strip().upper() for p in (relationship or "").split(",")}
    return format_relationship(
        is_officer="OFFICER" in parts,
        is_director="DIRECTOR" in parts,
        is_ten_pct="TENPERCENTOWNER" in parts,
        is_other="OTHER" in parts,
        title=(title or "").strip(),
    )


def _owner_rank(owner: dict):
    rel = owner["relationship"].upper()
    if rel.startswith("OFFICER"):
        tier = 0
    elif "DIRECTOR" in rel:
        tier = 1
    elif "10% OWNER" in rel:
        tier = 2
    else:
        tier = 3
    cik = owner["cik"]
    return (tier, int(cik) if cik.isdigit() else float("inf"), owner["name"])


def pick_primary_owner(owners: Iterable[dict]) -> dict:
    """Choose the owner a joint filing is attributed to.

    owners: dicts with 'name', 'cik' (normalized) and 'relationship' (formatted).
    Officers come first, then directors, then 10% owners, then anyone else;
    ties go to the lowest CIK. The order SEC lists owners in is not stable
    between the bulk files and the XML, so it must not decide attribution.

    Returns the chosen owner with an extra 'all_owners' list of every name.
    """
    owners = [o for o in owners if o.get("cik") or o.get("name")]
    if not owners:
        return {"name": "", "cik": "", "relationship": "Unknown", "all_owners": []}
    primary = dict(min(owners, key=_owner_rank))
    primary["all_owners"] = [o["name"] for o in sorted(owners, key=_owner_rank)]
    return primary


def apply_amendments(conn) -> int:
    """Let amendments replace the trades they correct, so nothing counts twice.

    A 4/A (or 5/A) names the original filing by its filing date. For each
    trade line in an amendment, drop the lines with the same owner, trade date
    and transaction code from earlier filings for the same company and owner
    that were filed on that original date. Where several amendments correct
    the same trade, only the latest one is kept. Original lines the amendment
    does not mention are left alone, because many amendments correct one line.

    Some amendments give an original date that can't be right: missing, a
    typo like 2000 for 2020 (before the trade itself), or the amendment's own
    date. Then the amendment line replaces earlier lines with the same owner,
    trade date, code and number of shares: that is the same trade reported again.
    Where the share count differs too, it may be a different trade, so it is
    left alone.

    Returns the number of rows removed.
    """
    removed = conn.execute("""
        DELETE FROM insider_transactions WHERE id IN (
            SELECT o.id
            FROM insider_transactions a
            JOIN insider_transactions o
              ON o.company_id = a.company_id
             AND o.reporting_cik = a.reporting_cik
             AND o.transaction_date = a.transaction_date
             AND o.transaction_type = a.transaction_type
             AND o.shares_transacted = a.shares_transacted
             AND o.accession_number != a.accession_number
            WHERE a.document_type IN ('4/A', '5/A')
              AND o.document_type NOT IN ('4/A', '5/A')
              AND (a.date_of_orig_sub IS NULL
                   OR a.date_of_orig_sub >= a.filing_date
                   OR a.date_of_orig_sub < a.transaction_date)
              AND o.filing_date <= a.filing_date
        )
    """).rowcount
    removed += conn.execute("""
        DELETE FROM insider_transactions WHERE id IN (
            SELECT o.id
            FROM insider_transactions a
            JOIN insider_transactions o
              ON o.company_id = a.company_id
             AND o.reporting_cik = a.reporting_cik
             AND o.transaction_date = a.transaction_date
             AND o.transaction_type = a.transaction_type
             AND o.accession_number != a.accession_number
            WHERE a.document_type IN ('4/A', '5/A')
              AND a.date_of_orig_sub IS NOT NULL
              AND (
                    (o.filing_date = a.date_of_orig_sub AND o.document_type NOT IN ('4/A', '5/A'))
                 OR (o.document_type IN ('4/A', '5/A')
                     AND o.date_of_orig_sub = a.date_of_orig_sub
                     AND (o.filing_date < a.filing_date
                          OR (o.filing_date = a.filing_date AND o.accession_number < a.accession_number)))
              )
        )
    """).rowcount
    conn.commit()
    return removed


def remove_duplicate_filings(conn) -> int:
    """Keep one copy of a trade that several filings report identically.

    The same trade reaches SEC more than once when a large group of joint
    owners has to split itself over several forms (each repeats the trade),
    when a filer re-submits a form instead of amending it, or when a Form 5
    repeats a trade already on a Form 4. A line counts as a repeat when another
    filing has the same company, owner, trade date, code, shares, price and
    shares held afterwards. Two separate trades by one holder cannot leave the
    same holding afterwards, so lines without that figure are never touched.
    The earliest filing is kept, since that is when the market learned of it.

    Returns the number of rows removed.
    """
    # Lines from the earliest filing in each group stay (including identical
    # lots within that one filing); the same line in any later filing goes.
    removed = conn.execute("""
        DELETE FROM insider_transactions WHERE id IN (
            SELECT id FROM (
                SELECT id, filing_date || '|' || accession_number AS filing_key,
                       MIN(filing_date || '|' || accession_number) OVER (
                           PARTITION BY company_id, reporting_cik, transaction_date, transaction_type,
                                        shares_transacted, price, shares_owned_after
                       ) AS first_filing_key
                FROM insider_transactions
                WHERE shares_transacted IS NOT NULL AND shares_owned_after IS NOT NULL
            ) WHERE filing_key != first_filing_key
        )
    """).rowcount
    conn.commit()
    return removed


def remove_self_reported(conn) -> int:
    """Drop trades where the company is listed as its own insider.

    Some small companies file their officers' and directors' Forms 4 under the
    company's own SEC login, so SEC records the company as the reporting owner
    (a "CEO" called CTT Pharmaceutical Holdings, Inc.). The real person's name
    is lost, and the trade can't be told apart from the company dealing in its
    own shares, so it can't be shown as insider buying or selling. A company's
    old CIKs (company_ciks) count as the company too.

    Returns the number of rows removed.
    """
    removed = conn.execute("""
        DELETE FROM insider_transactions WHERE id IN (
            SELECT t.id FROM insider_transactions t JOIN companies c ON c.id = t.company_id
            WHERE t.reporting_cik = CAST(c.cik AS TEXT)
               OR t.reporting_cik IN (SELECT CAST(cik AS TEXT) FROM company_ciks
                                      WHERE company_id = t.company_id)
        )
    """).rowcount
    conn.commit()
    return removed


# Words that mark an owner as an organisation rather than a person.
_ENTITY_NAME = re.compile(
    r"\b(L\.?P|L\.?L\.?C|INC|CORP(ORATION)?|LTD|LIMITED|PLC|COMPANY|TRUST|FUND|PARTNERS|"
    r"PARTNERSHIP|CAPITAL|HOLDINGS?|MANAGEMENT|ADVISORS|ADVISERS|INVESTMENTS?|VENTURES|GROUP|"
    r"ASSOCIATES|FOUNDATION|GMBH|S\.?A|N\.?V|B\.?V|PTE|S\.?L\.?U|ACQUIROR|SPONSOR)\b\.?",
    re.IGNORECASE)


def is_entity(name: str) -> bool:
    """A fund, manager, trust or other organisation, judged by its name.

    The role can't tell: funds hold board seats ("Director"), people with no
    role given show as "Unknown" or "Other", and co-founders who each own
    their own shares are 10% owners too.
    """
    return bool(_ENTITY_NAME.search(name or ""))


# Words in an ownership note that don't say who holds the shares.
_GENERIC_WORDS = {
    "by", "as", "for", "of", "the", "and", "a", "an", "in", "to", "with", "through", "via", "on",
    "behalf", "held", "hold", "holds", "shares", "owned", "indirect", "indirectly", "directly",
    "spouse", "spouses", "wife", "husband", "his", "her", "their", "reporting", "person", "persons",
    "trustee", "trustees", "co", "cotrustee", "trust", "trusts", "family", "revocable", "irrevocable",
    "living", "ira", "roth", "401k", "plan", "account", "custodian", "child", "children", "son",
    "daughter", "minor", "llc", "lp", "inc", "gst", "agreement", "dated", "fbo", "utma", "ugma",
    "see", "footnote", "note", "member", "members", "immediate", "household",
    # Notes that point elsewhere or only describe the kind of holding.
    "footnotes", "fn", "explanation", "below", "above", "please", "remarks", "s", "benefit",
    "deferred", "comp", "compensation", "foundation", "managed", "corporation", "limited",
    "partnership", "partnerships", "other", "entity", "entities", "various", "holding", "rabbi",
    "deferral", "unvested", "serves", "is", "director", "directors", "officer", "grat",
    "sister", "brother", "mother", "father", "interest", "interests", "proportionate", "represents",
    "each", "responses", "which", "he", "she", "stock", "ownership", "joint", "jointly", "nephew",
    "niece", "reflects", "brokerage", "merrill", "lynch", "schwab", "fidelity",
}
_SPOUSE = re.compile(r"\b(spouse|wife|husband)\b", re.IGNORECASE)


def _holder_words(nature: str) -> frozenset:
    """The words in an ownership note that name who holds the shares."""
    words = re.findall(r"[a-z0-9]+", (nature or "").lower().replace("401(k)", "401k"))
    return frozenset(w for w in words if w not in _GENERIC_WORDS)


def _couple(lines):
    """The two owners, when there are exactly two and one reports the shares as a spouse's."""
    owners = frozenset(line["cik"] for line in lines)
    if len(owners) == 2 and any(_SPOUSE.search(line["nature"]) for line in lines):
        return owners
    return None


def one_holding(lines, couples=frozenset()) -> bool:
    """Whether identical trades filed by different owners are one holding.

    lines: dicts with the owner's 'cik', 'name' and the line's 'nature'
    (the ownership note, e.g. "By Spouse"), all with the same trade and
    the same shares held afterwards. True when:
    - an owner is an organisation (is_entity): a fund, its general partner
      and its manager all report the fund's holding;
    - every line's note names the same holder ("As Trustee for the Bristow
      Trust" and "By spouse as Trustee for the Bristow Trust"); notes that
      only say "By Trust" or "By IRA" don't name anyone and don't count;
    - the two owners are a couple (in couples, see find_couples), like a
      CEO and their spouse both reporting the same purchase.
    Otherwise they are separate people who happen to trade alike, such as
    directors given the same grant, each holding their own shares.
    """
    if any(is_entity(line["name"]) for line in lines):
        return True
    holders = {_holder_words(line["nature"]) for line in lines}
    if len(holders) == 1:
        (words,) = holders
        if any(w.isalpha() and len(w) >= 3 for w in words):
            return True
    return _couple(lines) in couples


def find_couples(groups) -> set:
    """Pairs of owners who report each other's trades as their spouse's.

    A pair counts when it files identical trades, with one side noting the
    shares are held by a spouse, at least twice. Once is not enough: two
    directors given the same grant, one of whom also counts a spouse's
    shares, can match by chance, but not trade after trade.
    """
    seen = Counter(_couple(lines) for lines in groups)
    return {pair for pair, n in seen.items() if pair and n >= 2}


def _line(name, cik, raw_json) -> dict:
    raw = json.loads(raw_json) if raw_json else {}
    return {"name": name or "", "cik": cik or "", "nature": raw.get("ownership_nature") or "",
            "relationship": raw.get("relationship") or "Unknown", "raw": raw}


def repeated_across_owners(conn) -> list:
    """Groups of lines that are one trade filed separately by related owners.

    Lines are grouped when they have the same company, trade date, code,
    shares, price and shares held afterwards and come from different owners;
    a group is returned when one_holding says they report the same holding.
    Trades where the holding afterwards is just the shares bought are left
    out: that is several people making their first purchase in an offering.
    """
    rows = conn.execute("""
        SELECT t.id, t.reporting_name, t.reporting_cik, t.raw_json, t.filing_date, t.accession_number, g.key
        FROM insider_transactions t JOIN (
            SELECT company_id, transaction_date, transaction_type, shares_transacted, price,
                   shares_owned_after, MIN(id) AS key
            FROM insider_transactions
            WHERE shares_transacted > 0 AND shares_owned_after IS NOT NULL
              AND shares_owned_after != shares_transacted
            GROUP BY company_id, transaction_date, transaction_type,
                     shares_transacted, price, shares_owned_after
            HAVING COUNT(DISTINCT reporting_cik) > 1
        ) g USING (company_id, transaction_date, transaction_type, shares_transacted, shares_owned_after)
        WHERE t.price IS g.price""").fetchall()
    groups = {}
    for row_id, name, cik, raw_json, filed, accession, key in rows:
        groups.setdefault(key, []).append(
            {**_line(name, cik, raw_json), "id": row_id, "filing_key": f"{filed}|{accession}"})
    couples = find_couples(groups.values())
    return [lines for lines in groups.values() if one_holding(lines, couples)]


def merge_related_owner_filings(conn) -> int:
    """Keep one copy of a trade that related owners each filed separately.

    A fund, its general partner and its manager (or the members of a buyout
    consortium, or a couple) often file one form each for the same purchase,
    so it looked like several insiders buying and its value counted several
    times. repeated_across_owners finds these.

    The trade is credited to one owner chosen as in pick_primary_owner, from
    that owner's earliest filing, with every owner's name added to its
    all_owners list. Run after remove_duplicate_filings.

    Returns the number of rows removed.
    """
    removed = 0
    for lines in repeated_across_owners(conn):
        primary = min(lines, key=_owner_rank)
        first_filing = min(line["filing_key"] for line in lines if line["cik"] == primary["cik"])
        keep = [line for line in lines if line["cik"] == primary["cik"] and line["filing_key"] == first_filing]
        names = []
        for line in sorted(lines, key=_owner_rank):
            for owner in [line["name"]] + line["raw"].get("all_owners", []):
                if owner and owner not in names:
                    names.append(owner)
        for line in keep:
            conn.execute("UPDATE insider_transactions SET raw_json = ? WHERE id = ?",
                         (json.dumps({**line["raw"], "all_owners": names}), line["id"]))
        kept_ids = {line["id"] for line in keep}
        drop = [line["id"] for line in lines if line["id"] not in kept_ids]
        conn.execute(f"DELETE FROM insider_transactions WHERE id IN ({','.join('?' * len(drop))})", drop)
        removed += len(drop)
    conn.commit()
    return removed
