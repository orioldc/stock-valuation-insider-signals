#!/usr/bin/env python3
"""
Fast async ingestion of Form 4 data from SEC EDGAR.
Uses aiohttp with rate limiting (10 req/s) for much faster ingestion.
"""
import asyncio
import aiohttp
import sqlite3
import json
import time
import sys
import os
import xml.etree.ElementTree as ET
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_ingestion.data_loader import load_universe, get_db, ensure_company

HEADERS = {"User-Agent": "InsiderTracker Research contact@openclaw.ai"}
RATE_LIMIT = 9  # requests per second (SEC allows 10, stay under)
MAX_FILINGS_PER_TICKER = 150
SINCE_DATE = "2020-01-01"

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "insider_signals.db")


class RateLimiter:
    """Token bucket rate limiter allowing bursts up to rate_per_second."""
    def __init__(self, rate_per_second):
        self.rate = rate_per_second
        self.tokens = rate_per_second
        self.max_tokens = rate_per_second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
                self._last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
            await asyncio.sleep(0.05)


class AsyncEdgarClient:
    def __init__(self, session, rate_limiter):
        self.session = session
        self.rl = rate_limiter
        self.request_count = 0

    async def get(self, url):
        await self.rl.acquire()
        self.request_count += 1
        async with self.session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 429:
                logger.warning(f"Rate limited on {url}, sleeping 2s")
                await asyncio.sleep(2)
                return await self.get(url)
            resp.raise_for_status()
            return await resp.text()

    async def get_json(self, url):
        text = await self.get(url)
        return json.loads(text)

    async def fetch_form4_filings(self, cik):
        """Fetch Form 4 filing metadata for a CIK."""
        cik_padded = str(cik).zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        try:
            data = await self.get_json(url)
        except Exception as e:
            logger.debug(f"Failed submissions for CIK {cik}: {e}")
            return []

        def extract(filing_data):
            forms = filing_data.get("form", [])
            dates = filing_data.get("filingDate", [])
            accessions = filing_data.get("accessionNumber", [])
            primary_docs = filing_data.get("primaryDocument", [])
            hits = []
            for i, form in enumerate(forms):
                if form == "4" and dates[i] >= SINCE_DATE:
                    hits.append({
                        "filing_date": dates[i],
                        "accession_number": accessions[i],
                        "primary_doc": primary_docs[i],
                        "cik": str(cik),
                    })
            return hits

        filings_obj = data.get("filings", {})
        results = extract(filings_obj.get("recent", {}))

        # Fetch older files if needed
        if len(results) < MAX_FILINGS_PER_TICKER:
            for file_info in filings_obj.get("files", []):
                fname = file_info.get("name", "")
                if not fname:
                    continue
                try:
                    older_data = await self.get_json(f"https://data.sec.gov/submissions/{fname}")
                    older = extract(older_data)
                    if not older:
                        break
                    results.extend(older)
                    if len(results) >= MAX_FILINGS_PER_TICKER:
                        break
                except Exception:
                    continue

        return results[:MAX_FILINGS_PER_TICKER]

    async def parse_form4_xml(self, cik, accession_number, primary_doc):
        """Fetch and parse a Form 4 XML."""
        acc_no_dashes = accession_number.replace("-", "")
        xml_filename = primary_doc.split("/")[-1] if "/" in primary_doc else primary_doc
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{xml_filename}"

        try:
            content = await self.get(url)
        except Exception as e:
            return []

        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []

        def strip_ns(tag):
            return tag.split("}", 1)[1] if "}" in tag else tag

        def find(el, path):
            parts = path.split(".")
            current = el
            for part in parts:
                found = None
                for child in current:
                    if strip_ns(child.tag) == part:
                        found = child
                        break
                if found is None:
                    return None
                current = found
            return current

        def find_text(el, path, default=None):
            node = find(el, path)
            return node.text.strip() if node is not None and node.text else default

        def find_all_recursive(el, tag):
            return [child for child in el.iter() if strip_ns(child.tag) == tag]

        owners = find_all_recursive(root, "reportingOwner")
        owner_name = owner_cik = ""
        relationship = "Unknown"

        if owners:
            owner = owners[0]
            owner_name = find_text(owner, "reportingOwnerId.rptOwnerName", "")
            owner_cik = find_text(owner, "reportingOwnerId.rptOwnerCik", "")
            rel = find(owner, "reportingOwnerRelationship")
            if rel is not None:
                roles = []
                is_officer = find_text(rel, "isOfficer", "0") in ("1", "true")
                is_director = find_text(rel, "isDirector", "0") in ("1", "true")
                is_ten = find_text(rel, "isTenPercentOwner", "0") in ("1", "true")
                title = find_text(rel, "officerTitle", "")
                if is_officer:
                    roles.append(f"Officer ({title})" if title else "Officer")
                if is_director:
                    roles.append("Director")
                if is_ten:
                    roles.append("10% Owner")
                if roles:
                    relationship = ", ".join(roles)

        transactions = find_all_recursive(root, "nonDerivativeTransaction")
        results = []

        for txn in transactions:
            try:
                txn_date = find_text(txn, "transactionDate.value", "")
                txn_code = find_text(txn, "transactionCoding.transactionCode", "")
                amounts = find(txn, "transactionAmounts")
                shares = price = None
                if amounts:
                    s = find_text(amounts, "transactionShares.value")
                    p = find_text(amounts, "transactionPricePerShare.value")
                    shares = float(s) if s else None
                    price = float(p) if p else None
                post = find(txn, "postTransactionAmounts")
                shares_after = None
                if post:
                    sa = find_text(post, "sharesOwnedFollowingTransaction.value")
                    shares_after = float(sa) if sa else None

                results.append({
                    "insider_name": owner_name,
                    "insider_cik": owner_cik,
                    "relationship": relationship,
                    "transaction_code": txn_code,
                    "transaction_date": txn_date,
                    "shares": shares,
                    "price": price,
                    "total_value": (shares * price) if shares and price else None,
                    "shares_owned_after": shares_after,
                })
            except Exception:
                continue

        return results


async def ingest_ticker(client, ticker, cik, db_conn):
    """Ingest all Form 4 data for a single ticker."""
    company_id = ensure_company(db_conn, ticker, cik)
    
    filings = await client.fetch_form4_filings(cik)
    if not filings:
        return 0

    inserted = 0
    # Process filings concurrently in batches
    sem = asyncio.Semaphore(5)  # limit concurrent XML parses
    
    async def process_filing(filing):
        nonlocal inserted
        async with sem:
            txns = await client.parse_form4_xml(
                filing["cik"], filing["accession_number"], filing["primary_doc"]
            )
            for txn in txns:
                try:
                    db_conn.execute("""
                        INSERT OR IGNORE INTO insider_transactions
                        (company_id, filing_date, transaction_date, reporting_name, reporting_cik,
                         transaction_type, shares_transacted, price, shares_owned_after, source, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDGAR', ?)
                    """, (
                        company_id, filing["filing_date"], txn["transaction_date"],
                        txn["insider_name"], txn["insider_cik"], txn["transaction_code"],
                        txn["shares"], txn["price"], txn["shares_owned_after"],
                        json.dumps(txn),
                    ))
                    inserted += 1
                except Exception:
                    pass

    await asyncio.gather(*[process_filing(f) for f in filings])
    db_conn.commit()
    return inserted


async def main():
    start = time.time()
    
    # Clear existing data
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    old_count = cur.fetchone()[0]
    print(f"Clearing {old_count} existing transactions...")
    cur.execute("DELETE FROM insider_transactions")
    conn.commit()
    
    tickers = load_universe()
    seen = set()
    tickers = [t for t in tickers if t not in seen and not seen.add(t)]
    print(f"Ingesting {len(tickers)} tickers with up to {MAX_FILINGS_PER_TICKER} filings each...")
    
    # Fetch ticker->CIK map
    rl = RateLimiter(RATE_LIMIT)
    async with aiohttp.ClientSession() as session:
        client = AsyncEdgarClient(session, rl)
        
        # Get CIK map
        text = await client.get("https://www.sec.gov/files/company_tickers.json")
        ticker_data = json.loads(text)
        ticker_map = {v['ticker']: v['cik_str'] for v in ticker_data.values()}
        print(f"Got {len(ticker_map)} ticker->CIK mappings")
        
        # Process tickers sequentially to avoid DB contention
        # but filings within each ticker are concurrent
        total_txns = 0
        errors = 0
        
        for i, ticker in enumerate(tickers):
            if (i + 1) % 25 == 0 or i == 0:
                elapsed = time.time() - start
                rate = (i + 1) / elapsed * 60 if elapsed > 0 else 0
                print(f"[{i+1}/{len(tickers)}] {rate:.1f} tickers/min | "
                      f"{total_txns} txns | {client.request_count} HTTP reqs | "
                      f"{elapsed/60:.1f} min elapsed")
            
            cik = ticker_map.get(ticker)
            if not cik:
                errors += 1
                continue
            
            try:
                count = await ingest_ticker(client, ticker, cik, conn)
                total_txns += count
            except Exception as e:
                errors += 1
                logger.warning(f"Error on {ticker}: {e}")
    
    elapsed = time.time() - start
    print(f"\n{'='*50}")
    print(f"INGESTION COMPLETE")
    print(f"{'='*50}")
    print(f"Tickers: {len(tickers)}")
    print(f"Transactions inserted: {total_txns}")
    print(f"Errors: {errors}")
    print(f"HTTP requests: {client.request_count}")
    print(f"Time: {elapsed/60:.1f} minutes")
    
    # Verify
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM insider_transactions")
    print(f"Total in DB: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
