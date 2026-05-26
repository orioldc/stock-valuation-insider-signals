"""Fetch and cache Damodaran's free sector-level datasets from NYU."""

import json
import time
import difflib
import warnings
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

DAMODARAN_BASE = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "damodaran"
CACHE_MANIFEST = CACHE_DIR / "cache_manifest.json"
CACHE_TTL_DAYS = 30
ERP_CACHE_TTL_DAYS = 7

# Damodaran's dataset URLs
DATASETS = {
    "wacc":      f"{DAMODARAN_BASE}wacc.html",
    "beta":      f"{DAMODARAN_BASE}Betas.html",
    "pe":        f"{DAMODARAN_BASE}pedata.html",
    "ev":        f"{DAMODARAN_BASE}vebitda.html",
    "psdata":    f"{DAMODARAN_BASE}psdata.html",
    "roe":       f"{DAMODARAN_BASE}roe.html",
    "margins":   f"{DAMODARAN_BASE}margin.html",
    "erp":       f"{DAMODARAN_BASE}histimpl.html",
    "ctryprem":  f"{DAMODARAN_BASE}ctryprem.html",
}

# Sector-level fallback mappings (yfinance sector → Damodaran label prefix)
SECTOR_MAP = {
    "Technology":               "Software",
    "Financial Services":       "Bank",
    "Healthcare":               "Healthcare",
    "Consumer Cyclical":        "Retail",
    "Consumer Defensive":       "Food",
    "Industrials":              "Machinery",
    "Basic Materials":          "Chemical",
    "Energy":                   "Oil/Gas",
    "Real Estate":              "Real Estate",
    "Utilities":                "Utility",
    "Communication Services":   "Telecom",
}

# Industry-level overrides (yfinance industry → exact Damodaran label prefix)
INDUSTRY_MAP = {
    "Consumer Electronics":             "Electronics  (Consumer",
    "Electronic Components":            "Electronics (General",
    "Semiconductors":                   "Semiconductor",
    "Semiconductor Equipment":          "Semiconductor Equip",
    "Software—Application":             "Software  (System",
    "Software—Infrastructure":          "Software  (System",
    "Internet Content & Information":   "Software (Internet",
    "Entertainment":                    "Software  (Entertainment",
    "Computer Hardware":                "Computers/Peripherals",
    "Information Technology Services":  "Computer  Services",
    "Banks—Diversified":                "Bank (Money Center",
    "Banks—Regional":                   "Banks  (Regional",
    "Insurance—Diversified":            "Insurance (General",
    "Insurance—Life":                   "Insurance  (Life",
    "Insurance—Property & Casualty":    "Insurance (Prop/Cas.",
    "Asset Management":                 "Investments  & Asset",
    "Capital Markets":                  "Brokerage  & Investment",
    "REIT—Retail":                      "Retail  (REITs",
    "REIT—Office":                      "R.E.I.T.",
    "REIT—Industrial":                  "R.E.I.T.",
    "REIT—Residential":                 "R.E.I.T.",
    "REIT—Healthcare Facilities":       "R.E.I.T.",
    "REIT—Diversified":                 "R.E.I.T.",
    "Oil & Gas E&P":                    "Oil/Gas (Production",
    "Oil & Gas Integrated":             "Oil/Gas  (Integrated",
    "Oil & Gas Equipment & Services":   "Oilfield Svcs",
    "Biotechnology":                    "Drugs  (Biotechnology",
    "Drug Manufacturers—General":       "Drugs (Pharmaceutical",
    "Medical Devices":                  "Healthcare Products",
    "Medical Care Facilities":          "Hospitals/Healthcare",
    "Health Information Services":      "Heathcare Information",
    "Telecom Services":                 "Telecom.  Services",
    "Wireless Telecom":                 "Telecom  (Wireless",
    "Telecom Equipment":                "Telecom. Equipment",
    "Auto Manufacturers":               "Auto & Truck",
    "Auto Parts":                       "Auto  Parts",
    "Aerospace & Defense":              "Aerospace/Defense",
    "Steel":                            "Steel",
    "Gold":                             "Precious Metals",
    "Copper":                           "Metals  & Mining",
    "Agricultural Inputs":              "Farming/Agriculture",
    "Restaurants":                      "Restaurant/Dining",
    "Airlines":                         "Air Transport",
    "Railroads":                        "Transportation (Railroads",
}


def _load_manifest() -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_MANIFEST.exists():
        return json.loads(CACHE_MANIFEST.read_text())
    return {}


def _save_manifest(manifest: dict):
    CACHE_MANIFEST.write_text(json.dumps(manifest, indent=2))


def _is_stale(key: str, ttl_days: int) -> bool:
    manifest = _load_manifest()
    ts = manifest.get(key)
    if ts is None:
        return True
    return (time.time() - ts) > ttl_days * 86400


def _fetch_html_table(url: str, table_index: int = 0) -> pd.DataFrame:
    """Download a page and extract the first HTML table."""
    from io import StringIO
    headers = {"User-Agent": "Mozilla/5.0 (academic research)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tables = pd.read_html(StringIO(resp.text))
    if not tables:
        raise ValueError(f"No tables found at {url}")
    df = tables[table_index]
    # Damodaran tables often have multi-row headers; find and promote the row
    # that contains 'Industry Name' as the true column header row.
    header_row = None
    for i in range(min(5, len(df))):
        if str(df.iloc[i, 0]).strip().lower() == "industry name":
            header_row = i
            break
    if header_row is not None:
        df.columns = [str(v) for v in df.iloc[header_row]]
        df = df.iloc[header_row + 1:].reset_index(drop=True)
    elif all(isinstance(c, int) for c in df.columns):
        # No 'Industry Name' found but integer columns — promote first row anyway
        df.columns = [str(v) for v in df.iloc[0]]
        df = df.iloc[1:].reset_index(drop=True)
    return df


def _fetch_and_cache(dataset_name: str, force_refresh: bool = False) -> pd.DataFrame:
    """Download a Damodaran dataset, save as CSV, return DataFrame."""
    csv_path = CACHE_DIR / f"{dataset_name}.csv"
    if not force_refresh and csv_path.exists() and not _is_stale(dataset_name, CACHE_TTL_DAYS):
        return pd.read_csv(csv_path)

    url = DATASETS[dataset_name]
    try:
        df = _fetch_html_table(url)
        df.to_csv(csv_path, index=False)
        manifest = _load_manifest()
        manifest[dataset_name] = time.time()
        _save_manifest(manifest)
        return df
    except Exception as e:
        if csv_path.exists():
            print(f"[damodaran] Warning: fetch failed ({e}), using cached {dataset_name}")
            return pd.read_csv(csv_path)
        raise


def _col(c: str) -> str:
    """Normalize column name for matching: lowercase + collapse whitespace."""
    return " ".join(c.lower().split())


def _first_valid_float(val) -> float:
    """Extract a float from a value that may be scalar or Series (duplicate columns).

    Damodaran tables sometimes have duplicate column names (e.g. two "EV/EBITDA"
    columns for with/without negative EV firms). When accessing by name, pandas
    returns a Series. This function takes the first valid (non-NaN) numeric value.
    """
    import pandas as pd
    if isinstance(val, pd.Series):
        for v in val:
            try:
                f = float(str(v).replace(",", ""))
                if not pd.isna(f):
                    return f
            except (ValueError, TypeError):
                continue
        raise ValueError("No valid float in Series")
    return float(str(val).replace(",", ""))


def _match_sector(sector: str, candidates: list, industry: str = "") -> str | None:
    """
    Fuzzy-match a sector/industry name against Damodaran's labels.
    Tries industry-level lookup first (most specific), then sector-level prefix,
    then fuzzy match.
    """
    str_candidates = [str(c) for c in candidates if c is not None and str(c).strip()]

    def _prefix_match(prefix: str) -> str | None:
        for c in str_candidates:
            if c.lower().startswith(prefix.lower()):
                return c
        return None

    # 1. Industry-level exact override
    if industry and industry in INDUSTRY_MAP:
        m = _prefix_match(INDUSTRY_MAP[industry])
        if m:
            return m

    # 2. Sector-level prefix
    prefix = SECTOR_MAP.get(sector, sector)
    m = _prefix_match(prefix)
    if m:
        return m

    # 3. Fuzzy fallback on the original sector name
    matches = difflib.get_close_matches(sector, str_candidates, n=1, cutoff=0.4)
    return matches[0] if matches else None


def get_sector_data(sector: str, industry: str = "", force_refresh: bool = False) -> dict:
    """
    Return Damodaran sector benchmarks for a given sector/industry.

    Args:
        sector: yfinance sector string (e.g. 'Technology')
        industry: yfinance industry string for disambiguation (e.g. 'Software—Application')
        force_refresh: bypass cache

    Returns dict with: wacc, beta_unlevered, pe_sector, ev_ebitda_sector,
                       ev_sales_sector, roe_sector, net_margin_sector, erp, rf
    """
    result = {
        "sector_label": sector,
        "wacc": None,
        "beta_unlevered": None,
        "pe_sector": None,
        "ev_ebitda_sector": None,
        "ev_sales_sector": None,
        "operating_margin_sector": None,
        "roe_sector": None,
        "net_margin_sector": None,
        "erp": get_current_erp(),
        "rf": get_risk_free_rate(),
    }

    try:
        # WACC
        wacc_df = _fetch_and_cache("wacc", force_refresh)
        wacc_df.columns = [str(c) for c in wacc_df.columns]
        sector_col = wacc_df.columns[0]
        wacc_col = [c for c in wacc_df.columns if "wacc" in _col(c) or "cost of capital" in _col(c)]
        if wacc_col:
            matched = _match_sector(sector, wacc_df[sector_col].dropna().tolist(), industry)
            if matched:
                row = wacc_df[wacc_df[sector_col] == matched].iloc[0]
                val = str(row[wacc_col[0]]).replace("%", "").strip()
                try:
                    result["wacc"] = float(val) / 100 if float(val) > 1 else float(val)
                    result["sector_label"] = matched
                except ValueError:
                    pass
    except Exception as e:
        print(f"[damodaran] WACC fetch failed: {e}")

    try:
        # Beta
        beta_df = _fetch_and_cache("beta", force_refresh)
        beta_df.columns = [str(c) for c in beta_df.columns]
        sector_col = beta_df.columns[0]
        # Ch 8, p.200: prefer "Unlevered beta corrected for cash" — this removes
        # the effect of cash drag from the unlevered beta, yielding a purer
        # measure of operating asset risk.  When relevering, use net D/E for consistency.
        beta_col_corrected = [c for c in beta_df.columns
                              if "corrected" in _col(c) and "cash" in _col(c)]
        beta_col_plain = [c for c in beta_df.columns
                          if "unlevered" in _col(c) and "corrected" not in _col(c)]
        beta_col = beta_col_corrected or beta_col_plain
        if beta_col:
            matched = _match_sector(sector, beta_df[sector_col].dropna().tolist(), industry)
            if matched:
                row = beta_df[beta_df[sector_col] == matched].iloc[0]
                try:
                    result["beta_unlevered"] = float(str(row[beta_col[0]]).replace(",", "."))
                except (ValueError, KeyError):
                    pass
    except Exception as e:
        print(f"[damodaran] Beta fetch failed: {e}")

    try:
        # P/E
        pe_df = _fetch_and_cache("pe", force_refresh)
        pe_df.columns = [str(c) for c in pe_df.columns]
        sector_col = pe_df.columns[0]
        pe_col = [c for c in pe_df.columns if "p/e" in _col(c) or "current pe" in _col(c) or "trailing pe" in _col(c)]
        if pe_col:
            matched = _match_sector(sector, pe_df[sector_col].dropna().tolist(), industry)
            if matched:
                row = pe_df[pe_df[sector_col] == matched].iloc[0]
                try:
                    result["pe_sector"] = float(str(row[pe_col[0]]).replace(",", ""))
                except (ValueError, KeyError):
                    pass
    except Exception as e:
        print(f"[damodaran] P/E fetch failed: {e}")

    try:
        # EV multiples — table may have duplicate column names (e.g. two "EV/EBITDA")
        ev_df = _fetch_and_cache("ev", force_refresh)
        ev_df.columns = [str(c) for c in ev_df.columns]
        sector_col = ev_df.columns[0]
        ebitda_col = [c for c in ev_df.columns if "ebitda" in _col(c) and "r&d" not in _col(c)]
        sales_col = [c for c in ev_df.columns if "sales" in _col(c) or "revenue" in _col(c)]
        matched = _match_sector(sector, ev_df[sector_col].dropna().tolist(), industry)
        if matched:
            row = ev_df[ev_df[sector_col] == matched].iloc[0]
            if ebitda_col:
                try:
                    result["ev_ebitda_sector"] = _first_valid_float(row[ebitda_col[0]])
                except (ValueError, KeyError):
                    pass
            if sales_col:
                try:
                    result["ev_sales_sector"] = _first_valid_float(row[sales_col[0]])
                except (ValueError, KeyError):
                    pass
    except Exception as e:
        print(f"[damodaran] EV multiples fetch failed: {e}")

    try:
        # Revenue multiples (EV/Sales, operating margin) from psdata.html
        ps_df = _fetch_and_cache("psdata", force_refresh)
        ps_df.columns = [str(c) for c in ps_df.columns]
        sector_col = ps_df.columns[0]
        ev_sales_col = [c for c in ps_df.columns if "ev/sales" in _col(c)]
        op_margin_col = [c for c in ps_df.columns
                         if "operating margin" in _col(c) or "pre-tax" in _col(c)]
        matched = _match_sector(sector, ps_df[sector_col].dropna().tolist(), industry)
        if matched:
            row = ps_df[ps_df[sector_col] == matched].iloc[0]
            if ev_sales_col and result["ev_sales_sector"] is None:
                try:
                    result["ev_sales_sector"] = _first_valid_float(row[ev_sales_col[0]])
                except (ValueError, KeyError):
                    pass
            if op_margin_col:
                try:
                    val = str(row[op_margin_col[0]]).replace("%", "").strip()
                    result["operating_margin_sector"] = float(val) / 100 if abs(float(val)) > 1 else float(val)
                except (ValueError, KeyError):
                    pass
    except Exception as e:
        print(f"[damodaran] Revenue multiples fetch failed: {e}")

    try:
        # ROE / margins
        roe_df = _fetch_and_cache("roe", force_refresh)
        roe_df.columns = [str(c) for c in roe_df.columns]
        sector_col = roe_df.columns[0]
        roe_col = [c for c in roe_df.columns if "roe" in _col(c)]
        matched = _match_sector(sector, roe_df[sector_col].dropna().tolist(), industry)
        if matched and roe_col:
            row = roe_df[roe_df[sector_col] == matched].iloc[0]
            try:
                val = str(row[roe_col[0]]).replace("%", "").strip()
                result["roe_sector"] = float(val) / 100 if float(val) > 1 else float(val)
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"[damodaran] ROE fetch failed: {e}")

    try:
        # Net margins
        mg_df = _fetch_and_cache("margins", force_refresh)
        mg_df.columns = [str(c) for c in mg_df.columns]
        sector_col = mg_df.columns[0]
        margin_col = [c for c in mg_df.columns if "net margin" in _col(c) or "net profit" in _col(c)]
        matched = _match_sector(sector, mg_df[sector_col].dropna().tolist(), industry)
        if matched and margin_col:
            row = mg_df[mg_df[sector_col] == matched].iloc[0]
            try:
                val = str(row[margin_col[0]]).replace("%", "").strip()
                result["net_margin_sector"] = float(val) / 100 if float(val) > 1 else float(val)
            except (ValueError, KeyError):
                pass
    except Exception as e:
        print(f"[damodaran] Margins fetch failed: {e}")

    return result


def get_current_erp(force_refresh: bool = False) -> float:
    """Fetch Damodaran's current US implied equity risk premium."""
    cache_key = "erp_value"
    cache_path = CACHE_DIR / "erp_value.txt"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not force_refresh and cache_path.exists() and not _is_stale(cache_key, ERP_CACHE_TTL_DAYS):
        try:
            return float(cache_path.read_text().strip())
        except ValueError:
            pass

    try:
        df = _fetch_html_table(DATASETS["erp"])
        # Find the "Implied ERP" column and use the LAST (most recent) non-null value
        erp_col = next(
            (c for c in df.columns if "implied" in str(c).lower() or "erp" in str(c).lower()),
            None,
        )
        if erp_col:
            valid_vals = []
            for val in df[erp_col].dropna():
                s = str(val).replace("%", "").strip()
                try:
                    f = float(s)
                    valid_vals.append(f / 100 if f > 1 else f)
                except ValueError:
                    continue
            if valid_vals:
                erp = valid_vals[-1]  # Most recent year
                cache_path.write_text(str(erp))
                manifest = _load_manifest()
                manifest[cache_key] = time.time()
                _save_manifest(manifest)
                return erp
    except Exception as e:
        print(f"[damodaran] ERP fetch failed: {e}")

    # Fallback: Damodaran's long-run average (~5.5%)
    return 0.055


def get_risk_free_rate() -> float:
    """Get current 10-year US Treasury yield via yfinance (always live, no cache)."""
    try:
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if not hist.empty:
            rate = hist["Close"].iloc[-1]
            return rate / 100  # Convert from percentage
    except Exception as e:
        print(f"[damodaran] Risk-free rate fetch failed: {e}")
    # Fallback
    return 0.043


def get_country_risk_premium(country: str, force_refresh: bool = False) -> dict:
    """
    Fetch country risk premium from Damodaran's ctryprem.html dataset.

    Damodaran Ch 7, p.170: CRP = Country default spread × (σ_equity / σ_bond).
    Damodaran publishes pre-computed CRPs annually.

    Returns dict with:
        country_risk_premium: float (e.g., 0.0324 for Brazil's 3.24%)
        total_erp: float (mature ERP + CRP)
        rating: str (Moody's sovereign rating)
        found: bool
    """
    result = {"country_risk_premium": 0.0, "total_erp": None, "rating": None, "found": False}

    if not country:
        return result

    # Normalize country name
    country_clean = country.strip()

    # US and developed Aaa countries → CRP = 0 (fast path)
    _ZERO_CRP = {"United States", "Germany", "Switzerland", "Norway", "Denmark",
                  "Sweden", "Netherlands", "Luxembourg", "Singapore", "Australia"}
    if country_clean in _ZERO_CRP:
        result["found"] = True
        return result

    try:
        # Country risk premium table is at index 1 (index 0 is header/links)
        cache_path = CACHE_DIR / "ctryprem.csv"
        if not force_refresh and cache_path.exists() and not _is_stale("ctryprem", CACHE_TTL_DAYS):
            df = pd.read_csv(cache_path)
        else:
            df = _fetch_html_table(DATASETS["ctryprem"], table_index=1)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache_path, index=False)
            manifest = _load_manifest()
            manifest["ctryprem"] = time.time()
            _save_manifest(manifest)

        if df is None or df.empty:
            return result

        # Column 0 = Country, 1 = Rating, 3 = Country Risk Premium, 4 = Total ERP
        country_col = df.columns[0]

        # Try exact match first, then fuzzy
        matched_row = None
        for idx, row in df.iterrows():
            name = str(row[country_col]).strip()
            if name.lower() == country_clean.lower():
                matched_row = row
                break

        if matched_row is None:
            # Fuzzy match
            names = [str(r).strip() for r in df[country_col].dropna()]
            close = difflib.get_close_matches(country_clean, names, n=1, cutoff=0.6)
            if close:
                matched_row = df[df[country_col].str.strip() == close[0]].iloc[0]

        if matched_row is not None:
            # Parse CRP (column index 3) — values are in percentage form (e.g., "3.24%")
            crp_str = str(matched_row.iloc[3]).replace("%", "").strip()
            try:
                result["country_risk_premium"] = float(crp_str) / 100
            except ValueError:
                pass

            # Parse total ERP (column index 4) — also percentage form
            erp_str = str(matched_row.iloc[4]).replace("%", "").strip()
            try:
                result["total_erp"] = float(erp_str) / 100
            except ValueError:
                pass

            result["rating"] = str(matched_row.iloc[1]).strip()
            result["found"] = True

    except Exception as e:
        print(f"[damodaran] Country risk premium fetch failed for {country}: {e}")

    return result


if __name__ == "__main__":
    print("Risk-free rate:", get_risk_free_rate())
    print("ERP:", get_current_erp())
    data = get_sector_data("Technology", "Software—Application")
    for k, v in data.items():
        print(f"  {k}: {v}")
