# Damodaran Valuation Agent

Automated investment valuation using Aswath Damodaran's framework from *Investment Valuation* (3rd ed.). Given a US stock ticker, the agent selects the appropriate valuation method, runs the models, and produces a markdown report with a verdict.

## About this package

This is the `valuation` sub-package of the [stock-valuation-insider-signals](../../) monorepo. The repo's top-level [`README.md`](../../README.md) covers the noob-friendly install path (DXT bundle for Claude Desktop). The notes below are for power users running the agent directly.

## Direct (power-user) setup

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[all]
python packages/valuation/run_valuation.py AAPL
```

### Options

| Flag | Description |
|------|-------------|
| `--format telegram` | Print condensed ~500-word summary to stdout |
| `--format full` | Print full markdown report to stdout |
| `--no-cache` | Bypass all caches, fetch fresh data |
| `--output-dir PATH` | Custom report output directory |

### Examples

```bash
python run_valuation.py MSFT --format telegram    # Condensed summary
python run_valuation.py JPM --format full          # Full report to stdout
python run_valuation.py AAPL --no-cache            # Force fresh data
```

Reports are saved to `output/reports/{TICKER}_{YYYY-MM-DD}.md`.

## How It Works

The agent runs a 6-step pipeline (see `agent/orchestrator.py`):

```
[1/6] Company Profile     ── yfinance + FMP: sector, market cap, price, ratios
[2/6] Financial Data       ── TTM income statement, balance sheet, cash flow
[3/6] Damodaran Benchmarks ── Sector WACC, beta, P/E, EV/EBITDA from NYU datasets
[4/6] Insider Signals      ── Read-only query of insider-tracker DB (conviction, buybacks)
[5/6] Decision Tree + Models ── Select method, run DCF / Relative / Asset / Contingent
[6/6] Report Generation    ── Markdown report + optional Telegram summary
```

### Decision Tree (Figure 34.1)

The agent uses a deterministic decision tree (`agent/decision_tree.py`) to select the right valuation method based on company characteristics:

| Condition | Primary Method | Example |
|-----------|---------------|---------|
| BDC (business development company) | DDM + P/NAV + Reported NAV | HTGC, ARCC |
| Financial sector (bank, insurance) | DDM (augmented dividends) + P/E, P/B | JPM, GS |
| REIT | DDM + P/FFO + NAV | SPG, O |
| Asset-heavy (energy, mining, materials) | Asset-based + DCF | XOM, NEM |
| Distressed (ND/EBITDA > 4x or coverage < 1.5x) | Contingent claims | - |
| Pre-revenue | Contingent claims | - |
| Negative EBIT, positive revenue | Relative + normalized DCF | - |
| Default (positive earnings) | Two-stage FCFF DCF + Relative | AAPL, MSFT |

### Valuation Models

**DCF** (`valuation/dcf.py`) — Two-stage FCFF or FCFE:
- WACC from Damodaran sector beta, relevered at company D/E
- Cost of debt via synthetic rating table (Ch 15): interest coverage → bond rating → default spread
- Survival adjustment: P(Default) from rating applied to terminal value
- Stage 1 growth: median of fundamental (ROIC × reinvestment rate), historical CAGR, analyst estimate
- Growth cap is market-cap-aware: megacap 12%, large 18%, mid 25%, small 35%
- Terminal value: Gordon Growth, g capped at 2.5%
- 5x5 sensitivity table (WACC ±1.5% × terminal growth ±1%)
- **Banks**: DDM with augmented dividends (dividends + buybacks), levered beta, Ke only (no WACC)
- **REITs**: DDM with augmented dividends, market beta, growth capped at 8% (ROE×retention unreliable due to non-economic depreciation)

**Relative** (`valuation/relative.py`) — Sector multiple comparison:
- EV/EBITDA, P/E, EV/Sales, P/B depending on sector
- P/FFO for REITs (with justified P/FFO cross-check)
- Justified multiple cross-check (e.g. justified P/E = payout × (1+g) / (Ke - g))
- Composite implied price from available multiples

**Asset-Based** (`valuation/asset_based.py`) — Book value, liquidation value, and REIT NAV:
- REIT NAV: NOI / cap rate − net debt (Ch 26, cap rate blended from implied + market reference)

**Contingent Claims** (`valuation/contingent_claims.py`) — Black-Scholes Merton for distressed/pre-revenue

### Synthesis

Results from all methods are combined with method-dependent weights:
- DCF primary: 60% DCF / 40% Relative
- BDCs: 45% DDM / 30% Relative (P/NAV + P/E) / 25% Reported NAV
- Banks: 55% DDM / 45% Relative (P/E + P/B)
- REITs: 40% DDM / 35% P/FFO Relative / 25% NAV
- Relative primary: 60% Relative / 40% DCF
- Asset-based primary: 50% Asset / 30% DCF / 20% Relative
- Contingent claims primary: 70% Contingent / 30% DCF

Verdict: **UNDERVALUED** (>20% upside), **OVERVALUED** (>15% downside), or **FAIRLY VALUED**.

## Data Sources

| Source | What | Cache TTL |
|--------|------|-----------|
| yfinance | Company profile, prices, financial statements | 7 days |
| FMP API | Financial statements (fallback: yfinance) | 7 days |
| Damodaran NYU datasets | Sector WACC, beta, P/E, EV/EBITDA, ROE, margins | 30 days |
| Damodaran ERP | Implied equity risk premium (latest year) | 7 days |
| yfinance ^TNX | Risk-free rate (10Y Treasury yield) | Live |
| SEC EDGAR XBRL | R&D expense, interest expense (when yfinance is missing) | 7 days |
| SEC EDGAR Form 4 | Insider transactions (live fallback) | 7 days |
| Frozen snapshot | Pre-computed insider signals from insider-signal-tracker | Bundled |

Damodaran datasets are fetched from `pages.stern.nyu.edu/~adamodar/` and cached as CSVs in `cache/damodaran/`.

### Insider Data — Self-Contained

The agent incorporates insider trading signals without requiring a separate `insider-signal-tracker` repo. It supports two data paths:

| Path | Speed | Network | Data Quality | When Used |
|------|-------|---------|-------------|-----------|
| **Frozen snapshot** (`data/insider_frozen.json.gz`) | Instant | None | Full (with conviction scores) | Present by default; copy from insider-signal-tracker |
| **Live EDGAR** (Form 4 filings from SEC) | ~5–10s per ticker | SEC EDGAR | Raw transactions only (no conviction scoring) | Fallback when frozen file is absent |

**To update the frozen snapshot** from a fresh insider-signal-tracker run:
```bash
# In insider-signal-tracker repo:
python scripts/export_frozen_insider.py
cp output/insider_frozen.json.gz ../valuation-agent/data/
```

Set `INSIDER_FROZEN_DATA` env var to override the frozen file path, or delete the file to force live SEC
fetching exclusively.

**Cache:** Live EDGAR results are cached in `cache/insider/{TICKER}.json` (7-day TTL) and ticker→CIK
lookups in `cache/ticker_cik.json` (30-day TTL). Use `--no-cache` to force re-fetch from SEC.

## Project Structure

```
valuation-agent/
├── run_valuation.py              # CLI entrypoint
├── requirements.txt              # yfinance, pandas, numpy, scipy, lxml, etc.
│
├── agent/
│   ├── orchestrator.py           # 6-step pipeline + weighted synthesis
│   ├── decision_tree.py          # Figure 34.1: which model to apply
│   └── report_generator.py       # Markdown report + Telegram summary
│
├── data/
│   ├── company_profile.py        # yfinance + FMP profile (sector, cap, price)
│   ├── financials.py             # TTM financials, FCFF/FCFE/FFO computation
│   ├── damodaran_data.py         # NYU sector datasets (WACC, beta, multiples)
│   ├── insider_signals.py        # Dispatch: frozen snapshot or live EDGAR
│   ├── insider_fetcher.py        # Live SEC EDGAR per-ticker insider fetch
│   ├── insider_frozen.json.gz    # Pre-computed insider data (optional)
│   └── edgar_client.py           # SEC EDGAR API client (rate-limited)
│
├── valuation/
│   ├── dcf.py                    # Two-stage FCFF/FCFE DCF
│   ├── relative.py               # Sector multiple comparison
│   ├── asset_based.py            # Book value / liquidation value / REIT NAV
│   └── contingent_claims.py      # Black-Scholes Merton
│
├── knowledge/                    # Encoded Damodaran book knowledge
│   ├── damodaran_principles.md   # Key chapter summaries
│   ├── decision_tree_rules.md    # Figure 34.1 as conditional rules
│   ├── dcf_formulas.md           # Verbatim formulas (FCFF, WACC, TV)
│   ├── multiples_guide.md        # Which multiple to use when
│   ├── sector_adjustments.md     # Sector-specific overrides
│   └── red_flags.md              # Qualitative warning signs
│
├── cache/
│   ├── damodaran/                # Sector CSVs (30-day TTL)
│   ├── company/                  # Per-ticker profiles (7-day TTL)
│   ├── ticker_cik.json           # SEC ticker->CIK mapping (30-day TTL)
│   └── insider/                  # Per-ticker insider data (7-day TTL)
│
└── output/
    └── reports/                  # Generated reports: {TICKER}_{date}.md
```

## Integration

**Claude Code**: `/valuation TICKER` slash command

**OpenClaw (Telegram)**: Say "Value AAPL" — runs the agent and sends a condensed summary.

## Known Limitations

- FMP free tier returns 403 for financial statements; yfinance fallback handles this transparently
- Bank EBIT/EBITDA = $0 from yfinance is expected — these metrics don't apply to banks; the DDM model uses net income and augmented dividends instead
- REIT book equity is distorted by accumulated non-economic depreciation, making ROE artificially high (80%+); growth estimation skips ROE×retention and relies on historical revenue CAGR
- REIT NAV has partial circularity: cap rate is 60% implied from market price + 40% market reference, so NAV tends to track near market price
- Damodaran sector P/E tables don't separate REIT P/FFO; sector P/E is used as proxy, which can overstate the relative valuation benchmark
- Damodaran's "Electronics (Consumer & Office)" sector has only 8 firms, so sector averages may be skewed for companies like AAPL
- Some Damodaran sector ROE values are garbage (e.g., -2881%) — these are filtered but may affect edge cases
- yfinance returns NaN for interest_expense and issuance on some tickers — sanitized before use
- The agent does not fetch analyst consensus estimates; growth estimation relies on historical data and yfinance's `earningsGrowth` field
