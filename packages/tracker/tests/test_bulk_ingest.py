import os
import sqlite3
import zipfile

import bulk_edgar

SUBMISSION = [
    ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "DATE_OF_ORIG_SUB", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL"],
    ["A-1", "10-JAN-2025", "4", "", "0000001234", "ABC Corp", "ABC"],
    ["A-2", "01-FEB-2025", "4/A", "10-JAN-2025", "0000001234", "ABC Corp", "ABC"],
    ["A-3", "10-JAN-2025", "3", "", "0000001234", "ABC Corp", "ABC"],
]
OWNERS = [
    ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
    ["A-1", "0000000050", "Big Fund LP", "TenPercentOwner", ""],
    ["A-1", "0000000900", "Jane Doe", "Director,Officer", "CEO"],
    ["A-2", "0000000900", "Jane Doe", "Director,Officer", "CEO"],
    ["A-3", "0000000900", "Jane Doe", "Director,Officer", "CEO"],
]
NONDERIV = [
    ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
     "TRANS_PRICEPERSHARE", "SHRS_OWND_FOLWNG_TRANS", "TRANS_ACQUIRED_DISP_CD"],
    # Listed out of order on purpose; SK decides the line number.
    ["A-1", "12", "08-JAN-2025", "P", "100", "0.0", "1200", "A"],
    ["A-1", "11", "08-JAN-2025", "P", "100", "10.5", "1100", "A"],
    ["A-2", "20", "08-JAN-2025", "P", "100", "10.75", "1100", "A"],
    ["A-3", "30", "08-JAN-2025", "P", "999", "1", "999", "A"],
]


def _write_zip(bulk_dir):
    os.makedirs(bulk_dir, exist_ok=True)
    with zipfile.ZipFile(os.path.join(bulk_dir, "2025q1_form345.zip"), "w") as zf:
        for name, rows in (("SUBMISSION.tsv", SUBMISSION), ("REPORTINGOWNER.tsv", OWNERS),
                           ("NONDERIV_TRANS.tsv", NONDERIV)):
            zf.writestr(name, "\n".join("\t".join(r) for r in rows) + "\n")


def test_bulk_quarter_loads_and_amendments_apply(db_path, tmp_path, monkeypatch):
    bulk_dir = str(tmp_path / "bulk")
    _write_zip(bulk_dir)
    monkeypatch.setattr(bulk_edgar, "DB_PATH", db_path)
    monkeypatch.setattr(bulk_edgar, "BULK_DIR", bulk_dir)

    result = bulk_edgar.ingest_quarter(2025, 1)
    assert result["transactions"] == 3          # Form 3 line excluded
    # Loading the same quarter again adds nothing.
    assert bulk_edgar.ingest_quarter(2025, 1)["transactions"] == 0

    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT accession_number, line_number, reporting_cik, reporting_name, price,
               document_type, date_of_orig_sub, filing_date
        FROM insider_transactions ORDER BY accession_number, line_number
    """).fetchall()
    assert rows == [
        ("A-1", 1, "900", "Jane Doe", 10.5, "4", None, "2025-01-10"),
        ("A-1", 2, "900", "Jane Doe", None, "4", None, "2025-01-10"),
        ("A-2", 1, "900", "Jane Doe", 10.75, "4/A", "2025-01-10", "2025-02-01"),
    ]

    from form4_rules import apply_amendments
    # The 4/A restates the 8 Jan purchase, so both original lines for that
    # owner/date/code go and only the amended line remains.
    assert apply_amendments(conn) == 2
    assert conn.execute("SELECT accession_number FROM insider_transactions").fetchall() == [("A-2",)]
