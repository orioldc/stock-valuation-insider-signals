"""Fetch TTM and multi-year financial data from FMP (primary) and yfinance (fallback)."""

import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com"


def _fmp_get(endpoint: str, ticker: str, limit: int = 5) -> list[dict]:
    """Generic FMP annual statement fetch."""
    if not FMP_API_KEY:
        return []
    try:
        url = f"{FMP_BASE}/api/v3/{endpoint}/{ticker}"
        resp = requests.get(url, params={"apikey": FMP_API_KEY, "limit": limit}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[financials] FMP {endpoint} failed for {ticker}: {e}")
        return []


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _yfinance_fallback(ticker: str) -> dict:
    """Pull financials from yfinance as fallback for all critical fields."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        result = {
            "revenue_ttm": _safe_float(info.get("totalRevenue")),
            "ebitda_ttm": _safe_float(info.get("ebitda")),
            "ebit_ttm": _safe_float(info.get("ebit")),
            "net_income_ttm": _safe_float(info.get("netIncomeToCommon")),
            "total_debt": _safe_float(info.get("totalDebt")),
            "cash": _safe_float(info.get("totalCash")),
            "net_debt": _safe_float(info.get("totalDebt")) - _safe_float(info.get("totalCash")),
            "d_and_a_ttm": 0.0,
            "capex_ttm": _safe_float(info.get("capitalExpenditures")),
            "delta_wc_ttm": 0.0,
            "interest_expense_ttm": 0.0,
            "tax_rate_effective": 0.21,  # default US rate
            "shares_outstanding": _safe_float(info.get("sharesOutstanding")),
            "forward_pe": _safe_float(info.get("forwardPE")),
            "revenue_5yr": [],
            "ebit_5yr": [],
            "net_income_5yr": [],
            "roe_5yr": [],
            "retention_5yr": [],
            "capex_5yr": [],
            "total_equity": 0.0,
            "source": "yfinance",
        }

        # Try to get annual + quarterly statements from yfinance
        try:
            inc = t.income_stmt
            cf = t.cashflow
            bs = t.balance_sheet

            # ── Compute clean TTM from quarterly data ──
            # yfinance's info.ebit includes non-recurring items (write-offs, impairments,
            # acquisition charges). We compute TTM from quarterly Operating Income instead,
            # which represents recurring operating performance.
            try:
                q_inc = t.quarterly_income_stmt
                if q_inc is not None and not q_inc.empty and len(q_inc.columns) >= 4:
                    q_cols = q_inc.columns[:4]  # last 4 quarters

                    # TTM Revenue from quarters
                    for label in ["Total Revenue", "Revenue"]:
                        if label in q_inc.index:
                            ttm_rev = sum(_safe_float(q_inc.loc[label, c]) for c in q_cols)
                            if ttm_rev > 0:
                                result["revenue_ttm"] = ttm_rev
                            break

                    # TTM Operating Income (clean, excludes unusual items)
                    if "Operating Income" in q_inc.index:
                        ttm_oi = sum(_safe_float(q_inc.loc["Operating Income", c]) for c in q_cols)
                        # TTM EBIT (includes unusual items — for comparison)
                        ttm_ebit_raw = sum(_safe_float(q_inc.loc["EBIT", c]) for c in q_cols) if "EBIT" in q_inc.index else ttm_oi
                        nonrecurring = abs(ttm_ebit_raw - ttm_oi)

                        # If there's a material gap (>20% of revenue or >50% of OI),
                        # use Operating Income as the clean EBIT
                        if result["revenue_ttm"] > 0 and nonrecurring > result["revenue_ttm"] * 0.03:
                            print(f"[financials] {ticker}: Using Operating Income for EBIT "
                                  f"(non-recurring items: ${nonrecurring/1e6:.1f}M stripped)")
                            result["ebit_ttm"] = ttm_oi
                            result["_ebit_raw"] = ttm_ebit_raw
                            result["_nonrecurring_charges"] = nonrecurring
                        elif ttm_oi != 0:
                            result["ebit_ttm"] = ttm_oi

                    # TTM Net Income from quarters
                    for label in ["Net Income", "Net Income Common Stockholders"]:
                        if label in q_inc.index:
                            ttm_ni = sum(_safe_float(q_inc.loc[label, c]) for c in q_cols)
                            result["net_income_ttm"] = ttm_ni
                            break

                    # TTM R&D from quarters
                    for label in ["Research And Development", "Research Development"]:
                        if label in q_inc.index:
                            ttm_rd = sum(_safe_float(q_inc.loc[label, c]) for c in q_cols)
                            if ttm_rd > 0:
                                result["rd_expense_ttm"] = ttm_rd
                            break

                    # TTM D&A from quarterly cash flow
                    q_cf = t.quarterly_cashflow
                    if q_cf is not None and not q_cf.empty and len(q_cf.columns) >= 4:
                        qcf_cols = q_cf.columns[:4]
                        for label in ["Depreciation And Amortization", "Depreciation"]:
                            if label in q_cf.index:
                                result["d_and_a_ttm"] = abs(sum(_safe_float(q_cf.loc[label, c]) for c in qcf_cols))
                                break
                        for label in ["Capital Expenditure", "Capital Expenditures"]:
                            if label in q_cf.index:
                                result["capex_ttm"] = abs(sum(_safe_float(q_cf.loc[label, c]) for c in qcf_cols))
                                break

            except Exception as e:
                print(f"[financials] {ticker}: Quarterly TTM computation failed: {e}")

            if inc is not None and not inc.empty:
                rev_row = None
                for label in ["Total Revenue", "Revenue"]:
                    if label in inc.index:
                        rev_row = inc.loc[label]
                        break
                if rev_row is not None:
                    result["revenue_5yr"] = [_safe_float(v) for v in rev_row.values[:5]]

                ebit_row = None
                # Prefer Operating Income over EBIT for 5yr series too
                for label in ["Operating Income", "EBIT"]:
                    if label in inc.index:
                        ebit_row = inc.loc[label]
                        break
                if ebit_row is not None:
                    result["ebit_5yr"] = [_safe_float(v) for v in ebit_row.values[:5]]
                    if result["revenue_ttm"] == 0 and result["revenue_5yr"]:
                        result["revenue_ttm"] = result["revenue_5yr"][0]
                    if result["ebit_ttm"] == 0 and result["ebit_5yr"]:
                        result["ebit_ttm"] = result["ebit_5yr"][0]

                ni_row = None
                for label in ["Net Income", "Net Income Common Stockholders"]:
                    if label in inc.index:
                        ni_row = inc.loc[label]
                        break
                if ni_row is not None:
                    result["net_income_5yr"] = [_safe_float(v) for v in ni_row.values[:5]]
                    if result["net_income_ttm"] == 0 and result["net_income_5yr"]:
                        result["net_income_ttm"] = result["net_income_5yr"][0]

                # Tax rate from statements
                pretax_row = None
                tax_row = None
                for label in ["Pretax Income", "Income Before Tax"]:
                    if label in inc.index:
                        pretax_row = inc.loc[label]
                        break
                for label in ["Tax Provision", "Income Tax Expense"]:
                    if label in inc.index:
                        tax_row = inc.loc[label]
                        break
                if pretax_row is not None and tax_row is not None:
                    pretax = _safe_float(pretax_row.values[0])
                    tax = _safe_float(tax_row.values[0])
                    if pretax != 0:
                        result["tax_rate_effective"] = max(0.05, min(0.40, abs(tax / pretax)))

                # Interest expense
                for label in ["Interest Expense", "Net Interest Income"]:
                    if label in inc.index:
                        result["interest_expense_ttm"] = abs(_safe_float(inc.loc[label].values[0]))
                        break

            if cf is not None and not cf.empty:
                # D&A
                for label in ["Depreciation And Amortization", "Depreciation", "Depreciation & Amortization"]:
                    if label in cf.index:
                        result["d_and_a_ttm"] = abs(_safe_float(cf.loc[label].values[0]))
                        break
                # Capex
                for label in ["Capital Expenditure", "Capital Expenditures", "Purchase Of Property Plant And Equipment"]:
                    if label in cf.index:
                        result["capex_ttm"] = abs(_safe_float(cf.loc[label].values[0]))
                        result["capex_5yr"] = [abs(_safe_float(v)) for v in cf.loc[label].values[:5]]
                        break
                # Working capital change
                for label in ["Change In Working Capital", "Changes In Working Capital"]:
                    if label in cf.index:
                        result["delta_wc_ttm"] = _safe_float(cf.loc[label].values[0])
                        break

            # R&D expense for capitalization (Ch 9, pp.232-236)
            if inc is not None and not inc.empty:
                for label in ["Research And Development", "Research Development"]:
                    if label in inc.index:
                        rd_vals = [abs(_safe_float(v)) for v in inc.loc[label].values[:5]]
                        result["rd_expense_5yr"] = rd_vals
                        if rd_vals:
                            result["rd_expense_ttm"] = rd_vals[0]
                        break

            if bs is not None and not bs.empty:
                # ROE calculation using equity
                equity_row = None
                for label in ["Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"]:
                    if label in bs.index:
                        equity_row = bs.loc[label]
                        break
                if equity_row is not None:
                    equities = [_safe_float(v) for v in equity_row.values[:5]]
                    if equities:
                        result["total_equity"] = equities[0]
                    if result["net_income_5yr"]:
                        result["roe_5yr"] = [
                            ni / eq if eq != 0 else 0
                            for ni, eq in zip(result["net_income_5yr"], equities)
                        ]
                # Dividend payout for retention ratio
                div_row = None
                for label in ["Common Stock Dividend Paid", "Dividends Paid", "Payment Of Dividends"]:
                    if label in cf.index:
                        div_row = cf.loc[label]
                        break
                if div_row is not None and result["net_income_5yr"]:
                    divs = [abs(_safe_float(v)) for v in div_row.values[:5]]
                    result["retention_5yr"] = [
                        1 - (d / ni) if ni != 0 else 1.0
                        for d, ni in zip(divs, result["net_income_5yr"])
                    ]

        except Exception as e:
            print(f"[financials] yfinance statement parsing failed for {ticker}: {e}")

        # Marginal tax rate (Ch 10, p.250): use 21% for US (post-TCJA)
        # Terminal value must use marginal, not effective
        result["marginal_tax_rate"] = 0.21

        # Ensure ebitda (use clean EBIT + D&A)
        if result["ebitda_ttm"] == 0 and result["ebit_ttm"] != 0:
            result["ebitda_ttm"] = result["ebit_ttm"] + result["d_and_a_ttm"]
        # If EBITDA came from info but EBIT was cleaned up, recompute
        elif result.get("_nonrecurring_charges", 0) > 0:
            result["ebitda_ttm"] = result["ebit_ttm"] + result["d_and_a_ttm"]

        return result

    except Exception as e:
        print(f"[financials] yfinance fallback failed for {ticker}: {e}")
        return _empty_financials()


def _empty_financials() -> dict:
    return {
        "revenue_ttm": 0.0, "ebitda_ttm": 0.0, "ebit_ttm": 0.0,
        "net_income_ttm": 0.0, "d_and_a_ttm": 0.0, "capex_ttm": 0.0,
        "delta_wc_ttm": 0.0, "total_debt": 0.0, "cash": 0.0, "net_debt": 0.0,
        "interest_expense_ttm": 0.0, "tax_rate_effective": 0.21,
        "marginal_tax_rate": 0.21,
        "shares_outstanding": 0.0,
        "total_equity": 0.0,
        "revenue_5yr": [], "ebit_5yr": [], "net_income_5yr": [],
        "roe_5yr": [], "retention_5yr": [], "capex_5yr": [],
        "source": "empty",
    }


def get_ttm_financials(ticker: str, no_cache: bool = False) -> dict:
    """
    Fetch trailing twelve-month (TTM) and 5-year financial data.

    Priority: SEC XBRL (authoritative) → FMP → yfinance fallback.
    SEC XBRL is especially important for banks where yfinance EBIT is unreliable.
    yfinance quarterly data is used to compute TTM when XBRL only has annual.

    Returns standardized dict with all fields needed by valuation modules.
    """
    # Try SEC XBRL first — authoritative data directly from filings
    try:
        from data.sec_xbrl import get_xbrl_financials
        xbrl = get_xbrl_financials(ticker, use_cache=not no_cache)
        if xbrl and xbrl.get("net_income_ttm", 0) != 0:
            # XBRL has annual data. Enhance with yfinance quarterly for true TTM.
            try:
                t = yf.Ticker(ticker)
                info = t.info
                # Add forward PE from yfinance (not in XBRL)
                xbrl["forward_pe"] = _safe_float(info.get("forwardPE"))

                # Compute TTM from quarterly if available (more current than annual)
                q_inc = t.quarterly_income_stmt
                if q_inc is not None and not q_inc.empty and len(q_inc.columns) >= 4:
                    q_cols = q_inc.columns[:4]
                    for label in ["Total Revenue", "Revenue"]:
                        if label in q_inc.index:
                            ttm_rev = sum(_safe_float(q_inc.loc[label, c]) for c in q_cols)
                            if ttm_rev > 0 and ttm_rev > xbrl["revenue_ttm"] * 0.8:
                                xbrl["revenue_ttm"] = ttm_rev
                            break
                    # TTM Operating Income from quarters (cleaner than annual for transition periods)
                    if "Operating Income" in q_inc.index:
                        ttm_oi = sum(_safe_float(q_inc.loc["Operating Income", c]) for c in q_cols)
                        xbrl["ebit_ttm_quarterly"] = ttm_oi
                    # TTM Net Income from quarters
                    for label in ["Net Income", "Net Income Common Stockholders"]:
                        if label in q_inc.index:
                            ttm_ni = sum(_safe_float(q_inc.loc[label, c]) for c in q_cols)
                            if ttm_ni != 0:
                                xbrl["net_income_ttm"] = ttm_ni
                            break

                # Enhance cash flow items from quarterly data (XBRL often
                # misses aggregate working capital change)
                q_cf = t.quarterly_cashflow
                if q_cf is not None and not q_cf.empty and len(q_cf.columns) >= 4:
                    qcf_cols = q_cf.columns[:4]
                    # Working capital change (biggest gap — many companies
                    # report individual WC items in XBRL, not the aggregate)
                    if xbrl.get("delta_wc_ttm", 0) == 0:
                        for label in ["Change In Working Capital", "Changes In Working Capital"]:
                            if label in q_cf.index:
                                xbrl["delta_wc_ttm"] = sum(
                                    _safe_float(q_cf.loc[label, c]) for c in qcf_cols)
                                break
                    # D&A and CapEx — use quarterly TTM if XBRL has annual only
                    for label in ["Depreciation And Amortization", "Depreciation"]:
                        if label in q_cf.index:
                            ttm_da = abs(sum(_safe_float(q_cf.loc[label, c]) for c in qcf_cols))
                            if ttm_da > 0:
                                xbrl["d_and_a_ttm"] = ttm_da
                            break
                    for label in ["Capital Expenditure", "Capital Expenditures"]:
                        if label in q_cf.index:
                            ttm_capex = abs(sum(_safe_float(q_cf.loc[label, c]) for c in qcf_cols))
                            if ttm_capex > 0:
                                xbrl["capex_ttm"] = ttm_capex
                            break
                    # Cash from yfinance (often includes short-term investments)
                    yf_cash = _safe_float(info.get("totalCash"))
                    if yf_cash > xbrl.get("cash", 0):
                        xbrl["cash"] = yf_cash
                        xbrl["net_debt"] = xbrl.get("total_debt", 0) - yf_cash
                # R&D expense from yfinance annual statements (Ch 9, pp.232-236)
                inc_stmt = t.income_stmt
                if inc_stmt is not None and not inc_stmt.empty:
                    for label in ["Research And Development", "Research Development"]:
                        if label in inc_stmt.index:
                            rd_vals = [abs(_safe_float(v)) for v in inc_stmt.loc[label].values[:5]]
                            xbrl["rd_expense_5yr"] = rd_vals
                            break
                    # TTM R&D from quarters (more current)
                    if q_inc is not None and not q_inc.empty and len(q_inc.columns) >= 4:
                        for label in ["Research And Development", "Research Development"]:
                            if label in q_inc.index:
                                ttm_rd = sum(_safe_float(q_inc.loc[label, c]) for c in q_cols)
                                if ttm_rd > 0:
                                    xbrl["rd_expense_ttm"] = ttm_rd
                                break

            except Exception as e:
                print(f"[financials] yfinance TTM enhancement failed for {ticker}: {e}")

            # Marginal tax rate (Ch 10, p.250): 21% US post-TCJA
            xbrl.setdefault("marginal_tax_rate", 0.21)
            return xbrl
    except Exception as e:
        print(f"[financials] SEC XBRL failed for {ticker}: {e}")

    # Try FMP next for clean, structured data
    income_stmts = _fmp_get("income-statement", ticker, limit=5)
    cf_stmts = _fmp_get("cash-flow-statement", ticker, limit=5)
    bs_stmts = _fmp_get("balance-sheet-statement", ticker, limit=5)

    if not income_stmts:
        print(f"[financials] FMP unavailable for {ticker}, using yfinance fallback")
        return _yfinance_fallback(ticker)

    # Use most recent annual as base (FMP free tier: annual only)
    inc = income_stmts[0]
    cf = cf_stmts[0] if cf_stmts else {}
    bs = bs_stmts[0] if bs_stmts else {}

    # Build 5-year arrays from annual statements
    revenue_5yr = [_safe_float(s.get("revenue")) for s in income_stmts[:5]]
    ebit_5yr = [_safe_float(s.get("operatingIncome")) for s in income_stmts[:5]]
    net_income_5yr = [_safe_float(s.get("netIncome")) for s in income_stmts[:5]]
    capex_5yr = [abs(_safe_float(s.get("capitalExpenditure"))) for s in cf_stmts[:5]] if cf_stmts else []

    # ROE and retention ratio
    equity_5yr = [_safe_float(s.get("totalStockholdersEquity")) for s in bs_stmts[:5]] if bs_stmts else []
    roe_5yr = []
    retention_5yr = []
    for i in range(min(len(net_income_5yr), len(equity_5yr))):
        eq = equity_5yr[i]
        ni = net_income_5yr[i]
        roe_5yr.append(ni / eq if eq != 0 else 0.0)
        # Retention = 1 - (dividends / net_income)
        div_paid = abs(_safe_float((cf_stmts[i] if cf_stmts and i < len(cf_stmts) else {}).get("dividendsPaid")))
        retention_5yr.append(1.0 - (div_paid / ni if ni != 0 else 0.0))

    # Tax rate from income statement
    pretax = _safe_float(inc.get("incomeBeforeTax", 0))
    tax = _safe_float(inc.get("incomeTaxExpense", 0))
    tax_rate = max(0.05, min(0.40, abs(tax / pretax))) if pretax != 0 else 0.21

    # Debt components
    total_debt = _safe_float(bs.get("totalDebt") or bs.get("longTermDebt", 0))
    cash = _safe_float(bs.get("cashAndCashEquivalents") or bs.get("cash", 0))

    result = {
        "revenue_ttm": _safe_float(inc.get("revenue")),
        "ebit_ttm": _safe_float(inc.get("operatingIncome")),
        "ebitda_ttm": _safe_float(inc.get("ebitda")) or (
            _safe_float(inc.get("operatingIncome")) + _safe_float(cf.get("depreciationAndAmortization", 0))
        ),
        "net_income_ttm": _safe_float(inc.get("netIncome")),
        "d_and_a_ttm": abs(_safe_float(cf.get("depreciationAndAmortization"))),
        "capex_ttm": abs(_safe_float(cf.get("capitalExpenditure"))),
        "delta_wc_ttm": _safe_float(cf.get("changeInWorkingCapital")),
        "interest_expense_ttm": abs(_safe_float(inc.get("interestExpense"))),
        "tax_rate_effective": tax_rate,
        "marginal_tax_rate": 0.21,  # Ch 10, p.250: US post-TCJA
        "total_debt": total_debt,
        "cash": cash,
        "net_debt": total_debt - cash,
        "shares_outstanding": _safe_float(
            inc.get("weightedAverageShsOut") or bs.get("commonStock")
        ),
        "revenue_5yr": revenue_5yr,
        "ebit_5yr": ebit_5yr,
        "net_income_5yr": net_income_5yr,
        "total_equity": equity_5yr[0] if equity_5yr else 0.0,
        "roe_5yr": roe_5yr,
        "retention_5yr": retention_5yr,
        "capex_5yr": capex_5yr,
        "source": "fmp",
    }

    return result


def capitalize_rd(financials: dict, amort_life: int = 5) -> dict:
    """
    R&D Capitalization — Damodaran Ch 9, pp.232-236.

    Capitalizes R&D expenses as an investment, computing:
      - research_asset: unamortized value of past R&D investments
      - rd_amortization: annual amortization of the research asset
      - adjusted_ebit: EBIT + R&D_current - R&D_amortization
      - adjusted_invested_capital: IC + research_asset

    Args:
        financials: dict with rd_expense_5yr, ebit_ttm, total_equity, total_debt, cash
        amort_life: amortizable life in years (5 for tech, 10 for pharma)

    Returns dict with capitalization results (empty if R&D data insufficient).
    """
    rd_5yr = financials.get("rd_expense_5yr", [])
    rd_ttm = financials.get("rd_expense_ttm", 0)
    if not rd_5yr or rd_ttm <= 0:
        return {}

    # Filter NaN/None values, pad to amort_life years
    rd_history = [v for v in rd_5yr if v is not None and not (isinstance(v, float) and math.isnan(v)) and v > 0]
    if not rd_history:
        return {}
    while len(rd_history) < amort_life:
        rd_history.append(rd_history[-1] if rd_history else 0)

    # Value of research asset = Σ(R&D_year_k × unamortized_fraction)
    # Year 0 (current) = fully unamortized; Year k = max(0, 1 - k/amort_life)
    research_asset = 0.0
    rd_amortization = 0.0
    for k, rd in enumerate(rd_history):
        if k == 0:
            # Current year R&D: fully unamortized (not yet started depreciating)
            unamort_frac = 1.0
        else:
            unamort_frac = max(0, 1 - k / amort_life)
        research_asset += rd * unamort_frac
        # Each past year's R&D contributes rd/amort_life to this year's amortization
        if k > 0:
            rd_amortization += rd / amort_life

    ebit = financials.get("ebit_ttm", 0)
    adjusted_ebit = ebit + rd_ttm - rd_amortization

    bv_equity = financials.get("total_equity", 0)
    total_debt = financials.get("total_debt", 0)
    cash = financials.get("cash", 0)
    invested_capital = bv_equity + total_debt - cash
    adjusted_ic = invested_capital + research_asset

    t = financials.get("tax_rate_effective", 0.21)
    roic_unadjusted = ebit * (1 - t) / invested_capital if invested_capital > 0 else None
    roic_adjusted = adjusted_ebit * (1 - t) / adjusted_ic if adjusted_ic > 0 else None

    return {
        "research_asset": research_asset,
        "rd_amortization": rd_amortization,
        "rd_current": rd_ttm,
        "adjusted_ebit": adjusted_ebit,
        "adjusted_invested_capital": adjusted_ic,
        "roic_unadjusted": roic_unadjusted,
        "roic_adjusted": roic_adjusted,
        "amort_life": amort_life,
    }


def compute_fcff(financials: dict) -> float:
    """
    Free Cash Flow to Firm.
    FCFF = EBIT*(1-t) + D&A - Capex - delta_WC
    """
    ebit = financials.get("ebit_ttm", 0)
    t = financials.get("tax_rate_effective", 0.21)
    dna = financials.get("d_and_a_ttm", 0)
    capex = financials.get("capex_ttm", 0)
    dwc = financials.get("delta_wc_ttm", 0)
    return ebit * (1 - t) + dna - capex - dwc


def compute_fcfe(financials: dict) -> float:
    """
    Free Cash Flow to Equity.
    FCFE = Net_Income + D&A - Capex - delta_WC + net_borrowing
    Net_borrowing is approximated as 0 for stable firms.
    """
    ni = financials.get("net_income_ttm", 0)
    dna = financials.get("d_and_a_ttm", 0)
    capex = financials.get("capex_ttm", 0)
    dwc = financials.get("delta_wc_ttm", 0)
    return ni + dna - capex - dwc


def compute_ffo(financials: dict) -> dict:
    """
    Funds From Operations for REITs — Damodaran Ch 26, p.764.

    FFO = Net Income + Depreciation & Amortization - Gains on Sales
    AFFO = FFO - Maintenance Capex (recurring capital expenditures)

    FFO adds back depreciation because real estate depreciation (27.5yr residential,
    39yr commercial) is a legal fiction — property values typically appreciate.
    """
    ni = financials.get("net_income_ttm", 0)
    dna = financials.get("d_and_a_ttm", 0)
    capex = financials.get("capex_ttm", 0)

    # Gains on sales are not tracked separately in our data;
    # for REITs, these are typically small and non-recurring.
    # If they were material, they'd show up as non-recurring items
    # already stripped from operating income.
    gains_on_sales = 0

    ffo = ni + dna - gains_on_sales
    # AFFO = FFO - maintenance capex
    # Approximate maintenance capex as 15% of D&A for REITs (industry norm)
    # since we can't distinguish maintenance vs growth capex
    maintenance_capex = min(capex, dna * 0.15) if capex > 0 else dna * 0.10
    affo = ffo - maintenance_capex

    shares = financials.get("shares_outstanding", 0) or 1
    ffo_per_share = ffo / shares if shares > 0 else 0
    affo_per_share = affo / shares if shares > 0 else 0

    return {
        "ffo": ffo,
        "affo": affo,
        "ffo_per_share": ffo_per_share,
        "affo_per_share": affo_per_share,
        "maintenance_capex": maintenance_capex,
        "d_and_a_added_back": dna,
    }


def normalize_earnings(financials: dict, method: str = "average",
                       sector_data: dict = None) -> dict:
    """
    For cyclical or negative-EBIT companies: normalize earnings.

    Two approaches (Damodaran Ch 34 decision tree):
    1. "average": average over 5-year cycle (cyclical companies with some positive years)
    2. "sector_margin": apply sector-average operating margin to revenue
       (for persistently negative EBIT — revenue is positive but margins haven't turned)

    If method="average" but averaged EBIT is still negative and sector_data
    is provided, automatically falls back to sector_margin approach.
    """
    f = financials.copy()
    if method == "average":
        if financials["revenue_5yr"]:
            f["revenue_ttm"] = float(np.mean([v for v in financials["revenue_5yr"] if v > 0]))
        if financials["ebit_5yr"]:
            avg_ebit = float(np.mean([v for v in financials["ebit_5yr"] if v != 0]))
            f["ebit_ttm"] = avg_ebit
        if financials["net_income_5yr"]:
            f["net_income_ttm"] = float(np.mean([v for v in financials["net_income_5yr"] if v != 0]))
        f["normalized"] = True

        # Fallback: if averaged EBIT is still negative but we have sector margin data,
        # apply sector operating margin to revenue (Damodaran Ch 34: negative-earnings path)
        if f["ebit_ttm"] <= 0 and sector_data:
            op_margin = sector_data.get("operating_margin_sector")
            net_margin = sector_data.get("net_margin_sector")
            if op_margin and op_margin > 0:
                revenue = f["revenue_ttm"]
                f["ebit_ttm"] = revenue * op_margin
                f["ebitda_ttm"] = f["ebit_ttm"] + f.get("d_and_a_ttm", 0)
                if net_margin and net_margin > 0:
                    f["net_income_ttm"] = revenue * net_margin
                else:
                    tax = f.get("tax_rate_effective", 0.21) or 0.21
                    f["net_income_ttm"] = f["ebit_ttm"] * (1 - tax)
                f["sector_margin_normalized"] = True
    return f


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    f = get_ttm_financials(ticker)
    print(f"Revenue TTM: ${f['revenue_ttm']:,.0f}")
    print(f"EBIT TTM: ${f['ebit_ttm']:,.0f}")
    print(f"EBITDA TTM: ${f['ebitda_ttm']:,.0f}")
    print(f"Net Income TTM: ${f['net_income_ttm']:,.0f}")
    print(f"D&A: ${f['d_and_a_ttm']:,.0f}")
    print(f"Capex: ${f['capex_ttm']:,.0f}")
    print(f"Net Debt: ${f['net_debt']:,.0f}")
    print(f"Tax Rate: {f['tax_rate_effective']:.1%}")
    print(f"FCFF: ${compute_fcff(f):,.0f}")
    print(f"Source: {f['source']}")
