import edgar_client

XML = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>{doc}</documentType>
  {orig}
  <issuer><issuerCik>{issuer}</issuerCik><issuerTradingSymbol>ABC</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000000050</rptOwnerCik><rptOwnerName>Big Fund LP</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner></reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000000900</rptOwnerCik><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>true</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2025-01-08</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>10.5</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>1100</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2025-01-08</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


class _Resp:
    def __init__(self, text):
        self.text = text


def _parse(monkeypatch, issuer="0000001234", doc="4", orig=""):
    monkeypatch.setattr(edgar_client, "_get",
                        lambda url: _Resp(XML.format(issuer=issuer, doc=doc, orig=orig)))
    return edgar_client.parse_form4_xml("1234", "0000000000-25-000001", "x.xml", "2025-01-10")


def test_filing_about_another_issuer_is_dropped(monkeypatch):
    # Listed under CIK 1234 because 1234 is an owner, but the stock is CIK 999's.
    assert _parse(monkeypatch, issuer="0000000999") == []


def test_own_stock_trades_are_parsed_with_shared_rules(monkeypatch):
    rows = _parse(monkeypatch)
    assert [r["line_number"] for r in rows] == [1, 2]
    first, second = rows
    assert first["insider_name"] == "Jane Doe"
    assert first["insider_cik"] == "900"
    assert first["relationship"] == "Officer (CEO), Director"
    assert first["all_owners"] == ["Jane Doe", "Big Fund LP"]
    assert first["price"] == 10.5
    assert first["accession_number"] == "0000000000-25-000001"
    assert first["document_type"] == "4"
    assert first["date_of_orig_sub"] is None
    # Two same-size trades on the same day stay two rows; zero price is unknown.
    assert second["shares"] == 100 and second["price"] is None


def test_amendment_fields(monkeypatch):
    rows = _parse(monkeypatch, doc="4/A", orig="<dateOfOriginalSubmission>2025-01-09</dateOfOriginalSubmission>")
    assert rows[0]["document_type"] == "4/A"
    assert rows[0]["date_of_orig_sub"] == "2025-01-09"


def test_full_submission_text_with_any_issuer():
    # SEC's .txt wraps the XML in submission headers; cik=None keeps any issuer.
    text = "<SEC-HEADER>...</SEC-HEADER>\n<XML>\n" + XML.format(issuer="0000000999", doc="4", orig="") + "</XML>"
    rows = edgar_client.parse_form4_document(text, None, "0000000000-25-000001", "2025-01-10")
    assert [(r["issuer_cik"], r["line_number"]) for r in rows] == [("999", 1), ("999", 2)]
