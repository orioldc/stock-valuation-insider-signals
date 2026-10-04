import sqlite3

from form4_rules import (
    apply_amendments, normalize_cik, normalize_price, pick_primary_owner,
    relationship_from_bulk,
)


def test_cik_and_price_normalisation():
    assert normalize_cik("0001169896") == "1169896"
    assert normalize_cik(1169896) == "1169896"
    assert normalize_cik(None) == ""
    assert normalize_price(0.0) is None
    assert normalize_price(-1) is None
    assert normalize_price(12.5) == 12.5


def test_bulk_relationship_matches_xml_format():
    assert relationship_from_bulk("Director,Officer", "CEO") == "Officer (CEO), Director"
    assert relationship_from_bulk("TenPercentOwner", "") == "10% Owner"
    assert relationship_from_bulk("Other", "Trustee") == "Other (Trustee)"
    assert relationship_from_bulk("", "") == "Unknown"


def test_joint_filing_attribution_ignores_listing_order():
    fund = {"name": "Big Fund LP", "cik": "50", "relationship": "10% Owner"}
    manager = {"name": "Fund GP LLC", "cik": "40", "relationship": "10% Owner"}
    ceo = {"name": "Jane Doe", "cik": "900", "relationship": "Officer (CEO)"}
    a = pick_primary_owner([fund, manager, ceo])
    b = pick_primary_owner([ceo, manager, fund])
    assert a == b
    assert a["name"] == "Jane Doe"
    assert a["all_owners"] == ["Jane Doe", "Fund GP LLC", "Big Fund LP"]
    # Same tier: lowest CIK wins.
    assert pick_primary_owner([fund, manager])["cik"] == "40"


def test_pick_primary_owner_empty():
    assert pick_primary_owner([])["all_owners"] == []


def _row(conn, acc, line, filed, doc, orig=None, shares=100.0, owner="7", date="2025-01-08", code="P"):
    conn.execute("""
        INSERT INTO insider_transactions
        (company_id, filing_date, transaction_date, reporting_cik, transaction_type,
         shares_transacted, accession_number, line_number, document_type, date_of_orig_sub)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (filed, date, owner, code, shares, acc, line, doc, orig))


def _accessions(conn):
    return sorted({r[0] for r in conn.execute("SELECT accession_number || ':' || line_number FROM insider_transactions")})


def test_amendment_replaces_only_the_lines_it_restates(db_path):
    conn = sqlite3.connect(db_path)
    _row(conn, "orig", 1, "2025-01-10", "4", shares=100)
    _row(conn, "orig", 2, "2025-01-10", "4", shares=50, code="S")          # not restated
    _row(conn, "other", 1, "2025-01-12", "4", shares=100)                   # different filing date
    _row(conn, "amend", 1, "2025-02-01", "4/A", orig="2025-01-10", shares=1000)
    removed = apply_amendments(conn)
    assert removed == 1
    assert _accessions(conn) == ["amend:1", "orig:2", "other:1"]


def test_amendment_with_a_wrong_original_date_still_replaces_the_same_trade(db_path):
    conn = sqlite3.connect(db_path)
    _row(conn, "orig", 1, "2025-01-10", "4", shares=100)
    _row(conn, "resent", 1, "2025-01-11", "4", shares=100)                 # the same form sent again
    _row(conn, "orig", 2, "2025-01-10", "4", shares=70)                    # different size: may be another trade
    # Typed 2000 for 2025.
    _row(conn, "amend", 1, "2025-02-01", "4/A", orig="2000-01-10", shares=100)
    _row(conn, "amend", 2, "2025-02-01", "4/A", orig="2000-01-10", shares=75)
    assert apply_amendments(conn) == 2
    assert _accessions(conn) == ["amend:1", "amend:2", "orig:2"]
    assert apply_amendments(conn) == 0


def test_latest_of_several_amendments_wins(db_path):
    conn = sqlite3.connect(db_path)
    _row(conn, "orig", 1, "2025-01-10", "4")
    _row(conn, "a1", 1, "2025-02-01", "4/A", orig="2025-01-10")
    _row(conn, "a2", 1, "2025-03-01", "4/A", orig="2025-01-10")
    apply_amendments(conn)
    assert _accessions(conn) == ["a2:1"]
    # Running it again changes nothing.
    assert apply_amendments(conn) == 0


def test_amendment_does_not_touch_other_owners(db_path):
    conn = sqlite3.connect(db_path)
    _row(conn, "orig", 1, "2025-01-10", "4", owner="7")
    _row(conn, "orig2", 1, "2025-01-10", "4", owner="8")
    _row(conn, "amend", 1, "2025-02-01", "4/A", orig="2025-01-10", owner="7")
    apply_amendments(conn)
    assert _accessions(conn) == ["amend:1", "orig2:1"]


def _dup_row(conn, acc, line, filed, owner="7", shares=100.0, after=1100.0):
    conn.execute("""
        INSERT INTO insider_transactions
        (company_id, filing_date, transaction_date, reporting_cik, transaction_type,
         shares_transacted, price, shares_owned_after, accession_number, line_number, document_type)
        VALUES (1, ?, '2025-01-08', ?, 'S', ?, 10.0, ?, ?, ?, '4')
    """, (filed, owner, shares, after, acc, line))


def test_trade_repeated_in_later_filings_is_kept_once(db_path):
    from form4_rules import remove_duplicate_filings
    conn = sqlite3.connect(db_path)
    _dup_row(conn, "first", 1, "2025-01-10")
    _dup_row(conn, "split2", 1, "2025-01-10")             # same group, next form of a split joint filing
    _dup_row(conn, "refiled", 1, "2025-01-12")            # re-submitted two days later
    _dup_row(conn, "lots", 1, "2025-01-10", after=None)   # no holdings figure: never touched
    _dup_row(conn, "lots", 2, "2025-01-10", after=None)
    _dup_row(conn, "other", 1, "2025-01-10", owner="8")   # different insider
    assert remove_duplicate_filings(conn) == 2
    assert _accessions(conn) == ["first:1", "lots:1", "lots:2", "other:1"]


def test_identical_lots_inside_one_filing_are_kept(db_path):
    from form4_rules import remove_duplicate_filings
    conn = sqlite3.connect(db_path)
    _dup_row(conn, "one", 1, "2025-01-10")
    _dup_row(conn, "one", 2, "2025-01-10")
    assert remove_duplicate_filings(conn) == 0


def test_trades_filed_under_the_company_itself_are_removed(db_path):
    from form4_rules import remove_self_reported
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO companies (id, ticker, cik) VALUES (1, 'ABC', 7)")
    conn.execute("INSERT INTO company_ciks (cik, company_id) VALUES (5, 1)")    # the company's old CIK
    _row(conn, "self", 1, "2025-01-10", "4", owner="7")
    _row(conn, "old", 1, "2025-01-10", "4", owner="5")
    _row(conn, "ceo", 1, "2025-01-10", "4", owner="900")
    assert remove_self_reported(conn) == 2
    assert _accessions(conn) == ["ceo:1"]


def _owner_row(conn, acc, owner, name, relationship, after=5_000.0, shares=1_000.0, nature=""):
    import json
    raw = json.dumps({"relationship": relationship, "all_owners": [name], "ownership_nature": nature})
    conn.execute("""
        INSERT INTO insider_transactions
        (company_id, filing_date, transaction_date, reporting_cik, reporting_name, transaction_type,
         shares_transacted, price, shares_owned_after, raw_json, accession_number, line_number, document_type)
        VALUES (1, '2025-01-10', '2025-01-08', ?, ?, 'P', ?, 10.0, ?, ?, ?, 1, '4')
    """, (owner, name, shares, after, raw, acc))


def test_one_purchase_filed_by_a_fund_and_its_managers_is_kept_once(db_path):
    import json
    from form4_rules import merge_related_owner_filings
    conn = sqlite3.connect(db_path)
    _owner_row(conn, "fund", "50", "Big Fund LP", "10% Owner")
    _owner_row(conn, "gp", "40", "Fund GP LLC", "10% Owner")
    _owner_row(conn, "dir", "900", "Jane Doe", "Director")      # the fund's board seat
    assert merge_related_owner_filings(conn) == 2
    (name, raw), = conn.execute("SELECT reporting_name, raw_json FROM insider_transactions").fetchall()
    assert name == "Jane Doe"
    assert json.loads(raw)["all_owners"] == ["Jane Doe", "Fund GP LLC", "Big Fund LP"]
    assert merge_related_owner_filings(conn) == 0


def test_identical_trades_by_people_only_are_all_kept(db_path):
    from form4_rules import merge_related_owner_filings
    conn = sqlite3.connect(db_path)
    # Co-founders with equal stakes selling under the same plan: two sellers.
    _owner_row(conn, "a", "7", "Founder Anna", "Officer (Co-CEO), Director, 10% Owner")
    _owner_row(conn, "b", "8", "Founder Ben", "Officer (Co-CEO), Director, 10% Owner")
    _owner_row(conn, "c", "9", "Smith John", "Unknown")
    assert merge_related_owner_filings(conn) == 0


def test_entity_is_judged_by_name():
    from form4_rules import is_entity
    assert is_entity("KAYNE ANDERSON CAPITAL ADVISORS LP")
    assert is_entity("Mark & Robyn Jones Descendants Trust 2014")
    assert is_entity("Enagas, S.A.")
    assert not is_entity("Walpole Eugene H IV")
    assert not is_entity("Mudrick Jason")


def test_first_purchases_in_an_offering_are_all_kept(db_path):
    from form4_rules import merge_related_owner_filings
    conn = sqlite3.connect(db_path)
    # Holding afterwards = shares bought: each owner's first shares.
    _owner_row(conn, "fund", "50", "Big Fund LP", "10% Owner", after=1_000.0)
    _owner_row(conn, "other", "60", "Other Fund LP", "10% Owner", after=1_000.0)
    assert merge_related_owner_filings(conn) == 0


def test_purchases_reported_by_both_spouses_are_kept_once(db_path):
    from form4_rules import merge_related_owner_filings
    conn = sqlite3.connect(db_path)
    for acc, shares in (("1", 1_000.0), ("2", 2_000.0)):
        _owner_row(conn, f"ceo{acc}", "7", "Duggan Robert", "Officer (CEO), Director", shares=shares)
        _owner_row(conn, f"wife{acc}", "8", "Zanganeh Mahkam", "10% Owner", shares=shares, nature="By Spouse")
    assert merge_related_owner_filings(conn) == 2
    assert {r[0] for r in conn.execute("SELECT reporting_name FROM insider_transactions")} == {"Duggan Robert"}


def test_a_single_match_with_a_spouse_note_is_left_alone(db_path):
    from form4_rules import merge_related_owner_filings
    conn = sqlite3.connect(db_path)
    # Two directors given the same grant, one also counting a spouse's shares:
    # once is chance, so both stay.
    _owner_row(conn, "a", "7", "Kelly Edward", "Director")
    _owner_row(conn, "b", "8", "Lillis Terrance", "Director", nature="Held by spouse in revocable trust")
    assert merge_related_owner_filings(conn) == 0


def test_a_spouse_note_among_three_directors_is_left_alone(db_path):
    from form4_rules import merge_related_owner_filings
    conn = sqlite3.connect(db_path)
    # Three directors given the same grant; one also counts a spouse's shares.
    # Nothing says which of the other two is the spouse, so all three stay.
    _owner_row(conn, "a", "7", "Farnsworth Tom", "Director")
    _owner_row(conn, "b", "8", "Ingram David", "Director")
    _owner_row(conn, "c", "9", "Thompson Ken", "Director", nature="By Spouse")
    assert merge_related_owner_filings(conn) == 0


def test_owners_naming_the_same_trust_are_one_holding():
    from form4_rules import one_holding
    def lines(*notes):
        return [{"cik": str(i), "name": f"Person {i}", "nature": n} for i, n in enumerate(notes)]
    assert one_holding(lines("As Trustee for Peter M. Bristow Restated Trust",
                             "By spouse as Trustee for Peter M. Bristow Restated Trust"))
    assert one_holding(lines("As Co-Trustee of Sanfilippo 2017 GST", "As Co-Trustee: Sanfilippo 2017 GST",
                             "As Co-Trustee of Sanfilippo 2017 GST"))
    # Different holders, or notes that don't name anyone, are separate people.
    assert not one_holding(lines("By Hilrod Holdings XXVI, L.P.", "By Hilrod Holdings XVIII, L.P."))
    assert not one_holding(lines("By Trust", "By Trust"))
    assert not one_holding(lines("By IRA", "By 401(k) Plan"))
    assert not one_holding(lines("", ""))
    assert not one_holding(lines("See Footnotes", "See Footnotes"))
    assert not one_holding(lines("By Foundation managed by Reporting Person",
                                 "By Foundation managed by Reporting Person"))
