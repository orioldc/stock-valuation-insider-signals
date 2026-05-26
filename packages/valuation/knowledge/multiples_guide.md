# Multiples Guide — When and How to Use Each Multiple
# Source: Damodaran "Investment Valuation" Chapters 17-20, 34

## Core Principle
Every multiple has an underlying DCF model embedded in it.
If you understand what drives a multiple, you will know when it works and when it misleads.

---

## Chapter 17: Fundamental Principles of Relative Valuation (pp.453–467)

### Why Relative Valuation is Popular (pp.453–454)
Three reasons multiples dominate practice:
1. **Speed**: A relative valuation can be done with far fewer assumptions and far
   more quickly than a DCF. You need only a peer group and a multiple.
2. **Simplicity**: Easier to present and explain — "trades at 15x vs sector 20x."
3. **Market-reflective**: Reflects current market pricing, which may matter more
   to traders than intrinsic value.

**Pitfalls**:
- Relative valuation anchors on PRICE, not VALUE. If the sector is overvalued,
  the "cheap" company may still be overvalued in absolute terms.
- Easy to manipulate by choosing flattering peer groups or multiples.
- "The key is to make sure that you are aware of the biases that exist in
  estimates and build in safeguards against these biases." (p.454)

### Standardized Values and Multiples (pp.455–456)

Multiples are standardized estimates of price by dividing by a common metric:

1. **Earnings Multiples**: P/E, P/E relative, PEG
2. **Book Value or Replacement Value Multiples**: P/B, Tobin's Q (market value / replacement cost)
3. **Revenue Multiples**: P/S, EV/Sales
4. **Sector-Specific Multiples**: EV/Subscribers, EV/kWh, EV/Beds, etc.

The analyst must choose which standardizing variable best reflects what creates
value in the company's sector. (See Table 34.1 below for sector mapping.)

### The Four Basic Steps to Using Multiples (pp.457–466)

#### Step 1: Definitional Tests — Define the Multiple Consistently (p.457)

**Consistency test**: numerator and denominator must both be equity or both be firm.
```
EQUITY multiples: Price / [EPS, BV_equity, Sales_per_share, CF_per_share]
  → Numerator is market cap or price per share
  → Denominator is an equity-level metric

FIRM multiples: EV / [EBITDA, EBIT, FCFF, Revenue, Invested_Capital]
  → Numerator is enterprise value (equity + debt - cash)
  → Denominator is a pre-debt metric
```

**Common error**: using EV/Net_Income (mixes firm numerator with equity denominator)
or Market_Cap/EBITDA (mixes equity numerator with firm denominator).

**Uniformity test**: the multiple must be computed the same way across all
companies in the peer group. If one company uses diluted EPS and another uses
basic EPS, the comparison is invalid. Same for trailing vs forward, GAAP vs non-GAAP.

#### Step 2: Descriptive Tests — Describe the Distribution (pp.458–460)

Before using any multiple, examine its cross-sectional distribution:
- **Mean**: pulled by outliers; often meaningless for multiples (p.458)
- **Median**: more robust; PREFERRED for sector comparisons (p.458)
- **Standard deviation**: measures dispersion — high σ means the "average" is unreliable
- **Percentiles**: where does your company fall in the distribution?

**Biases to watch for** (pp.459–460):
1. **Negative-earnings firms are excluded** from P/E distributions. This creates
   survivorship bias: the average P/E of "profitable tech companies" overstates
   what a typical tech company trades at. (Figure 17.2, p.460)
2. **Outliers skew the mean**: a few firms with P/E > 200 can pull the average
   P/E to 40x even if the median is 20x. ALWAYS use median.
3. **Changing composition**: the set of comparable firms changes as firms enter/exit
   profitability. Year-over-year multiple comparisons are misleading if the sample changed.

#### Step 3: Analytical Tests — Analyze the Determinants (pp.461–462)

**THIS IS THE MOST IMPORTANT STEP.** Every multiple is driven by a small set of
fundamental variables (companion variables). These come from the DCF derivation:

##### Companion Variable Derivations (p.461)

Start from the stable-growth Gordon Growth model for equity:
```
P₀ = DPS₁ / (Ke - gₙ) = EPS₀ × Payout × (1 + gₙ) / (Ke - gₙ)
```

Divide both sides by the relevant denominator to get each justified multiple:

| Multiple | Justified Formula | Companion Variables |
|----------|-------------------|---------------------|
| **P/E** | Payout × (1 + g) / (Ke − g) | Payout ratio ↑, Growth ↑, Risk (Ke) ↓ |
| **P/B** | ROE × Payout × (1 + g) / (Ke − g) | ROE ↑, Payout ↑, Growth ↑, Risk ↓ |
| **P/S** | Net_Margin × Payout × (1 + g) / (Ke − g) | Margin ↑, Payout ↑, Growth ↑, Risk ↓ |
| **EV/EBITDA** | (1 − t)(1 − Reinv/EBITDA) / (WACC − g) | Tax ↓, Reinvestment ↓, Growth ↑, WACC ↓ |
| **EV/Capital** | (ROIC − g) / (WACC − g) | ROIC ↑, WACC ↓ |
| **EV/Sales** | After-tax_margin × (1 − Reinv/Revenue) / (WACC − g) | Margin ↑, Reinvestment ↓, WACC ↓ |
| **EV/FCFF** | 1 / (WACC − gₙ) | Only growth and WACC matter |

**Key insight** (p.462): "If a company has a high PE ratio, it should also have
above-average growth, lower risk, or a higher payout ratio. If it does not, it is
overvalued." The companion variables explain WHY a multiple should be high or low.

#### Step 4: Application Tests — Apply Carefully (pp.463–466)

##### What Is a "Comparable" Firm? (p.463)
"A comparable firm is one with similar cash flows, growth potential, and risk."
(p.463)

In practice:
- **Same sector** is necessary but NOT sufficient
- Must also match on **growth rate, risk (beta/leverage), and ROIC**
- The more different the comparable is on these dimensions, the less reliable
  the comparison
- "If you define comparable firms as other firms in the same business, you may
  end up with a list that is too small... on the other hand, if you are not careful
  about how you control for differences, you end up with a meaningless comparison." (p.463)

##### Three Ways to Control for Differences (pp.463–466)

**Method 1: Modified Multiples** (p.463)
Divide the multiple by the companion variable to create a "controlled" multiple:
```
PEG = P/E ÷ Expected_Growth_Rate(%)
Price/BV/ROE = (P/B) ÷ ROE
EV/EBITDA/WACC = (EV/EBITDA) ÷ (1/WACC)
```

Implicit assumptions of PEG (p.463):
- Assumes LINEAR relationship between P/E and growth (not true — the relationship
  is actually convex: doubling growth more than doubles fair P/E)
- Assumes all other variables (risk, payout) are held constant
- Use as a quick filter, NOT as a precise valuation

**Method 2: Companion Variable Adjustment** (p.464)
Manually adjust the multiple for the most important companion variable difference:
```
If sector_avg P/E = 20x at 10% growth
And your company grows at 15%:
Adjusted P/E = 20 × (15/10) = 30x  (linear scaling — approximate)
```
Better: use the justified P/E formula directly with company-specific inputs.

**Method 3: Statistical Regression** (pp.465–466) — MOST RIGOROUS

Run a cross-sectional regression of the multiple against its companion variables
across all firms in the sector (or market):

```
Predicted P/E = a + b × Expected_Growth + c × Payout + d × Beta
```

Then compare actual P/E to predicted P/E:
- Actual > Predicted → overvalued relative to fundamentals
- Actual < Predicted → undervalued relative to fundamentals

**Illustration 17.1 (p.465): Beverage sector PE regression**
Simple regression: PE = 11.02 + 61.33 × Expected_Growth (R² = 0.458)
- Coca-Cola: actual PE = 44.33, predicted = 11.02 + 61.33 × 0.161 = 20.87
  → ~24 turns above predicted → overvalued relative to growth

**Illustration 17.2 (p.466): Multiple regression**
PE = 21.95 + 63.83 × Growth − 21.59 × Payout − 3.78 × Beta (R² = 0.512)
- Better fit; payout is NEGATIVE (counter-intuitive but firms that pay out
  more may signal lower reinvestment opportunities)

**Practical limitation of regressions** (p.466):
- Multicollinearity: growth, payout, and risk are correlated
- Small sample sizes within sectors → unstable coefficients
- Solution: run market-wide regressions with industry dummies, or
  accept the noise and focus on large deviations (>1 std dev)

### Reconciling Relative and DCF Valuations (p.467)

When DCF and relative valuations disagree:
1. **DCF > Relative**: market is pricing the sector cheaply, or the company's
   fundamentals justify a premium that the sector doesn't reflect
2. **DCF < Relative**: sector may be overvalued, or company is priced at a
   premium to its intrinsic fundamentals
3. **Both agree**: strongest signal — convergence from two independent methods

"The difference in values between discounted cash flow and relative valuation
comes from different views about market efficiency... In discounted cash flow
valuation, we assume that markets make mistakes on individual stocks but that
they correct these mistakes over time... In relative valuation, we assume that
while markets make mistakes on individual stocks, they are right, on average,
in how they price stocks." (p.467)

**Agent rule**: Always present both DCF and relative values. When they diverge
by >30%, explicitly discuss which assumptions drive the gap. Never present
relative valuation alone as a definitive value estimate.

---

## Chapter 18: Earnings Multiples (pp.468–508)

### Price-Earnings Ratio — Definition (pp.468–470)

PE = Market price per share / Earnings per share

**Which earnings?** (p.468): current, trailing, or forward EPS. These can differ
substantially for high-growth or cyclical firms.
- **Current PE**: price / EPS in most recent fiscal year
- **Trailing PE**: price / EPS over trailing 12 months
- **Forward PE**: price / expected EPS next year (analyst consensus)
- Always use **diluted** EPS (accounts for options and convertibles)
- Must be consistent: compare trailing-to-trailing or forward-to-forward

**Management options** (p.469): High-growth firms have many employee options.
Differences between diluted and primary EPS can be large. Use diluted.

### Cross-Sectional Distribution of PE (pp.470–471, Table 18.1)

Table 18.1 — PE Ratios for US Companies, January 2011:
```
              Current PE   Trailing PE   Forward PE
Average         49.82        38.19         21.40
Median          19.50        17.79         16.16
25th pctile     13.44        11.99         12.44
75th pctile     33.44        28.02         22.13
Minimum          0.01         0            1.82
Maximum       11,270       6,680.7        3,928
Std dev        3,316       3,374          717
Sample size    5,928       5,928          5,928
```

**Key observations**:
- Mean >> Median → distribution is heavily right-skewed (outlier firms with PE > 200)
- Forward PE has lowest dispersion → most useful for comparison
- Maximum PE > 10,000 shows how extreme outliers can be
- **Agent rule**: ALWAYS use median (not mean) for sector PE benchmarks (p.470)

### Determinants of the PE Ratio — Stable Growth (p.471)

From the stable-growth Gordon Growth model:
```
P₀/EPS₀ = Payout_ratio × (1 + gₙ) / (Ke - gₙ)

Simplified (forward PE):
P₀/EPS₁ = Payout_ratio / (Ke - gₙ)

Where Payout_ratio = 1 - gₙ/ROE_st  (from fundamental growth equation)
```

The PE ratio increases with:
1. **Higher payout** (more cash returned to shareholders)
2. **Higher growth** (larger numerator)
3. **Lower Ke** (lower risk = lower discount rate = smaller denominator)

### Two-Stage Justified PE (pp.472–473)

For a firm with n years of high growth followed by stable growth:
```
         Payout_hg × (1+g) × [1 - (1+g)ⁿ/(1+Ke_hg)ⁿ]
PE  =   ─────────────────────────────────────────────────
                        Ke_hg - g

             Payout_st × (1+g)ⁿ × (1+gₙ)
      +   ─────────────────────────────────────
           (Ke_st - gₙ) × (1+Ke_hg)ⁿ

Where:
  g      = growth rate during high-growth phase
  gₙ     = terminal/stable growth rate
  n      = length of high-growth period (years)
  Ke_hg  = cost of equity during high-growth phase
  Ke_st  = cost of equity in stable growth
  Payout_hg = 1 - g/ROE_hg
  Payout_st = 1 - gₙ/ROE_st
```

**Illustration 18.1 (p.473): High-Growth Firm**
g=25%, n=5 years, Payout_hg=20%, Ke=11.5%, gₙ=8%, Payout_st=50%
→ Justified PE = 28.75x

**Illustration 18.2 (p.473): P&G**
g=10%, n=5 years, Payout_hg=50%, gₙ=3%, Payout_st=75%, ROE_st=12%
→ Justified PE = 18.04x (multiplied by EPS of $3.82 → $68.90, matching DDM)

### PE Sensitivity to Growth, Risk, and Interest Rates (pp.474–479)

**PE and Growth** (p.474, Figure 18.2):
- Relationship is approximately linear for moderate growth rates
- PE is MORE sensitive to growth changes at low interest rate environments
  (in a low-rate world, small changes in growth expectations cause large PE swings)

**PE and Risk** (p.476, Figure 18.4):
- As beta increases, PE decreases across all growth scenarios
- For very risky, high-growth firms: risk reduction increases PE more than
  growth increases do → risk matters more than growth at the margin

**PE across Time** (pp.477–479):
- Earnings yield (EP = 1/PE) tracks T-bond rates over time (Figure 18.5)
- Regression (p.478): EP = 0.0261 + 0.6689 × T-bond_rate − 0.9655 × (T-bond − T-bill)
  R² = 0.478 → "Every 1% increase in T-bond rate increases EP by 0.67%"
- At T-bond = 3.29% → predicted PE_2011 = 1/0.0481 = 20.77

**PE across Countries** (p.480, Illustration 18.6):
- PE = 42.62 − 360.9 × 10yr_rate + 846.6 × (10yr − 2yr_slope)  R² = 59%
- Countries with higher interest rates → lower PE
- Positively sloped yield curves → higher PE (signal of expected growth)

**PE with Emerging Markets** (p.481, Illustration 18.7):
- PE = 16.16 − 7.94 × Interest_rates + 15.40 × Real_growth − 0.112 × Country_risk
  R² = 74%
- Real growth and country risk are more important than interest rates

### Comparing PE Ratios across Firms (pp.482–486)

**Market-Wide Regression** (p.485):
```
Predicted PE = 6.37 + 83.56 × Expected_Growth + 5.06 × Beta + 5.83 × Payout_ratio
```
Compare actual PE to predicted → over/undervalued relative to fundamentals.

**Table 18.2 — Correlation between Independent Variables** (p.484):
```
              PE    Growth   Beta    Payout
PE           1.000
Growth       0.266   1.000
Beta        -0.102  -0.401   1.000
Payout      -0.024  -0.241  -0.174   1.000
```
All significant at 1%. Growth and beta negatively correlated (high-growth firms are
riskier). Multicollinearity is a problem → coefficients may be unstable.

**Sector Regression** (p.483, Illustration 18.8 — Global Telecom):
```
PE = 13.12 + 121.22 × Expected_Growth − 13.86 × Emerging_Market_Dummy   R² = 66%
```
Firms with higher growth → higher PE. Emerging market firms trade at ~14 PE points
lower due to higher risk.

### Normalizing Earnings for PE (p.486)

For **cyclical firms**: average earnings over the business cycle to normalize.

For **R&D-intensive firms** (p.486):
```
PE_before_R&D = Market_Cap / (Net_Income + R&D)        ← partial adjustment
PE_R&D_adjusted = Market_Cap / (Net_Income + R&D − R&D_Amortization)  ← full adjustment
```
"To complete the adjustment, you would need to capitalize R&D and compute the
amortization of R&D expenses as was done in Chapter 9." (p.486)

**Agent rule**: When computing PE for tech/pharma companies, prefer the R&D-adjusted
version to make PE comparable across firms with different R&D intensities.

### The PEG Ratio (pp.487–493)

**Definition**: PEG = PE / Expected_Growth_Rate(%)

**From the two-stage model** (p.490):
```
PEG = PE_two_stage / (g × 100)
```
"Even a cursory glance at this equation suggests that analysts who believe that
using the PEG ratio neutralizes the growth effect are wrong. Instead of disappearing,
the growth rate becomes even more deeply entangled in the multiple." (p.490)

**Key properties of PEG** (pp.491–493):
1. PEG initially DECREASES as growth increases, but reverses at very high growth
   (U-shaped relationship — Figure 18.8)
2. PEG should be HIGHER for firms with higher ROE (for a given growth rate),
   because: Expected_growth = ROE × (1 − Payout) → higher ROE means higher payout
   for the same growth → higher PE → higher PEG
3. PEG should be LOWER for riskier firms (higher beta → lower PE → lower PEG)
4. PEG should be HIGHER for firms with higher payout ratios

**Cross-sectional stats** (Table 18.3, p.489):
```
              Market     Technology
Firms          1,914       116
Average PEG    6.82        4.68
Median PEG     2.13        2.06
Std dev       73.21       12.8
```

**Using PEG for Comparisons** (p.493):
- Direct PEG comparison works ONLY if risk, payout, and ROE are similar
- "If this were the case, however, you could just as easily compare PE ratios."
- PEG comparisons that ignore risk and payout can be misleading
- **Agent rule**: Report PEG as supplementary information. Never use PEG alone
  as a valuation signal. Always accompany with justified PE analysis.

### Other PE Variants (pp.494–497)

**Relative PE** (p.494): PE_firm / PE_market
- Useful for cross-time and cross-market comparisons
- Eliminates the impact of interest rate and market-wide sentiment changes
- A relative PE of 0.5 means firm trades at half the market's PE

**Price to Future Earnings** (p.496):
- For firms with negative or distorted current earnings
- Estimate EPS in year 5, compute PE, discount back to present
- Example (Amgen, p.496): use EPS after R&D losses stop
  Price/EPS_adjusted = $13.28 / $0.15 = 88.53 → but this is before R&D adjustment
  After capitalizing R&D: Price/EPS_R&D_adj = $13.28 / $0.15 = different

### Enterprise Value to EBITDA — Full Derivation (pp.498–507)

**Why EV/EBITDA** (p.498–499):
- EBITDA is a pre-debt, pre-tax, pre-reinvestment measure
- Fewer firms have negative EBITDA than negative earnings → broader applicability
- Can compare firms with different capital structures and depreciation policies

**Derivation** (pp.604–605):
```
FCFF = EBIT(1-t) − (Capex − DA) − ΔWC
     = EBITDA(1-t) − DA(1-t) − (Capex − DA + ΔWC)

For a stable-growth firm: EV = FCFF₁ / (WACC − g)

EV         (1-t)     DA    (1-t)     Reinvestment
────── = ─────── − ────── × ─────── − ──────────────
EBITDA   WACC−g    EBITDA   WACC−g    EBITDA(WACC−g)
```

**Five Determinants of EV/EBITDA** (p.604):
1. **Tax rate**: lower taxes → higher EV/EBITDA (more after-tax cash flow)
2. **DA/EBITDA ratio**: firms that derive more EBITDA from D&A (vs real cash flow)
   should trade at LOWER multiples — "D&A is not real cash flow"
3. **Reinvestment requirements**: higher reinvestment needs → lower EV/EBITDA
4. **Cost of capital (WACC)**: lower WACC → higher multiple
5. **Expected growth**: higher growth → higher multiple (but requires reinvestment)

**Illustration 18.15 — Castle Cable** (p.605):
- WACC=10%, tax=36%, operating income=36% of EBITDA, capex=10%, DA=20% of EBITDA
- Stable 5% growth in perpetuity
- Justified EV/EBITDA derived from fundamental inputs

**Illustration 18.16 — Steel Sector Regression** (p.607):
```
EV/EBITDA = 8.65 − 7.20 × Tax_Rate − 8.08 × (DA/EBITDA)   R² = 30%
```
- Growth and WACC excluded because they were similar across steel firms
- "At 5.60× EBITDA, Birmingham Steel is overvalued" (predicted = 4.45)

**EV/EBITDA with Cross Holdings** (pp.602–603):
Three approaches to handle subsidiaries:
1. Use parent-only EBITDA (exclude subsidiary's EBITDA from denominator)
2. Use consolidated EBITDA but add back market value of minority holdings
3. Subtract proportional share of subsidiary EBITDA from consolidated

### Value/FCFF and Value/EBIT Variants (p.498)

```
Value/FCFF  = 1 / (WACC − g)              ← simplest enterprise multiple
Value/EBIT(1-t) = (1 − RIR) / (WACC − g)  ← where RIR = reinvestment rate
```
These are cleaner than EV/EBITDA because they use actual cash flows or after-tax
operating income, but fewer firms have clean FCFF data available.

### Conclusion (p.508)

"The price-earnings ratio and other earnings multiples are ultimately determined
by the same fundamentals that determine value in a discounted cash flow
model — expected growth, risk, and cash flow potential. Firms with higher growth,
lower risk, and higher payout ratios, and cash flow potential remaining the same,
should trade at much higher multiples of earnings than other firms." (p.508)

"There are several ways in which earnings multiples can be used in valuation. One
is to compare multiples across a narrowly defined group of comparable firms, and
to control for differences in fundamentals using statistical techniques. Another way
is to expand the definition of a comparable firm to the entire sector (such as technology)
or the market, and to control for differences in fundamentals using statistical
multiples of operating income."

---

## Damodaran Table 34.1 — Most Widely Used Multiples by Sector (Ch 34, p.936)

| Sector | Multiple | Rationale (from book) |
|--------|----------|-----------------------|
| Cyclical manufacturing | PE, relative PE | Often with normalized earnings |
| High tech, high growth | PEG | Big differences in growth across firms make PE hard to compare |
| High growth / negative earnings | PS, VS (EV/Sales) | Assume future margins will be positive |
| Infrastructure | EV/EBITDA | Firms have losses in early years; reported earnings vary by depreciation method |
| REIT | P/CF | Restrictions on investment policy; large depreciation charges make cash flows better measure than equity earnings |
| Financial services | PBV (P/B) | Book value often marked to market |
| Retailing | PS (similar leverage) or VS (different leverage) | Use equity or firm multiple depending on leverage comparability |

### How to pick the right multiple (Ch 34, p.935):
1. **Fundamentals approach**: Use the variable most highly correlated with firm's value
2. **Statistical approach**: Regress multiples against fundamentals; highest R-squared wins
3. **Conventional approach**: Use what the sector traditionally uses (e.g. P/B for banks)

Ideally all three converge. When the conventional multiple does NOT reflect fundamentals
(sector in transition or evolving), you will get misleading estimates of value.

---

## Multiple Selection by Company Type (extended)

| Company Type           | Primary Multiple  | Secondary         | Avoid        |
|------------------------|-------------------|-------------------|--------------|
| Technology (profitable)| EV/EBIT           | EV/EBITDA, P/E   | P/B          |
| Technology (unprofitable)| EV/Sales         | EV/Gross_Profit   | P/E, P/B     |
| Software / SaaS        | EV/ARR or EV/Sales| EV/EBIT           | P/E          |
| Financial (Bank)       | P/B               | P/E               | ALL EV/*     |
| Financial (Insurance)  | P/B               | P/E               | ALL EV/*     |
| Real Estate / REIT     | P/FFO             | P/B               | P/E          |
| Energy / Oil & Gas     | EV/EBITDA         | EV/DACF           | P/E          |
| Mining / Commodities   | EV/EBITDA         | EV/Reserves       | P/E          |
| Consumer Discretionary | EV/EBITDA         | P/E               | P/B          |
| Consumer Staples       | EV/EBITDA         | P/E               | P/B          |
| Healthcare (profitable)| EV/EBITDA         | P/E               | EV/Sales     |
| Healthcare (pipeline)  | EV/Sales          | Contingent Claims | P/E, P/B     |
| Retail                 | EV/EBITDAR        | P/E               | P/B          |
| Industrials            | EV/EBITDA         | EV/EBIT           | P/B          |
| Utilities              | P/E               | EV/EBITDA         | EV/Sales     |

Note: EBITDAR = EBITDA + Rent (for retail/airline, leases are effectively debt)

---

## EV/EBITDA

**What it measures:** Enterprise value relative to pre-interest, pre-tax, pre-D&A earnings.
**When to use:** Default for comparing companies across different capital structures.
**Limitation:** Does NOT penalize high-capex businesses. A company burning $2B in capex looks
  the same as one with $200M if EBITDA is equal. Use EV/EBIT instead for capex-heavy firms.
**Underlying DCF:** EV/EBITDA ≈ f(ROIC, growth, WACC, reinvestment rate)

**Justified EV/EBITDA:**
  = [(1 - reinvestment_rate) × (1 - tax_rate)] / (WACC - g)

Higher ROIC → lower reinvestment rate needed for same growth → higher justified EV/EBITDA.

---

## P/E Ratio

**What it measures:** Price per dollar of earnings.
**When to use:** Stable, profitable companies with clean earnings and stable leverage.
**Limitations:**
  - Distorted by one-time items (use normalized EPS)
  - Not comparable across companies with different leverage
  - Meaningless if earnings < 0

**Justified P/E (Damodaran):**
  Justified P/E = [Payout_Ratio × (1 + g)] / (Ke - g)

  Where:
    Payout_Ratio = Dividends / Net_Income (or 1 - Retention_Ratio)
    g = expected sustainable growth rate
    Ke = cost of equity

  Compare company P/E to justified P/E:
    - If actual P/E >> justified P/E: potentially overvalued
    - If actual P/E << justified P/E: potentially undervalued

---

## PEG Ratio

**Formula:** PEG = P/E ÷ (5-year expected EPS growth rate, in %)
**Rule of thumb (Peter Lynch):** PEG < 1 = potentially cheap; PEG > 2 = expensive
**Damodaran's view:** The PEG is useful but assumes a LINEAR relationship between P/E and growth.
  In reality the relationship is non-linear. Use as a quick filter only, not a precise valuation.

---

## EV/Sales (Revenue multiple)

**When to use:**
  - Company has negative or volatile EBITDA/earnings
  - High-growth revenue-stage businesses
  - Technology companies scaling but not yet profitable
  - Cross-company comparison when margins vary widely

**Underlying logic:** EV/Sales = EBITDA_Margin × EV/EBITDA
  Two companies with same revenue but different margins should have different EV/Sales.
  ALWAYS adjust for margin differences when using EV/Sales.

**Adjusted EV/Sales:** Compare EV/Sales at similar EBITDA margins.
  If company A has 30% margin and company B has 10%, A should trade at 3x B's EV/Sales.

---

## P/B Ratio (Price-to-Book)

**When to use:** Financial institutions primarily. Asset-heavy companies secondarily.
**Why financial firms:** Banks create value through their equity capital base.
  Tangible book value is the most meaningful anchor.

**Justified P/B:**
  P/B = ROE / Ke

  Where:
    ROE = Return on Equity (use trailing average)
    Ke = Cost of Equity

  Interpretation:
    - ROE > Ke → P/B should be > 1 (firm earns above its cost)
    - ROE < Ke → P/B should be < 1 (firm destroys value)
    - Trading below "justified P/B" → potential undervaluation

**Why NOT for operating companies:** Intangibles, goodwill, and brand are not on the balance sheet.
  Apple's "book value" is meaningless because the iPhone franchise is worth far more.
  Use for banks (assets are financial instruments at market/amortized value).

---

## EV/FFO (Funds From Operations) for REITs

**What it is:** FFO = Net Income + Depreciation - Gains on property sales
**Why:** Depreciation distorts REIT earnings because real estate often appreciates.
  FFO strips out the depreciation to give cash-based earnings.

**P/FFO for REITs** is equivalent to P/E for industrial companies.
The REIT sector average P/FFO typically ranges from 15-20x for core REITs.

---

## Sector-Specific Multiples

| Sector              | Special Multiple    | Notes                                    |
|---------------------|---------------------|------------------------------------------|
| Oil & Gas E&P       | EV/DACF             | Debt-adjusted cash flow (removes debt service) |
| Oil & Gas           | EV/Reserves ($/BOE) | Value per barrel of oil equivalent       |
| Mining              | EV/Reserves         | Value per tonne of ore                   |
| Internet/SaaS       | EV/ARR              | Annual recurring revenue                 |
| Telecom             | EV/EBITDA           | High capex; EBITDA better than earnings  |
| Airlines            | EV/EBITDAR          | Adds back lease expense                  |
| Hospital/Healthcare | EV/Beds             | Operational capacity metric              |
| Media/Content       | EV/Subscribers      | Growth-stage metric                      |

---

## How to Use Sector Averages (from Damodaran's data)

1. Fetch Damodaran's current-year sector multiples
2. Check that your company's industry matches the sector classification
3. Run the multiple on the company's own metrics
4. Note the premium or discount vs. sector average
5. EXPLAIN the premium/discount — it must be justified by:
   - Higher growth
   - Better margins
   - Lower risk
   - Better capital allocation
   - Superior competitive position

Without explanation, "cheaper than sector" is not an investment thesis.

---

## The Comparables Problem

The danger of relative valuation: if the entire sector is overvalued,
comparing to the sector average just tells you "less overvalued than peers."

ALWAYS sanity-check relative valuations against a DCF.
If both are overvalued, say so.
