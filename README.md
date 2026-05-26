# Stock Valuation + Insider Signals

A Claude Desktop extension that surfaces insider-trading clusters, share-buyback signals, and Damodaran-style valuations for any US stock.

Ask Claude things like:

> *"Show me the top insider-buying signals right now."*
> *"Has BKNG been buying back its shares?"*
> *"Run a valuation on MSFT."*

## Install (60 seconds, no Terminal)

1. **Install Claude Desktop** from [anthropic.com/download](https://www.anthropic.com/download) if you don't have it yet.
2. **Download the latest** `stock-valuation-insider-signals.mcpb` from the [Releases page](https://github.com/orioldc/stock-valuation-insider-signals/releases).
3. **Double-click the file**. Claude Desktop will register the extension and download a ~98 MB data snapshot in the background. First launch takes 2–3 minutes; later launches are instant.

That's it. Ask Claude any of the questions above.

## What you get

Four tools become available inside Claude:

| Tool | What it does |
|---|---|
| `get-signal-scanner` | Ranks ~7,000 US tickers by a size-adjusted composite of insider buying and share buybacks. Mid- and large-cap dominate the top of the list — small/micro-cap signals are deliberately down-weighted to suppress noise. |
| `get-cluster-detail` | For one ticker, returns BOTH the insider-buying cluster AND the buyback status, with market-cap tier and percentile rank within that tier. Renders a UI card. |
| `get-buyback-status` | Yes/no + magnitude buyback check for one ticker. Independent of the scanner intensity gate, so it answers "is anyone doing buybacks here?" even for low-intensity cases. |
| `run-valuation` | Damodaran DCF + relative-multiples valuation. Returns intrinsic value, current price, an UNDERVALUED / FAIRLY VALUED / OVERVALUED verdict, and folds in the insider signal. |

## Limitations

- **Data is a monthly snapshot.** The DB is rebuilt on the 1st of each month from SEC EDGAR Form 4 filings and shares-outstanding data. Activity from the last few weeks may be missing. For live data, see the power-user section below.
- **US equities only.** SEC EDGAR coverage; international tickers are not in scope.
- **macOS only at launch.** Windows DXT support is on the roadmap.
- **Not investment advice.** Surface signals; the LLM commentary is not personalized financial advice.

## How it works

```
Claude Desktop
     │  (stdio MCP)
     ▼
 Node MCP server  ──HTTP──►  FastAPI bridge  ──Python──►  SQLite snapshot
   (packages/mcp/)               (port 8502)              (data/insider_signals.db)
                                                         + Damodaran DCF agent
                                                         + signal scoring (size-adjusted)
```

Everything runs locally on your Mac. No data leaves your machine.

The signal scoring is documented in `packages/tracker/signals/`:

- `insider_clusters.py` — detects ≥2 distinct insiders buying within a rolling 30-day window
- `share_count_change.py` — computes QoQ and trailing-4Q shares-outstanding deltas
- `size_adjustment.py` — bucket-percentile-rank within `[micro, small, mid, large, mega]` × tier weight, so a 3% buyback at a $130B large-cap can outrank a 20% buyback at a $50M micro-cap

---

## Power-user notes

These sections are for developers / quants who want to refresh data themselves or run the components standalone.

### Refresh data with your own FMP key

The default install ships with the monthly snapshot and does not need an API key. To pull fresher data:

1. Sign up at [financialmodelingprep.com](https://site.financialmodelingprep.com/) (insider-trading endpoint requires a paid plan).
2. Set the FMP API key in Claude Desktop's extension settings, or:
   ```bash
   cd ~/Library/Application\ Support/Claude/extensions/stock-valuation-insider-signals
   export FMP_API_KEY=your_key_here
   ./.venv/bin/python packages/tracker/refresh.py
   ```

### Run components standalone

```bash
git clone https://github.com/orioldc/stock-valuation-insider-signals.git
cd stock-valuation-insider-signals
bash scripts/install.sh       # one-time setup
bash scripts/start.sh         # launch FastAPI + MCP stdio
```

Then `curl http://localhost:8502/health` to confirm the backend is up, or browse:

- `GET /signals?limit=20` — top size-adjusted composite signals
- `GET /buyback/AAPL` — buyback status for a ticker
- `GET /cluster/MSFT` — full per-ticker view
- `GET /valuation/NVDA` — DCF + relative valuation

### Repo layout

```
stock-valuation-insider-signals/
├── manifest.json                    # MCPB (Claude Desktop) manifest
├── pyproject.toml                   # unified Python project
├── packages/
│   ├── tracker/                     # signal pipeline + SQLite schema
│   ├── valuation/                   # Damodaran DCF + relative valuation
│   └── mcp/                         # Node MCP server + FastAPI bridge
├── scripts/
│   ├── install.sh                   # idempotent first-run setup
│   └── start.sh                     # launch FastAPI + MCP
├── data/                            # DB lives here; populated at install
└── .github/workflows/
    └── monthly-snapshot.yml         # cron: rebuild DB, publish release
```

### Credits

- SEC EDGAR data via [edgar_client](packages/tracker/data_ingestion/edgar_client.py)
- Damodaran framework from *Investment Valuation* (3rd ed.) — see `packages/valuation/knowledge/`
- Insider-trading literature: Lakonishok-Lee (2001), Cohen-Malloy-Pomorski (2012)

## License

MIT.
