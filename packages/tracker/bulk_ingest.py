#!/usr/bin/env python3
"""
Bulk ingest insider transactions from SEC quarterly data sets.
Downloads pre-parsed TSV files — no individual XML parsing needed.
"""
import os
import sys
import time
import sqlite3
import zipfile
import requests
import pandas as pd
import logging
from io import BytesIO

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_ingestion.data_loader import load_universe, get_db, ensure_company

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "insider_signals.db")
BULK_DIR = os.path.join(os.path.dirname(__file__), "bulk_data")
HEADERS = {"User-Agent": "InsiderTracker Research contact@openclaw.ai"}

# All quarterly files from 2020 Q1 to 2025 Q4
QUARTERS = []
for year in range(2020, 2027):
    for q in range(1, 5):
        if year == 2026 and q > 1:
            break
        QUARTERS.append(f"{year}q{q}")


def download_and_extract(quarter):
    """Download and extract a quarterly data set."""
    url = f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{quarter}_form345.zip"
    zip_path = os.path.join(BULK_DIR, f"{quarter}_form345.zip")
    extract_dir = os.path.join(BULK_DIR, quarter)
    
    if os.path.exists(os.path.join(extract_dir, "NONDERIV_TRANS.tsv")):
        logger.info(f"{quarter}: Already extracted")
        return extract_dir
    
    if not os.path.exists(zip_path):
        logger.info(f"Downloading {quarter}...")
        time.sleep(0.15)  # rate limit
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 404:
                logger.warning(f"{quarter}: Not available (404)")
                return None
            resp.raise_for_status()
            with open(zip_path, 'wb') as f:
                f.write(resp.content)
        except Exception as e:
            logger.warning(f"Failed to download {quarter}: {e}")
            return None
    
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in ['NONDERIV_TRANS.tsv', 'SUBMISSION.tsv', 'REPORTINGOWNER.tsv']:
                zf.extract(name, extract_dir)
    except Exception as e:
        logger.warning(f"Failed to extract {quarter}: {e}")
        return None
    
    return extract_dir


def load_quarter(extract_dir):
    """Load and join the 3 TSVs for a quarter."""
    try:
        trans = pd.read_csv(os.path.join(extract_dir, "NONDERIV_TRANS.tsv"), 
                           sep='\t', low_memory=False, dtype=str)
        sub = pd.read_csv(os.path.join(extract_dir, "SUBMISSION.tsv"), 
                         sep='\t', low_memory=False, dtype=str)
        owner = pd.read_csv(os.path.join(extract_dir, "REPORTINGOWNER.tsv"), 
                           sep='\t', low_memory=False, dtype=str)
    except Exception as e:
        logger.warning(f"Failed to load TSVs from {extract_dir}: {e}")
        return None
    
    # Join: trans -> sub (for ticker, filing date, issuer) and trans -> owner (for insider name/cik)
    merged = trans.merge(sub[['ACCESSION_NUMBER', 'FILING_DATE', 'ISSUERTRADINGSYMBOL', 
                              'ISSUERCIK', 'ISSUERNAME', 'DOCUMENT_TYPE']], 
                        on='ACCESSION_NUMBER', how='left')
    merged = merged.merge(owner[['ACCESSION_NUMBER', 'RPTOWNERCIK', 'RPTOWNERNAME', 
                                 'RPTOWNER_RELATIONSHIP', 'RPTOWNER_TITLE']], 
                         on='ACCESSION_NUMBER', how='left')
    
    # Filter to Form 4 only
    merged = merged[merged['DOCUMENT_TYPE'] == '4']
    
    return merged


def parse_date(date_str):
    """Parse SEC date format (DD-MMM-YYYY) to YYYY-MM-DD."""
    if pd.isna(date_str) or not date_str:
        return None
    try:
        from datetime import datetime
        dt = datetime.strptime(str(date_str).strip(), "%d-%b-%Y")
        return dt.strftime("%Y-%m-%d")
    except:
        return None


def main():
    start = time.time()
    os.makedirs(BULK_DIR, exist_ok=True)
    
    # Full SEC EDGAR universe — no ticker filtering
    logger.info("Ingesting ALL SEC EDGAR tickers (full universe)")
    
    # Clear existing transactions
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    old = cur.fetchone()[0]
    logger.info(f"Clearing {old} existing transactions...")
    cur.execute("DELETE FROM insider_transactions")
    conn.commit()
    
    # Build CIK -> ticker map from SEC company_tickers.json
    from data_ingestion.edgar_client import fetch_company_tickers
    ticker_map = fetch_company_tickers()  # ticker -> cik
    # Build reverse map: cik -> ticker
    cik_to_ticker = {}
    for t, c in ticker_map.items():
        cik_to_ticker[str(c)] = t
    logger.info(f"Loaded {len(ticker_map)} ticker mappings from SEC")
    
    # Cache for company_id lookups
    ticker_to_id = {}
    
    def get_company_id(ticker_str, cik_str, name_str=None):
        """Get or create company, with caching."""
        key = ticker_str.upper() if ticker_str else f"CIK{cik_str}"
        if key in ticker_to_id:
            return ticker_to_id[key]
        cik_int = int(cik_str.lstrip('0')) if cik_str and cik_str.strip().lstrip('0') else None
        company_id = ensure_company(conn, ticker_str or key, cik_int, name_str)
        ticker_to_id[key] = company_id
        return company_id
    
    # Download and process each quarter
    total_inserted = 0
    for quarter in QUARTERS:
        extract_dir = download_and_extract(quarter)
        if not extract_dir:
            continue
        
        df = load_quarter(extract_dir)
        if df is None or len(df) == 0:
            continue
        
        # Clean ticker column
        df['ticker_upper'] = df['ISSUERTRADINGSYMBOL'].str.upper().str.strip()
        # Drop rows with no ticker
        df = df[df['ticker_upper'].notna() & (df['ticker_upper'] != '') & (df['ticker_upper'] != 'NAN')]
        
        if len(df) == 0:
            logger.info(f"{quarter}: No transactions with valid tickers")
            continue
        
        # Parse and insert
        inserted = 0
        for _, row in df.iterrows():
            ticker = row['ticker_upper']
            cik_str = str(row.get('ISSUERCIK', '')).strip()
            name = row.get('ISSUERNAME', '')
            company_id = get_company_id(ticker, cik_str, name)
            
            filing_date = parse_date(row.get('FILING_DATE'))
            trans_date = parse_date(row.get('TRANS_DATE'))
            trans_code = row.get('TRANS_CODE', '')
            
            try:
                shares = float(row['TRANS_SHARES']) if pd.notna(row.get('TRANS_SHARES')) else None
                price = float(row['TRANS_PRICEPERSHARE']) if pd.notna(row.get('TRANS_PRICEPERSHARE')) else None
                shares_after = float(row['SHRS_OWND_FOLWNG_TRANS']) if pd.notna(row.get('SHRS_OWND_FOLWNG_TRANS')) else None
            except (ValueError, TypeError):
                shares = price = shares_after = None
            
            raw_json = json.dumps({
                'relationship': row.get('RPTOWNER_RELATIONSHIP', ''),
                'title': row.get('RPTOWNER_TITLE', ''),
                'acq_disp': row.get('TRANS_ACQUIRED_DISP_CD', ''),
            })
            
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO insider_transactions
                    (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
                     transaction_type, shares_transacted, price, shares_owned_after, source, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SEC_BULK', ?)
                """, (
                    company_id, filing_date, trans_date,
                    row.get('RPTOWNERNAME', ''), row.get('RPTOWNERCIK', ''),
                    trans_code, shares, price, shares_after, raw_json,
                ))
                inserted += 1
            except Exception as e:
                pass
        
        conn.commit()
        total_inserted += inserted
        logger.info(f"{quarter}: Inserted {inserted} transactions ({len(df)} matched universe)")
    
    elapsed = time.time() - start
    
    # Stats
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT company_id) FROM insider_transactions")
    companies = cur.fetchone()[0]
    cur.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM insider_transactions")
    date_range = cur.fetchone()
    
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"BULK INGESTION COMPLETE")
    print(f"{'='*50}")
    print(f"Transactions inserted: {total_inserted}")
    print(f"Total in DB: {total}")
    print(f"Companies with data: {companies}")
    print(f"Date range: {date_range[0]} to {date_range[1]}")
    print(f"Time: {elapsed:.1f} seconds")


if __name__ == "__main__":
    import json
    main()
