import sqlite3

import data_loader
from refresh import _quarters_after


def _txn(acc, line):
    return {
        "insider_name": "Jane Doe", "insider_cik": "900", "relationship": "Director",
        "all_owners": ["Jane Doe"], "transaction_code": "P", "transaction_date": "2025-01-08",
        "shares": 100.0, "price": 10.0, "total_value": 1000.0, "shares_owned_after": None,
        "acq_disp": "A", "accession_number": acc, "line_number": line,
        "document_type": "4", "date_of_orig_sub": None,
        "issuer_ticker": "ABC", "issuer_name": "ABC Corp",
    }


def test_filings_already_stored_are_not_refetched(db_path, monkeypatch):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO companies (id, ticker, cik) VALUES (1, 'ABC', 1234)")
    conn.execute("""INSERT INTO insider_transactions (company_id, filing_date, accession_number, line_number, source)
                    VALUES (1, '2025-01-10', 'OLD', 1, 'EDGAR_BULK')""")
    conn.commit()

    fetched = []

    def fake_parse(cik, acc, doc, filing_date=None):
        fetched.append(acc)
        return [_txn(acc, 1), _txn(acc, 2)]

    monkeypatch.setattr(data_loader, "parse_form4_xml", fake_parse)
    monkeypatch.setattr(data_loader, "_validate_price_value",
                        lambda price, *a, **k: (True, None, price))
    filings = [{"cik": "1234", "accession_number": a, "primary_doc": "x.xml",
                "filing_date": "2025-04-02", "form": "4"} for a in ("OLD", "NEW")]
    assert data_loader._store_filings(conn, 1, "ABC", filings) == 2
    assert fetched == ["NEW"]
    assert conn.execute("SELECT * FROM issuer_tickers").fetchall() == [
        (1234, "ABC", "ABC Corp", "2025-04-02", "2025-04-02")]
    # Second pass: everything known, nothing fetched or inserted.
    assert data_loader._store_filings(conn, 1, "ABC", filings) == 0
    assert fetched == ["NEW"]


def test_rejected_price_keeps_the_trade(db_path, monkeypatch):
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO companies (id, ticker, cik) VALUES (1, 'ABC', 1234)")
    monkeypatch.setattr(data_loader, "parse_form4_xml", lambda *a, **k: [_txn("X", 1)])
    monkeypatch.setattr(data_loader, "_validate_price_value",
                        lambda *a, **k: (False, "exceeds market cap", None))
    filings = [{"cik": "1234", "accession_number": "X", "primary_doc": "x.xml",
                "filing_date": "2025-04-02", "form": "4"}]
    assert data_loader._store_filings(conn, 1, "ABC", filings) == 1
    assert conn.execute("SELECT price FROM insider_transactions").fetchone() == (None,)


def test_quarters_after(monkeypatch):
    import datetime as dt
    import refresh

    class FakeDT(dt.datetime):
        @classmethod
        def now(cls):
            return cls(2026, 10, 2)

    monkeypatch.setattr(refresh, "datetime", FakeDT)
    assert _quarters_after("2026-06-30") == [(2026, 3), (2026, 4)]
    assert _quarters_after("2026-09-30") == [(2026, 4)]
    assert _quarters_after("2026-10-01") == []
    assert _quarters_after(None) == [(2026, 4)]


def test_form_index_parsing_survives_long_company_names():
    from backfill_quarter_index import parse_form_index
    header = "Form Type   Company Name\n" + "-" * 140 + "\n"
    normal = ("4           AARON'S COMPANY, INC. (THE)                                   "
              "1807966     2026-07-01  edgar/data/1807966/0001209191-26-046401.txt\n")
    long_name = ("4/A         SOME EXTREMELY LONG REPORTING OWNER NAME THAT RUNS PAST THE COLUMN "
                 "of   2102046     2026-07-02  edgar/data/2102046/0001209191-26-046999.txt\n")
    form3 = ("3           NEW INSIDER                                                   "
             "1111111     2026-07-01  edgar/data/1111111/0001209191-26-047000.txt\n")
    rows = parse_form_index(header + normal + long_name + form3)
    assert [(r["form_type"], r["cik"], r["filing_date"], r["accession_number"]) for r in rows] == [
        ("4", "1807966", "2026-07-01", "0001209191-26-046401"),
        ("4/A", "2102046", "2026-07-02", "0001209191-26-046999"),
    ]
