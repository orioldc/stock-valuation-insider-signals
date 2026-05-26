import sqlite3
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edgar_client import fetch_company_tickers, fetch_form4_filings, parse_form4_xml, _get

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "insider_signals.db")


def load_universe():
    """Dynamic universe: all tickers with at least 1 purchase transaction in the last 2 years."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT c.ticker FROM companies c
        JOIN insider_transactions it ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.transaction_date >= date('now', '-2 years')
          AND c.ticker IS NOT NULL
          AND c.ticker != ''
          AND c.ticker NOT LIKE 'CIK%'
        ORDER BY c.ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    if tickers:
        return tickers
    # Fallback to hardcoded list if DB is empty (first run)
    return _hardcoded_universe()


def load_full_universe():
    """Return ALL tickers in the companies table."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker FROM companies
        WHERE ticker IS NOT NULL AND ticker != '' AND ticker NOT LIKE 'CIK%'
        ORDER BY ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


def load_active_universe(months=6):
    """Tickers with purchase activity in the last N months (for incremental XML refresh)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT DISTINCT c.ticker FROM companies c
        JOIN insider_transactions it ON it.company_id = c.id
        WHERE it.transaction_type = 'P'
          AND it.transaction_date >= date('now', '-{months} months')
          AND c.ticker IS NOT NULL
          AND c.ticker != ''
          AND c.ticker NOT LIKE 'CIK%'
        ORDER BY c.ticker
    """)
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


def _hardcoded_universe():
    """[LEGACY] Original hardcoded universe — kept as fallback for first run."""
    return [
        # === S&P 500 LARGE CAPS (~200) ===
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "BRK-B", "LLY", "AVGO", "JPM",
        "TSLA", "UNH", "XOM", "V", "MA", "PG", "COST", "JNJ", "HD", "ABBV",
        "WMT", "NFLX", "BAC", "CRM", "CVX", "MRK", "KO", "ORCL", "AMD", "PEP",
        "ACN", "TMO", "LIN", "MCD", "CSCO", "ADBE", "ABT", "DHR", "WFC", "PM",
        "TXN", "QCOM", "ISRG", "GE", "INTU", "AMGN", "CAT", "MS", "NEE", "VZ",
        "IBM", "AMAT", "NOW", "PFE", "UBER", "GS", "LOW", "RTX", "UNP", "BKNG",
        "HON", "SPGI", "BLK", "T", "SYK", "SBUX", "ELV", "BA", "MDT", "PLD",
        "DE", "GILD", "ADP", "SCHW", "LRCX", "VRTX", "MMC", "CB", "BMY", "ADI",
        "REGN", "PANW", "TMUS", "CI", "SO", "DUK", "MU", "KLAC", "CME", "FI",
        "CL", "ICE", "SHW", "MCO", "SNPS", "CDNS", "ZTS", "CMG", "PNC", "USB",
        "APD", "AIG", "AJG", "ALL", "ANET", "AON", "APH", "BDX", "BK", "BSX",
        "C", "CARR", "CBRE", "CCI", "CHTR", "COF", "COP", "CPRT", "CRH", "CTAS",
        "CTVA", "D", "DD", "DLR", "DOW", "DPZ", "ECL", "ED", "EL", "EMR",
        "EOG", "EQIX", "EQR", "ETN", "EW", "FANG", "FAST", "FCX", "FICO", "GD",
        "GEV", "GLW", "GM", "GPN", "GWW", "HAL", "HCA", "HUBS", "HUM", "HWM",
        "IR", "IRM", "IT", "ITW", "JCI", "KDP", "KHC", "KMI", "KR", "KVUE",
        "LDOS", "LHX", "LMT", "LVS", "MAR", "MCK", "MDLZ", "MELI", "MET", "MLM",
        "MMM", "MNST", "MOS", "MPC", "MRVL", "MSCI", "MTB", "NDAQ", "NOC", "NUE",
        "ODFL", "OKE", "ON", "ORCL", "OTIS", "OXY", "PCAR", "PH", "PPG", "PRU",
        "PSA", "PSX", "PVH", "PWR", "RCL", "ROST", "RSG", "SLB", "SNA", "SPG",
        # === REGIONAL BANKS (high insider signal) ===
        "ZION", "KEY", "CFG", "FITB", "RF", "HBAN", "CMA", "FHN", "SNV", "WTFC",
        "UMBF", "PNFP", "IBOC", "FFIN", "CBSH", "BOKF", "FNB", "VLY", "ONB", "ABCB",
        "SFBS", "HOPE", "TCBI", "WSFS", "CADE", "HWC", "OZK", "GBCI", "BANR", "WAFD",
        "TRMK", "UBSI", "NWBI", "SBCF", "RNST", "FULT", "BHLB", "CVBF", "INDB", "PPBI",
        "AUB", "SASR", "FCNCA", "EWBC", "WAL", "PACW", "FRC", "SIVB", "SBNY", "ALLY",
        "TFC", "HBAN", "NTRS", "CFR", "COLB", "SFNC",
        # === BIOTECH / PHARMA (high insider signal) ===
        "BMRN", "EXEL", "SRPT", "ALNY", "IONS", "NBIX", "PCVX", "ARGX",
        "SGEN", "MRNA", "BNTX", "INCY", "HALO", "RARE", "FOLD", "ARVN", "KRYS",
        "PTCT", "CORT", "INSM", "MYGN", "UTHR", "MDGL", "RCKT", "RVMD", "CRNX",
        "APLS", "AXSM", "BHVN", "CPRX", "DAWN", "DYN", "GERN", "IMVT", "KRTX",
        "LGND", "LQDA", "MIRM", "NUVB", "PRTA", "RXRX", "SAVA", "SMMT", "TGTX",
        "VCEL", "VERA", "XNCR", "CYTK", "ELAN", "BEAM", "CRSP", "NTLA", "EDIT",
        "ACAD", "ALKS", "ARWR", "GBTG", "ITCI", "MNKD", "SAGE", "TARS",
        # === SMALL/MID CAP INDUSTRIALS ===
        "AIT", "AIMC", "ATKR", "AWI", "AZEK", "BMI", "CBT", "CSWI", "EAF", "EPAC",
        "ESE", "GATX", "GGG", "GMS", "HNI", "IIIN", "KAI", "KNF", "LNN", "MBC",
        "MLI", "MWA", "NDSN", "NPO", "NVT", "POWL", "RBC", "RRX", "SSD", "SXI",
        "TRS", "TTC", "WMS", "WSO", "XPEL",
        # === REITs ===
        "O", "AMT", "SPG", "WELL", "DLR", "PSA", "EQIX", "AVB", "EQR", "VTR",
        "ARE", "BXP", "CPT", "CUZ", "DEI", "DOC", "EGP", "EPR", "FR", "GTY",
        "HIW", "HR", "IRT", "KRC", "LSI", "MAC", "NNN", "OHI", "PDM", "PEAK",
        "RLJ", "ROIC", "SLG", "STAG", "SUI", "UDR", "VNO", "WPC", "COLD",
        # === ENERGY E&Ps ===
        "DVN", "PXD", "FANG", "PR", "CTRA", "OVV", "SM", "MTDR", "CHRD", "VTLE",
        "ESTE", "REPX", "GPOR", "MGY", "NOG", "PDCE", "RRC", "SWN", "AR", "EQT",
        "CNX", "CIVI", "SBOW", "HPK", "BATL", "CRGY", "EPSN",
        # === TECH MID/SMALL ===
        "PAYC", "PCTY", "JAMF", "ALTR", "CWAN", "DDOG", "NET", "CFLT", "MDB", "SNOW",
        "ZS", "CRWD", "OKTA", "S", "GTLB", "DT", "PATH", "FRSH", "BRZE", "SMAR",
        "BILL", "TOST", "SHOP", "FOUR", "RELY", "SQ", "AFRM", "UPST", "SOFI", "HOOD",
        # === CONSUMER / RETAIL ===
        "DKS", "FIVE", "GOOS", "LULU", "RH", "TJX", "BURL", "WSM", "BOOT", "CROX",
        "DECK", "SKX", "FL", "GPC", "AAP", "ORLY", "AZO", "TSCO",
        # === MISC MID/SMALL WITH INSIDER ACTIVITY ===
        "AEIS", "AMKR", "CARG", "CARS", "CENTA", "CLH", "CW", "DY", "ENS", "FLS",
        "FORM", "FSS", "HAYW", "HELE", "IOSP", "KFRC", "LBRT", "MATX", "MHO", "MTH",
        "NSIT", "PLXS", "PRIM", "SIG", "SPB", "THO", "UFPI", "VIRT", "WGO", "WOR",
        # === ADDITIONAL SMALL-CAP BANKS ===
        "SBSI", "BUSE", "TOWN", "FBNC", "CATY", "NBHC", "NBTB", "IBTX", "TBBK", "HTH",
        "BRKL", "DCOM", "BY", "SRCE", "EFSC", "BSIG", "STBA", "HOMB", "BFST", "HTLF",
        "TMP", "FFBC", "CZFS", "ORRF", "MBWM", "GABC", "CPF", "VBTX", "HTBI", "FISI",
        # === ADDITIONAL BIOTECH/PHARMA ===
        "RGNX", "AGIO", "BBIO", "CLOV", "GRFS", "IRWD", "MRTX", "MRUS", "NKTX", "OLPX",
        "PGNY", "RNA", "ROIV", "SWTX", "TVTX", "UNIT", "VRNA", "XERS",
        "AGEN", "ACLX", "ADVM", "AKRO", "ANNX", "ARQT", "AUPH", "AVXL", "BCRX", "BOLT",
        "CELC", "DCPH", "DVAX", "FATE", "FGEN", "GTHX", "HRTX", "IOVA", "ITOS", "JANX",
        "KROS", "LRMR", "LYEL", "MEIP", "MGTX", "MNMD", "NRIX", "NUVB", "OCUL", "PLRX",
        "PRTK", "RETA", "RLAY", "SNDX", "STRO", "TALK", "TECL", "TMDX", "VNDA", "XENE",
        # === ADDITIONAL INDUSTRIALS / MATERIALS ===
        "ASTE", "AVNT", "AYI", "BCC", "BLBD", "CADE", "CMCO", "CRS", "CSL", "DXC",
        "EXPO", "FBRT", "GBX", "GNTX", "GVA", "HI", "HUBG", "JBLU", "KMPR", "LAKE",
        "LGIH", "MDU", "MHK", "MOG-A", "MTOR", "MUR", "NJR", "NMIH", "NWE", "OGS",
        "ORA", "PATK", "PBH", "ROCK", "RUSHA", "SEM", "SITE", "SJW", "SR", "SWX",
        "TEX", "TREX", "TRN", "VICR", "VMI", "WCC", "WTS",
        # === ADDITIONAL MID-CAP TECH ===
        "ASAN", "AVLR", "BOX", "CERT", "CIEN", "COMM", "CRNC", "CSGP", "CXM", "DOCU",
        "ENPH", "ESTC", "EVBG", "FIVN", "GLOB", "HCP", "HLIT", "IDCC", "INTA", "KD",
        "LPSN", "MANH", "MASI", "MIDD", "NEOG", "NOVT", "NTNX", "OLED", "PI", "QLYS",
        "RAMP", "SAIL", "SWAV", "TENB", "TYL", "VRNS", "WDAY", "WK", "ZI",
        # === ADDITIONAL CONSUMER / DISCRETIONARY ===
        "BROS", "CAKE", "CHUY", "COTY", "DIN", "EAT", "ELF", "FWRG", "JACK", "LEVI",
        "MNST", "NKE", "ODP", "PLNT", "RRGB", "SAM", "SHAK", "TXRH", "WING", "YETI",
        # === ADDITIONAL ENERGY ===
        "ARIS", "AROC", "CLNE", "CPE", "DEN", "ERF", "GPRE", "HES", "KOS", "LPI",
        "MEG", "NEXT", "PARR", "REI", "TALO", "VNOM", "WPX",
    ]


def load_russell2000_additions():
    """[LEGACY] No longer needed — universe is now dynamic from DB. Returns empty list."""
    return []


def _hardcoded_russell2000_additions():
    """[LEGACY] Original hardcoded Russell 2000 additions — kept as reference."""
    return [
        # Industrials
        "ACHR", "AIRS", "ALIT", "AMPH", "APOG", "ARCB", "ASTE", "ATSG", "AVAV", "AXL",
        "BWXT", "CACI", "CALX", "CDRE", "CENX", "CHX", "CMPR", "COHU", "CPA", "CRTO",
        "CXT", "DAN", "DNOW", "DXPE", "ECPG", "EPC", "EVTC", "FBRT", "FBP", "FHI",
        "FRHC", "GBX", "GEF", "GIC", "GNW", "HCC", "HEES", "HGV", "HLNE", "HQY",
        "HUBG", "IBKR", "IDYA", "IFS", "IMKTA", "INVA", "IRTC", "ITGR", "JBT", "JBSS",
        # Healthcare / Biotech small caps
        "ACHC", "ADUS", "AMEH", "AMN", "ANIP", "ATEC", "ATRA", "AVNS", "BHC", "BIO",
        "BLFS", "BRKR", "CARA", "CCRN", "CERT", "CHE", "CLOV", "CNMD", "CORT", "CRL",
        "CRVL", "CSTL", "CYH", "DXCM", "EBS", "ECPG", "ENSG", "EHC", "FTRE", "GKOS",
        "HAE", "HCAT", "HRMY", "HUM", "ICUI", "INSP", "ISEE", "KIDS", "KNSA", "LNTH",
        "MASI", "MDXG", "MGPI", "MMS", "MOH", "NARI", "NEO", "NEOG", "NVAX", "NVC",
        # Technology small caps
        "AAON", "AGYS", "ALRM", "ANET", "APPF", "APPS", "ASGN", "AVNW", "BL", "BLKB",
        "BOOT", "CACI", "CALX", "CASA", "CCCS", "CCOI", "CG", "CHDN", "CIEN", "CLSK",
        "COOP", "CRAI", "CRK", "CRUS", "CSGS", "CSWI", "CVLT", "CWST", "DBRG", "DCO",
        "DIOD", "DOCN", "DV", "EEFT", "EGAN", "ENV", "EPAM", "ESMT", "EVTC", "EXLS",
        "FFIV", "FIZZ", "FN", "FOCS", "FTNT", "GDYN", "GH", "GIII", "GSHD", "GTLS",
        # Financials small caps
        "ABCB", "AHH", "AKR", "APAM", "ARI", "ASB", "BANF", "BCPC", "BHF", "BHLB",
        "BPOP", "BRSP", "BSIG", "CADE", "CARG", "CASH", "CBRL", "CCB", "CFFN", "CHCO",
        "CIVI", "CLBK", "CNO", "CNOB", "COLB", "COWN", "CPK", "CSGS", "CUBI", "CVBF",
        "CWBC", "DCOM", "EGBN", "ENVA", "ESGR", "ESSA", "EVBN", "FBMS", "FBNC", "FCFS",
        "FELE", "FIBK", "FINV", "FNF", "FRME", "FUNC", "GABC", "GNTY", "GSBC", "GWB",
        # Consumer small caps
        "AAP", "AKAM", "AMED", "ARCO", "AZZ", "BBSI", "BDC", "BGS", "BJRI", "BKE",
        "BLMN", "BRBR", "BRY", "BWFG", "CAKE", "CASY", "CBRL", "CHEF", "CIVI", "CNNE",
        "COLM", "COOK", "CRC", "CRVL", "CSR", "CWST", "DENN", "DIN", "DLTH", "DLX",
        "EAT", "EBF", "ETSY", "EYE", "FANG", "FDP", "FLGT", "FRPT", "FWRD", "GDEN",
        "GEF", "GHC", "GOLF", "GPRE", "GVA", "HAIN", "HBI", "HLF", "HTLD", "HTZ",
        # Energy small caps
        "AM", "AMPY", "ARCH", "AROC", "ASC", "BCEI", "BHVN", "BKR", "BORR", "BPMC",
        "BROG", "BRY", "BSM", "BTMD", "CALF", "CHAP", "CHK", "CLMT", "CLR", "CNTG",
        "COP", "CPG", "CPK", "CRGY", "CRK", "CTRA", "CVI", "CVE", "DK", "DMLP",
        "DNOW", "DO", "DRQ", "EGHT", "EGY", "EPSN", "ERF", "ET", "FLNG", "FTI",
        # Materials
        "ATKR", "ATI", "AVY", "AXTA", "BALL", "CC", "CF", "CLF", "CMC", "CMP",
        "CRS", "CSTM", "DINO", "EMN", "FMC", "GATO", "GCP", "GPRK", "GPK", "GRA",
        "HBM", "HCC", "HWKN", "IAG", "IPI", "KGC", "KNF", "KOP", "LPX", "MP",
        "MTX", "NEM", "NGVT", "OEC", "OLN", "OR", "PBF", "PCT", "PKG", "PPG",
        # REITs small caps
        "ADC", "AFCG", "AGNCN", "AHH", "AIV", "AKR", "ALX", "APLE", "BDN", "BFS",
        "BNL", "BRSP", "BRT", "CIO", "CLPR", "CMCT", "CSR", "CTO", "DEA", "DHC",
        "DRH", "ELME", "ESRT", "FPI", "FSP", "GNL", "GOOD", "GPT", "IIPR", "INN",
        # Utilities / Misc
        "AES", "ALE", "AMPS", "AQN", "AVA", "BEP", "BIP", "BKH", "CWEN", "EBR",
        "EVRG", "FE", "GPRE", "IDA", "MDU", "NRG", "OGE", "OGS", "PNM", "PNW",
    ]


def get_db():
    return sqlite3.connect(DB_PATH)


def ensure_company(conn, ticker, cik, name=None):
    """Insert company if not exists, return company_id."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM companies WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE companies SET cik = ? WHERE id = ?", (cik, row[0]))
        return row[0]
    cur.execute("INSERT INTO companies (ticker, cik, name) VALUES (?, ?, ?)",
                (ticker, cik, name or ticker))
    conn.commit()
    return cur.lastrowid


def ticker_already_ingested(ticker):
    """Check if ticker already has insider transactions in DB."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE c.ticker = ?
    """, (ticker,))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0


def get_latest_filing_date(ticker):
    """Get the most recent filing_date for a ticker in the DB."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(it.filing_date) FROM insider_transactions it
        JOIN companies c ON it.company_id = c.id
        WHERE c.ticker = ?
    """, (ticker,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def ingest_incremental(ticker, ticker_map=None):
    """Fetch only NEW Form 4 filings since the last ingestion date for this ticker.
    Returns count of newly inserted transactions."""
    if ticker_map is None:
        ticker_map = fetch_company_tickers()

    cik = ticker_map.get(ticker)
    if not cik:
        logger.warning(f"No CIK found for {ticker}")
        return 0

    latest_date = get_latest_filing_date(ticker)
    if not latest_date:
        # No data yet — do full ingest for this ticker
        return ingest_insider_trades(ticker, ticker_map)

    conn = get_db()
    company_id = ensure_company(conn, ticker, cik)

    # Fetch filings only since the day after the latest filing date
    filings = fetch_form4_filings(cik, limit=None, since_date=latest_date)
    # Filter to only truly new filings (strictly after latest_date)
    filings = [f for f in filings if f["filing_date"] > latest_date]

    inserted = 0
    for filing in filings:
        transactions = parse_form4_xml(
            filing["cik"],
            filing["accession_number"],
            filing["primary_doc"]
        )
        for txn in transactions:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO insider_transactions
                    (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
                     transaction_type, shares_transacted, price, shares_owned_after, source, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDGAR', ?)
                """, (
                    company_id,
                    filing["filing_date"],
                    txn["transaction_date"],
                    txn["insider_name"],
                    txn["insider_cik"],
                    txn["transaction_code"],
                    txn["shares"],
                    txn["price"],
                    txn["shares_owned_after"],
                    json.dumps(txn),
                ))
                inserted += 1
            except Exception as e:
                logger.warning(f"Error inserting transaction for {ticker}: {e}")

    conn.commit()
    conn.close()
    if inserted > 0:
        logger.info(f"{ticker}: Incrementally inserted {inserted} new transactions")
    return inserted


def populate_sector_yfinance(ticker):
    """Use yfinance to populate sector/market_cap for a ticker if missing."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT sector FROM companies WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    if row and row[0] and row[0] not in ('', 'Unknown'):
        conn.close()
        return  # already has sector
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        sector = info.get('sector', 'Unknown')
        market_cap = info.get('marketCap', None)
        cur.execute("UPDATE companies SET sector=?, market_cap=? WHERE ticker=?",
                    (sector, market_cap, ticker))
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to get sector for {ticker} via yfinance: {e}")
    conn.close()


def ingest_insider_trades(ticker, ticker_map=None):
    """Resolve ticker→CIK, fetch Form 4s, parse, store."""
    if ticker_map is None:
        ticker_map = fetch_company_tickers()
    
    cik = ticker_map.get(ticker)
    if not cik:
        logger.warning(f"No CIK found for {ticker}")
        return 0
    
    conn = get_db()
    company_id = ensure_company(conn, ticker, cik)
    
    filings = fetch_form4_filings(cik)
    inserted = 0
    
    for fi, filing in enumerate(filings):
        if fi % 50 == 0 and fi > 0:
            logger.info(f"  {ticker}: parsed {fi}/{len(filings)} filings, {inserted} inserted so far")
        
        transactions = parse_form4_xml(
            filing["cik"],
            filing["accession_number"],
            filing["primary_doc"]
        )
        for txn in transactions:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO insider_transactions
                    (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
                     transaction_type, shares_transacted, price, shares_owned_after, source, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDGAR', ?)
                """, (
                    company_id,
                    filing["filing_date"],
                    txn["transaction_date"],
                    txn["insider_name"],
                    txn["insider_cik"],
                    txn["transaction_code"],
                    txn["shares"],
                    txn["price"],
                    txn["shares_owned_after"],
                    json.dumps(txn),
                ))
                inserted += 1
            except Exception as e:
                logger.warning(f"Error inserting transaction for {ticker}: {e}")
    
    conn.commit()
    conn.close()
    logger.info(f"{ticker}: Inserted {inserted} insider transactions")
    return inserted


def ingest_shares_outstanding(ticker, ticker_map=None):
    """Fetch shares outstanding from EDGAR XBRL and store."""
    if ticker_map is None:
        ticker_map = fetch_company_tickers()
    
    cik = ticker_map.get(ticker)
    if not cik:
        logger.warning(f"No CIK found for {ticker}")
        return 0
    
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik_padded}/dei/EntityCommonStockSharesOutstanding.json"
    
    try:
        resp = _get(url)
        data = resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch shares outstanding for {ticker}: {e}")
        return 0
    
    conn = get_db()
    company_id = ensure_company(conn, ticker, cik)
    
    inserted = 0
    units = data.get("units", {})
    for unit_key, entries in units.items():
        for entry in entries:
            date = entry.get("end") or entry.get("filed")
            val = entry.get("val")
            if date and val:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO shares_outstanding
                        (company_id, date, shares, source)
                        VALUES (?, ?, ?, 'EDGAR_XBRL')
                    """, (company_id, date, float(val)))
                    inserted += 1
                except Exception:
                    pass
    
    conn.commit()
    conn.close()
    logger.info(f"{ticker}: Inserted {inserted} shares outstanding records")
    return inserted


def run_full_ingestion(tickers=None, skip_existing=True):
    """Run ingestion for all tickers."""
    if tickers is None:
        tickers = load_universe()
    
    # Deduplicate
    seen = set()
    unique_tickers = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)
    tickers = unique_tickers
    
    logger.info(f"Starting ingestion for {len(tickers)} tickers (skip_existing={skip_existing})")
    ticker_map = fetch_company_tickers()
    if not ticker_map:
        logger.error("Failed to fetch ticker map")
        return
    
    total_txns = 0
    total_shares = 0
    errors = []
    skipped = 0
    
    for i, ticker in enumerate(tickers):
        if (i + 1) % 50 == 0 or i == 0:
            logger.info(f"=== Progress: {i+1}/{len(tickers)} tickers (skipped {skipped}, errors {len(errors)}) ===")
        
        if skip_existing and ticker_already_ingested(ticker):
            skipped += 1
            continue
        
        try:
            txns = ingest_insider_trades(ticker, ticker_map)
            total_txns += txns
        except Exception as e:
            errors.append(f"{ticker} (trades): {e}")
            logger.error(f"Error ingesting trades for {ticker}: {e}")
        
        try:
            shares = ingest_shares_outstanding(ticker, ticker_map)
            total_shares += shares
        except Exception as e:
            errors.append(f"{ticker} (shares): {e}")
            logger.error(f"Error ingesting shares for {ticker}: {e}")
    
    print(f"\n{'='*50}")
    print(f"INGESTION COMPLETE")
    print(f"{'='*50}")
    print(f"Tickers in universe: {len(tickers)}")
    print(f"Skipped (already in DB): {skipped}")
    print(f"Newly ingested: {len(tickers) - skipped}")
    print(f"Insider transactions inserted: {total_txns}")
    print(f"Shares outstanding records: {total_shares}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors[:20]:
            print(f"  - {e}")
        if len(errors) > 20:
            print(f"  ... and {len(errors)-20} more")
    else:
        print("No errors!")
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    print(f"\nTotal insider_transactions in DB: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM shares_outstanding")
    print(f"Total shares_outstanding in DB: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM companies")
    print(f"Total companies in DB: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    run_full_ingestion()
