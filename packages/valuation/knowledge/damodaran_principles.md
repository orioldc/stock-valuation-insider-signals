# Damodaran Investment Valuation — Core Principles
# Source: "Investment Valuation" by Aswath Damodaran (3rd Ed.)
# Key chapter summaries encoded for agent context loading

## Chapter 1-2: What is Value? How Do We Value?

### Four Approaches to Valuation (Figure 34.1)

1. **Asset-Based Valuation**
   - Liquidation Value: what you get if you sell assets today
   - Replacement Cost: what it costs to replicate the business
   - Best for: firms with separable, marketable assets (real estate, natural resources)

2. **Discounted Cash Flow (DCF) Valuation**
   - Intrinsic value = PV of expected future cash flows
   - Best for: firms with cash flows that can be estimated, even if currently negative
   - Key inputs: cash flows, discount rate, growth rate, terminal value

3. **Relative Valuation (Multiples)**
   - Value based on how similar assets are priced in the market
   - Best for: when you can find comparable companies at similar stages
   - Limitation: only tells you relative value, not absolute value

4. **Contingent Claim Models (Real Options)**
   - Equity is a call option on firm value; patents are options on future cash flows
   - Best for: distressed firms, firms with unexploited resources, early-stage firms

### Intrinsic Value vs. Price
- **Intrinsic value** = true economic worth based on fundamentals
- **Price** = what the market is currently willing to pay (includes sentiment, momentum)
- The gap between intrinsic value and price is the investment opportunity
- Gap may persist for 1-3 years; mean-reversion is not instant

### Value is NOT:
- The multiple other people are paying (circular)
- What you paid for it (sunk cost)
- What management says it's worth (self-interested)
- A formula output without judgment (garbage in, garbage out)

---

## Chapter 9: Measuring Earnings (pp.228–249)

### Accounting vs. Financial Balance Sheets (p.230)
The income statement provides measures of both operating income (EBIT) and equity
income (net income). Two critical considerations when using these as valuation inputs:
1. Get as **updated** an estimate as possible — use trailing 12-month (TTM) from
   quarterly reports, not just the latest annual (p.231, Illustration 9.1)
2. Reported earnings may bear little resemblance to true earnings — adjustments needed

### Three Types of Expense Misclassification (p.232)
Companies have three types of expenses:
1. **Operating expenses**: generate benefits only in the current period (e.g., labor)
2. **Capital expenses**: generate benefits over multiple periods (e.g., factory, R&D)
3. **Financial expenses**: cost of financing (e.g., interest on debt)

The two most common misclassifications:
- **R&D treated as operating expense** → should be capital (benefits span years)
- **Operating leases treated as operating expense** → really financing (pre-ASC 842)

### R&D Capitalization (pp.232–236, Illustration 9.2)
R&D expenses should be capitalized because they generate benefits over multiple periods.

**Step 1: Determine amortizable life by sector** (p.234):
- Pharmaceutical/biotech: ~10 years (long FDA approval + patent life)
- Technology/software: ~5 years (rapid obsolescence)
- Other industries: judgment based on product lifecycle

**Step 2: Capitalize R&D**
```
Value of research asset = Σ(R&D_expense_year_n × unamortized_fraction_n)
  where n goes from current year back to (amortizable_life) years ago
  unamortized fraction for year -k = max(0, 1 - k/amortizable_life)

Amortization this year = Σ(R&D_expense_year_n / amortizable_life)
  for each of the prior years within the amortizable life
```

**Step 3: Adjust operating income, net income, and capital**
```
Adjusted EBIT = Reported EBIT + Current R&D expense − R&D amortization this year
Adjusted Net Income = Reported NI + Current R&D − R&D amortization
  (Tax effect drops out because R&D is fully tax-deductible; p.235)
Adjusted Book Equity = Book Equity + Value of research asset (after-tax)
Adjusted Invested Capital = Invested Capital + Value of research asset
```

**Example — Amgen (Illustration 9.2, p.234–235)**:
- 10-year amortizable life, current R&D = $3,030M
- Value of research asset = $13,284M
- Amortization this year = $1,694M
- Adjusted EBIT: $5,594 + $3,030 − $1,694 = $6,930M
- ROE: unadjusted 23.5% → adjusted 18.6% (materially different!)
- Pre-tax ROIC: unadjusted 25.4% → adjusted 20.5%

### Operating Lease Capitalization (pp.238–239, Illustration 9.5)
**Note**: Post-ASC 842 (2019+), US firms capitalize leases on the balance sheet.
This adjustment is still relevant for: (a) verifying GAAP adjustments are correct,
(b) non-US firms under older standards, (c) adjusting operating income.

**Converting leases to debt** (p.238):
```
Lease_debt = PV of future lease commitments, discounted at firm's pretax Kd
Adjusted total debt = Conventional debt + Lease_debt
```

**Adjusting operating income — full method** (p.239):
```
Adjusted OI = Operating Income + Operating lease expense − Depreciation on leased asset
  where Depreciation on leased asset = Lease_debt / Lease_life (straight-line)
```

**Approximate method** (p.239, simpler):
```
Adjusted OI ≈ Operating Income + Lease_debt × Pretax Kd
```

**Example — The Gap (Illustration 9.5, p.239)**:
- PV of leases at 5.5% = $4,208M → added to debt
- Full method: Adjusted OI = $1,968 + $1,129 − $601 = $2,496M
- Approximate: $1,968 + $4,208 × 0.055 = $2,199M

### Capitalizing Other Operating Expenses (pp.236–237)
SG&A and customer acquisition costs may deserve capitalization for firms where
the benefit spans multiple periods (e.g., Amazon's customer acquisition, consulting
firms' training expenses). Procedure is identical to R&D: determine amortizable life,
cumulate expenses, compute unamortized asset, adjust operating income.

### Stock-Based Compensation (p.237, also Ch 3)
- SBC is a REAL COST (dilution to existing shareholders)
- GAAP does expense it, but management often presents "adjusted EBITDA" excluding SBC
- Do NOT use management's adjusted EBITDA that excludes SBC in valuation
- If SBC is > 10% of revenue, it warrants specific discussion

### One-Time, Recurring, and Unusual Items (pp.242–243)

**Three categories**:
1. **Truly one-time** (occurred only once in 10 years): Back out entirely.
   A large restructuring charge that is truly one-time can be excluded from
   normalized earnings.

2. **Recurring but irregular** (e.g., restructuring every 3 years): **Annualize**.
   If $1.5B restructuring every 3 years → deduct $500M/year from operating income
   as a recurring cost. "The best way to deal with such items is to normalize."

3. **Expenses that change year to year** but are genuinely recurring:
   Average items across time and deduct the average annually.
   These items should stay in operating income, just smoothed.

**Key principle (p.243)**: "The underlying principle is that earnings should include
only normal expenses on the part of companies to move normal operating expenses
into the nonrecurring column."

### Adjusting for Cross-Holdings (pp.244–245)

Three types of equity holdings in other firms:
1. **Minority passive** (< ~20%): receive dividends only → strip from income, value separately
2. **Minority active** (~20-50%): proportional income shown in income statement → strip out
3. **Majority active** (> 50%): consolidated operating income → subtract minority interests

**Safest approach**: Ignore cross-holding income when valuing operating assets.
Add back the market value of cross-holdings separately after computing operating value.

### Warning Signs in Earnings Reports (p.248)

Checklist to gauge the possibility of earnings manipulation:
- Earnings growth outstripping revenue growth by a large magnitude year after year
- One-time or nonoperating charges occur frequently (different label each year)
- Operating expenses as % of revenues swing wildly from year to year
- Company beats analyst estimates quarter after quarter for extended periods
- Substantial proportion of revenues from subsidiaries or related holdings
- Accounting rules for inventory or depreciation changed frequently
- Acquisitions followed by miraculous increases in earnings
- Working capital ballooning out as revenues and earnings surge

"None of these factors, by themselves, suggest that we distrust earnings... but
combinations of the factors can be viewed as a warning signal that the earnings
statement needs to be held up to higher scrutiny." (p.248)

---

## Chapter 10: From Earnings to Cash Flows (pp.250–269)

### Tax Rate Selection (pp.250–257)
- Use **effective** tax rate for the current year's NOPAT
- **Converge** toward the **marginal** tax rate over the projection period
- Terminal year: **always marginal** (21% for US post-TCJA, or weighted avg for multinationals)
- Firms with NOLs: start at 0% effective, exhaust NOLs year by year, then marginal
- Using effective rate throughout **overstates** intrinsic value (Conway example: $2,935M vs $1,957M)

### Reinvestment (pp.258–268)
- Net capex = Capex − Depreciation; normalize over 3-5 years if lumpy
- With R&D capitalization: Adjusted net capex = Capex + R&D − Depreciation − R&D_amortization
- Acquisitions: include in capex if you include acquisition-driven growth (must be consistent)
- Noncash WC = (Current assets − Cash) − (Current liabilities − Short-term debt)
- Project WC as % of revenues; use **industry average** for terminal year
- Negative WC changes are a cash source short-term but normalize in terminal year

---

## Chapter 8-12: Cash Flow Estimation

### FCFF vs. FCFE Decision
Use FCFF + WACC when:
- Capital structure is changing significantly
- Leverage is unstable or actively managed
- Company is being valued as an acquisition target (EV matters)
- Financial firm exclusion: NEVER use FCFF for banks/insurance

Use FCFE + Ke when:
- Capital structure is stable
- Firm is a financial institution
- Dividends closely approximate FCFE (stable payout ratio)
- You want to model equity value directly

### Negative FCF Companies
Can still be valued via DCF! Three approaches:
1. Normalize first: estimate what FCF would be at "normalized" margins
2. Build up: project revenue growth → margin expansion → FCF positive
3. Value via components: operating business value - value of losses being funded

Key question: Is the negative FCF temporary (investment phase) or structural?

---

## Chapter 13: Growth Rates

### The Fundamental Growth Equation
```
g = ROE × Retention_Ratio    [for equity]
g = ROIC × Reinvestment_Rate [for firm]
```

### Growth Rate Hierarchy (most to least reliable)
1. Fundamental growth (ROE × retention) — anchored in capital allocation economics
2. Historical growth (5-year CAGR) — backward-looking, may not predict future
3. Analyst estimates — forward-looking but optimistic and mean-reverting
4. GDP growth — floor/ceiling for long-run terminal growth

### Growth and ROIC
- High growth is valuable ONLY if ROIC > WACC
- If ROIC < WACC, growth DESTROYS value (more investment at below-cost returns)
- "Growth for growth's sake" is not a strategy; it's value destruction
- Fastest-growing firms are not necessarily best investments

### Growth Periods
- Short-term (1-5 years): can sustain above-average growth
- Medium-term (6-10 years): growth should converge toward industry average
- Terminal: must equal or be below long-run economy growth

---

## Chapter 16: Terminal Value

### The Terminal Value Problem
Terminal value often represents 60-80%+ of DCF value for growing firms.
This creates model sensitivity that cannot be eliminated — only acknowledged.

### Three Approaches to Terminal Value
1. **Stable Growth DCF** (Gordon Growth Model) — PREFERRED
   TV = FCF × (1+g) / (WACC - g)
   Assumes perpetual stable growth at rate g

2. **Multiple-Based Terminal Value**
   TV = EBITDA_final × sector_EV/EBITDA
   Cross-checks Gordon model; uses market pricing for terminal value

3. **Liquidation Value**
   TV = asset values at terminal year
   Conservative; appropriate for declining businesses

### Terminal Growth Rate Selection
- Cannot exceed nominal GDP growth in the long run (2-3% for US)
- Should not equal the current stage 1 growth rate (companies slow down)
- Consider: is the company in a shrinking industry? Then g < inflation is possible
- Conservative: match to 10-year Treasury yield (risk-free rate)

### Common Terminal Value Mistakes
- Using the current growth rate as terminal growth (unsustainable)
- Not reinvesting enough in the terminal year (need consistent ROIC × reinvestment)
- Ignoring competitive effects that compress margins over time
- Terminal capex < terminal D&A (businesses must invest to sustain even stable growth)

In terminal year: Reinvestment = g / ROIC
(If ROIC = 10% and g = 2.5%, reinvestment rate = 25%)

---

## Chapter 17-18: Relative Valuation

### The Logic of Multiples
Every multiple has an embedded DCF. For example:
- P/E = Payout_Ratio × (1+g) / (Ke - g)
- P/B = ROE / Ke (simplified Gordon Growth)
- EV/EBITDA = f(tax_rate, reinvestment, ROIC, WACC, g)

This means: when a multiple is "cheap" or "expensive," it implies something about
growth, risk, or return expectations. The analyst must verify whether those implications
are correct for the specific company.

### The Comparables Selection Problem
True comparables are hard to find. A perfect comparable would have:
- Same industry, same competitive position
- Same growth rate (both near-term and long-term)
- Same risk (business and financial)
- Same return on capital

In practice, use a portfolio of comparables and focus on the MIDDLE of the range.
Extremes in a peer group are outliers, not references.

### Price vs. Value in Multiples
Relative valuation anchors on market prices.
If the market has mispriced an entire sector, relative valuation doesn't help you.
Example: In 1999, tech P/Es were 50-100x. The "cheap" tech stock at 40x P/E was still overvalued.

---

## Chapter 34: Choosing the Right Model (Figure 34.1 Decision Tree)

The four key dimensions:
1. **Asset marketability**: can you sell assets individually at fair value?
   → Yes: asset-based or hybrid
   → No: DCF or relative

2. **Cash flow generation**: does the firm generate positive, estimable cash flows?
   → Yes: DCF (preferred)
   → No: normalize, then DCF, OR relative valuation on revenue/growth

3. **Unique characteristics**: is the firm a financial institution, REIT, commodity producer?
   → Yes: sector-specific adjustments required
   → No: standard DCF + relative

4. **Analyst context**: for a quick screen, relative is faster. For investment decisions, DCF is more rigorous.
   → Both should agree directionally; investigate when they diverge

---

## The Uncertainty Principle of Valuation

"All models are wrong; some are useful." — George Box
Applied to valuation: every DCF is wrong (assumptions are never exactly right).
But a DCF is useful if it forces you to be explicit about your assumptions and
quantifies the sensitivity of value to those assumptions.

The goal is not to find the "right" number. The goal is to:
1. Understand what assumptions are embedded in the current market price
2. Assess whether those assumptions are reasonable
3. Identify where your view differs from the consensus
4. Quantify the upside/downside of being right vs. wrong

Damodaran's advice: "In valuation, it is better to be approximately right than precisely wrong."
