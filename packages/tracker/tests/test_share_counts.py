import json
import sqlite3
import zipfile

from share_counts import (
    add_previous_quarter, classify_change, drop_spikes, rebuild_share_tables, share_change_as_of,
    share_changes, share_counts,
)

CHANGE_COLUMNS = """INSERT INTO share_count_changes
    (company_id, accession_number, filed, period_end, basis, shares, shares_year_ago,
     repurchases, shares_prev_quarter)"""


def _fact(val, end, accn, filed, start=None, form="10-Q"):
    e = {"val": val, "end": end, "accn": accn, "filed": filed, "form": form}
    if start:
        e["start"] = start
    return e


def _facts(**concepts):
    """_facts(WeightedAverageNumberOfSharesOutstandingBasic=[...], ...) -> companyfacts 'facts'."""
    taxonomy = {"EntityCommonStockSharesOutstanding": "dei"}
    units = {"NetIncomeLoss": "USD", "EarningsPerShareBasic": "USD/shares",
             "PaymentsForRepurchaseOfCommonStock": "USD"}
    facts = {}
    for name, entries in concepts.items():
        facts.setdefault(taxonomy.get(name, "us-gaap"), {})[name] = {
            "units": {units.get(name, "shares"): entries}}
    return facts


def _quarter_average(accn, filed, now, year_ago):
    return [_fact(now, "2025-06-30", accn, filed, start="2025-04-01"),
            _fact(year_ago, "2024-06-30", accn, filed, start="2024-04-01")]


# Year-on-year changes

def test_change_compares_two_figures_from_the_same_filing():
    facts = _facts(
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 95, 100),
        PaymentsForRepurchaseOfCommonStock=[
            _fact(5e6, "2025-06-30", "A", "2025-08-01", start="2025-01-01")])
    assert share_changes(facts) == [{
        "accession_number": "A", "filed": "2025-08-01", "period_end": "2025-06-30",
        "basis": "average_3m", "shares": 95.0, "shares_year_ago": 100.0, "repurchases": True}]


def test_balance_sheet_change_only_from_annual_reports():
    # A 10-Q's balance sheet compares with the last year end, not a year ago.
    quarterly = [_fact(90, "2025-06-30", "Q", "2025-08-01"), _fact(100, "2024-06-30", "Q", "2025-08-01")]
    annual = [_fact(90, "2025-12-31", "K", "2026-02-20", form="10-K"),
              _fact(100, "2024-12-31", "K", "2026-02-20", form="10-K")]
    changes = share_changes(_facts(CommonStockSharesOutstanding=quarterly + annual))
    assert [(c["accession_number"], c["basis"], c["repurchases"]) for c in changes] == [
        ("K", "year_end", False)]


# Quarter-on-quarter changes

def _quarter(accn, filed, period_end, shares, year_ago, basis="average_3m"):
    return {"accession_number": accn, "filed": filed, "period_end": period_end, "basis": basis,
            "shares": shares, "shares_year_ago": year_ago, "repurchases": False}


def _history(*latest):
    """Four quarters of 2024 reported at 100, then the 2025 quarters given."""
    year_before = [_quarter(f"Y{i}", f"2024-{m:02d}-01", end, 100, 101)
                   for i, (m, end) in enumerate([(5, "2024-03-31"), (8, "2024-06-30"),
                                                 (11, "2024-09-30")])]
    return year_before + list(latest)


def test_previous_quarter_set_when_both_filings_share_a_basis():
    changes = add_previous_quarter(_history(
        _quarter("Q1", "2025-05-01", "2025-03-31", 98, 100),
        _quarter("Q2", "2025-08-01", "2025-06-30", 96, 100)))
    by_accn = {c["accession_number"]: c["shares_prev_quarter"] for c in changes}
    assert by_accn["Q2"] == 98
    # Q1's previous quarter (2024 Q4) was never filed, so nothing to compare.
    assert by_accn["Q1"] is None


def test_previous_quarter_dropped_after_a_split():
    # The 2025 filings restate 2024 at 1,000 (a 10-for-1 split): Q2's year-ago
    # figure no longer matches what was reported a year before, so the
    # previous quarter's figure can't be trusted to be on the same basis.
    changes = add_previous_quarter(_history(
        _quarter("Q1", "2025-05-01", "2025-03-31", 98, 100),
        _quarter("Q2", "2025-08-01", "2025-06-30", 960, 1_000)))
    assert {c["accession_number"]: c["shares_prev_quarter"] for c in changes}["Q2"] is None


def test_previous_quarter_dropped_when_it_more_than_doubles():
    changes = add_previous_quarter(_history(
        _quarter("Q1", "2025-05-01", "2025-03-31", 40, 100),
        _quarter("Q2", "2025-08-01", "2025-06-30", 100, 100)))
    assert {c["accession_number"]: c["shares_prev_quarter"] for c in changes}["Q2"] is None


def test_previous_quarter_only_for_quarterly_averages():
    changes = add_previous_quarter(_history(
        _quarter("Q1", "2025-05-01", "2025-03-31", 98, 100),
        _quarter("K", "2025-08-01", "2025-06-30", 96, 100, basis="year_end")))
    assert {c["accession_number"]: c["shares_prev_quarter"] for c in changes}["K"] is None


# Share count levels

def test_cover_count_kept_when_it_agrees_with_the_average():
    facts = _facts(
        EntityCommonStockSharesOutstanding=[_fact(1_020, "2025-07-25", "A", "2025-08-01")],
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 1_000, 1_050))
    assert share_counts(facts) == [{"date": "2025-07-25", "shares": 1_020.0, "source": "sec_cover"}]



def test_mistyped_cover_date_falls_back_to_the_filing_date():
    facts = _facts(
        EntityCommonStockSharesOutstanding=[_fact(1_020, "2035-07-25", "A", "2025-08-01")],
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 1_000, 1_050))
    assert share_counts(facts) == [{"date": "2025-08-01", "shares": 1_020.0, "source": "sec_cover"}]


def test_cover_classes_are_added_up():
    # Meta-style: class A and class B on the cover, the average covers both.
    facts = _facts(
        EntityCommonStockSharesOutstanding=[_fact(2_200, "2025-07-25", "A", "2025-08-01"),
                                            _fact(340, "2025-07-25", "A", "2025-08-01")],
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 2_530, 2_600))
    assert share_counts(facts)[0]["shares"] == 2_540.0


def test_figure_in_wrong_units_is_caught_by_net_income_over_eps():
    # McDonald's-style: the average is tagged in millions; net income / EPS
    # confirms the cover count instead.
    facts = _facts(
        EntityCommonStockSharesOutstanding=[_fact(707_600_000, "2025-07-25", "A", "2025-08-01")],
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 707.6, 715.0),
        NetIncomeLoss=[_fact(2_000_000_000, "2025-06-30", "A", "2025-08-01", start="2025-04-01")],
        EarningsPerShareBasic=[_fact(2.83, "2025-06-30", "A", "2025-08-01", start="2025-04-01")])
    assert share_counts(facts) == [{"date": "2025-07-25", "shares": 707_600_000.0, "source": "sec_cover"}]


def test_average_used_when_the_cover_lists_one_class_only():
    facts = _facts(
        EntityCommonStockSharesOutstanding=[_fact(100, "2025-07-25", "A", "2025-08-01")],
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 1_000, 1_000),
        CommonStockSharesOutstanding=[_fact(1_010, "2025-06-30", "A", "2025-08-01")])
    assert share_counts(facts) == [{"date": "2025-08-01", "shares": 1_000.0, "source": "sec_average"}]


def test_uncorroborated_figure_is_dropped():
    facts = _facts(
        WeightedAverageNumberOfSharesOutstandingBasic=_quarter_average("A", "2025-08-01", 1_000, 1_000),
        # A tiny EPS is too rounded to check anything.
        NetIncomeLoss=[_fact(50_000, "2025-06-30", "A", "2025-08-01", start="2025-04-01")],
        EarningsPerShareBasic=[_fact(0.05, "2025-06-30", "A", "2025-08-01", start="2025-04-01")])
    assert share_counts(facts) == []


def test_one_off_spike_is_dropped_but_a_split_is_kept():
    def counts(*shares):
        return [{"date": f"2025-0{i + 1}-01", "shares": s, "source": "sec_cover"}
                for i, s in enumerate(shares)]
    assert [c["shares"] for c in drop_spikes(counts(100, 100_000, 102))] == [100, 102]
    assert [c["shares"] for c in drop_spikes(counts(100, 1_000, 1_000))] == [100, 1_000, 1_000]


def test_classify_change():
    def change(pct, repurchases=False):
        return {"shares": 100 + pct, "shares_year_ago": 100, "repurchases": repurchases}
    assert classify_change(change(-5)) == "buyback"
    assert classify_change(change(-30)) == "unexplained_decline"
    assert classify_change(change(-30, repurchases=True)) == "buyback"
    assert classify_change(change(5)) == "dilution"
    assert classify_change(change(0.5)) == "stable"


# Rebuilding the tables

def _company_with_trade(conn, cik, ticker):
    company_id = conn.execute("INSERT INTO companies (ticker, cik) VALUES (?, ?)", (ticker, cik)).lastrowid
    conn.execute("""INSERT INTO insider_transactions (company_id, transaction_type, accession_number, line_number)
                    VALUES (?, 'P', ?, 1)""", (company_id, f"T-{cik}"))
    return company_id


def test_old_cik_figures_stop_where_the_successor_starts(db_path, tmp_path):
    def cover_and_average(accn, cover_date, filed, shares):
        return {"cover": _fact(shares, cover_date, accn, filed),
                "average": [_fact(shares, filed[:4] + "-03-31", accn, filed, start=filed[:4] + "-01-01")]}

    def facts_for(*filings):
        return {"facts": _facts(
            EntityCommonStockSharesOutstanding=[f["cover"] for f in filings],
            WeightedAverageNumberOfSharesOutstandingBasic=[e for f in filings for e in f["average"]])}

    # Howard Hughes-style: the old CIK keeps filing as a subsidiary with 10 shares.
    old = facts_for(cover_and_average("O1", "2022-04-25", "2022-05-01", 50e6),
                    cover_and_average("O2", "2024-04-25", "2024-05-01", 10))
    new = facts_for(cover_and_average("N1", "2023-10-25", "2023-11-01", 50e6))
    zip_path = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("CIK0000000002.json", json.dumps(new))
        z.writestr("CIK0000000001.json", json.dumps(old))

    conn = sqlite3.connect(db_path)
    company_id = _company_with_trade(conn, 2, "HHH")
    conn.execute("INSERT INTO company_ciks (cik, company_id) VALUES (1, ?)", (company_id,))
    rebuild_share_tables(conn, str(zip_path))

    assert conn.execute("SELECT date, shares FROM shares_outstanding ORDER BY date").fetchall() == [
        ("2022-04-25", 50e6), ("2023-10-25", 50e6)]


def test_change_is_only_visible_once_filed(db_path):
    conn = sqlite3.connect(db_path)
    company_id = _company_with_trade(conn, 1, "ABC")
    conn.executemany(CHANGE_COLUMNS + " VALUES (?, ?, ?, ?, 'average_3m', ?, 100, ?, ?)", [
        (company_id, "A1", "2025-05-01", "2025-03-31", 98, 0, None),
        (company_id, "A2", "2025-08-01", "2025-06-30", 90, 1, 98)])
    assert share_change_as_of(conn, company_id, "2025-04-30") is None
    assert share_change_as_of(conn, company_id, "2025-07-31")["period_end"] == "2025-03-31"
    latest = share_change_as_of(conn, company_id, "2025-08-01")
    assert (latest["period_end"], latest["repurchases"], round(latest["change_pct"], 6)) == (
        "2025-06-30", True, -10.0)
    assert round(latest["change_qoq_pct"], 4) == round((90 / 98 - 1) * 100, 4)
    assert share_change_as_of(conn, company_id, "2025-07-31")["change_qoq_pct"] is None


# What the signal reports

def test_compute_share_delta(db_path, monkeypatch):
    from signals import share_count_change
    monkeypatch.setattr(share_count_change, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    rows = {"BUY": (90, 1, "2025-06-30", None), "DROP": (60, 0, "2025-06-30", None),
            "OLD": (90, 1, "2023-06-30", None), "NONE": None, "QOQ": (90, 1, "2025-06-30", 92)}
    for cik, (ticker, row) in enumerate(rows.items(), start=1):
        company_id = _company_with_trade(conn, cik, ticker)
        if row:
            shares, repurchases, period_end, prev_quarter = row
            conn.execute(CHANGE_COLUMNS + " VALUES (?, 'A', '2025-08-01', ?, 'average_3m', ?, 100, ?, ?)",
                         (company_id, period_end, shares, repurchases, prev_quarter))
    conn.commit()

    def delta(ticker):
        return share_count_change.compute_share_delta(ticker, as_of="2025-09-01")
    buy = delta("BUY")
    assert (buy["trend"], buy["delta_4q"], buy["score"], buy["delta_qoq"]) == ("buyback", -10.0, 0.5, None)
    assert (delta("DROP")["trend"], delta("DROP")["score"]) == ("unexplained_decline", 0)
    assert (delta("OLD")["trend"], delta("OLD")["score"]) == ("stale", 0)
    assert (delta("NONE")["trend"], delta("NONE")["delta_4q"]) == ("insufficient_data", None)
    assert delta("MISSING")["trend"] == "insufficient_data"
    assert delta("QOQ")["delta_qoq"] == round((90 / 92 - 1) * 100, 4)
