# Stock Valuation + Insider Signals

A Claude Desktop extension that surfaces insider-trading clusters, share-buyback signals, and DCF valuations for any US stock.

Ask Claude things like:

> *"Show me the top insider-buying signals right now."*
> *"Has BKNG been buying back its shares?"*
> *"Run a valuation on MSFT."*

---

## Before you start (one-time)

You need three things on your Mac before the extension can run. If you've ever used Python or Node before, you probably already have them — but install fresh copies if in doubt.

| Requirement | Why | How to get it |
|---|---|---|
| **Claude Desktop** | The host app | https://www.anthropic.com/download |
| **Python 3.11 or newer** | Runs the local data backend | https://www.python.org/downloads (download the latest 3.x for macOS) |
| **Node.js 20 or newer** | Runs the MCP bridge | https://nodejs.org (the "LTS" button) |

`xz` (used to unpack the data file) ships with macOS Sonoma and newer. If you're on an older macOS, install [Homebrew](https://brew.sh) and run `brew install xz` once.

You don't need a GitHub account, an FMP API key, or any other paid service. The extension downloads a free monthly data snapshot on first launch.

---

## Install (60 seconds, no Terminal)

1. **[Download the extension](https://github.com/orioldc/stock-valuation-insider-signals/releases/latest)** — click the `.mcpb` file under "Assets". The latest version always lives at the top of the [releases page](https://github.com/orioldc/stock-valuation-insider-signals/releases/latest).

2. **Open the downloaded `.mcpb` file.** Double-click in Finder. If macOS says *"can't be opened because it is from an unidentified developer"*, right-click the file → **Open** → **Open** again in the confirmation dialog. (This is a one-time signing step Anthropic hasn't enabled yet for community extensions.)

3. **Claude Desktop will show a registration dialog.** Click **Install**.

That's it for the install step.

---

## What you'll see on first launch

When you next open Claude Desktop, the extension takes **2–3 minutes** to set itself up (downloading ~98 MB of data, creating a Python virtualenv, installing dependencies). You can leave Claude Desktop open and use it for unrelated chats while this happens.

You'll know it worked when you start a new chat and ask:

> *"What tools do you have from the Stock Valuation MCP?"*

Claude should respond with four tools: `get-signal-scanner`, `get-cluster-detail`, `get-buyback-status`, `run-valuation`.

If you ask one of the example questions at the top of this README, you should see a response in 5–30 seconds with actual numbers.

---

## What you get

Four tools become available inside Claude:

| Tool | Plain-English version |
|---|---|
| **Find signals** (`get-signal-scanner`) | "Show me companies where insiders are buying their own stock or where the company is buying back its own shares. Rank them so the most meaningful signals come first — not just the loudest." |
| **Look up a company** (`get-cluster-detail`) | "For this one ticker, tell me: are insiders buying? Is the company buying back shares? How does this compare to other companies of similar size?" |
| **Just the buyback question** (`get-buyback-status`) | "Yes-or-no: is this company repurchasing its own stock?" |
| **What's it worth?** (`run-valuation`) | "Run a discounted-cash-flow valuation on this ticker. Tell me if it looks under-, over-, or fairly valued." |

The signal ranking uses **size-adjusted scoring** — a 3% buyback at a $130B company can outrank a 20% buyback at a $50M company. This is on purpose: research shows mid- and large-cap insider activity is more reliable than micro-cap noise.

---

## Limitations

- **Data is a monthly snapshot.** The DB is rebuilt on the 1st of each month from SEC EDGAR Form 4 filings and shares-outstanding data. Activity from the last few days/weeks may be missing.
- **US equities only.** SEC EDGAR coverage.
- **macOS only at launch.** Windows DXT support is on the roadmap.
- **Not investment advice.** This surfaces signals; Claude's commentary is not personalized financial advice.

---

## Troubleshooting

If the tools don't appear in Claude Desktop, or you get errors when calling them:

### Check the install log
```
~/Library/Application Support/Claude/Claude Extensions/.../logs/install.log
```
The path depends on Claude Desktop's exact install layout — search Finder for `install.log` if needed. The bottom of this file will show the most recent error.

A file named `.install_failed` next to the log means the most recent install attempt failed. The log explains why.

### Common failures

**`python3 not found` / `python3 is too old`**
→ Install Python 3.11+ from https://python.org/downloads, then restart Claude Desktop.

**`node not found` / `node is too old`**
→ Install Node 20+ from https://nodejs.org, then restart Claude Desktop.

**`xz not found`**
→ You're on older macOS. Install Homebrew (https://brew.sh), then `brew install xz`.

**Tools appear but return errors like "no DB"**
→ The data download may have failed mid-install. Restart Claude Desktop; the install script is idempotent and will retry the DB download.

**"Insider relevance: 0.00" on every ticker**
→ The scanner CSV didn't download. Restart Claude Desktop (this was fixed in v0.1.1; make sure you have v0.1.2 or newer).

### Still stuck

Open an issue at https://github.com/orioldc/stock-valuation-insider-signals/issues with the tail of `install.log` attached.

---

## How it works (for the curious)

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
- `size_adjustment.py` — bucket-percentile-rank within `[micro, small, mid, large, mega]` × tier weight

---

## Power-user notes

These sections are for developers / quants who want to refresh data themselves or run the components standalone. **You can ignore this section if you're a noob — the install above is all you need.**

### Refresh data with your own FMP key

The default install ships with the monthly snapshot and does not need an API key. To pull fresher data:

1. Sign up at [financialmodelingprep.com](https://site.financialmodelingprep.com/) (insider-trading endpoint requires a paid plan).
2. Set the FMP API key in Claude Desktop's extension settings, or:
   ```bash
   cd ~/Library/Application\ Support/Claude/Claude\ Extensions/.../stock-valuation-insider-signals
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
