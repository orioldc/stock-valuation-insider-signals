"""When a cluster becomes tradeable, and what one ticker's summary reports."""

import json
import sqlite3

import pandas as pd

from backtest.historical_backtest import detect_clusters
from data_ingestion.share_counts import share_change_as_of
from signals.ticker_summary import summarize_ticker


def _purchases(*rows):
    """rows: (cik, name, trade date, filing date or None, price)."""
    return pd.DataFrame([{
        "ticker": "ABC", "sector": "Technology",
        "transaction_date": pd.Timestamp(traded),
        "filing_date": pd.Timestamp(filed) if filed else pd.NaT,
        "reporting_name": name, "reporting_cik": cik,
        "shares_transacted": 1000, "price": price, "total_value": 1000 * price,
        "raw_json": json.dumps({"relationship": "Director"}),
    } for cik, name, traded, filed, price in rows])


def test_cluster_signal_date_is_the_last_filing_not_the_last_trade():
    clusters = detect_clusters(_purchases(
        ("1", "A", "2025-03-03", "2025-03-04", 10),
        ("2", "B", "2025-03-05", "2025-03-12", 10)))
    row = clusters.iloc[0]
    assert (row["last_trade_date"], row["signal_date"]) == ("2025-03-05", "2025-03-12")


def test_trades_without_a_believable_filing_date_are_left_out():
    clusters = detect_clusters(_purchases(
        ("1", "A", "2025-03-03", "2025-03-04", 10),
        ("2", "B", "2025-03-05", "2025-03-06", 10),
        ("3", "C", "2025-03-06", None, 10),
        ("4", "D", "2025-03-07", "2025-03-01", 10)))
    assert clusters.iloc[0]["n_insiders"] == 2


def test_late_amendment_for_an_older_year_does_not_hide_a_newer_quarter(db_path):
    conn = sqlite3.connect(db_path)
    company_id = conn.execute("INSERT INTO companies (ticker, cik) VALUES ('ABC', 1)").lastrowid
    conn.executemany("""INSERT INTO share_count_changes
        (company_id, accession_number, filed, period_end, basis, shares, shares_year_ago,
         repurchases, shares_prev_quarter) VALUES (?, ?, ?, ?, 'average_3m', ?, 100, 0, NULL)""", [
        (company_id, "Q2", "2025-08-01", "2025-06-30", 90),
        (company_id, "K-A", "2025-09-15", "2024-12-31", 110)])
    assert share_change_as_of(conn, company_id, "2025-10-01")["period_end"] == "2025-06-30"


def _trade(conn, company_id, n, cik, name, traded, price, relationship="Director"):
    conn.execute("""INSERT INTO insider_transactions
        (company_id, filing_date, transaction_date, reporting_name, reporting_cik, transaction_type,
         shares_transacted, price, raw_json, accession_number, line_number)
        VALUES (?, ?, ?, ?, ?, 'P', 1000, ?, ?, ?, 1)""",
        (company_id, traded, traded, name, cik, price,
         json.dumps({"relationship": relationship}), f"A{n}"))


def test_summary_counts_insiders_by_cik_and_keeps_unpriced_trades_out_of_dollars(db_path):
    conn = sqlite3.connect(db_path)
    company_id = conn.execute(
        "INSERT INTO companies (ticker, cik, sector) VALUES ('ABC', 1, 'Technology')").lastrowid
    _trade(conn, company_id, 1, "10", "Smith John", "2025-09-01", 20, "CEO")
    _trade(conn, company_id, 2, "10", "John Smith", "2025-09-02", 20, "CEO")  # same person
    _trade(conn, company_id, 3, "11", "Jane Doe", "2025-09-03", None)
    _trade(conn, company_id, 4, "12", "Old Buyer", "2025-01-02", 50)  # outside 120 days

    s = summarize_ticker(conn, "abc", as_of="2025-09-30")
    assert (s["n_insiders"], s["total_value"], s["trades_unpriced"]) == (2, 40000, 1)
    assert s["cluster_detected"] and s["cluster_n_insiders"] == 2 and s["has_ceo"]
    assert s["cluster_total_value"] == 40000 and s["cluster_trades_unpriced"] == 1
    assert s["share_trend"] == "insufficient_data"
    assert summarize_ticker(conn, "NOPE", as_of="2025-09-30") is None


def test_summary_has_no_cluster_or_quality_for_one_buyer(db_path):
    conn = sqlite3.connect(db_path)
    company_id = conn.execute("INSERT INTO companies (ticker, cik) VALUES ('ABC', 1)").lastrowid
    _trade(conn, company_id, 1, "10", "Solo", "2025-09-01", 20)
    s = summarize_ticker(conn, "ABC", as_of="2025-09-30")
    assert (s["cluster_detected"], s["quality"], s["n_insiders"]) == (False, None, 1)
