# Red Flags — Qualitative Warning Signs
# Source: Damodaran "Investment Valuation" + general valuation practice
# These are checked programmatically by the orchestrator and surfaced in reports.

## Financial Statement Quality

### Earnings Quality (Damodaran Ch 9, p.248)

**Damodaran's Warning Signs Checklist:**
- Earnings growth outstripping revenue growth by a large magnitude year after year
  → May be efficiency gains, but persistent gaps suggest expense manipulation
- One-time or nonoperating charges occur frequently (different label each year)
  → "May reflect a conscious effort to move regular operating expenses into nonoperating items"
- Operating expenses as % of revenues swing wildly year to year
  → SG&A or other line items may include nonoperating expenses
- Company beats analyst estimates quarter after quarter for extended periods
  → Likely earnings management; "as growth levels off, this practice can catch up with them"
- Substantial proportion of revenues from subsidiaries or related holdings
  → Transfer pricing manipulation risk
- Accounting rules for inventory or depreciation changed frequently
- Acquisitions followed by miraculous increases in earnings
  → Acquisition strategy that claims instant success requires scrutiny
- Working capital ballooning as revenues and earnings surge
  → May indicate lending to customers to generate revenues

**Quantitative Checks:**
- Revenue growth >> cash flow growth for 3+ consecutive years
  → Possible revenue recognition issues or working capital trap
  → Check: Operating cash flow / Net income ratio (should be > 0.8 for quality earnings)

- Gross margin declining while revenue growing
  → Revenue quality issue — pricing pressure or unfavorable mix shift

- EBITDA >> operating cash flow (large difference)
  → Working capital is consuming cash faster than EBITDA implies
  → Receivables or inventory building is a cash trap

### Balance Sheet Red Flags
- Goodwill > 30% of total assets
  → Heavy acquisition history; impairment risk if deals underperform
  → Question: what was paid vs. what was received

- Accounts receivable growing faster than revenue (DSO expansion)
  → Either aggressive revenue recognition or customer payment stress

- Intangibles growing faster than revenue
  → Possible capitalization of expenses that should flow through income

- Off-balance-sheet obligations (operating leases, pension deficits, contingent liabilities)
  → Must be added to debt for true leverage calculation

### Cash Flow Red Flags
- Capex << Depreciation for 3+ years (ratio < 0.8 consistently)
  → Underinvestment — future competitive position and earnings may deteriorate
  → Exception: mature businesses in decline (acceptable if planned)

- Negative working capital trend (WC shrinking despite growing revenue)
  → Usually fine for retailers/SaaS (good thing)
  → Red flag if it means supplier pressure or customer payment issues

- Free cash flow consistently negative despite positive reported earnings
  → High growth consuming cash (acceptable if temporary)
  → Problem if the business model structurally cannot generate cash

---

## Leverage and Financial Risk

- Net Debt / EBITDA > 4x
  → HIGH LEVERAGE — consider contingent claims approach for equity valuation
  → Check debt maturity schedule: near-term maturities are existential risk

- Interest Coverage (EBIT / Interest Expense) < 1.5x
  → Minimal cushion; any earnings deterioration threatens solvency
  → Below 1.0x: not covering interest from operations

- Rapid debt growth without corresponding asset or earnings growth
  → Financial engineering, not value creation

- Variable rate debt > 50% of total debt in rising rate environment
  → Interest expense will grow significantly

---

## Management and Capital Allocation

- Share count growing while earnings shrinking
  → Dilution to fund losses; watch carefully

- Serial acquirer with goodwill > 50% of equity
  → Acquisition multiple expansion is not value creation
  → Damodaran: "Most acquisitions destroy value for the acquirer"

- Executive compensation as % of net income > 10%
  → Misaligned incentives

- Insider selling (net) in the insider-tracker data while company talks up prospects
  → Walk vs. talk divergence — actions matter more than words

- Frequent guidance cuts (>2x in 18 months)
  → Management credibility problem AND visibility into the business is poor

- Stated ROE >> adjusted ROE (after adding back goodwill)
  → Returns look better without the true cost of acquisitions
  → Use RONIC (Return on New Invested Capital) including goodwill

---

## Valuation-Specific Risks

- Terminal value > 80% of DCF value
  → Model is highly sensitive to long-run assumptions
  → Report as a RANGE, not a point estimate

- DCF and relative valuation diverge by > 30%
  → One method (or both) may have flawed assumptions
  → Must investigate and explain before concluding undervaluation

- "Cheap on P/E" but negative FCF
  → Accrual earnings are not the same as cash earnings
  → Check FCF yield independently

- Low multiple vs. sector but lower growth than sector
  → Not cheap — the discount may be entirely justified by lower growth
  → Apply PEG ratio or justified multiple analysis

- Sustained trading below book value
  → Either asset impairment is needed OR the market sees long-term ROE < Ke
  → Not automatically cheap; often a value trap

---

## Insider Signal Cross-Reference

When combining with insider-tracker data:

POSITIVE COMBINATION:
- High conviction insider buying + DCF undervaluation → strong thesis
- Insider cluster + share buyback + undervalued on multiples → strongest signal
- CEO/Officer buying own shares + trading below justified P/E → notable

NEGATIVE COMBINATION (dilutes the insider signal):
- Insider buying + net insider selling by different insiders in same period → mixed signal
- Insider buying + rapidly rising share count (dilution) → partial offset
- Insider buying + worsening balance sheet → may be "catching a falling knife"

NEUTRAL:
- Insider buying + fair/overvalued on DCF → insider may have information advantage,
  OR insider may be wrong (insiders buy at all stages of the cycle)
- Insider buying + no fundamental thesis → use as a catalyst to research, not a conclusion

---

## Macroeconomic Context to Flag

- Rising interest rates environment: impacts WACC and terminal value significantly
  → Re-run sensitivity at WACC +200bp to stress-test
  → Long-duration assets (high P/E, low yield) most affected

- Recession risk: for cyclical companies, normalize down not up
  → Do not anchor to current earnings if a downturn is likely

- Currency risk: for international revenue, check USD strength impact
  → Particularly relevant for US companies with >40% international revenue

---

## When to FLAG but NOT BLOCK the Valuation

Red flags are warnings, not automatic disqualifiers. The correct response is:
1. Surface the flag clearly in the report
2. Quantify the impact where possible
3. Run a "bear case" scenario addressing the flag
4. Let the analyst make the final judgment

Only the distress check (interest coverage < 1.5x, ND/EBITDA > 4x) should
switch the PRIMARY model — everything else is commentary.
