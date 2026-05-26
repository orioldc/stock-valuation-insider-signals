# Insider Signal Tracker

Tracks insider trading signals using SEC EDGAR data. Detects insider buying clusters and share buyback patterns across ~500 stocks spanning large, mid, and small caps.

## About this package

This is the `tracker` sub-package of the [stock-valuation-insider-signals](../../) monorepo. The repo's top-level [`README.md`](../../README.md) covers the noob-friendly install path (DXT bundle). The notes below are for power users running the pipeline directly.

## Direct (power-user) setup

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[all]                     # installs tracker + valuation + dev deps
python packages/tracker/db/init_db.py     # create schema
bash scripts/install.sh --db-only         # download latest monthly snapshot from GitHub Releases
```

To rebuild the DB from scratch instead of downloading, set `FMP_API_KEY` then run `python packages/tracker/run_expanded_pipeline.py` (~45 min full pipeline).

The pre-built DB is published on this repo's [GitHub Releases](../../releases) as `insider_signals.db.xz` (~98 MB compressed, ~863 MB extracted).

## Universe
**7,454 tickers** covering the full SEC EDGAR universe, including:
- S&P 500 large caps, mid/small-cap industrials
- Regional banks, biotech/pharma, REITs, energy E&Ps
- Tech mid/small, consumer, and more

## Architecture
- **Data Source:** SEC EDGAR (Form 4 filings + XBRL shares outstanding)
- **DB:** SQLite (`db/insider_signals.db`)
- **Signals:** Insider clusters (≥2 insiders buying within 30 days) + share count changes
- **Scoring:** Composite = 0.6 × cluster_signal + 0.4 × buyback_signal

## Export for valuation-agent
```bash
python scripts/export_frozen_insider.py
cp output/insider_frozen.json.gz ../valuation-agent/data/
```
See [valuation-agent](https://github.com/fuertesito91/valuation-agent) for details.

## Files
- `data_ingestion/edgar_client.py` — SEC API client (rate-limited)
- `data_ingestion/data_loader.py` — Universe definition + ingestion
- `signals/insider_clusters.py` — Cluster detection
- `signals/share_count_change.py` — Buyback detection
- `signals/composite_scorer.py` — Composite scoring
- `run_expanded_pipeline.py` — Full pipeline with detailed output
- `scripts/export_frozen_insider.py` — Export snapshot for valuation-agent
- `scripts/download_db.sh` — Download + decompress pre-built DB
- `output/latest_signals.csv` — Latest ranked results
