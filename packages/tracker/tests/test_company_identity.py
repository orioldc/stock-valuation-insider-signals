import sqlite3

from company_identity import assign_tickers, company_for_cik, merge_predecessors, record_issuer_tickers


def _trade(conn, cik, accession, filing_date, ticker, name):
    company_id = company_for_cik(conn, cik, name)
    conn.execute("""INSERT INTO insider_transactions
                    (company_id, filing_date, transaction_type, accession_number, line_number)
                    VALUES (?, ?, 'P', ?, 1)""", (company_id, filing_date, accession))
    record_issuer_tickers(conn, {(str(cik), ticker): (name, filing_date, filing_date)})


def _trades_by_ticker(conn):
    return sorted(conn.execute("""SELECT c.ticker, c.cik, t.accession_number
                                  FROM insider_transactions t JOIN companies c ON c.id = t.company_id"""))


def test_reused_ticker_does_not_pull_in_another_issuer(db_path):
    conn = sqlite3.connect(db_path)
    # A previous release left BOX pointing at the wrong issuer.
    conn.execute("INSERT INTO companies (ticker, cik, name) VALUES ('BOX', 20, 'Box Inc')")
    _trade(conn, 10, "A-1", "2023-01-05", "BOX", "BOX INC")
    _trade(conn, 20, "A-2", "2023-06-01", "BOX", "BOXABL Inc.")

    assert merge_predecessors(conn, {"BOX": 10}) == []
    assign_tickers(conn, {"BOX": 10}, {10: "BOX INC"})

    assert _trades_by_ticker(conn) == [("BOX", 10, "A-1"), ("CIK20", 20, "A-2")]
    assert conn.execute("SELECT name FROM companies WHERE cik = 20").fetchone() == ("BOXABL Inc.",)


def test_reregistered_company_keeps_its_history(db_path):
    conn = sqlite3.connect(db_path)
    _trade(conn, 100, "A-1", "2023-03-01", "BLK", "BlackRock Inc.")
    _trade(conn, 100, "A-2", "2024-09-05", "BLK", "BlackRock Inc.")
    _trade(conn, 200, "A-3", "2024-10-02", "BLK", "BlackRock, Inc.")

    assert merge_predecessors(conn, {"BLK": 200}) == [(100, 200, "BLK")]
    assign_tickers(conn, {"BLK": 200})

    assert _trades_by_ticker(conn) == [("BLK", 200, "A-1"), ("BLK", 200, "A-2"), ("BLK", 200, "A-3")]
    assert conn.execute("SELECT cik FROM company_ciks").fetchall() == [(100,)]


def test_same_name_filing_side_by_side_is_not_merged(db_path):
    # A REIT and its operating partnership both report DLR for years.
    conn = sqlite3.connect(db_path)
    _trade(conn, 1, "A-1", "2020-01-15", "DLR", "DIGITAL REALTY TRUST, INC.")
    _trade(conn, 2, "A-2", "2020-01-17", "DLR", "DIGITAL REALTY TRUST, L.P.")
    _trade(conn, 2, "A-3", "2025-12-01", "DLR", "DIGITAL REALTY TRUST, L.P.")

    assert merge_predecessors(conn, {"DLR": 1}) == []
    assign_tickers(conn, {"DLR": 1})
    assert _trades_by_ticker(conn) == [("CIK2", 2, "A-2"), ("CIK2", 2, "A-3"), ("DLR", 1, "A-1")]


def test_delisted_ticker_goes_to_its_latest_user_and_placeholders_are_dropped(db_path):
    conn = sqlite3.connect(db_path)
    _trade(conn, 1, "A-1", "2020-01-03", "CART", "Carolina Trust BancShares, Inc.")
    _trade(conn, 2, "A-2", "2022-05-01", "NONE", "Some Private Co")
    _trade(conn, 3, "A-3", "2021-02-01", "XYZ", "Old XYZ Corp")
    _trade(conn, 4, "A-4", "2023-02-01", "XYZ", "XYZ Mining Ltd")

    assign_tickers(conn, {})
    assert _trades_by_ticker(conn) == [("CART", 1, "A-1"), ("CIK2", 2, "A-2"),
                                       ("CIK3", 3, "A-3"), ("XYZ", 4, "A-4")]


def test_ensure_company_never_repoints_an_existing_company(db_path, monkeypatch):
    import data_loader
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO companies (ticker, cik, name) VALUES ('ABC', 1, 'ABC Corp')")

    other = data_loader.ensure_company(conn, "ABC", 2, "ABC Holdings")
    assert conn.execute("SELECT ticker, cik FROM companies ORDER BY id").fetchall() == [
        ("ABC", 1), ("CIK2", 2)]
    assert data_loader.ensure_company(conn, "ABC", 1) != other
    assert data_loader.ensure_company(conn, "NEW", 3) == conn.execute(
        "SELECT id FROM companies WHERE ticker = 'NEW' AND cik = 3").fetchone()[0]


def test_lookalike_successors_are_not_merged(db_path):
    conn = sqlite3.connect(db_path)
    # An acquirer that takes the target's name already has its own history.
    _trade(conn, 1, "A-1", "2020-01-10", "CWBC", "COMMUNITY WEST BANCSHARES /")
    _trade(conn, 1, "A-2", "2024-04-05", "CWBC", "COMMUNITY WEST BANCSHARES /")
    _trade(conn, 2, "A-3", "2020-01-06", "CVCY", "Central Valley Community Bancorp")
    _trade(conn, 2, "A-4", "2024-06-01", "CWBC", "Community West Bancshares")
    # A company taken private and re-listed later is a different stock.
    _trade(conn, 3, "A-5", "2020-03-26", "INST", "INSTRUCTURE INC")
    _trade(conn, 4, "A-6", "2021-07-23", "INST", "INSTRUCTURE HOLDINGS, INC.")

    assert merge_predecessors(conn, {"CWBC": 2, "INST": 4}) == []


def test_ticker_waits_for_new_holding_company_to_file(db_path):
    # SEC already lists XOM under the new holding company (CIK 2), which has
    # filed nothing yet; the history is all on the old CIK.
    conn = sqlite3.connect(db_path)
    _trade(conn, 1, "A-1", "2026-06-01", "XOM", "EXXON MOBIL CORP")
    sec, titles = {"XOM": 2}, {2: "ExxonMobil Holdings Corp"}
    assert merge_predecessors(conn, sec) == []
    assign_tickers(conn, sec, titles)
    assert _trades_by_ticker(conn) == [("XOM", 1, "A-1")]

    # Once the new CIK files, the old one folds into it and the ticker moves.
    _trade(conn, 2, "A-2", "2026-07-15", "XOM", "Exxon Mobil Corp")
    assert merge_predecessors(conn, sec) == [(1, 2, "XOM")]
    assign_tickers(conn, sec, titles)
    assert _trades_by_ticker(conn) == [("XOM", 2, "A-1"), ("XOM", 2, "A-2")]


def test_ticker_reused_by_a_company_that_never_files_goes_to_it(db_path):
    # Barrick (foreign, no insider forms) owns B; Barnes Group used it until 2025.
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO companies (ticker, cik, name) VALUES ('B', 9984, 'BARNES GROUP INC')")
    _trade(conn, 9984, "A-1", "2025-01-29", "B", "BARNES GROUP INC")
    assign_tickers(conn, {"B": 756894}, {756894: "BARRICK MINING CORP"})
    assert _trades_by_ticker(conn) == [("CIK9984", 9984, "A-1")]
