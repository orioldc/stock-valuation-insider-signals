"""Which filed purchase prices the cleanup treats as typos."""

import sqlite3

from scripts.cleanup_corrupt_prices import find_corrupt_prices


def _corrupt_ids(db_path, ticker, closes, trades):
    """closes: (date, close); trades: (trade date, price). Returns ids found corrupt."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, close REAL)")
    conn.execute("CREATE TABLE split_events (ticker TEXT, date TEXT, ratio REAL)")
    company_id = conn.execute("INSERT INTO companies (ticker, cik) VALUES (?, 1)", (ticker,)).lastrowid
    conn.executemany("INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)",
                     [(ticker, d, c) for d, c in closes])
    ids = [conn.execute("""INSERT INTO insider_transactions
        (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
         transaction_type, shares_transacted, price, accession_number, line_number)
        VALUES (?, ?, ?, 'A', '1', 'P', 100, ?, ?, 1)""",
        (company_id, d, d, p, f"A{i}")).lastrowid for i, (d, p) in enumerate(trades)]
    corrupt, _ = find_corrupt_prices(conn)
    return [ids.index(r[0]) for r in corrupt if r[0] in ids]


def test_dollar_total_typed_as_the_price_is_corrupt(db_path):
    assert _corrupt_ids(db_path, "ABC", [("2025-03-03", 20.0)],
                        [("2025-03-03", 20.5), ("2025-03-03", 100000.0)]) == [1]


def test_a_sub_cent_close_from_another_security_does_not_condemn_real_prices(db_path):
    assert _corrupt_ids(db_path, "TRLV", [("2024-11-08", 0.001)], [("2024-11-08", 6.98)]) == []



def test_a_close_from_months_before_the_trade_still_catches_a_dollar_total(db_path):
    assert _corrupt_ids(db_path, "CCNE", [("2021-02-12", 19.78)], [("2021-06-15", 24307.0)]) == [0]
