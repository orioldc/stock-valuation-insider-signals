# DCF Formulas — Verbatim Reference
# Source: Damodaran "Investment Valuation" Chapters 7, 8, 9, 10, 11, 12, 15
# These formulas are the ground truth for valuation/dcf.py

## Free Cash Flow Definitions

### FCFF — Free Cash Flow to Firm
```
FCFF = EBIT × (1 - tax_rate) + D&A - Capex - ΔWorking_Capital

Where:
  EBIT = Earnings Before Interest and Taxes (operating income)
  tax_rate = see Ch 10 tax rate section below (marginal for terminal, effective→marginal for projection)
  D&A = Depreciation and Amortization
  Capex = Capital Expenditures (maintenance + growth)
  ΔWorking_Capital = Change in non-cash working capital (positive = use of cash)

Note: Use OPERATING income, NOT net income. Interest is excluded.

R&D Adjustment (Ch 9, pp.232-236):
  For R&D-heavy firms (tech, pharma, biotech), EBIT should be adjusted:
    Adjusted_EBIT = Reported_EBIT + R&D_Expense - R&D_Amortization
  This recapitalizes R&D as an investment. Without this, ROIC is overstated
  and invested capital is understated, producing misleading growth estimates.
  Amortizable life: ~5 years for tech, ~10 years for pharma.
  See damodaran_principles.md Ch 9 for full procedure.

Operating Lease Adjustment (Ch 9, pp.238-239):
  For firms with significant operating leases (retailers, airlines):
    Adjusted_EBIT ≈ Reported_EBIT + PV(lease_commitments) × Pretax_Kd
  Post-ASC 842, most US firms already capitalize leases on balance sheet,
  but the EBIT adjustment may still be needed.
```

### FCFE — Free Cash Flow to Equity
```
FCFE = Net_Income + D&A - Capex - ΔWorking_Capital + Net_Borrowing

Where:
  Net_Borrowing = New debt issued - Debt repaid (net change in debt)
  For stable firms, net_borrowing ≈ 0

Use FCFE when:
  - Firm is a financial institution (regulatory capital = operating input)
  - Firm has a stable, target leverage ratio
  - Dividends are close to FCFE (payout ratio meaningful)
```

### Reinvestment Rate
```
Reinvestment_Rate = (Capex - D&A + ΔWorking_Capital) / NOPAT

Where:
  NOPAT = EBIT × (1 - tax_rate)

Implied Growth Check:
  g_implied = ROIC × Reinvestment_Rate
  This should approximate Stage 1 growth assumption.
  If they diverge by > 30%, review assumptions.
```

---

## Chapter 10: From Earnings to Cash Flows (pp.250–269)

### The Tax Effect (pp.250–257)

#### Marginal vs Effective Tax Rate (pp.250–252)
```
CRITICAL RULE: Use the MARGINAL tax rate, not effective, for FCFF computation.

Reason (p.250): "While we use the effective tax rate to compute the
after-tax operating income for the current year, the marginal tax rate
is used to estimate the after-tax operating income for future years."

The effective tax rate (tax paid / taxable income) often differs from marginal
because of:
  - NOL carryforwards (effective = 0% if firm has large accumulated losses)
  - Tax credits (R&D credits, foreign tax credits)
  - Differences in tax rates across jurisdictions (multinationals)
  - Deferred tax assets/liabilities
  - Tax-exempt income

For projecting FCFF over time:
  Year 1-2: use CURRENT effective tax rate (may be 0% if NOLs)
  Years 3-N: CONVERGE toward marginal tax rate
  Terminal year: MUST use marginal tax rate

US marginal tax rate: 21% (corporate, post-TCJA 2017)
For multinationals: weighted average of marginal rates across jurisdictions
```

#### NOL Treatment (pp.253–256, Illustration 10.1)
```
When a firm has Net Operating Losses (NOLs):
  1. Estimate the accumulated NOL from financial statements
  2. As the firm becomes profitable, NOLs shelter taxable income
  3. Taxes paid = max(0, Taxable_income - NOL_remaining) × marginal_rate
  4. NOL balance decreases each year by the amount of income sheltered
  5. Once NOLs are exhausted, tax rate = full marginal rate

Tesla example approach (from book concepts):
  Year 0: NOL balance = $4.7B, effective tax rate = 0%
  Year 1: Taxable income = $500M → sheltered entirely → tax = 0%, NOL = $4.2B
  Year 2: Taxable income = $800M → sheltered → tax = 0%, NOL = $3.4B
  ...continues until NOL exhausted, then marginal rate applies

Conway example (Illustration 10.1, p.253):
  Effective rate 20% throughout → Value = $2,935M (WRONG — overstates value)
  Marginal rate 40% throughout → Value = $1,957M (WRONG — understates early value)
  Blended (effective→marginal) → intermediate and CORRECT approach

Impact: Using effective tax rate throughout can OVERVALUE the firm by 20-50%.
```

#### R&D Tax Benefits When Capitalizing (p.256)
```
When R&D is capitalized (Ch 9), the tax benefit calculation changes:

Under GAAP: R&D is fully expensed → full tax deduction in current year
Under capitalization: R&D is amortized → deduction spreads over amortizable life

But actual tax treatment follows GAAP (R&D is expensed for tax purposes).
Therefore when adjusting EBIT for R&D capitalization:
  Adjusted EBIT = EBIT + R&D_current - R&D_amortization
  Tax on adjusted EBIT: use marginal rate on the ADJUSTED EBIT

The tax benefit of full R&D expensing is already captured in the firm's
actual tax bill. No double-counting needed.
```

### Reinvestment Needs (pp.258–268)

#### Net Capital Expenditure (pp.258–261)
```
Net_Capex = Capital_Expenditure - Depreciation

For most firms, use the reported capex and depreciation.

Normalization for lumpy capex (p.260):
  - Some firms have highly variable capex (mining, infrastructure, utilities)
  - Averaging capex over 3-5 years gives a better estimate of sustainable reinvestment
  - Alternative: use industry average capex/revenue ratio
  - "Companies may have capital expenditure patterns that are largely
    driven by a few large investments, rather than frequent ones."

The CAGR approach is preferable to single-year:
  Normalized_Capex = Average(Capex_year1, ..., Capex_yearN)
  or: Normalized_Capex = Revenue × Industry_avg_capex_to_revenue_ratio
```

#### Adjusted Net Capex with R&D Capitalization (p.261)
```
When R&D is capitalized (Ch 9 adjustments apply):

  Adjusted_Net_Capex = Capex + R&D_current - Depreciation - R&D_amortization

This is because:
  - R&D_current is effectively a capital expenditure (investing for future)
  - R&D_amortization is the "depreciation" of past R&D investments

Amgen example (p.261):
  Reported: Capex = $1,073M, Depreciation = $1,149M → Net capex = -$76M
  Adjusted: $1,073 + $3,030 - $1,149 - $1,694 = $1,260M
  The adjusted number shows Amgen IS reinvesting significantly (R&D is investment).
```

#### Acquisitions as Capital Expenditure (pp.262–263)
```
For serial acquirers, acquisitions ARE capital expenditure.

Two approaches — MUST be internally consistent:

Approach 1: Include acquisitions in capex
  - Capex = Regular capex + Cash spent on acquisitions
  - Growth rate includes acquisition-driven growth
  - ROIC measured on total capital including goodwill

Approach 2: Exclude acquisitions from capex
  - Capex = Regular (organic) capex only
  - Growth rate = ORGANIC growth only (strip out acquired revenue)
  - ROIC measured on organic capital only

"The key is consistency — if you count the spending on acquisitions
as part of capital expenditure, you have to count the cash flows
resulting from the acquisitions as well."

Cisco example (p.263): $9.79B acquisitions over 5 years
  Organic net capex = $2.47B → reinvestment rate = 28.29%
  Including acquisitions = $12.26B → reinvestment rate = 141%
  The higher rate is more honest if Cisco's growth depends on acquisitions.

Normalizing lumpy acquisitions:
  Average acquisition spending over 5-10 years
  Include in reinvestment rate calculation
```

#### Noncash Working Capital (pp.264–268)
```
Definition (p.264):
  Noncash_WC = (Current_Assets - Cash) - (Current_Liabilities - Short_Term_Debt)

WHY exclude cash: Cash is not an operating asset (it earns risk-free return).
WHY exclude short-term debt: It is a financing item, not an operating obligation.

Change in noncash WC:
  Δ_Noncash_WC = Noncash_WC_this_year - Noncash_WC_last_year
  Positive Δ = use of cash (investment)
  Negative Δ = source of cash (release of capital)

Five approaches to estimating WC changes (p.265):
  1. Use actual change from most recent year → volatile, unreliable
  2. Average change over last 3-5 years → better, but still noisy
  3. WC as % of revenues (PREFERRED) → project forward with revenue growth
  4. Industry average WC/revenue ratio → good for terminal year
  5. Build up from components (DSO, DIO, DPO) → most detailed, data-intensive

Best practice for DCF (p.266):
  - Stage 1: WC as % of revenue, using firm's own recent average
  - Terminal year: WC as % of revenue, using INDUSTRY AVERAGE
    (firm converges to industry norms in stable growth)
  - For negative WC firms (prepaid business models like Amazon, SaaS):
    negative WC is valid and represents a competitive advantage
  - For firms where WC % is declining: may normalize toward 0%
    but don't project continued improvement indefinitely

Negative WC change warning (p.267-268):
  - A negative change (WC declining) generates cash flow
  - This is valid in the short term but NOT sustainable at the same rate
  - As % of revenue stabilizes, WC changes approach zero
  - In terminal year: Δ_WC = g_terminal × Noncash_WC_terminal_year
    (WC grows at the same rate as revenue)
```

---

## Riskless Rates and Risk Premiums (Chapter 7, pp.154–181)

### The Risk-Free Rate (pp.154–159)
```
Requirements for an asset to be risk-free (p.154):
  1. No default risk — only government securities qualify
  2. No reinvestment risk — duration must match the cash flow horizon

Practical rule (p.155):
  Use the 10-YEAR government bond rate in the CURRENCY of the cash flows.
  - US company, USD cash flows → 10Y US Treasury yield
  - European company, EUR cash flows → 10Y German Bund yield
  - For short-term analysis (< 1 year) → T-bill rate

The Consistency Principle (p.156):
  The risk-free rate MUST match the currency of the cash flows.
  - If CFs in USD → Rf in USD (US Treasury)
  - If CFs in GBP → Rf in GBP (UK Gilt)
  Differences in Rf across currencies reflect EXPECTED INFLATION, not
  real return differences. Using different currencies yields the SAME
  value if both CFs and discount rate are consistent.

Real vs Nominal (p.156):
  - Nominal Rf → use with nominal cash flows (DEFAULT for our model)
  - Real Rf → use with real cash flows (rare; TIPS yield ≈ 2%)
  - Under high/unstable inflation: real valuation is better

Risk-Free Rate for Non-US Countries (pp.157–159):
  Option 1: Local currency government bond − sovereign default spread
    Example (India, p.157): Gov bond yield 8.00%, Moody's Ba2, spread 2.40%
    Risk-free rate in INR = 8.00% − 2.40% = 5.60%

  Option 2: Inflation conversion from US rate
    Rf_local = (1 + Rf_USD) × (1 + inflation_local) / (1 + inflation_US) − 1
    Example (Indonesia): Rf_USD=4%, inflation_Indo=11%, inflation_US=2%
    Rf_INR = 1.04 × 1.11 / 1.02 − 1 = 13.18%

  Option 3: CDS market
    Rf_local = Local gov bond rate − CDS spread
    Example (Brazil): Gov bond 8.25% − CDS 0.75% = 7.50%

  Option 4: Build-up
    Rf = Expected real return + Expected inflation
```

### Equity Risk Premium — ERP (pp.160–176)

#### Historical Risk Premium (pp.160–163)
```
Table 7.3 — Historical Risk Premiums for the United States (1928–2010):

| Period    | Stocks − T-Bonds (Arithmetic) | Stocks − T-Bonds (Geometric) |
|-----------|-------------------------------|------------------------------|
| 1928–2010 | 5.67%                         | 4.31%                        |
| 1960–2010 | 4.44%                         | 3.09%                        |
| 2000–2010 | −0.79%                        | −4.11%                       |

ALWAYS use GEOMETRIC average (p.162):
  Geometric = (Value_end / Value_start)^(1/N) − 1
  Arithmetic overstates expected compound returns.

Standard error of estimates (Table 7.2, p.162):
  SE = σ_annual / √N ≈ 20% / √N
  5 years: 8.94%  |  10 years: 6.32%  |  25 years: 4.00%  |  50 years: 2.83%
  → Historical estimates have HUGE uncertainty bands.

Problems with historical approach:
  - Results vary wildly by time period
  - Survivorship bias (US market was unusually successful)
  - Standard errors are large even over 50 years
  - Backward-looking: doesn't reflect current market conditions
```

#### Implied Equity Risk Premium (pp.173–176) — PREFERRED METHOD
```
Damodaran's approach: Solve for the discount rate (r) that makes the
current index level = PV of expected cash flows:

  Level of index = Σ [CF_t / (1+r)^t]   →  solve for r
  Implied ERP = r − Rf

Where CF = expected dividends + buybacks for the index.

S&P 500 example (January 2011, p.174):
  Index = 1257.64, cash flows (div+buybacks) starting at $57.72
  Growth: 6.95% for 5 years, then 3.29% (T-bond rate) in perpetuity
  Solving: required return r = 8.49%, implied ERP = 8.49% − 3.50% = 4.99%

Key properties of implied ERP:
  - Market-driven and CURRENT (not backward-looking)
  - Changes daily as index level and cash flow estimates change
  - Average implied ERP (1960–2010): 3.95%
  - Mean-reverts: spikes during crises (6.4% in 2008), drops in booms

Historical vs Implied — Decision Rule (p.176):
  "If you believe that the market is RIGHT in the aggregate,
   you should use the CURRENT implied equity risk premium."
  "If you believe that markets often make mistakes in the aggregate,
   you should go with the HISTORICAL premium."

  THIS BOOK USES THE CURRENT IMPLIED ERP in its valuations.
  → Our model correctly uses Damodaran's published implied ERP. ✓
```

### Country Risk Premium — CRP (pp.166–173)

#### Should There Be a CRP? (pp.166–167)
```
For US-only companies: CRP = 0. The base ERP already captures US market risk.

For non-US companies or companies with international operations:
  Country risk IS priced in practice — markets are NOT fully integrated.
  Even globally diversified investors face barriers: political risk,
  capital controls, information asymmetry, currency risk.

  Empirical evidence: correlations across markets are positive and
  increasing, but not 1.0. Country risk is NOT fully diversifiable.
```

#### Measuring Country Risk Premium (pp.168–171)

##### Approach 1: Sovereign Default Spread (simplest)
```
CRP = Sovereign default spread (from Moody's/S&P rating)

Table 7.5 — Sovereign Ratings and Default Spreads (January 2011):

| Country      | Rating (Moody's) | Default Spread | 10yr CDS |
|--------------|-------------------|----------------|----------|
| Argentina    | B3                | 6.00%          | 6.62%    |
| Brazil       | Baa3              | 2.00%          | 1.59%    |
| Chile        | Aa3               | 0.70%          | 0.99%    |
| China        | Aa3               | 0.70%          | N/A      |
| India        | Ba1               | 2.40%          | 2.06%    |
| Indonesia    | Ba2               | 2.75%          | N/A      |
| Malaysia     | A3                | 1.15%          | 0.92%    |
| Peru         | Baa3              | 2.00%          | 1.54%    |
| Poland       | A2                | 1.00%          | 1.68%    |
| Russia       | Baa1              | 1.50%          | 1.65%    |
| South Africa | A3                | 1.15%          | N/A      |
| Turkey       | Ba2               | 2.75%          | 2.01%    |

NOTE: Damodaran publishes UPDATED country risk premiums annually at:
  pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html
  Our model fetches from this source.
```

##### Approach 2: Relative Standard Deviations (p.170)
```
CRP = Sovereign default spread × (σ_equity / σ_bond)

Where:
  σ_equity = annualized std dev of country's equity market
  σ_bond = annualized std dev of country's dollar-denominated bonds

Brazil example (p.170):
  Default spread = 2.00%, σ_equity (Bovespa) = 17.65%, σ_bond = 7.32%
  CRP = 2.00% × (17.65% / 7.32%) = 4.82%

This approach scales up the default spread because equity markets are
more volatile than bond markets — equity investors bear more risk.
```

##### Approach 3: Implied Equity Risk Premium by Country
```
Use Damodaran's published country ERPs (updated annually).
These are already computed using the relative standard deviation approach.

Source: pages.stern.nyu.edu/~adamodar/
```

#### Integrating CRP into Cost of Equity (p.172)
```
Damodaran's PREFERRED approach (p.172):

  Ke = Rf + β × (Mature market ERP) + λ × CRP

Where:
  Rf = US Treasury 10Y yield (base risk-free rate)
  β = bottom-up beta (relevered)
  Mature market ERP = Damodaran's implied US ERP
  λ = company's exposure to country risk
  CRP = country risk premium

Three views on λ:
  1. λ = 1 for all companies in the country (simple, adds full CRP)
  2. λ = β (country risk proportional to market risk)
  3. λ = f(revenue mix) — varies by company based on where revenues come from

Practical rule: For a company domiciled in country X:
  - If ≥80% revenue from that country: λ = 1.0
  - If multinational: λ = % revenue from emerging markets
  - For US companies with all US revenue: λ = 0 (no CRP)

Petrobras example (p.172):
  Rf = 3.50%, β = 0.80, US ERP = 4.31%, Brazil CRP = 4.82%, λ = 0.50
  Ke = 3.50% + 0.80 × 4.31% + 0.50 × 4.82% = 9.36%

⚠️ DANGER OF DOUBLE COUNTING (p.168):
  If beta is estimated against the LOCAL country index (e.g., Bovespa),
  country risk is ALREADY embedded in beta. Do NOT add CRP on top.
  Use beta against a GLOBAL or US market index when adding CRP separately.

  Our model uses Damodaran's sector unlevered betas (US/global) → safe to
  add CRP without double counting. ✓
```

---

## Estimating Risk Parameters and Costs of Financing (Chapter 8, pp.182–223)

### Bottom-Up Betas — The Preferred Approach (pp.195–203)

#### Why Bottom-Up Over Regression Betas (pp.183–195)
```
Regression beta problems (pp.183–195):
  - High standard errors: Boeing's SE = 0.23, 95% CI = [0.10, 1.02]
  - Low R²: R² of 19% means 81% of risk is firm-specific (p.187)
  - Index choice matters: Titan Cement β = 0.93 (Athens), 0.63 (MSCI Europe),
    0.08 (MSCI Global) — wildly different results (pp.190-191)
  - Bloomberg/services "adjust" betas toward 1.0: Adj β = Raw β × 0.67 + 1.0 × 0.33
    This is arbitrary and not particularly useful (p.187)
  - Regression reflects PAST risk; may not reflect current business mix

Figure 8.7 (p.193): Distribution of SE on beta for US firms (2008-2010):
  ~50% of firms have SE > 0.20, making individual regression betas unreliable.

Bottom-up betas are ALWAYS preferable because:
  1. Average across many firms → lower standard error (SE/√n)
  2. Forward-looking: can adjust for business changes
  3. Don't need historical stock price data (works for private firms)
```

#### The 5-Step Bottom-Up Beta Process (p.197)
```
Step 1: Identify the business(es) the firm operates in
Step 2: Find publicly traded firms in each business and obtain their
        regression betas
Step 3: Estimate the average unlevered beta for each business by
        unlevering: β_u = β_levered / [1 + (1 - t)(D/E)]
Step 4: Compute a weighted average unlevered beta for the firm:
        β_unlevered_firm = Σ(β_unlevered_i × Value_weight_i)
        Use revenues or firm value as weights.
Step 5: Relever at the firm's current D/E and tax rate:
        β_levered = β_unlevered × [1 + (1 - t)(D/E)]

Boeing example (pp.202-203):
  Two segments: Commercial aircraft (70.39%) and ISDS/defense (29.61%)
  Commercial aircraft β_u = 0.91, ISDS β_u = 0.80
  Weighted β_u = 0.91 × 0.7039 + 0.80 × 0.2961 = 0.8774
  D/E ratio = $7.86B / $55.2B = 0.1424, tax = 35%
  β_levered = 0.8774 × [1 + (1-.35)(0.1424)] = 0.9586

NOTE: Damodaran publishes sector unlevered betas on his website. Our model
uses these directly — this IS the bottom-up approach (Step 3 already done).
```

#### Cash and Betas (p.200)
```
Cash has a beta of ZERO. When a sector has large cash balances, the
unlevered beta of the operating assets is HIGHER than reported.

β_unlevered_corrected = β_unlevered / (1 - Cash/Firm_Value)

Example (p.200): Entertainment sector
  Average β_u = 1.30, unlevered at D/E=0.50: β = 1.00
  Cash = 10% of firm value
  β_corrected = 1.00 / (1 - 0.10) = 1.11

Damodaran's website provides BOTH columns:
  - "Unlevered beta" — includes cash effect
  - "Unlevered beta corrected for cash" — operating assets only ← USE THIS

When using cash-corrected beta, use NET DEBT for D/E ratio:
  Net D/E = (Total Debt - Cash) / Market Cap
  This is consistent: cash is excluded from both beta and leverage.
```

### Determinants of Betas (pp.184-185)
```
Three variables determine beta:
  1. Type of business: Cyclical firms (auto, housing) > Stable (food, utility)
     Discretionary products > Necessity products
  2. Degree of operating leverage: High fixed costs → more volatile OI → higher β
     Operating leverage = % change in OI / % change in Sales
  3. Degree of financial leverage: β_levered = β_u × [1 + (1-t)(D/E)]
     As D/E increases, beta increases proportionally

Size, growth, and betas (p.195):
  - Smaller firms appear riskier (higher betas) but this is partly operating leverage
  - High growth firms tend to have higher betas (more uncertainty)
  - As firms mature: beta → 1.0, growth → GDP growth, ROC → WACC
```

### Lambda (λ) — Country Risk Exposure (pp.206-208)
```
λ measures a company's ACTUAL exposure to country risk (Ch 7 introduced CRP).

Three approaches to estimate λ:
  1. Revenue breakdown (simplest):
     λ = Proportion of revenues from risky country / Average for local firms
     Example — Embraer (p.208):
       9% of revenues from Brazil, average Brazilian firm = 60%
       λ = 0.09/0.60 = 0.15

  2. For emerging market companies with large foreign revenues:
     λ < 1.0 (less exposed than average domestic firm)
     Example: Nestlé, Coca-Cola with global revenues → low λ

  3. For developed market companies with emerging market exposure:
     λ > 0 even though domiciled in US/Europe
     Example: Mining company with operations in Africa → add partial CRP

Practical rules for our model:
  - US company, all domestic revenue: λ = 0 (no CRP)
  - Company domiciled in emerging market, mostly domestic: λ = 1.0 (default)
  - Multinational from emerging market with <50% domestic: λ = domestic revenue %
  - Our model uses λ = 1.0 as default for non-US companies (conservative)
```

### Small Firm Premium (pp.210-211)
```
Ke_small = Rf + β × ERP + Small_cap_premium

Evidence: Small stocks have earned ~4% more annually than large stocks.

CAUTION (4 important caveats, p.210-211):
  1. Higher betas of small firms may ALREADY capture some of the premium
  2. Operating leverage is typically higher for small firms → in beta
  3. Standard error on small cap premium is ~2% (not precisely measured)
  4. Using both a higher beta AND a small cap premium may DOUBLE-COUNT

Damodaran's view: "The true small cap premium can be 0 percent or perhaps
2 percent... your company's beta should be 8 percent" (paraphrased, p.210).

Our model: 2.5% for market cap < $2B. This is conservative and acceptable
given the caveats about double counting.
```

### Synthetic Rating Tables — Market-Cap Dependent (pp.212-213)

#### Table 8.1 — Large Market Cap Firms (> $5 billion)
```
| Interest Coverage Ratio | Rating | Default Spread |
|-------------------------|--------|----------------|
| > 12.5                  | AAA    | 0.50%          |
| 9.50 – 12.50            | AA     | 0.65%          |
| 7.50 – 9.50             | A+     | 0.85%          |
| 6.00 – 7.50             | A      | 1.00%          |
| 4.50 – 6.00             | A-     | 1.10%          |
| 3.50 – 4.50             | BBB    | 1.60%          |
| 3.00 – 3.50             | BB+    | 3.35%          |
| 2.50 – 3.00             | BB     | 3.75%          |
| 2.00 – 2.50             | B+     | 5.00%          |
| 1.50 – 2.00             | B      | 5.25%          |
| 1.25 – 1.50             | B-     | 5.75%          |
| 0.80 – 1.25             | CCC    | 8.00%          |
| 0.65 – 0.80             | CC     | 10.00%         |
| 0.20 – 0.65             | C      | 12.00%         |
| < 0.20                  | D      | 15.00%         |

Source: Capital IQ, BondsOnline.com (early 2011)
```

#### Table 8.2 — Small Market Cap Firms (< $5 billion)
```
| Interest Coverage Ratio | Rating | Default Spread |
|-------------------------|--------|----------------|
| > 8.50                  | AAA    | 0.50%          |
| 6.50 – 8.50             | AA     | 0.65%          |
| 5.50 – 6.50             | A+     | 0.85%          |
| 4.25 – 5.50             | A      | 1.10%          |
| 3.00 – 4.25             | A-     | 1.60%          |
| 2.50 – 3.00             | BBB    | 3.35%          |
| 2.25 – 2.50             | BB+    | 3.75%          |
| 2.00 – 2.25             | BB     | 5.00%          |
| 1.75 – 2.00             | B+     | 5.25%          |
| 1.50 – 1.75             | B      | 5.75%          |
| 1.25 – 1.50             | B-     | 8.00%          |
| 0.80 – 1.25             | CCC    | 10.00%         |
| 0.65 – 0.80             | CC     | 12.00%         |
| 0.20 – 0.65             | C      | 14.00%         |
| < 0.20                  | D      | 14.00%         |

Small firms need LOWER coverage for the same rating (thresholds are lower)
but pay HIGHER spreads for the same rating (more risk per dollar of coverage).

Modified coverage for firms with operating leases (p.217):
  Modified IC = (EBIT + Operating lease expense) / (Interest + Lease expense)
```

### Cost of Debt for Emerging Market Firms (pp.214-216)
```
Cost of debt = Rf + Company default spread + Country default spread

Embraer example (p.216):
  Synthetic rating: BBB (coverage ratio → Table 8.1)
  Company default spread: 1.60%
  Country default spread (Brazil): 2.00%
  Pretax Kd in USD = 3.8% + 1.60% + 2.00% = 7.30%
  After-tax = 7.30% × (1 - 0.34) = 4.82%

For US companies: no country spread in Kd.
For non-US companies: add the country's default spread to Kd.
```

### Cost of Preferred Stock (p.217)
```
Kp = Preferred_Dividend_per_share / Market_Price_per_share

Example — Ford Motor (p.217):
  Annual dividend = $1.875, market price = $26.475
  Kp = $1.875 / $26.475 = 7.08%

Key properties:
  - Preferred dividends are NOT tax deductible (unlike debt interest)
  - Preferred stock is safer than common equity but riskier than debt
  - Should command a LOWER cost than equity, HIGHER than debt
```

### Gross Debt versus Net Debt (pp.220-221)
```
Gross debt = All interest-bearing debt outstanding
Net debt = Gross debt − Cash and marketable securities

When to use net debt:
  - Firms with LARGE cash balances (especially tech companies)
  - Cash can be used to pay down debt → net exposure is lower
  - Use net debt for D/E when using cash-corrected beta (consistent)

When to use gross debt:
  - Cash is restricted or earmarked for specific purposes
  - Cash is held overseas with tax repatriation costs
  - BB-rated or worse firms where cash provides a liquidity buffer

CRITICAL (p.220): If you use net debt ratios, do NOT add back cash
to equity value at the end. It's already excluded from the debt.

Boeing example (p.221):
  Gross debt = $7,847M, Cash = $4,437M, Net debt = -$1,422/12,729 = negative
  With net debt approach, Boeing's D/(D+E) goes from 12.45% to negative
  → weight on equity > 100%, but the math still works correctly.

Our model: Uses gross debt for total_debt but yfinance cash for cash.
When using cash-corrected beta, we should use net D/E for relevering.
```

### Market Value Weights (pp.218-221)
```
Cost of capital = Ke × [E/(D+E+PS)] + Kd × [D/(D+E+PS)] + Kp × [PS/(D+E+PS)]

Where E, D, PS are market values.

For debt: market value ≈ book value if interest rates haven't changed much
  Market value of debt can be estimated as a coupon bond:
  MV = Coupon × [1 - 1/(1+r)^n] / r + Face / (1+r)^n

For equity: market value = current stock price × shares outstanding

Circularity problem (p.221): WACC depends on weights which depend on
market values which depend on WACC. Solution: iterate, or use current
market values (they embed investors' assessment of risk/value).
```

---

## WACC — Weighted Average Cost of Capital

```
WACC = (E/V) × Ke + (D/V) × Kd × (1 - tax_rate)

Where:
  E = Market Value of Equity (market cap)
  D = Market Value of Debt (use book value as proxy)
  V = E + D (total capital)
  Ke = Cost of Equity
  Kd = Pre-tax Cost of Debt
  tax_rate = effective marginal tax rate

CRITICAL: Use MARKET VALUE weights, not book value weights.
```

### Cost of Equity — CAPM
```
Ke = Rf + β_relevered × ERP + size_premium + λ × CRP

Where:
  Rf = Risk-free rate (10-year US Treasury yield)
  β_relevered = Company's beta relevered at its own D/E
  ERP = Equity Risk Premium (Damodaran's implied ERP for US)
  size_premium = 0.025 (2.5%) for companies with market cap < $2B
  λ = country risk exposure (0 for US companies, 1.0 default for non-US)
  CRP = country risk premium (0 for US / developed markets)

Beta Relevering (Hamada equation):
  β_relevered = β_unlevered_corrected × [1 + (1 - tax_rate) × (Net_D/E)]

  Where:
    β_unlevered_corrected = Damodaran's sector "unlevered beta corrected for cash"
      (Ch 8, p.200: removes effect of cash holdings on sector beta)
    Net_D/E = max(0, Total_Debt - Cash) / Market_Cap
      (Ch 8, p.220: consistent with cash-corrected beta)

  NOTE: Using cash-corrected beta with net D/E is consistent because both
  exclude cash from the calculation. The alternative (uncorrected beta with
  gross D/E) gives similar but less precise results.
```

### Cost of Debt
```
For US companies:
  Kd_pretax = Rf + Company_default_spread

For non-US companies (Ch 8, p.216):
  Kd_pretax = Rf + Company_default_spread + Country_default_spread

Company default spread: from synthetic rating (Tables 8.1/8.2)
Country default spread: from Damodaran's country risk data (sovereign rating)

Kd_aftertax = Kd_pretax × (1 - tax_rate)

Actual Kd from financials:
  Kd_actual = Interest_Expense_TTM / Total_Debt
  Use actual if reasonable (within 2.5x of synthetic); else use synthetic.
```

---

## Growth Rate Estimation (Chapter 11: Estimating Growth, pp.271–302)

### Core Principle (p.283)
With both historical and analyst estimates, growth is an **exogenous** variable.
The soundest approach is to make growth **endogenous** — tied to how much the firm
reinvests and what return it earns on those reinvestments.

---

### A. Growth in EQUITY Earnings (for FCFE models)

#### Simple version: Growth in EPS (p.286)
```
g_EPS = Retention ratio × ROE = b × ROE

Where:
  b = Retention ratio = 1 - Dividend payout ratio
  ROE = Net Income / Book value of equity (end of prior year)

Assumption: firm does NOT issue new equity; only source of new equity = retained earnings.
```

#### More accurate: Growth in Net Income (p.287)
```
g_NI = Equity reinvestment rate × ROE

Where:
  Equity reinvested = Capex - Depreciation + ΔWorking Capital - (New debt issued - Debt repaid)
  Equity reinvestment rate = Equity reinvested / Net Income

NOTE: Unlike retention ratio, equity reinvestment rate can EXCEED 100%
(firm raising new equity to fund growth). Can also be negative.
```

#### With changing ROE (p.280)
```
g = ROE_t × Retention ratio + (ROE_t - ROE_{t-1}) / ROE_{t-1}

The second term is a one-time boost (or drag) from the change in return on equity.
After the change year, growth reverts to the simple ROE × retention formula.
```

#### Determinants of ROE (p.288)
```
ROE = ROC + D/E × [ROC - i(1-t)]

Where:
  ROC = EBIT(1-t) / (BV of equity + BV of debt - Cash)
  D/E = BV of debt / BV of equity
  i = Interest expense / BV of debt
  t = Tax rate

WARNING: If ROC < book interest rate i, leverage REDUCES ROE.
High ROE caused by high D/E, low effective tax, or nonoperating profits may not be sustainable.
```

---

### B. Growth in OPERATING Income (for FCFF models — Chapter 11, pp.280-300)

Three scenarios depending on the firm's return on capital:

#### Scenario 1: Stable Return on Capital (p.280)
```
g_EBIT = Reinvestment rate × Return on capital

Reinvestment rate = (Capex - Depreciation + Δ Noncash WC) / [EBIT × (1 - tax rate)]
Return on capital (ROIC) = EBIT(1-t) / (BV Equity + BV Debt - Cash)

Both measures should be FORWARD-LOOKING, not just trailing.
```

**Reinvestment rate notes (p.281):**
- Use average over 3-5 years, not just current year (too volatile, especially for firms with lumpy capex or acquisitions)
- As firms grow and mature, reinvestment needs tend to decrease
- R&D expenses should be capitalized and treated as part of capital expenditures
- Industry average reinvestment rate can substitute for highly volatile firm-level numbers

**When reinvestment rate is negative (p.283):**
- If temporary (lumpy capex/volatile WC): replace with historical or industry average
- If firm grows through acquisitions: failure to incorporate acquisitions into capex
- If firm overinvested in past: can live off past investment → growth from improving ROIC
- If firm is liquidating: use the negative reinvestment rate → negative expected growth

**Return on capital notes (p.281):**
- BV of capital may not reflect true capital invested (historical cost, depreciation policy)
- Current ROIC on existing assets may differ from marginal ROIC on new investments
- If current ROIC >> industry average → forecast lower ROIC (competition will erode)
- ROIC > cost of capital = earning excess returns = competitive advantage
- High excess returns for long periods → permanent competitive advantage

**Cross-check: Implied ROIC must converge to sustainable levels by terminal year.**

#### Scenario 2: Positive and Changing Return on Capital (p.284)
```
g = ROC_t × Reinvestment rate + (ROC_t - ROC_{t-1}) / ROC_{t-1}

Two types of candidates:
  - Firms with poor ROIC improving efficiency → growth >> reinvestment rate × ROC
    (ROC going from 1% to 2% doubles earnings = 100% growth)
  - Firms with very high ROIC expecting competition to erode returns
```

Multi-year changing ROC (Motorola example, p.285):
```
Expected growth rate = ROC_marginal × Reinvestment rate_current
                     + {[1 + (ROC_future - ROC_current)/ROC_current]^(1/years) - 1}
```

#### Scenario 3: Negative Return on Capital (p.285-300)
For firms losing money:
1. **Project revenue growth** — growth rate decreases as revenue increases
   - Five rules (p.285-286):
     a. Growth rate DECREASES as revenues increase (law of large numbers)
     b. Compounded growth rates are deceptive (20% for 10yr = 6x; 40% for 10yr = ~30x)
     c. Track dollar revenues vs overall market size — don't project >90% market share
     d. Revenue growth and operating margin assumptions must be internally consistent
     e. Subjective judgments about competition, capacity, marketing needed
   - Top-down approach (p.296): estimate target market share at maturity,
     compute implied CAGR to get there

2. **Estimate operating margins over time** — converge to sector average
   - Target margin = average pretax operating margin of established firms in the sector
   - Margins improve faster in earlier years, taper off approaching maturity
   - Two margin convergence formulas (p.297):
     - Faster convergence: Next year margin = (Current margin + Target margin) / 2
     - Slower convergence: Next year margin = Current margin + (Target margin - Current margin) / 3

3. **Link reinvestment to revenue via sales-to-capital ratio** (p.299)
   ```
   Reinvestment in year t = Δ Revenue_t / Sales-to-capital ratio

   Where:
     Sales-to-capital ratio = Δ Revenue / Reinvestment (measured historically or industry avg)
     Lower ratio → more reinvestment needed → less cash flow
     Higher ratio → less reinvestment needed → more cash flow
   ```

4. **Cross-check: compute implied ROIC each year** (p.300)
   - ROIC should converge to sustainable levels (industry average or cost of capital) by terminal year
   - If projected ROIC is wildly above industry average → sales-to-capital ratio is too high

---

### C. Historical Growth (Chapter 11, pp.272–281)

#### Arithmetic vs Geometric Average (p.272)
```
Arithmetic average = (g1 + g2 + ... + gN) / N
Geometric average  = (Value_end / Value_start)^(1/N) - 1

ALWAYS use GEOMETRIC average. Arithmetic average overstates growth
when earnings are volatile because it ignores compounding effects.

Motorola illustration (p.273):
  Revenue: arithmetic = 7.08%, geometric = 6.82%  (close — revenue is stable)
  EBIT:    arithmetic = 10.89%, geometric = 5.39%  (HUGE gap — earnings volatile)
```

#### Linear vs Log-Linear Regression (p.274)
```
Linear model:     EPS_t = a + b × t        → absolute growth ($/year)
Log-linear model: ln(EPS_t) = a + b × t    → percentage growth (b ≈ g)

Log-linear is better for percentage growth rates.
R² of the regression measures how well growth fits a trend.
Low R² → growth rate is unreliable as a predictor.
```

#### Negative Earnings (p.275)
```
When base-year earnings are negative:
  - Growth rate is MEANINGLESS (cannot compute % change from negative base)
  - Do NOT use historical earnings growth for these firms
  - Fall back to revenue growth (which is almost always positive)
  - Or use fundamental growth (reinvestment × ROIC) once earnings normalize
```

#### Historical Growth as a Predictor (pp.276–278)
```
Key findings from research (Little 1960 "Higgledy-Piggledy Growth"):
  - Little evidence that past earnings growth predicts future earnings growth
  - Correlation between growth rates in consecutive 5-year periods is low
  - Revenue growth is MORE persistent and predictable than earnings growth
    (Figure 11.3: revenue correlation across periods >> earnings correlation)
  - The correlation is LOWER for smaller firms (more volatile)

Implication: historical earnings growth should get LESS weight for:
  - Small firms
  - Firms with volatile earnings (high std dev)
  - Long forecast horizons (> 2-3 years)
```

#### Firm Size Effects (pp.278–280)
```
As firms grow larger, it becomes harder to sustain high growth rates.
This is the "law of large numbers" — a $100B firm growing at 25%
must add $25B in revenue per year, which is harder than a $1B firm
adding $250M.

Cisco illustration (p.279):
  1990–1999 (small → large): Revenue CAGR = 83.78%
  2000–2010 (large → mega):  Revenue CAGR = 7.78%

Implication: scale down historical growth for large firms.
The market-cap-based growth caps in Section E enforce this.
```

#### Guidelines for Using Historical Growth (p.280)
```
1. Use REVENUE growth, not earnings growth, as the historical input
   (more stable, more persistent across periods)
2. Use GEOMETRIC average, not arithmetic
3. Use 3–5 year period (long enough to smooth, short enough to be relevant)
4. For high-growth firms: look at YEAR-OVER-YEAR trends (is growth decelerating?)
   — if YoY growth is declining, use recent rate, not full-period average
5. Assign LESS weight to historical growth when:
   - Firm has undergone significant restructuring
   - Industry structure has changed
   - Firm size has changed dramatically
   - Earnings are highly volatile (low R² in regression)
6. Historical growth is only useful for NEAR-TERM projections (1–3 years)
   — for longer horizons, fundamental growth is more reliable
```

### D. Analyst Estimates (Chapter 11, pp.282–285)

#### What Analysts Add Beyond Historical Data (p.282)
```
Analysts incorporate 5 types of information:
  1. Firm-specific information announced since last earnings report
  2. Macroeconomic information affecting future growth
  3. Information from competitors' earnings reports
  4. Private information from management or industry contacts
  5. Public information not yet reflected in trailing numbers
```

#### Quality of Analyst Forecasts (pp.282–283)
```
Research findings:
  - Analyst forecasts are SUPERIOR to time-series models
    for SHORT-TERM (1–2 quarters ahead)
  - This advantage DETERIORATES for longer horizons (3–5 years)
  - For 5-year forecasts, analyst accuracy is NOT much better than naive models
  - Analysts tend to be OVERLY OPTIMISTIC on average

Implication for our model:
  - Use analyst estimates primarily as a SHORT-TERM input
  - For Stage 1 growth (5–10 year horizon), weight fundamental growth > analyst
  - Check analyst consensus against fundamental growth for consistency
```

#### Four Factors for Weighting Analyst Estimates (p.283)
```
Give MORE weight to analyst estimates when:
  1. Recent firm-specific information exists that historical data doesn't capture
     (e.g., new product launch, acquisition, restructuring announced)
  2. MORE analysts cover the firm (broader information aggregation)
  3. LESS disagreement among analysts (lower std dev of estimates)
     — high disagreement = high uncertainty = less reliable consensus
  4. Higher quality analysts (track record, industry specialization)

Give LESS weight when:
  - Few analysts (< 3) cover the firm
  - High standard deviation in estimates
  - Forecasts are for distant future (> 2 years)
  - Analysts haven't updated estimates recently
```

#### EPS Growth vs Operating Income Growth (p.284)
```
WARNING: Analyst consensus typically forecasts EPS growth, not EBIT growth.

EPS growth ≠ Operating income growth because:
  - EPS includes interest expense, taxes, share buybacks
  - A firm can grow EPS faster than EBIT by increasing leverage or buying back shares
  - Or EPS can grow slower if dilution occurs

For FCFF models: do NOT directly use analyst EPS growth as the operating
income growth rate. Either:
  - Find analyst operating income forecasts specifically, or
  - Adjust EPS growth for expected changes in leverage and share count, or
  - Use fundamental growth (reinvestment × ROIC) instead
```

#### Reconciling Analyst vs Fundamental Growth (p.285)
```
When analyst estimates diverge significantly from fundamental growth:
  - If fundamental > analyst: analysts may be too pessimistic, or the firm's
    reinvestment/ROIC assumptions may be too aggressive
  - If analyst > fundamental: analysts may be forecasting efficiency gains
    (rising ROIC) or acquisition-driven growth not captured in organic reinvestment
  - INVESTIGATE the divergence — don't just average blindly
  - The divergence itself is informative: it signals where assumptions differ
```

### E. Reconciliation
```
For FCFF:
  g_fundamental = Reinvestment rate × ROIC   (NOT ROE × retention)
For FCFE:
  g_fundamental = Equity reinvestment rate × ROE  (or simple: retention × ROE)

Stage 1 Growth = median(g_fundamental, g_historical, g_analyst)
Cap based on market cap (law of large numbers):
  Megacap (>$200B): 12%
  Large cap ($50B–$200B): 18%
  Mid cap ($10B–$50B): 25%
  Small/micro: 35%
```

---

## Terminal Value (Chapter 12, pp.304–321)

### Three Approaches to Terminal Value (p.304–306)
```
Value of firm = Σ CF_t/(1+k_c)^t + Terminal_value_n/(1+k_c)^n

Three methods for terminal value:
1. Liquidation value — assume firm ceases operations, sells assets
2. Multiple approach — apply EV/EBITDA or P/E to terminal year earnings
3. Stable growth model (Gordon Growth) — assume perpetual growth

ONLY use stable growth model or liquidation value in DCF.
Using multiples from comparable firms mixes relative and DCF → inconsistent.
```

### Stable Growth Model (Gordon Growth) (p.306)
```
For FCFF (firm valuation):
  Terminal_Value = FCFF_{n+1} / (WACC - g_terminal)

For FCFE (equity valuation):
  Terminal_Value = FCFE_{n+1} / (Ke - g_terminal)
```

### Constraints on Stable Growth Rate (pp.306–308)
```
The stable growth rate CANNOT exceed the growth rate of the economy.

Three questions to determine the limit (p.307):
1. Domestic or multinational?
   - Domestic → domestic GDP growth is the cap
   - Multinational → global GDP growth (or relevant regions)

2. Nominal or real valuation?
   - Nominal (default) → nominal growth rate (includes inflation)
   - Real → lower constraint
   - US company (nominal $): g_terminal ≤ ~2.0–2.5%

3. What currency?
   - High-inflation currency → higher g_terminal allowed
   - Low-inflation currency → lower g_terminal

Rule of thumb: g_terminal should NOT exceed the risk-free rate.
  Nominal Rf = Real Rf + Expected inflation
  Long-term: Real Rf converges to real growth rate of economy
  Therefore: Rf ≈ nominal GDP growth ≈ ceiling for g_terminal

Stable growth CAN be lower than economy growth (firm shrinks as % of economy).
```

### Negative Stable Growth (p.308)
```
Stable growth CAN be negative. Negative g implies the firm partially
liquidates each year until it eventually disappears. This is an intermediate
between complete liquidation and going concern forever.

Example: $100M after-tax cash flows, g = -5%, WACC = 10%
  Value = 100(1 - .05) / [.10 - (-.05)] = 95/0.15 = $633 million

Use negative terminal growth for:
  - Firms in industries being phased out (landline phones)
  - Defense contractors losing major government contracts
  - Companies in secular decline with no pivoting ability
```

### Key Assumptions in Stable Growth (pp.308–310)
```
Three critical assumptions:
1. WHEN the firm becomes a stable growth firm
2. WHAT the firm's characteristics will be in stable growth
3. HOW the firm transitions from high growth to stable growth
```

### Length of High Growth Period (pp.308–309)
```
All firms ultimately become stable growth firms. High growth that creates
value (footnote 1: "growth without excess returns makes a firm larger but
not more valuable") comes from firms earning EXCESS RETURNS (ROC > WACC).

Three factors for how long high growth lasts (p.308):
1. Size of firm — smaller firms have more room to grow
2. Existing growth rate and excess returns — momentum matters;
   high ROIC firms with rapid revenue growth sustain longer
3. Magnitude and sustainability of competitive advantages — MOST CRITICAL
   - Significant barriers to entry → longer high growth
   - Fading advantages → shorter, be conservative

Competitive Advantage Period (CAP): coined by Michael Mauboussin (CSFB).
  Period during which firm earns excess returns.
  Firm value = Capital invested + PV(excess returns over CAP)
  Market-implied CAP (MICAP) = how long CAP must last to justify current price.
```

### Illustration 12.1 — Growth Period Length (p.310)
```
Con Edison: regulated monopoly, restricted returns → already stable growth
P&G: strong brand names → 5 years of high growth
Amgen: patent protection + long drug approval cycle → 10 years of high growth
```

### Characteristics of Stable Growth Firm (pp.310–313)

#### Equity Risk / Beta (p.311)
```
High growth firms have higher betas. As firms mature, betas converge toward 1.

Rule of thumb for stable period betas:
  - Should NOT exceed 1.2
  - Should NOT be lower than 0.8
  - Two-thirds of US firms have betas between 0.8 and 1.2

Exceptions:
  - Commodity companies staying in current business: leave beta at existing level
  - If growth requires entering new businesses: adjust beta toward 1
```

#### Project Returns / ROIC in Stable Growth (p.311)
```
In stable growth, excess returns (ROC > WACC) are harder to sustain.

Conservative assumption: ROC = Cost of capital (no excess returns)
  → Terminal value becomes insensitive to growth rate (see Illustration 12.2)
Practical compromise: move ROC TOWARD cost of capital, allow some residual
  excess returns for firms with durable competitive advantages.
Use industry averages for ROE/ROC as target for stable growth.
```

#### Debt Ratios and Cost of Debt (p.312)
```
High growth firms use less debt. As firms mature, debt capacity increases.
In stable growth:
  - Move debt ratio toward industry average of larger, mature firms
  - Credit risk decreases → cost of debt decreases
  - Stable growth firms should have at least investment grade (Baa or higher)
```

#### Reinvestment Rate in Terminal Year (pp.312–314) — CRITICAL
```
FCFF terminal reinvestment:
  Reinvestment_rate_terminal = g_terminal / ROC_stable

  Where ROC_stable = return on capital the firm can sustain in stable growth

FCFE terminal reinvestment:
  Equity_reinvestment_rate_terminal = g_terminal / ROE_stable
  Retention_ratio = g_terminal / ROE_stable

Examples:
  P&G:  retention = 3% / 12% = 25% → payout = 75%
  KO:   equity reinvestment = 3% / 15% = 20%
  Amgen: reinvestment = 3% / 10% = 30%

CRITICAL INSIGHT (p.313-314): Growth and reinvestment are linked.
  - Increasing g while holding reinvestment constant → dramatically increases TV
  - But increasing g also INCREASES reinvestment → offsetting effect
  - Whether higher g increases value depends ENTIRELY on excess returns:
    • If ROC > WACC: higher g → higher value
    • If ROC = WACC: higher g has NO EFFECT on value (offsets perfectly)
    • If ROC < WACC: higher g → LOWER value (destroying value with each investment)
```

### Terminal Value with Explicit Reinvestment (p.314) — KEY FORMULA
```
Terminal_Value = NOPAT_{n+1} × (1 - Reinvestment_rate) / (WACC - g_terminal)

Substituting Reinvestment_rate = g / ROC:

Terminal_Value = NOPAT_{n+1} × (1 - g/ROC) / (WACC - g)

Special case: ROC = WACC (no excess returns):
  Terminal_Value = NOPAT_{n+1} / WACC

This is independent of growth! When there are no excess returns,
terminal value is unaffected by growth assumptions.

Illustration 12.2 (Alloy Mills, p.314-315):
  NOPAT=$100M, WACC=10%, high g=10%, ROC high=20%, ROC stable=varies

  Case 1: ROC_stable=20%, g_stable=5%
    Reinvestment = 5%/20% = 25%, TV = $169.10×(1-.25)/(.10-.05) = $2,537M
    Firm value = $2,075M

  Case 2: ROC_stable=10% (=WACC), g_stable=5%
    Reinvestment = 5%/10% = 50%, TV = $169.10×(1-.50)/(.10-.05) = $1,691M
    Firm value = $1,300M

  Case 3: ROC_stable=10%, g_stable=4%
    Reinvestment = 4%/10% = 40%, TV = $167.49×(1-.40)/(.10-.04) = $1,675M
    Firm value = $1,300M  ← SAME as Case 2!

  Case 4: ROC_stable=10%, g_stable=0%
    Reinvestment = 0%/10% = 0%, TV = $161.05/(.10-0) = $1,610.5M
    Firm value = $1,300M  ← STILL SAME!

PROOF: When ROC = WACC, changing g has NO effect on firm value.
```

### Transition to Stable Growth (p.317)
```
Three models:
1. Two-stage: high growth → abrupt drop to stable
   Best for: firms with moderate growth rates, shift not too dramatic

2. Three-stage: high growth → transition → stable
   Best for: very high growth firms; allows gradual adjustment of
   growth, ROC, reinvestment, and risk characteristics

3. N-stage: characteristics change each year from initial to stable
   Best for: very young firms or firms with negative operating margins

Our implementation: two-stage with linear decay in Stage 2 (years 6-10)
  → approximates three-stage model

Note (p.318): You can have an "extraordinary growth period" even when
g ≤ economy growth rate, if other inputs (ROC, beta) need to transition
to stable-growth-appropriate levels. The transition period is about ALL
inputs converging, not just the growth rate.
```

### The Survival Issue (pp.318–321)

#### Cash Burn Ratio (p.318)
```
For young firms with negative earnings:
  Cash_burn_ratio = Cash_balance / |EBITDA|

  Where EBITDA is negative, use absolute value.
  Gives estimated months/years until cash runs out.

  Example: Cash = $1B, EBITDA = -$1.5B → cash lasts ~8 months
```

#### Likelihood of Failure and Valuation (p.319)
```
Two views on whether to adjust for survival:

View 1: Expected cash flows already reflect failure probability
  - If you model a range of scenarios (good to bad), failure is embedded
  - Higher failure risk → higher discount rate → lower expected cash flows
  - For firms with substantial assets and small distress probability:
    this view is correct. Extra survival discount = DOUBLE COUNTING risk.

View 2: DCF has optimistic bias, doesn't adequately reflect nonsurvival
  - DCF value overstates operating assets
  - For younger/smaller firms with real bankruptcy risk

Decision rule:
  - Established firms with assets + low distress probability → DO NOT adjust
  - Young/small firms where cash flows don't model failure → ADJUST
```

#### Survival Adjustment Formula (p.319)
```
Adjusted_value = DCF_value × (1 - P_distress) + Distressed_sale_value × P_distress

Example: DCF = $1B, distressed sale = $500M, P(distress) = 20%
  Adjusted = $1B × 0.8 + $500M × 0.2 = $900M

Key points:
  - It's not failure per se that destroys value — it's the DISTRESSED SALE DISCOUNT
  - Estimating P(distress) is hard:
    depends on cash reserves, market conditions, access to capital
  - In buoyant markets, even cash-poor firms survive (can raise funds)
  - In downturns, even cash-rich firms may face threats
```

#### Estimating Probability of Distress (p.320)
```
Two methods:
1. Statistical (probit model): identify historically failed firms,
   find variables that predicted failure, apply to current firm
   (Altman Z-score approach)

2. Bond rating: use empirical default rates
   Example: B-rated bonds → 36.80% default rate over a decade
   (Altman's annual series at NYU Stern)
   Limitation: only works for rated firms; assumes stable rating standards
```

### Terminal Value Warning
```
If PV(Terminal_Value) / Total_EV > 80%:
  FLAG: Model is highly sensitive to terminal assumptions.
  Report a range of intrinsic values, not a point estimate.
  Show sensitivity table prominently.

Damodaran's defense of terminal value (p.320-321):
  - TV SHOULD be large — bulk of returns come from selling (price appreciation)
  - TV is NOT easy to manipulate IF two rules are followed:
    1. Growth rate cannot exceed growth rate of economy
    2. Reinvestment must be consistent with growth (g/ROC formula)
```

---

## Equity Value Bridge (FCFF → per-share price)

```
Enterprise_Value = PV(FCFF years 1-10) + PV(Terminal_Value)
Equity_Value = Enterprise_Value - Net_Debt
  Where: Net_Debt = Total_Debt - Cash_and_Equivalents

Intrinsic_Value_per_Share = Equity_Value / Shares_Outstanding

Margin_of_Safety = (Intrinsic_Value - Current_Price) / Intrinsic_Value
Upside_Pct = (Intrinsic_Value - Current_Price) / Current_Price × 100
```

---

## Sensitivity Analysis

Always run sensitivity on the two most impactful variables:
1. WACC (discount rate) — range: ±1.5% in 0.5% steps
2. Terminal Growth Rate — range: ±1.0% in 0.5% steps

Produces a 5×5 matrix of intrinsic values.
The base case sits in the middle cell.

---

## FCFE Discount Model (for Financial Firms)

```
Equity_Value = Σ [FCFE_t / (1 + Ke)^t] + [Terminal_FCFE / (Ke - g)] / (1 + Ke)^N

For stable financial firms with predictable dividends:
  Equity_Value = DPS_next / (Ke - g_stable)   [Gordon Growth Model on dividends]
```

---

## Firm Valuation: Cost of Capital Approach (Chapter 15, pp.380–419)

### Cash Flow Hierarchy — Table 15.1 (p.382)
```
| Cash Flow   | What it measures | Discount at    | Yields           |
|-------------|------------------|----------------|------------------|
| FCFF        | Operating CF     | WACC           | Value of ops     |
| FCFE        | Equity CF        | Ke (cost of eq)| Equity value     |
| EBITDA      | Pre-tax/DA CF    | —              | Rough firm value |
| EBIT(1-t)   | After-tax op CF  | —              | No-growth firm   |

FCFF = EBIT(1-t) + Depreciation − Capex − Δ Working Capital
FCFE = FCFF − Interest(1-t) − Principal repaid + New debt − Pref dividends

EBITDA caveat: assumes no taxes AND that the firm will disinvest (D&A > 0
without replacement). Do NOT use EBITDA for perpetuity-based valuation.

EBIT(1-t) caveat: assumes no reinvestment beyond depreciation (i.e.,
depreciation maintains existing assets). Infinite life but no growth.
```

### Growth in FCFE vs FCFF (pp.382–383)
```
g_NI   = Equity reinvestment rate × ROE
g_EBIT = Reinvestment rate × ROC

ROE = ROC + (D/E) × (ROC − i(1−t))

Key insight: In stable growth, equity and operating income growth rates
CONVERGE. The difference between g_NI and g_EBIT matters only during
the high-growth phase when leverage is changing.
```

### Two-Stage FCFF Model (p.386)
```
Value = Σ [FCFF_t / (1+WACC_hg)^t] + [FCFF_{n+1} / (WACC_st − g_n)] / (1+WACC_hg)^n

Where:
  WACC_hg = WACC during high-growth period
  WACC_st = WACC during stable-growth period (lower beta, higher debt ratio)

WACC can differ between stages because:
  1. Beta converges toward 1 (Ch 12, p.311: stable beta = 0.8–1.2)
  2. Debt ratio typically increases for mature firms
  3. Cost of debt may decrease as firm becomes more creditworthy
```

### Market Value Weights and Circularity (p.386)
```
Problem: WACC requires market value weights, but market value is what we're
trying to compute.

MUST use MARKET VALUE weights, not book value.

Circularity resolution:
  1. Start with current market weights
  2. Compute WACC → derive firm value
  3. Recompute weights from derived value
  4. Iterate until weights converge (usually 2–3 rounds)

In practice: for most public companies, using current market cap as E and
book value of debt as D is adequate. Iteration is mainly needed when
the firm's current capital structure is clearly transitioning.
```

### Net Debt vs Gross Debt (p.396)
```
Two approaches to debt in EV bridge:

Gross debt approach (RECOMMENDED by Damodaran):
  EV = Equity value + Gross debt
  Separate: cash as a non-operating asset, valued at face
  Equity value = EV − Gross debt + Cash

Net debt approach:
  EV = Equity value + (Gross debt − Cash)
  Equity value = EV − Net debt

Special case: Net debt < 0 (cash exceeds debt):
  Set net debt to 0 and treat excess cash separately.

Cost of debt on net debt basis:
  Kd_net = (Kd_pretax × Gross_debt − Rf × Cash) / (Gross_debt − Cash)
```

### Synthetic Rating for Cost of Debt (Chapter 15, p.407)

#### Interest Coverage → Bond Rating → Default Spread
```
For firms without traded bonds, estimate cost of debt synthetically:
  Kd_pretax = Rf + Default_spread

Step 1: Compute interest coverage ratio = EBIT / Interest expense
Step 2: Map to synthetic bond rating using table below
Step 3: Add default spread to risk-free rate

Table — Interest Coverage Ratio → Rating → Spread (p.407):

| Coverage Ratio      | Rating | Default Spread |
|---------------------|--------|----------------|
| > 8.50              | AAA    | 1.25%          |
| 6.50 – 8.50         | AA     | 1.75%          |
| 5.50 – 6.50         | A+     | 2.25%          |
| 4.25 – 5.50         | A      | 2.50%          |
| 3.00 – 4.25         | A−     | 3.00%          |
| 2.50 – 3.00         | BBB    | 3.50%          |
| 2.25 – 2.50         | BB+    | 4.25%          |
| 2.00 – 2.25         | BB     | 5.00%          |
| 1.75 – 2.00         | B+     | 6.00%          |
| 1.50 – 1.75         | B      | 7.25%          |
| 1.25 – 1.50         | B−     | 8.50%          |
| 0.80 – 1.25         | CCC    | 10.00%         |
| 0.65 – 0.80         | CC     | 12.00%         |
| 0.20 – 0.65         | C      | 15.00%         |
| < 0.20              | D      | 20.00%         |

NOTE: These spreads are for LARGE firms (>$5B revenue). For smaller firms,
Damodaran uses a different (wider-spread) table. Our model uses the large-
firm table and adds a size premium to cost of equity instead.
```

#### Tax Rate Adjustment at High Leverage (p.410)
```
When Interest expense > EBIT, the firm CANNOT fully deduct interest.

Standard:     Kd_aftertax = Kd_pretax × (1 − t)
Adjusted:     Kd_aftertax = Kd_pretax × (1 − t_adj)

Where: t_adj = min(t, EBIT / Interest_expense × t)

At extreme leverage:
  If EBIT / Interest < 1: effective tax benefit is reduced
  If EBIT / Interest ≈ 0: no tax benefit from debt at all

Example (Disney at 90% debt, p.410):
  Interest = $11,421M, EBIT = $5,228M
  t_adj = EBIT/Interest × 0.361 = 5228/11421 × 0.361 = 16.53%
  → Much lower tax shield than statutory 36.1%
```

### Adjusted Present Value (APV) Approach (Chapter 15, pp.398–402)

#### Three Steps
```
Step 1: Value of unlevered firm
  V_unlevered = E(FCFF₁) / (ρ_u − g)
  Where ρ_u = unlevered cost of equity = Rf + β_u × ERP
  (No debt in the discount rate)

Step 2: Present value of tax benefits
  If debt is PERMANENT (perpetuity):
    PV(tax benefits) = t_c × D
  If debt changes over time (e.g., LBO):
    PV = Σ [Tax_rate × Interest_t × (1 + Kd)^(-t)]

Step 3: Expected bankruptcy cost
  E(bankruptcy cost) = P(bankruptcy) × BC
  Where:
    P(bankruptcy) = probability of default (from bond rating, Table 15.2)
    BC = bankruptcy cost as % of unlevered firm value
       ≈ 20–30% for most firms (legal fees, fire-sale discounts, lost business)

Value of levered firm = V_unlevered + PV(tax benefits) − E(bankruptcy costs)
```

#### Table 15.2 — Bond Rating → Probability of Default (p.399)
```
| Rating | P(Default) |
|--------|------------|
| AAA    | 0.07%      |
| AA     | 0.51%      |
| A+     | 0.60%      |
| A      | 0.66%      |
| A−     | 2.50%      |
| BBB    | 7.54%      |
| BB+    | 10.00%     |
| BB     | 16.63%     |
| B+     | 25.00%     |
| B      | 36.80%     |
| B−     | 45.00%     |
| CCC    | 59.01%     |
| CC     | 70.00%     |
| C      | 85.00%     |
| D      | 100.00%    |

Source: Altman (compiled at NYU Stern). These are CUMULATIVE default rates
over a 10-year horizon based on historical bond cohorts.
```

#### APV Illustration — J.Crew LBO (pp.400–401)
```
Unlevered cost of equity: 8.5% (β_u = 1.00, Rf = 3.5%, ERP = 5%)
Unlevered firm value: $2,321M

PV of tax benefits from declining debt schedule: $305M
  (Tax rate × Interest in each year, discounted at Kd)

Expected bankruptcy cost:
  = (V_unlevered + PV_tax_benefits) × P(default) × Bankruptcy cost %
  = ($2,321 + $305) × 30% × 20% = $158M

Value of levered firm = $2,321 + $305 − $158 = $2,469M
```

### APV vs Cost of Capital — When to Use Each (p.402)
```
Both approaches generally give SIMILAR answers under same assumptions.

Use WACC (cost of capital) when:
  - Firm has a stable, target debt PROPORTION (e.g., 30% D/(D+E))
  - Ongoing operating firm (not undergoing restructuring)
  - Capital structure is not expected to change dramatically

Use APV when:
  - Debt is a DOLLAR amount that changes over time (e.g., LBO paydown)
  - Firm is undergoing leveraged recapitalization
  - Capital structure is transitioning significantly
  - You want to separately assess the value of tax shields and bankruptcy costs

Limitations of APV:
  - If you exclude bankruptcy costs, APV overestimates optimal leverage (100%)
  - Bankruptcy cost estimation is subjective
  - Tax benefit computation depends on tax rate assumptions over time
```

### Effect of Leverage on Firm Value (pp.406–418)

#### Disney Cost of Capital at Different Debt Ratios (pp.406–414)
```
| D/(D+E) | Beta  | Ke     | Rating | Kd(AT) | WACC   |
|---------|-------|--------|--------|--------|--------|
| 0%      | 0.73  | 7.90%  | AAA    | —      | 7.90%  |
| 10%     | 0.80  | 8.34%  | AAA    | 2.47%  | 7.75%  |
| 20%     | 0.90  | 8.92%  | AAA    | 2.47%  | 7.63%  |
| 30%     | 1.04  | 9.72%  | A−     | 3.10%  | 7.73%  |
| 40%     | 1.04  | 9.72%  | A−     | 3.72%  | 7.32%  | ← OPTIMAL
| 50%     | 1.68  | 13.58% | B+     | 4.21%  | 8.89%  |
| 60%     | 2.26  | 17.09% | CCC    | 5.84%  | 10.34% |
| 70%     | 3.23  | 22.95% | CCC    | 7.00%  | 11.78% |
| 80%     | 4.13  | 28.38% | CC     | 7.97%  | 12.05% |
| 90%     | 5.05  | 33.83% | CC     | 8.84%  | 11.34% |

Key observations:
  1. WACC first DECREASES (tax benefit > risk increase) then INCREASES
  2. Optimal debt ratio = 40% D/(D+E) where WACC is minimized at 7.32%
  3. Beta increases with leverage (Hamada equation)
  4. Cost of debt jumps at rating downgrades (discontinuous)
  5. At 90% debt, tax benefit is REDUCED because Interest > EBIT
```

---

## Key Ratios to Report

```
ROIC = NOPAT / Invested_Capital   (profitability of reinvestment)
Interest_Coverage = EBIT / Interest_Expense   (< 1.5 = distress)
Net_Debt / EBITDA   (> 4x = high leverage)
FCF_Yield = FCFF / Enterprise_Value × 100   (inverse of EV/FCF)
Capex / Revenue   (capital intensity indicator)
```

---

## What NOT to Do (Common Mistakes)
1. Never use net income as the numerator in FCFF
2. Never use book value weights in WACC
3. Never set terminal growth = historical growth (companies slow down)
4. Never ignore working capital changes (can be as big as capex)
5. Never accept a single-point DCF as "the answer" — always show a range
6. Never ignore the reinvestment rate check (g must be consistent with reinvestment)
