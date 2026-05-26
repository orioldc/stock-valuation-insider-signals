# Decision Tree Rules — Damodaran Chapter 34
# Source: "Investment Valuation" by Aswath Damodaran (3rd ed.), Chapter 34, pp. 925–938

## The Core Problem (p.925)

"The problem in valuation is not that there are not enough models to value an asset,
it is that there are too many. Choosing the right model to use in valuation is as
critical to arriving at a reasonable value as understanding how to use the model."

---

## Figure 34.1 — The Choices in Valuation Models (p.926)

Four top-level approaches, each with sub-choices:

### 1. Asset-Based Valuation
- Liquidation value: what the market would pay if assets sold today
- Replacement cost: how much to replicate the firm's assets

### 2. Discounted Cash Flow Models
- **Equity valuation**: Dividends OR Free cash flow to equity (FCFE)
- **Firm valuation**: Cost of capital (WACC), APV, or Excess return models
- **Growth models**: Stable-growth, Two-stage, Three-stage or n-stage
- **Earnings base**: Current earnings OR Normalized earnings

### 3. Relative Valuation
- **Equity multiples** (P/E, P/B, P/S) or **Firm multiples** (EV/EBITDA, EV/Sales)
- Compared to **sector** or to **entire market**
- Based on: Earnings, Book value, Revenues, or Sector-specific metrics

### 4. Contingent Claim (Option Pricing) Models
- Option to delay (patents, undeveloped reserves)
- Option to expand (young firms, undeveloped land)
- Option to liquidate (equity in troubled firm)

---

## Which Approach Should You Use? (pp.926–929)

The choice depends on **five factors**, each mapped to a figure in the book:

### Factor 1: Marketability of Assets (Figure 34.2, p.827)

```
Mature businesses                                Growth businesses
Separable & marketable assets ──────────── Linked & nonmarketable assets
        │                                              │
Liquidation / replacement cost             Other valuation models
```

- Real estate, closed-end funds → asset-based is easy (separable assets)
- Brand-dependent firms (e.g. P&G), high-growth firms → liquidation/replacement cost bears little resemblance to true value

### Factor 2: Cash-Flow-Generating Capacity (Figure 34.3, p.827)

Three categories of assets:

1. **Generating cash flows now (or expected to soon)** → DCF or relative valuation
   - Includes most publicly traded companies
   - Even negative-cash-flow startups can be valued with DCF (project improving margins)
2. **Cash flows only if a contingency occurs** → Option pricing models
   - Drug patents, undeveloped oil/mining reserves, undeveloped land
   - DCF with probability-weighted scenarios will UNDERSTATE value
3. **Will never generate cash flows** (primary residence, art, collectibles) → Relative valuation only

### Factor 3: Uniqueness / Presence of Comparables (Figure 34.4, p.828)

```
Unique asset or business ──────────── Large number of similar priced assets
        │                                              │
DCF or option pricing models                  Relative valuation models
```

### Factor 4: Time Horizon (Figure 34.5, p.828)

```
Very short ─────── Short ─────── Medium ─────── Long time horizon
    │                 │              │                │
Liquidation      Relative       Option pricing      DCF
value            valuation      models              value
```

- DCF = long-term perspective, going concern (perpetuity)
- Liquidation = assume operations cease today
- Relative and contingent claims = intermediate

### Factor 5: Beliefs about Markets (Figure 34.7, p.929)

```
Markets correct on average    Asset markets and financial    Markets make mistakes
but make mistakes on          markets may diverge            but correct them over time
individual assets
        │                              │                              │
Relative valuation            Liquidation value              DCF / Option pricing
```

---

## Choosing the Right DCF Model — Figure 34.8 (p.934)

This is the **operational decision tree** for selecting a DCF model.
Three parallel branches, evaluated simultaneously:

### Branch 1: Can you estimate cash flows?

```
Can you estimate cash flows?
├── YES → Is leverage stable or likely to change over time?
│         ├── Stable leverage → FCFE (equity valuation)
│         └── Unstable leverage → FCFF (firm valuation, WACC)
│
└── NO → Use dividend discount model
```

**When you CANNOT estimate cash flows** (p.931):
- Insufficient/contradictory information about debt payments and reinvestments
- Trouble defining what comprises debt (financial services firms — Ch 21 rationale)
- Significant restrictions on buybacks/cash returns → dividends are only reliable cash flow

**When leverage is unstable** (p.930–931):
- Firm valuation (WACC) is simpler: doesn't require projecting interest/principal payments
- Cost of capital is less sensitive to leverage changes than cost of equity
- If you prefer to model dollar debt directly → use APV approach instead

### Branch 2: Are current earnings positive and normal?

```
Are the current earnings positive and normal?
├── YES → Use current earnings as base
│
└── NO → Is the cause temporary?
          ├── YES → Replace with normalized earnings
          │         (cyclical downturn, extraordinary charge, restructuring)
          │
          └── NO → Is the firm likely to survive?
                    ├── YES → Adjust margins over time to nurse firm
                    │         to financial health
                    │
                    └── NO → Does the firm have a lot of debt?
                              ├── YES → Value equity as an option to liquidate
                              └── NO → Estimate liquidation value
```

**When to normalize** (temporary causes, p.931):
- Cyclical firm: depressed in downturn, elevated in boom — neither captures true earnings potential
- Extraordinary charge: abnormally low earnings in one period
- Restructuring: temporarily low earnings while changes take effect

**When NOT to normalize** (long-term causes, p.932):
Three groups of firms where negative earnings are persistent:

1. **Firms with long-term operating/strategic/financial problems**:
   - Do NOT replace with normalized earnings — it would overvalue them
   - If imminent default risk → option pricing model (high leverage) or liquidation value
   - If troubled but survivable → adjust margins over time to nurse back to health

2. **Infrastructure firms**: Negative earnings because investments take time to pay off.
   Capex disproportionately large vs depreciation. Once infrastructure built, capex drops,
   margins improve → positive cash flows in future years.

3. **Young start-up companies**: Negative earnings early in life cycle.
   Value with combination of high revenue growth AND improving operating margins over time.

### Branch 3: What rate is the firm growing at?

```
What rate is the firm growing at currently?
├── ≤ Growth rate of economy → Use stable-growth model
│
└── > Growth rate of economy → Are the firm's competitive advantages time-limited?
                                ├── YES → Use two-stage model
                                └── NO → Use three-stage or n-stage model
```

**Growth momentum categories** (p.932):
1. **Stable-growth**: Earnings and revenues at or below nominal growth rate of the economy. Note: can even be negative.
2. **Moderate-growth**: Growth within 8 to 10 percent of the economy's growth rate (rule of thumb).
3. **High-growth**: Growth rate much higher than the nominal growth rate of the economy.

**Source of growth determines model structure** (p.933):
- **Specific legal barriers** (patents, licenses) → growth is high for a specific period then drops abruptly → **two-stage** model
- **General competitive advantages** (brand, economies of scale) → growth erodes gradually as new competitors enter → **three-stage** or n-stage model

Three factors that determine speed of competitive advantage loss:
- **Nature of competitive advantage**: Brand names (consumer products) are harder to overcome → longer growth period. First-mover advantage erodes faster.
- **Competence of management**: Better management slows loss of competitive advantage by finding new markets.
- **Ease of entry into the business**: Greater barriers (capital requirements, technology) → slower loss of competitive advantage.

---

## Choosing the Right Relative Valuation Model (pp.935–937)

### Which Multiple Should I Use? (p.935)

Three approaches to select the best multiple:

1. **Fundamentals approach**: Use the variable most highly correlated with the firm's value.
   Current earnings and value are much more correlated in consumer product companies
   than in young technology companies. P/E makes more sense for the former.

2. **Statistical approach**: Run regressions of each multiple against the fundamentals.
   The multiple with the highest R-squared is the one that best explains value using
   fundamentals, and should be the multiple you use.

3. **Conventional multiple approach**: Over time, a specific multiple becomes the standard
   for a sector (e.g., P/B for financial services companies). Ideally, all three approaches
   converge. When the conventional multiple does NOT reflect fundamentals (sector in
   transition), you will get misleading estimates of value.

### Table 34.1 — Most Widely Used Multiples by Sector (p.936)

| Sector | Multiple | Rationale |
|--------|----------|-----------|
| Cyclical manufacturing | PE, relative PE | Often with normalized earnings |
| High tech, high growth | PEG | Big differences in growth across firms make PE hard to compare |
| High growth / negative earnings | PS, VS (EV/Sales) | Assume future margins will be positive |
| Infrastructure | EV/EBITDA | Firms have losses in early years; reported earnings vary by depreciation method |
| REIT | P/CF | Restrictions on investment policy; large depreciation charges make cash flows better than earnings |
| Financial services | PBV (P/B) | Book value often marked to market |
| Retailing | PS (similar leverage) or VS (different leverage) | Use equity or firm multiple depending on leverage comparability |

### Market or Sector Valuation? (p.836)

- **Sector**: Compare to firms in the same industry. Can define narrowly (same business, similar size) or broadly (more firms, use regression to control for differences).
- **Market**: Compare to entire market. Larger universe, different question: is the firm undervalued relative to ALL stocks?
- A firm can be undervalued relative to its sector but overvalued relative to the market — if the entire sector is mispriced.

---

## When DCF and Relative Valuation Diverge (p.937)

**Key insight: "Can a Firm Be Undervalued and Overvalued at the Same Time?"**

Yes. The two methods answer different questions:

| DCF says | Relative says | Interpretation |
|----------|---------------|----------------|
| **Overvalued** | Undervalued | The **sector** is overvalued. The firm is overvalued on fundamentals, but even more so than its peers. (Amazon, March 2000: DCF = $30, trading at $70, but undervalued vs other dot-coms.) |
| **Undervalued** | Overvalued | The **sector** is undervalued. The firm is cheap on fundamentals, but its sector is even cheaper. (Amazon, March 2001: price $15, DCF says undervalued, but overvalued vs sector after crash.) |
| **Undervalued** | Undervalued | **Strongest buy signal**. Undervalued both on fundamentals and relative to peers. "You benefit from market corrections both across time (DCF) and across companies (relative)." |
| **Overvalued** | Overvalued | Avoid. Overvalued on both dimensions. |

---

## When to Use Option Pricing Models (pp.937–938)

Three rules:
1. **Use options sparingly.** Restrict to where they make the biggest difference in valuation.
   Options affect value most at **smaller firms** that derive the bulk of their value from
   assets that resemble options. Valuing patents as options makes more sense for a small
   biotech than for a drug giant like Merck (which has dozens of patents generating cash
   flows from a developed portfolio).

2. **Opportunities are not always options.** Don't mistake growth potential for valuable
   embedded options. For opportunities to be valuable options, you need **exclusivity** —
   either from legal restrictions on competition or a significant competitive edge.

3. **Do not double count options.** Don't value patents as options AND set a higher growth
   rate in DCF to reflect those same patents. If your DCF already assumes high growth
   from undeveloped reserves, you cannot add an option value for them on top.

---

## Conclusion (p.938)

"The analyst faced with the task of valuing a firm/asset or its equity has to choose
among three different approaches—discounted cash flow valuation, relative valuation,
and option pricing models—and within each approach, between different models. This
choice will be driven largely by the characteristics of the firm/asset being valued—
the level of its earnings, its growth potential, the sources of earnings growth,
the stability of its leverage, and its dividend policy. Matching the valuation model
to the asset or firm being valued is as important a part of valuation as understanding
the models and having the right inputs."
