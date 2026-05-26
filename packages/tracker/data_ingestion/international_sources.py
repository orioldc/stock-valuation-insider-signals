"""
International Insider Transaction Sources
==========================================

SEC EDGAR only covers US-listed companies. For international markets, different
regulatory frameworks require insider/PDMR transaction disclosures:

Markets with Public Insider Transaction Disclosure:
----------------------------------------------------

1. **UK — FCA / RNS (Regulatory News Service)**
   - PDMRs (Persons Discharging Managerial Responsibilities) must report trades
     within 3 business days under UK MAR (Market Abuse Regulation).
   - Data available via: London Stock Exchange RNS feed, FCA notification search.
   - URL: https://www.londonstockexchange.com/news?tab=news-explorer
   - Free, but requires scraping. No clean REST API.

2. **EU — PDMR / MAR (Market Abuse Regulation)**
   - Each national competent authority (NCA) publishes PDMR notifications.
   - Germany (BaFin): https://portal.mvp.bafin.de/database/DealingsInfo/
   - France (AMF): https://bdif.amf-france.org/
   - Fragmented across ~27 NCAs. No single API.

3. **Canada — SEDI (System for Electronic Disclosure by Insiders)**
   - Insiders must file within 5 days of a trade.
   - URL: https://www.sedi.ca/
   - Free, but UI-based. Can be scraped with effort.

4. **Australia — ASX**
   - Directors must notify within 5 business days.
   - ASX announcements platform: https://www.asx.com.au/asx/v2/statistics/announcements.do
   - Can filter by announcement type (Director's Interest Notice - 3Y, 3Z).

5. **Hong Kong — HKEX**
   - Directors/substantial shareholders must disclose within 3 business days.
   - URL: https://www.hkexnews.hk/

Current Expansion Targets:
--------------------------
- Phase 1: UK FTSE 350 + Canadian TSX 60 (stub functions below)
- Phase 2: EU major markets (Germany DAX, France CAC 40)
- Phase 3: Australia ASX 200, Hong Kong HSI
"""

import logging

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# UK FTSE 350 Universe
# ══════════════════════════════════════════════════════════════════════════════

FTSE_350_SAMPLE = [
    # FTSE 100 (top constituents)
    "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "LSEG.L",
    "DGE.L", "REL.L", "BA.L", "NG.L", "LLOY.L", "BARC.L", "AHT.L", "RKT.L",
    "CRH.L", "VOD.L", "PRU.L", "CPG.L", "ANTO.L", "SSE.L", "ABF.L", "STAN.L",
    "IHG.L", "INF.L", "TSCO.L", "WPP.L", "AVV.L", "BHP.L", "MNG.L", "EXPN.L",
    "NWG.L", "AAL.L", "IMB.L", "SVT.L", "SDR.L", "AV.L", "LGEN.L", "BDEV.L",
    "ADM.L", "CNA.L", "PHNX.L", "SGRO.L", "ENT.L", "JET.L", "RTO.L", "WTB.L",
    "III.L", "TW.L",
    # FTSE 250 (selected)
    "DARK.L", "AUTO.L", "IGG.L", "BVIC.L", "CINE.L", "DCC.L", "DPLM.L",
    "FCIT.L", "GRI.L", "HLMA.L", "HWDN.L", "JDW.L", "KGF.L", "MNDI.L",
    "OSB.L", "PAGE.L", "RWS.L", "SMIN.L", "TRN.L", "UDG.L", "VCT.L", "WIX.L",
]

# ══════════════════════════════════════════════════════════════════════════════
# Canadian TSX 60 Universe
# ══════════════════════════════════════════════════════════════════════════════

TSX_60 = [
    "RY.TO", "TD.TO", "ENB.TO", "CNR.TO", "BN.TO", "BMO.TO", "CP.TO", "BNS.TO",
    "TRI.TO", "CSU.TO", "MFC.TO", "ATD.TO", "SU.TO", "CNQ.TO", "ABX.TO", "CM.TO",
    "SLF.TO", "NTR.TO", "FNV.TO", "WCN.TO", "QSR.TO", "TRP.TO", "IFC.TO", "NA.TO",
    "GIB-A.TO", "OTEX.TO", "SAP.TO", "WPM.TO", "AEM.TO", "DOL.TO", "FFH.TO", "L.TO",
    "FM.TO", "EMA.TO", "MGA.TO", "CCO.TO", "IMO.TO", "WFG.TO", "CTC-A.TO", "GWO.TO",
    "FTS.TO", "POW.TO", "RBA.TO", "TFII.TO", "AQN.TO", "CAR-UN.TO", "IGM.TO",
    "BYD.TO", "CCL-B.TO", "CU.TO", "EFN.TO", "GFL.TO", "H.TO", "IAG.TO", "K.TO",
    "ONEX.TO", "PKI.TO", "STN.TO", "X.TO",
]


def fetch_uk_insider_trades(tickers=None):
    """
    Fetch UK PDMR (insider) transactions from RNS/FCA sources.

    STUB — To be implemented.

    Approach:
    1. Query London Stock Exchange RNS feed for "PDMR Dealing" announcements
    2. Parse the structured notification fields (name, role, instrument, price, volume, date)
    3. Map to our insider_transactions schema
    4. Store with source='UK_RNS'

    RNS feed URL pattern:
        https://www.londonstockexchange.com/news?tab=news-explorer&headlinetypes=PDMR
    
    Returns: list of dicts matching insider_transactions schema
    """
    if tickers is None:
        tickers = FTSE_350_SAMPLE
    logger.info(f"fetch_uk_insider_trades: STUB — would fetch for {len(tickers)} UK tickers")
    # TODO: Implement RNS scraping
    return []


def fetch_canada_insider_trades(tickers=None):
    """
    Fetch Canadian insider transactions from SEDI.

    STUB — To be implemented.

    Approach:
    1. Query SEDI (https://www.sedi.ca/) insider filing search
    2. For each issuer, fetch recent insider transaction reports
    3. Parse: insider name, relationship, security, transaction type, # units, price, date
    4. Map to our insider_transactions schema
    5. Store with source='CANADA_SEDI'

    SEDI has a web UI with POST-based search. Would need requests + HTML parsing.
    
    Returns: list of dicts matching insider_transactions schema
    """
    if tickers is None:
        tickers = TSX_60
    logger.info(f"fetch_canada_insider_trades: STUB — would fetch for {len(tickers)} Canadian tickers")
    # TODO: Implement SEDI scraping
    return []


def get_international_universe():
    """Return combined international ticker lists for reference."""
    return {
        "uk_ftse350": FTSE_350_SAMPLE,
        "canada_tsx60": TSX_60,
    }
