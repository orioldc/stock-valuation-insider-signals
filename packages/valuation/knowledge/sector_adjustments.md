# Sector-Specific Valuation Adjustments
# Source: Damodaran "Investment Valuation" Chapters 22-32

## Financial Institutions (Banks, Insurance, Investment Banks)
### Source: Damodaran Chapter 21, pp.581–608

### Why Financial Firms Are Different (pp.582–584)
```
1. DEBT IS RAW MATERIAL, NOT FINANCING (p.582)
   Debt to a bank is like steel to an auto manufacturer.
   Deposits are operational inputs, not financing decisions.
   Capital = equity only (reinforced by regulatory definition).
   WACC is MEANINGLESS for banks — cannot separate operating from financing debt.

2. REINVESTMENT IS INVISIBLE (p.583)
   Banks invest in intangible assets (human capital, brand, relationships)
   categorized as operating expenses in accounting.
   Statement of cash flows shows little or no capital expenditures.
   Working capital definition (current assets - current liabilities) captures
   most of the balance sheet and is meaningless for banks.
   → Cannot compute FCFF or standard FCFE

3. REGULATORY OVERLAY (p.583)
   Banks must maintain capital ratios (CET1 ~7%+ under Basel III).
   This constrains where and how much they can invest.
   Growth assumptions must pass regulatory capital constraints.
   Regulations restrict entry, affecting competitive dynamics and growth period length.
```

### General Framework for Valuation (p.584)
```
Two broad rules:
  1. Value EQUITY directly (not the firm) — discount at Ke, not WACC
  2. Use a cash flow measure that does NOT require estimating reinvestment,
     OR redefine reinvestment to be meaningful for financial firms

Three DCF approaches:
  a. Dividend Discount Model (DDM) — simplest, most reliable
  b. FCFE with reinvestment = change in regulatory capital
  c. Excess Return Model — BV equity + PV(excess returns)

For multiples: use EQUITY multiples only (P/E, P/B)
  NOT firm value multiples (EV/EBITDA, EV/Sales) — p.600
```

### Dividend Discount Model (pp.585–591)
```
Basic model:
  Value per share = Σ DPS_t / (1 + Ke)^t

Stable growth (Gordon Growth for banks):
  Value per share = DPS_next / (Ke - g)

  Where:
    DPS_next = Expected dividends per share next year
    Ke = Cost of equity
    g = Expected growth rate in perpetuity

Inputs specific to financial firms:

COST OF EQUITY (p.586):
  Ke = Rf + Beta × ERP
  - Use LEVERED beta directly (do NOT unlever/relever)
  - Banks are homogeneously leveraged → comparable betas are already levered
  - Debt is hard to define → unlevering is meaningless
  - In stable growth: beta should converge toward 1 (p.588)

PAYOUT RATIOS (p.587):
  - Financial firms conventionally pay out MORE in dividends than other firms
  - Include stock buybacks (average over 3+ years — buybacks are lumpy)
  - Banks invest less in net capex → more earnings available for payout

EXPECTED GROWTH (p.587–588):
  g_EPS = Retention ratio × ROE
  - Retention ratio for banks INCLUDES equity reinvested in regulatory capital
  - ROE is a more meaningful measure of investment quality for banks
    because financial assets are marked to market → BV equity is reliable
  - If ROE is expected to change:
    g = Retention × ROE_{t+1} + (ROE_{t+1} - ROE_t) / ROE_t

STABLE GROWTH PAYOUT (p.588):
  Payout ratio in stable growth = 1 - g / ROE_stable

  Example (HSBC, p.589):
    g=3.5%, ROE=7.27% (conservative) → payout = 1 - 3.5%/7.27% = 51.9%
    g=3.5%, ROE=9.5% (actual Ke) → payout = 1 - 3.5%/9.5% = 63.2%

STABLE GROWTH for banks — three factors (p.588):
  1. Size relative to market served (larger → harder to sustain high growth)
  2. Nature of competition (restricted = longer growth, intense = shorter)
  3. Regulatory effects (restrict entry = help growth; restrict activities = limit growth)
```

### Illustration 21.1: HSBC — Stable Growth DDM (p.589)
```
EPS = 74.8 pence, DPS = 36 pence, Payout = 48.13%
Beta = 1.0, Rf = 4%, ERP = 5.5%, Country premium = 0.5%
Ke = 4% + 1(5.5%) = 9.5%, g_stable = 3.5%
Value = 36(1.035) / (.095 - .035) = 621 pence/share
(Stock at 635 → fairly valued)

If ROE = Ke = 9.5%:
  Payout = 1 - 3.5%/9.5% = 63.16%
  DPS = 74.8 × 0.6316 = 47.24
  Value = 47.24(1.035) / (.095 - .035) = 729 pence
```

### Illustration 21.2: State Bank of India — High-Growth DDM (pp.590–591)
```
Three-phase model: high growth (4 yr) → transition (4 yr) → stable
High growth: ROE=19.72%, retention=93.59%, g=18.46%, Ke=20.34%
Transition: g declines linearly, payout rises, Ke drops (country risk falls)
Stable: g=10%, ROE=18%, Ke=17.6%, payout=1-10%/18%=44.4%
Terminal = EPS_8 × (1.10) × 0.4444 / (0.176 - 0.10) = Rs 809.18
Value = PV(dividends) + PV(terminal) = Rs 243.55
```

### FCFE Model for Banks (pp.582–584, 592)
```
Since standard FCFE can't be computed, redefine reinvestment:

FCFE = Net Income - Investment in Regulatory Capital

Where:
  Investment in regulatory capital = Change in equity needed to meet
  capital ratio requirement as asset base grows

Example: Bank with 5% capital ratio earning $15M on $200M equity
  Pays $5M dividend → retains $10M → equity grows to $210M
  Can support $210M / 5% = $4.2B in assets (was $4B)
  Growth in lending capacity = 5%

If the bank does NOT use retained earnings productively (capital ratio
rises above regulatory minimum), the retained earnings are NOT true
reinvestment — they're cash accumulating in the firm.

For banks that don't pay dividends (p.592):
  - Use expected payout ratio: earnings are positive → will eventually pay out
  - Expected payout = 1 - g/ROE
  - Value today reflects expected future dividends, not current zero dividends
```

### Excess Return Model (pp.597–598) — Goldman Sachs example
```
Value of equity = BV of equity invested currently
                + PV of expected excess equity returns

Excess equity return = (ROE - Ke) × BV of equity

If ROE = Ke → market value = book value (no excess returns)
If ROE > Ke → market value > book value (creating value)
If ROE < Ke → market value < book value (destroying value)

Goldman Sachs (May 2011, p.598):
  BV equity = $78,228M, ROE = 11.66%, Ke = 9.5%, Payout = 10%
  Retained = $9,067-12,230M/yr → BV grows to $116,588M by year 5
  Excess return each year = (11.66% - 9.50%) × BV_equity
  After year 5: ROE = Ke = 9.50% → no more excess returns → PV(TV) = 0
  Value = $78,228 + $7,880 + $0 = $86,068M
  Per share: $86,068 / 517.735 = $166.24
  (Stock at $140.63 → undervalued by ~18%)
```

### Relative Valuation for Banks (pp.600–607)

#### Multiples to Use (p.600)
```
CANNOT use firm value multiples (EV/EBITDA, EV/Sales)
  - Neither "value" nor "operating income" can be easily estimated
  - Revenue/sales not measurable in traditional sense

USE equity multiples ONLY:
  1. P/E ratio (primary for insurance companies)
  2. P/B ratio (primary for banks — companion variable is ROE)
  3. Do NOT use price-to-sales ratios
```

#### P/E for Financial Firms (pp.601–602)
```
PE = Price / EPS  (same as any firm)

Key issues specific to banks:
  1. Provisions for bad debts affect reported earnings
     - Conservative banks → lower earnings → higher P/E
     - Aggressive banks → higher earnings → lower P/E
     → Compare provision ratios vs actual bad debt rates over time
  2. Diversified banks (commercial + investment + asset mgmt):
     each business warrants different P/E
     → Break earnings by segment, apply segment P/E (Illustration 21.6)

P/E regression for insurance companies (p.802):
  PE = 12.311 - 1.953(Beta) + 9.70(Payout ratio)    R² = 37.6%
  - Growth was not statistically significant
  - Payout ratio is dominant variable for financial firm P/E
```

#### P/B for Financial Firms (pp.602–605) — PRIMARY MULTIPLE
```
P/B = Price per share / Book value of equity per share

Why P/B is better for banks than for other firms (p.603):
  1. Financial assets are marked to market → BV is close to fair value
     (unlike manufacturing where BV is historical cost)
  2. Depreciation is negligible → no BV distortion
  3. ROE is a more meaningful profitability measure for banks

Companion variable: ROE is the KEY driver of P/B
  Justified P/B = ROE / Ke  (for stable growth)
  More precisely: P/B = (ROE - g) / (Ke - g)

Figure 21.1 (p.603): US Banks scatter plot
  P/BV vs ROE: R² = 0.601 (strong linear relationship)
  Banks with high ROE (WABC, BOH) → P/B > 2x
  Banks with low/negative ROE (SUSQ, BXS) → P/B < 1x

P/B regression for European banks (p.605):
  PBV = 0.712 + 7.20(ROE) + 0.40(Expected growth) - 0.42(Beta)
  R² = 42.7%

Interpretation: if ROE < Ke → P/B < 1 is JUSTIFIED (not a bargain)
```

#### Illustration 21.6: JP Morgan — Sum-of-Parts (p.802)
```
Break net income by business segment, apply segment-appropriate P/E:

  Investment banking:   $6,639M × 12.15 = $80,664M
  Retail financial:     $2,526M × 14.8  = $37,385M
  Credit cards:         $2,074M × 14.8  = $30,695M
  Commercial banking:   $2,084M × 10.8  = $22,507M
  Treasury/securities:  $1,079M × 10.8  = $11,653M
  Asset management:     $1,710M × 15.67 = $26,796M
  Private equity:       $1,258M × 8.08  = $10,165M
  TOTAL:               $17,370M          $219,865M

Market cap at time: $168.29B → undervalued by ~30%
```

### Issues in Valuing Financial Firms (pp.605–607)

#### Provisions for Losses (p.605)
```
Provisions reduce net income in current period to cover future bad debts.
- If provisions = actual losses over time → earnings smoothing (acceptable)
- If provisions consistently > losses → net income understated → ROE depressed
  → Fix: recompute net income using actual bad debt ratio
  Example: bank provisions 8% but actual losses 4% → restate using 4%
- If provisions consistently < losses → net income overstated → ROE inflated
```

#### Regulatory Risk (p.606)
```
- Regulatory risk is mostly diversifiable → should NOT affect discount rate
  (exception: when financial firms dominate a market)
- Regulation primarily affects CASH FLOWS, not discount rate:
  - Growth limited by restrictions on where banks can invest
  - ROE limited by regulated rates of return
  - If investment restrictions are severe → low ROE for foreseeable future
```

#### Financing Mix (p.606)
```
- Do NOT analyze financing mix (D/E optimization) for banks
- Banks already use as much debt as they can afford (deposits)
- Regulatory capital ratios may not always be rational
  → Banks meeting minimum requirements may actually be over-leveraged
```

### Conclusion (p.607)
```
Summary of what makes financial firms unique for valuation:
1. Debt is hard to define → value equity directly at cost of equity
2. Capex and working capital are unmeasurable → use dividends or
   modify reinvestment definition to include regulatory capital
3. Multiples: equity multiples only (P/E, P/B) — NOT EV/EBITDA
4. Control for differences in: risk, growth, cash flows, loan quality
```

---

## Business Development Companies (BDCs)
### Source: Damodaran Chapter 21 (Financial Firms) + RIC Regulations

### What BDCs Are
```
BDCs are publicly traded vehicles that lend to and invest in private companies
(typically middle-market or venture-stage). Regulated under the Investment Company
Act of 1940. Structured as Regulated Investment Companies (RICs).

Key characteristics:
1. MUST DISTRIBUTE 90%+ of taxable income as dividends (like REITs)
2. Leverage is structural — debt funds the lending portfolio (like banks)
3. Revenue = spread income (interest earned - interest paid)
4. Portfolio is marked to market quarterly (ASC 820)
5. NAV per share is reported directly (no need to estimate like REITs)
```

### Why Standard DCF Fails (same as banks, Ch 21)
```
- Debt is raw material (funds loans), not financing → WACC is meaningless
- EBIT/EBITDA are not meaningful metrics for spread-lenders
- Cannot separate operating from financing cash flows
- Reinvestment = originating new loans (invisible in standard capex)
```

### Valuation Framework
```
1. PRIMARY: Dividend Discount Model (DDM)
   - BDCs distribute 90%+ of income → dividends ≈ true cash flow to equity
   - Use augmented dividends (dividends + buybacks) for accuracy
   - Discount at Ke only (cost of equity via CAPM, no WACC)
   - Growth = ROE × (1 - payout), typically 2-8% given high mandatory payout
   - Growth cap: 10% (portfolio expansion from new loans)

2. RELATIVE: P/NAV (primary), P/E (secondary)
   - P/NAV is the dominant BDC metric (like P/FFO for REITs)
   - NAV = book value (ASC 820 mark-to-market of loan portfolio)
   - Justified P/NAV = payout × (1+g) / (Ke - g)
   - BDCs trading above NAV: market expects ROE > Ke (value creation)
   - BDCs trading below NAV: market skeptical on credit quality
   - P/E is valid as secondary (net income from lending spreads)

3. ASSET-BASED: Reported NAV
   - BDCs report NAV directly (no cap rate estimation needed)
   - Book value ≈ NAV (portfolio marked to fair value quarterly)
   - Simpler than REIT NAV (no property appraisal or cap rate issues)
   - P/NAV premium/discount is the key signal

4. SYNTHESIS WEIGHTS: DDM 45%, Relative 30%, NAV 25%
```

### Key Differences from Banks
```
- BDCs have transparent NAV (marked-to-market), banks don't
- BDCs have mandatory 90%+ payout (banks: discretionary 40-60%)
- BDCs lend to higher-risk borrowers (venture/growth companies)
- Credit loss rate is the critical variable for BDCs
- BDC beta: use market beta directly (like banks — structural leverage)
```

### Implementation Notes
```
Detection: yfinance reports sector="Financial Services", industry="Asset Management"
           (same as non-BDC asset managers). Use longBusinessSummary containing
           "business development company" for identification.
Beta: use market levered beta directly (structural leverage like banks)
Growth: ROE × augmented retention, capped at 10%
NAV: yfinance bookValue = reported NAV per share for BDCs
Examples: HTGC, ARCC, MAIN, BXSL
```

---

## Real Estate Investment Trusts (REITs)
### Source: Damodaran Chapter 26, pp.739–768

### Why Real Estate Is Different from Financial Assets (pp.739–740)
```
1. Each real estate investment is UNIQUE (unlike securities)
   - No two buildings are identical → comparisons are approximate
   - Fewer transactions → less market-derived data
   - Individual property risk harder to diversify

2. DEPRECIATION DISTORTS NET INCOME (p.764)
   - Tax depreciation (27.5yr residential, 39yr commercial) is a
     legal fiction — real estate often APPRECIATES
   - Net income understates true economic earnings
   - FFO = Net Income + Real Estate Depreciation - Gains on Sales
   - AFFO = FFO - Maintenance Capex (recurring capital expenditures)

3. ORGANIZATIONAL STRUCTURE (pp.764–765)
   - Must distribute 95% of taxable income to qualify as REIT
     (actually 90% under current IRS rules, Damodaran uses 95%)
   - No corporate-level taxation if distribution requirement met
   - Restricted from many non-real-estate investments
   → HIGH payout ratio is STRUCTURAL, not discretionary
   → Dividend Discount Model is a natural fit (like banks)
```

### Estimating Discount Rates for Real Estate (pp.740–748)

#### Risk-Free Rate and ERP for Real Estate (p.741)
```
Standard CAPM applies: Ke = Rf + Beta × ERP

Problem: real estate betas are measured against REIT indices,
which may understate true market beta because:
  - Property values are appraised, not continuously marked to market
  - Reported returns are smoothed → lower apparent correlation with market
  - REIT betas typically range 0.5–1.2 (lower than true economic risk)

Table 26.1 (p.742): Returns by Asset Class 1947–1982
  Real estate had lower correlation with stocks (~0.15) than bonds (~0.40)
  → Suggests real estate offers diversification benefits
  → But the low correlation partly reflects appraisal smoothing

Practical solution (p.744): Use REIT-derived betas as reasonable proxies.
  REITs trade on exchanges → provide market-based risk measures.
  Adjust: Total beta = Market_beta / Correlation(REIT, Market)
  Use total beta if the REIT is illiquid or closely held.
```

#### From Cost of Equity to Cost of Capital (pp.746–748)
```
Levered beta for real estate (p.747):
  Levered_beta = Unlevered_beta × (1 + (1 - Tax_rate) × (D/E))

Note: For REITs, tax_rate is LOW (pass-through entity), so:
  - Tax shield from debt is smaller than for C-corps
  - (1 - t) ≈ 0.8–0.95 for REITs (vs 0.79 for C-corps at 21% tax)

Illustration 26.1 (p.754):
  Unlevered beta = 0.62
  D/E = 60/40 = 1.5
  Tax rate = 38%
  Levered beta = 0.62 × (1 + (1-0.38)(1.5)) = 1.20
  Ke = 5% + 1.20 × 5.5% = 11.6%
  Kd = 6% (pre-tax), after-tax = 6% × (1-38%) = 3.72%
  WACC = 40% × 11.6% + 60% × 3.72% = 6.87%
  (Damodaran uses 10.20% for the example, different inputs)

For a REIT (pass-through):
  Tax benefit of debt is reduced → WACC is higher relative to a C-corp
  with same debt level. This partially offsets the higher payout ratio.
```

### Estimating Cash Flows for Real Estate (pp.750–756)

#### Cash Inflows (pp.750–752)
```
1. RENTAL INCOME
   - Function of: occupancy rate × rent per unit/sq ft
   - Lease structure matters: triple-net vs gross vs modified gross
   - Vacancy assumption: use market vacancy rate, not building-specific
   - Lease roll-over: when existing leases expire, new leases at market rates

2. OTHER INCOME
   - Parking, laundry, storage, management fees
   - Usually 2–5% of total revenue

Key variable: OCCUPANCY RATE
   - Market-level occupancy drives rents (supply/demand)
   - Building-level occupancy reflects property quality
   - Use stabilized occupancy in projections (not current if unusual)
```

#### Cash Outflows (pp.752–754)
```
1. PROPERTY TAXES (typically 1–3% of assessed value)
2. INSURANCE (0.1–0.5% of replacement cost)
3. MAINTENANCE/REPAIRS (recurring — deducted from FFO to get AFFO)
4. MANAGEMENT FEES (3–8% of gross revenue)
5. CAPITAL EXPENDITURES
   - Maintenance capex (roof, HVAC, elevators) — recurring, deduct for AFFO
   - Growth capex (renovations, expansions) — discretionary investment

NET OPERATING INCOME (NOI):
  NOI = Gross Revenue - Operating Expenses (excluding financing)
  NOI is the real estate equivalent of EBIT for operating companies
  This is the key cash flow measure for property valuation
```

### Terminal Value: Capitalization Rate Approach (pp.756–758)
```
Terminal value = NOI_{n+1} / Cap_Rate

Where Cap Rate (capitalization rate) is:
  Cap_Rate = (WACC - g) / (1 + g)
  Or equivalently: Cap_Rate = NOI / Property_Value

  More practically:
  Cap_Rate = r - g  (where r = discount rate, g = NOI growth)

This is just the Gordon Growth Model rearranged:
  V = NOI / (r - g)  →  Cap_Rate = NOI / V = r - g

Market cap rates by property type (as reference, varies over time):
  - Industrial/Logistics: 4–6%
  - Multifamily/Apartments: 4–6%
  - Office (Class A): 5–7%
  - Retail (Malls): 6–8%
  - Hotels: 7–10%
  - Data Centers: 4–6%

WARNING (p.757): Cap rate compression (declining cap rates) means
  property values are rising faster than NOI. When cap rates are
  at historical lows, be cautious — mean reversion is likely.
  Compare cap rate spread vs 10Y Treasury yield for context.
```

### Relative/Comparable Valuation for Real Estate (pp.759–763)
```
Why comparables work BETTER for real estate than for stocks (p.759):
  1. Properties in same area with similar features are truly comparable
  2. Fewer variables differ between comparable properties vs companies
  3. Extensive transaction data in most markets

STANDARDIZED MEASURES:
  1. Price per square foot (most common)
  2. Price per unit (multifamily)
  3. Price per room (hotels)
  4. Cap rate (NOI / Price) — lower cap rate = higher relative price

For REITs specifically:
  Primary: P/FFO (price to funds from operations)
  Secondary: P/AFFO (price to adjusted FFO — more conservative)
  Tertiary: P/NAV (price relative to net asset value)

  FFO = Net Income + Depreciation & Amortization - Gains on Sales
  AFFO = FFO - Maintenance Capex
  NAV = (Property NOI / Market Cap Rate) - Net Debt + Other Assets

REGRESSION APPROACH (pp.761–763):
  Illustration 26.4: Regress price per square foot against:
    - Age of building, stories, proximity to transit, location quality
  Result: can identify over/undervalued properties after controlling
  for characteristics

For REIT-level valuation:
  Compare P/FFO across REITs in same property type
  Justified P/FFO = Payout × (1+g) / (Ke - g)
  (Same as justified P/E since FFO replaces earnings)
  Companion variable: FFO growth rate, occupancy, debt level
```

### Valuing REITs: Summary Framework (pp.764–768)
```
THREE APPROACHES (in order of reliability for REITs):

1. DIVIDEND DISCOUNT MODEL (DDM) — PRIMARY
   Since REITs MUST distribute 95% of income, dividends ≈ true
   cash flow to equity. This makes DDM the most natural model.
   Value = DPS_next / (Ke - g)
   Where:
     - DPS_next = FFO × target payout ratio × (1+g)
     - Or simply use current dividends per share × (1+g)
     - g = (1 - payout) × ROE  [limited by retention]
     - Ke = Rf + beta × ERP

2. P/FFO RELATIVE VALUATION — SECONDARY
   Compare to sector peers after controlling for:
     - FFO growth rate
     - Debt level (D/E or LTV)
     - Property type mix
     - Occupancy rate
   Justified P/FFO = Payout × (1+g) / (Ke - g)

3. NET ASSET VALUE (NAV) — CROSS-CHECK
   NAV per share = (Σ Property_NOI_i / Cap_Rate_i) - Net_Debt + Cash
                   / Shares_Outstanding

   Premium/Discount to NAV tells you:
     - P/NAV > 1.0: market expects growth or mgmt premium
     - P/NAV < 1.0: market discounts mgmt, sees asset risk,
       or liquidation value exceeds market cap (potential target)

KEY REIT-SPECIFIC METRICS:
  - FFO per share (replaces EPS)
  - AFFO per share (FFO minus recurring capex — more conservative)
  - Same-store NOI growth (organic rent growth, excludes acquisitions)
  - Occupancy rate (target: 90–95% for healthy REITs)
  - LTV (Loan-to-Value) — < 40% is conservative
  - Cap rate spread vs risk-free rate (spread compression = risk)
  - Dividend yield vs sector average
  - Payout ratio as % of FFO (should be 60–80% of FFO,
    even though 95%+ of taxable income — FFO > taxable income)
```

### Implementation Notes for Code
```
1. DECISION TREE: Route REITs to DDM (like banks), not generic FCFF
   - Primary: dcf_fcfe (DDM with dividends/FFO as cash flow)
   - Secondary: relative (P/FFO) + asset_based (NAV)

2. DCF MODEL: Use dividends as cash flow (same as financial firms)
   - REITs distribute ~95% of taxable income → dividends are NOT discretionary
   - Growth = (1 - FFO_payout) × ROE
   - Discount at Ke only (no WACC for equity-level valuation)

3. RELATIVE: P/FFO replaces P/E and EV/EBITDA
   - FFO = NI + D&A - Gains_on_Sales
   - AFFO = FFO - maintenance_capex
   - Justified P/FFO = Payout × (1+g) / (Ke - g)

4. ASSET-BASED: NAV = NOI/Cap_Rate - Net_Debt
   - Use market cap rates from comparable transactions
   - Approximate: NOI ≈ EBIT + D&A (for REITs, D&A is non-economic)
   - Cap rate ≈ WACC - g (from Gordon Growth rearranged)

5. BETA: Use market levered beta directly (like banks)
   - REIT betas are already at REIT-typical leverage
   - Unlevering/relevering with D/E of 1.5x produces similar results
     since REITs have structural leverage like banks
```

---

## Energy — Oil & Gas Exploration & Production (E&P)

**Why different:** Asset value depends on commodity price assumptions. Earnings are highly volatile.
  D&A is large and non-cash (depletion of reserves).

**Valuation approach:**
  1. Normalize commodity prices to long-run average (10-year avg oil/gas prices)
     Do NOT value at spot price — commodity cycles are mean-reverting
  2. Primary: NAV (reserve-based)
     Reserve_Value = Σ [Reserves × (Normalized_Price - Production_Cost)]
     Discounted at WACC; subtract net debt
  3. Relative: EV/DACF, EV/Reserves ($/BOE)
     DACF = EBITDA - interest expense (debt-adjusted cash flow)
  4. DCF using normalized FCF — sensitive to price deck assumption

**Commodity price normalization:**
  - Oil: 10-year Brent average (~$70-80/bbl as of 2025)
  - Natural gas: 10-year Henry Hub average (~$3/MMBtu)
  - Do NOT use futures strip prices beyond 2 years

**Key metrics:**
  - Reserve Replacement Ratio (> 100% = sustainable)
  - Finding & Development Cost (F&D cost per BOE)
  - Production cost per BOE vs. commodity price
  - Reserve life index (reserves / annual production)

---

## Technology — Software / SaaS

**Why different:** High initial customer acquisition cost, recurring revenues, low marginal cost.
  Reported earnings are distorted by stock-based compensation (SBC) and deferred revenue.

**Valuation approach:**
  1. For profitable SaaS: EV/EBIT (not EBITDA — need to penalize capex/R&D)
     Or EV/FCF (free cash flow is the right metric; less distorted by D&A)
  2. For pre-profit growth SaaS: EV/ARR or EV/Revenue
     "Rule of 40": Revenue growth % + FCF margin % > 40 is healthy
  3. SBC adjustment: Add back SBC to get clean cash earnings, but NOTE it is a real cost

**Key SaaS metrics:**
  - ARR Growth Rate — primary growth driver
  - Net Revenue Retention (NRR) / Net Dollar Retention — > 120% = best-in-class
  - Gross Margin — SaaS should be > 65% for pure software
  - CAC Payback Period — < 18 months is healthy
  - LTV/CAC Ratio — > 3x is generally sustainable

**Multiples ranges (2024-2025 context):**
  - High-growth SaaS (>30% ARR growth): 8-15x ARR
  - Mid-growth SaaS (15-30%): 5-10x ARR
  - Mature SaaS (<15%): 3-7x ARR
  - Note: these compress significantly when interest rates rise

---

## Biotechnology / Pharma

**Why different:** Most value is in unproven future drug approvals. Current earnings are negative.
  Risk is binary at each clinical trial phase.

**Valuation approach:**
  1. Sum-of-Parts: value each drug/program separately
     Value of Drug = Peak_Sales × Probability_of_Approval × (1 / (1 + Ke)^Years_to_Launch)
     Peak_Sales for typical drug: $500M - $5B depending on indication
     Probability by phase:
       - Preclinical → Phase 1: ~10%
       - Phase 1 → Phase 2: ~55%
       - Phase 2 → Phase 3: ~30%
       - Phase 3 → Approval: ~60%
       - Cumulative pre-clinical to approval: ~1%
       - Phase 2 to approval: ~18%
       - Phase 3 to approval: ~60%
  2. Subtract net debt and unallocated costs from pipeline value
  3. Add cash on hand (important runway metric)

**Key metrics:**
  - Cash runway (months until dilution needed)
  - Phase of lead asset
  - Total addressable market for lead indication
  - Competitive landscape in indication

---

## Industrials / Manufacturing (Cyclical)

**Why different:** Revenue and earnings are tied to the economic cycle.
  Peak earnings will not persist; trough earnings understate normal profitability.

**Valuation approach:**
  1. Normalize earnings to mid-cycle (5-year average or analyst mid-cycle estimate)
  2. Use normalized earnings in DCF — NOT current-quarter earnings
  3. EV/EBITDA at normalized margins vs. sector peers
  4. Be very careful with timing — buying at cyclical peak is dangerous

**Normalization process:**
  Step 1: Estimate "normalized revenue" (long-run trend line or average of cycle)
  Step 2: Apply "normal margin" (sector average or historical mid-cycle)
  Step 3: Use normalized EBIT as the basis for FCFF

**Key signals:**
  - Current earnings vs. 5-year average (above = peak, below = trough)
  - Backlog trend (leading indicator)
  - Capacity utilization
  - PMI and industrial production index (macro backdrop)

---

## Retail

**Why different:** Operating leases are large, off-balance-sheet financial commitments.
  They are economically equivalent to debt.

**Valuation approach:**
  1. Capitalize operating leases and add to debt
     PV(Leases) = Annual_Lease_Payment × (1/r) × [1 - 1/(1+r)^N]
  2. Add lease obligation to total debt; add back lease expense to EBITDA → EBITDAR
  3. Use EV/EBITDAR as primary comparable
  4. Same-store sales growth is the key driver — always check

**Key metrics:**
  - Same-Store Sales (SSS) growth — organic unit economics
  - Gross margin trend
  - Inventory turns
  - E-commerce penetration
  - Lease expiry schedule (restructuring flexibility)

---

## Consumer Staples / Defensive

Relatively stable earnings, strong dividends. DCF works well.
- Use two-stage DCF (low growth but persistent)
- Dividend Discount Model is appropriate given stable payouts
- EV/EBITDA and P/E both work given earnings quality
- Premium justified by: pricing power, brand strength, geographic diversification

---

## Utilities

Regulated businesses with predictable cash flows and mandated returns.
- Rate-of-return regulation sets allowed ROE (typically 9-11%)
- P/B is a key anchor: utilities should trade near book value if allowed ROE ≈ Ke
- P/E and EV/EBITDA both applicable
- DCF works well with stable, regulated terminal growth assumption (=inflation, ~2%)
- Primary risk: rising interest rates (utilities are bond-like; duration risk is high)

---

## Communication Services / Telecom

High capex for network infrastructure. Large depreciation.
- Use EV/EBITDA (ignores D&A which is meaningful but non-cash for networks)
- Subscriber metrics and ARPU are the business drivers
- Network capex cycle is important: peak investment → margin expansion follows
- Watch out for goodwill impairment risk from overpriced acquisitions

---

## General Red Flags for Any Sector

1. Goodwill > 30% of total assets: acquisition risk, impairment possible
2. Non-recurring items > 15% of earnings in two consecutive years: probably recurring
3. Capex < D&A for 3+ years: underinvestment that cannot continue indefinitely
4. Revenue growth >> cash flow growth for 3+ years: check working capital trap
5. Management guidance cut more than twice in 18 months: credibility and forecasting problem
