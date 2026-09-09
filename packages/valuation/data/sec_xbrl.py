"""SEC EDGAR XBRL CompanyFacts — authoritative financial data directly from filings.

Uses data.sec.gov/api/xbrl/companyfacts endpoint.
One API call per company returns every XBRL concept ever filed (10-K, 10-Q).
No API key needed. Requires SEC-compliant User-Agent.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "xbrl"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_DAYS = 7

SEC_UA = "InsiderSignalTracker admin@fuertesito.dev"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
XBRL_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Path to the tracker database (for CIK lookup when ticker not in SEC map)
DB_PATH = Path(__file__).resolve().parents[2] / "tracker" / "db" / "insider_signals.db"

# ── CIK Resolution ──────────────────────────────────────────────────────────

_ticker_to_cik: dict[str, str] | None = None


def _load_ticker_map() -> dict[str, str]:
    global _ticker_to_cik
    if _ticker_to_cik is not None:
        return _ticker_to_cik

    cache_file = CACHE_DIR / "ticker_cik_map.json"
    if cache_file.exists():
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        if age_days < 30:
            _ticker_to_cik = json.loads(cache_file.read_text())
            return _ticker_to_cik

    try:
        r = requests.get(TICKER_MAP_URL, headers={"User-Agent": SEC_UA}, timeout=15)
        r.raise_for_status()
        data = r.json()
        _ticker_to_cik = {v["ticker"]: str(v["cik_str"]) for v in data.values()}
        cache_file.write_text(json.dumps(_ticker_to_cik))
        return _ticker_to_cik
    except Exception as e:
        print(f"[sec_xbrl] Failed to load ticker map: {e}")
        _ticker_to_cik = {}
        return _ticker_to_cik


def ticker_to_cik(ticker: str) -> str | None:
    """Resolve ticker to CIK, trying SEC map first then database lookup.

    For tickers not in SEC's canonical map (e.g., stale ticker symbols like FI
    for Fiserv which is now FISV), falls back to the tracker database CIK.
    """
    # Try SEC map first (canonical tickers)
    m = _load_ticker_map()
    cik = m.get(ticker.upper())
    if cik:
        return cik

    # Fall back to database lookup (handles stale tickers)
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT cik FROM companies WHERE ticker = ? AND cik IS NOT NULL", (ticker.upper(),))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception as e:
        print(f"[sec_xbrl] Database CIK lookup failed for {ticker}: {e}")

    return None


# ── XBRL Fetch ──────────────────────────────────────────────────────────────

def _fetch_company_facts(cik: str, use_cache: bool = True) -> dict | None:
    """Fetch full XBRL companyfacts for a CIK. Returns parsed JSON or None."""
    cache_file = CACHE_DIR / f"CIK{cik.zfill(10)}.json"

    if use_cache and cache_file.exists():
        age_days = (time.time() - cache_file.stat().st_mtime) / 86400
        if age_days < CACHE_TTL_DAYS:
            return json.loads(cache_file.read_text())

    url = XBRL_URL.format(cik=cik.zfill(10))
    try:
        r = requests.get(url, headers={"User-Agent": SEC_UA}, timeout=20)
        if r.status_code == 404:
            print(f"[sec_xbrl] No XBRL data for CIK {cik}")
            return None
        r.raise_for_status()
        data = r.json()
        cache_file.write_text(json.dumps(data))
        return data
    except Exception as e:
        print(f"[sec_xbrl] Failed to fetch XBRL for CIK {cik}: {e}")
        return None


# ── Value Extraction Helpers ─────────────────────────────────────────────────

def _get_concept_values(facts: dict, concept: str, unit: str = "USD", taxonomy: str = "us-gaap") -> list[dict]:
    """Get all values for a XBRL concept, sorted by period end date descending.

    Args:
        facts: CompanyFacts JSON from SEC API
        concept: XBRL concept name (e.g. "StockholdersEquity", "EntityCommonStockSharesOutstanding")
        unit: Unit type (e.g. "USD", "shares")
        taxonomy: Taxonomy namespace (e.g. "us-gaap", "dei")
    """
    taxonomy_data = facts.get("facts", {}).get(taxonomy, {})
    if concept not in taxonomy_data:
        return []
    units = taxonomy_data[concept].get("units", {})
    values = units.get(unit, [])
    # Filter to 10-K and 10-Q only, sort by end date descending
    values = [v for v in values if v.get("form") in ("10-K", "10-Q")]
    values.sort(key=lambda v: v.get("end", ""), reverse=True)
    return values


def _latest_annual(facts: dict, concept: str, unit: str = "USD", taxonomy: str = "us-gaap") -> tuple[float | None, str]:
    """Get the most recent 10-K value for a concept.

    Returns (value, end_date) tuple. end_date is used by _try_concepts to
    pick the concept with the most recent data when a company switched
    XBRL concept names (e.g. Revenues → RevenueFromContract...).
    """
    values = _get_concept_values(facts, concept, unit, taxonomy)
    for v in values:
        if v.get("form") == "10-K":
            # Annual = full-year (start to end is ~12 months, or no start)
            start = v.get("start")
            end = v.get("end", "")
            if start and end:
                days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
                if days > 300:  # ~full year
                    return float(v["val"]), end
            elif not start:
                # Balance sheet item (point-in-time)
                return float(v["val"]), end
    # Fallback: just return latest 10-K
    for v in values:
        if v.get("form") == "10-K":
            return float(v["val"]), v.get("end", "")
    return None, ""


def _annual_series(facts: dict, concept: str, n_years: int = 5, unit: str = "USD") -> list[float]:
    """Get last N years of annual values for a concept (most recent first)."""
    values = _get_concept_values(facts, concept, unit)
    annuals = []
    seen_years = set()
    for v in values:
        if v.get("form") != "10-K":
            continue
        start = v.get("start")
        end = v.get("end")
        year = end[:4] if end else None
        if not year or year in seen_years:
            continue
        # For income/flow items, ensure it's a full-year value
        if start:
            days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
            if days < 300:
                continue
        seen_years.add(year)
        annuals.append(float(v["val"]))
        if len(annuals) >= n_years:
            break
    return annuals


def _latest_value(facts: dict, concept: str, unit: str = "USD", taxonomy: str = "us-gaap") -> tuple[float | None, str]:
    """Get the most recent value (10-K or 10-Q) for a concept.

    Returns (value, end_date) tuple.
    """
    values = _get_concept_values(facts, concept, unit, taxonomy)
    if values:
        return float(values[0]["val"]), values[0].get("end", "")
    return None, ""


# ── Main API ─────────────────────────────────────────────────────────────────

def get_xbrl_financials(ticker: str, use_cache: bool = True) -> dict | None:
    """
    Fetch structured financial data from SEC XBRL for a ticker.
    Returns a dict compatible with the valuation agent's financials format,
    or None if data is unavailable.

    Works for ALL company types but especially good for financials/banks
    where yfinance EBIT is unreliable.
    """
    cik = ticker_to_cik(ticker)
    if not cik:
        print(f"[sec_xbrl] No CIK found for {ticker}")
        return None

    facts = _fetch_company_facts(cik, use_cache=use_cache)
    if not facts:
        return None

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return None

    # ── Extract values using multiple concept name variants ──

    def _try_concepts(concepts, annual=True, unit="USD"):
        # Pick the concept with the MOST RECENT data — companies often switch
        # XBRL concept names (e.g. Revenues → RevenueFromContract...) and
        # old concepts retain stale historical values.
        best_val = None
        best_date = ""
        for c in concepts:
            val, date = _latest_annual(facts, c, unit) if annual else _latest_value(facts, c, unit)
            if val is not None and date > best_date:
                best_val = val
                best_date = date
        return best_val if best_val is not None else 0.0

    def _try_series(concepts, n=5, unit="USD"):
        # Pick the concept with the most recent data (by first element's year)
        best_series = []
        best_date = ""
        for c in concepts:
            s = _annual_series(facts, c, n, unit)
            if s:
                # Get the end date of the most recent value to compare
                _, date = _latest_annual(facts, c, unit)
                if date > best_date:
                    best_series = s
                    best_date = date
        return best_series

    # Revenue
    revenue = _try_concepts([
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet", "TotalRevenuesAndOtherIncome",
        "InterestAndNoninterestIncome",
    ])

    # Net Interest Income (banks)
    nii = _try_concepts(["InterestIncomeExpenseNet", "NetInterestIncome"])

    # Non-Interest Income (banks)
    noninterest_income = _try_concepts(["NoninterestIncome"])

    # Non-Interest Expense (banks)
    noninterest_expense = _try_concepts(["NoninterestExpense"])

    # Operating Income / EBIT
    ebit = _try_concepts([
        "OperatingIncomeLoss", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ])

    # Net Income
    net_income = _try_concepts([
        "NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ])

    # EBITDA (often not filed directly — compute from components)
    ebitda = _try_concepts(["EarningsBeforeInterestTaxesDepreciationAndAmortization"])
    da = _try_concepts([
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "Depreciation",
    ])
    if not ebitda and ebit and da:
        ebitda = ebit + da

    # Interest expense
    interest = _try_concepts(["InterestExpense", "InterestExpenseDebt"])

    # Tax
    pretax = _try_concepts([
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ])
    tax_provision = _try_concepts(["IncomeTaxExpenseBenefit"])
    tax_rate = abs(tax_provision / pretax) if pretax != 0 else 0.21
    tax_rate = max(0.05, min(0.40, tax_rate))

    # Balance sheet
    total_assets = _try_concepts(["Assets"], annual=False)
    # Equity: corporate stockholders equity first, then MLP partners' capital
    # (can legitimately be negative for partnerships, so don't filter negatives)
    total_equity = _try_concepts([
        "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital", "LimitedPartnersCapitalAccount",
    ], annual=False)
    total_debt = _try_concepts([
        "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
        "DebtAndCapitalLeaseObligations",
    ], annual=False)
    cash = _try_concepts([
        "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
    ], annual=False)
    tangible_book = _try_concepts(["TangibleBookValue"], annual=False)

    # Tangible assets for replacement-cost asset valuation
    ppe_net = _try_concepts(["PropertyPlantAndEquipmentNet"], annual=False)
    oil_gas_full_cost = _try_concepts(["OilAndGasPropertyFullCostMethodNet"], annual=False)
    oil_gas_successful_efforts = _try_concepts(["OilAndGasPropertySuccessfulEffortMethodNet"], annual=False)

    # Shares: corporate common stock first, then MLP units from DEI taxonomy
    shares = _try_concepts(
        ["CommonStockSharesOutstanding",
         "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
         "WeightedAverageNumberOfDilutedSharesOutstanding"],
        annual=False, unit="shares"
    )
    # MLP units from DEI taxonomy (for partnerships like NGL, DKL)
    if shares == 0:
        dei_units, _ = _latest_value(facts, "EntityCommonStockSharesOutstanding", unit="shares", taxonomy="dei")
        if dei_units:
            shares = dei_units

    # Capex & D&A
    capex = abs(_try_concepts([
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpendituresIncurredButNotYetPaid",
    ]))
    if not da:
        da = abs(_try_concepts([
            "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        ]))

    # Working capital change
    delta_wc = _try_concepts([
        "IncreaseDecreaseInOperatingCapital",
        "IncreaseDecreaseInOtherOperatingCapitalNet",
    ])

    # Dividends paid
    dividends_paid = abs(_try_concepts([
        "PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
        "DividendsCommonStockCash",
    ]))

    # ── Multi-year series ──
    revenue_5yr = _try_series([
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ])
    net_income_5yr = _try_series(["NetIncomeLoss", "ProfitLoss"])
    ebit_5yr = _try_series(["OperatingIncomeLoss"])

    # ROE series
    equity_series = _try_series([
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ])
    roe_5yr = []
    retention_5yr = []
    for i in range(min(len(net_income_5yr), len(equity_series))):
        eq = equity_series[i]
        ni = net_income_5yr[i]
        roe_5yr.append(ni / eq if eq != 0 else 0.0)
        retention_5yr.append(1.0 - (dividends_paid / ni if ni != 0 else 0.0))

    # ── Bank-specific fields ──
    is_bank = nii > 0 and noninterest_expense > 0
    bank_data = {}
    if is_bank:
        deposits = _try_concepts(["Deposits"], annual=False)
        loans = _try_concepts([
            "LoansAndLeasesReceivableNetReportedAmount",
            "LoansAndLeasesReceivableNetOfDeferredIncome",
            "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
        ], annual=False)
        provision = _try_concepts([
            "ProvisionForLoanLeaseAndCreditLosses",
            "ProvisionForCreditLosses",
            "ProvisionForLoanLossesExpensed",
        ])
        tier1 = _try_concepts(["Tier1CapitalToRiskWeightedAssets"], annual=False)

        # Efficiency ratio = NonInterestExpense / (NII + NonInterestIncome)
        total_revenue_bank = nii + noninterest_income
        efficiency = noninterest_expense / total_revenue_bank if total_revenue_bank > 0 else 0

        # For banks, EBIT doesn't apply — use pre-provision net revenue
        ppnr = total_revenue_bank - noninterest_expense

        bank_data = {
            "is_bank": True,
            "net_interest_income": nii,
            "noninterest_income": noninterest_income,
            "noninterest_expense": noninterest_expense,
            "provision_for_credit_losses": provision,
            "deposits": deposits,
            "total_loans": loans,
            "efficiency_ratio": round(efficiency, 4),
            "ppnr": ppnr,  # pre-provision net revenue
            "tier1_ratio": tier1,
        }

        # Override revenue for banks (NII + NonII is the real "revenue")
        if total_revenue_bank > 0:
            revenue = total_revenue_bank

        # For banks, set EBIT to pre-provision net revenue (closest equivalent)
        if ebit == 0 and ppnr != 0:
            ebit = ppnr
            ebitda = ppnr + da  # banks have minimal D&A

    # ── Build result ──
    result = {
        "revenue_ttm": revenue,
        "ebit_ttm": ebit,
        "ebitda_ttm": ebitda or (ebit + da if ebit else 0),
        "net_income_ttm": net_income,
        "d_and_a_ttm": da,
        "capex_ttm": capex,
        "delta_wc_ttm": delta_wc,
        "interest_expense_ttm": interest,
        "tax_rate_effective": tax_rate,
        "total_debt": total_debt,
        "cash": cash,
        "net_debt": total_debt - cash,
        "shares_outstanding": shares,
        "total_equity": total_equity,
        "total_assets": total_assets,
        "tangible_book_value": tangible_book,
        "ppe_net": ppe_net,
        "oil_gas_property_full_cost": oil_gas_full_cost,
        "oil_gas_property_successful_efforts": oil_gas_successful_efforts,
        "forward_pe": 0.0,  # not available from XBRL
        "revenue_5yr": revenue_5yr,
        "ebit_5yr": ebit_5yr,
        "net_income_5yr": net_income_5yr,
        "roe_5yr": roe_5yr,
        "retention_5yr": retention_5yr,
        "capex_5yr": [],
        "source": "sec_xbrl",
        **bank_data,
    }

    print(f"[sec_xbrl] {ticker}: Revenue=${revenue/1e6:.0f}M NI=${net_income/1e6:.0f}M "
          f"Equity=${total_equity/1e6:.0f}M" +
          (f" NII=${nii/1e6:.0f}M Efficiency={efficiency:.1%}" if is_bank else f" EBIT=${ebit/1e6:.0f}M"))

    return result


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "TBBK"
    data = get_xbrl_financials(ticker, use_cache=False)
    if data:
        for k, v in sorted(data.items()):
            if isinstance(v, float) and abs(v) > 1000:
                print(f"  {k:35s} = {v:>15,.0f}")
            elif isinstance(v, list):
                print(f"  {k:35s} = {v}")
            else:
                print(f"  {k:35s} = {v}")
