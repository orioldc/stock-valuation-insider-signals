import sqlite3

from test_parse_form4_xml import XML

from pipeline import correctness_audit


def _trade(conn, accession, line, shares=100.0, price=10.5, owner="900", code="P", date="2025-01-08"):
    conn.execute("""
        INSERT INTO insider_transactions
        (company_id, filing_date, transaction_date, reporting_cik, transaction_type,
         shares_transacted, price, accession_number, line_number, document_type)
        VALUES (1, '2025-01-10', ?, ?, ?, ?, ?, ?, ?, '4')
    """, (date, owner, code, shares, price, accession, line))


def _db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO companies (id, ticker, cik) VALUES (1, 'ABC', 1234)")
    return conn


def _results(results):
    return {r["id"]: r for r in results}


def test_broken_rules_fail(db_path):
    conn = _db(db_path)
    _trade(conn, "joint", 1, owner="900")
    _trade(conn, "joint", 2, owner="50")              # same filing credited to two insiders
    _trade(conn, "late", 1, date="2025-03-01")        # traded after it was filed
    results = _results(correctness_audit.check_invariants(conn))
    assert results["invariant.one_owner_per_filing"]["passed"] is False
    assert results["invariant.cik_format"]["passed"] is True
    late = results["invariant.trade_not_after_filing"]
    assert (late["passed"], late["severity"], late["measured"]) == (False, "WARN", {"rows": 1})


def test_stored_trade_that_differs_from_the_filing_is_reported(db_path, monkeypatch):
    conn = _db(db_path)
    filing = XML.format(issuer="0000001234", doc="4", orig="")
    _trade(conn, "good", 1)                          # matches the filing
    _trade(conn, "bad", 1, shares=1000)              # filing says 100
    _trade(conn, "lost", 9)                          # the filing has no line 9
    monkeypatch.setattr(correctness_audit, "_fetch_filing", lambda accession, ciks: filing)

    class AllRows:
        def sample(self, ids, n):
            return ids
    results = _results(correctness_audit.check_rows_against_sec(conn, 6, AllRows()))
    rows = results["sec.rows_match_filings"]
    assert rows["passed"] is False
    assert rows["measured"]["checked"] == 3
    assert sorted(rows["problems"]) == [
        "ABC bad line 1: shares 1000.0, SEC says 100.0",
        "ABC lost line 9: no such trade line in the filing",
    ]


def test_unreachable_filings_fail_the_check(db_path, monkeypatch):
    conn = _db(db_path)
    _trade(conn, "gone", 1)
    monkeypatch.setattr(correctness_audit, "_fetch_filing", lambda accession, ciks: None)

    class AllRows:
        def sample(self, ids, n):
            return ids
    rows = _results(correctness_audit.check_rows_against_sec(conn, 2, AllRows()))["sec.rows_match_filings"]
    assert (rows["passed"], rows["measured"]["unreachable"]) == (False, 1)
