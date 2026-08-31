## Conviction Scoring Model

Walk-forward segment base-rate model for insider cluster scoring.

### Design

**Target**: Percentile rank of 252-day forward return within same-sector, same-size peer pool.
Binary outcome: above-median (1) or below-median (0).

**Why base rates, not fitted classifiers?**
- Fitted classifiers (L2 logistic + isotonic calibration) produced AUC 0.500-0.565 with negative Brier skill on sector-relative targets
- The signal is size within sector: micro-caps beat peers 63.1%, large-caps 51.0%
- Most other features (buyback, technicals, cluster frequency) were sector proxies that double-counted size
- Base rates are calibrated by construction (empirical frequencies) and honest out-of-sample via walk-forward

**Architecture**:
- Segment-level rates by (sector, size_tier) when n ≥ 60
- Degrades to tier-level rates when segment is too sparse
- Reports SPY/QQQ beat rates (tier-level) alongside peer-relative rates
- Returns None with reason when no data exists

**Algorithm**: Walk-forward Wald 95% confidence intervals on empirical hit rates.
No model fitting, no sklearn dependency.

### Features (15 total)

**Market/price**:
- `logmcap`: log10(market_cap) at signal date
- `vol60`: 60-day realized volatility
- `d52`: % distance from 252-day high
- `sma`: SMA50/SMA200 - 1
- `mom3`, `mom12`: 3-month and 12-month momentum

**Cluster**:
- `ni`: n_insiders (capped at 8)
- `logval`: log10(total cluster value)
- `ntx`: num_transactions (capped at 20)
- `ceo`, `dirf`: CEO present, director present (binary flags)

**Buyback**:
- `buyback`: Split-adjusted 365-day share count change (negative = buyback)
- `bb_missing`: 1 if buyback unusable (data quality flags)

**Historical**:
- `hist_n`: log1p(count of prior resolved clusters for this ticker)
- `tech_missing`: 1 if technical features missing

### Files

- `peer_rank.py`: Peer-relative ranking logic
- `features.py`: Feature extraction (includes split-adjusted buyback)
- `base_rates.py`: Segment and tier base-rate computation with fallback logic
- `train_base_rates.py`: Walk-forward training entrypoint (called by monthly-snapshot.yml)
- `test_components.py`: Unit tests for core components

### Usage

#### Training
```bash
# Walk-forward base-rate computation (~5-10 minutes)
python packages/tracker/scoring/train_base_rates.py

# Unit tests (~30 seconds)
python packages/tracker/scoring/test_components.py
```

Trained base rates are saved to `segment_base_rates` and `tier_base_rates` tables in the DB.

#### Scoring

```python
from tracker.scoring import score_cluster
from tracker.scoring.base_rates import DB_PATH

# Score a cluster (sector, size_tier)
result = score_cluster(DB_PATH, sector='Technology', size_tier='micro')

print(f"Base rate: {result['base_rate']:.1%}")
print(f"95% CI: [{result['ci_lower']:.1%}, {result['ci_upper']:.1%}]")
print(f"Sample size: n={result['n_samples']}")
print(f"Level used: {result['level_used']}")  # 'segment', 'tier', or None
print(f"SPY beat rate: {result['spy_beat_rate']:.1%}")
print(f"QQQ beat rate: {result['qqq_beat_rate']:.1%}")
```

**Degradation**: When segment n < 60, falls back to tier-level rates (`suppressed=True`). When no data exists at any level, returns `base_rate=None` with a `reason` string. Bridge should handle None gracefully (e.g., fall back to neutral default or defer to user).

### Database Schema

```sql
CREATE TABLE segment_base_rates (
    sector TEXT NOT NULL,
    size_tier TEXT NOT NULL,
    year INTEGER NOT NULL,
    hit_rate REAL NOT NULL,
    n_samples INTEGER NOT NULL,
    ci_lower REAL NOT NULL,
    ci_upper REAL NOT NULL,
    mean_peer_rank REAL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (sector, size_tier, year)
);

CREATE TABLE tier_base_rates (
    size_tier TEXT NOT NULL,
    year INTEGER NOT NULL,
    hit_rate REAL NOT NULL,
    n_samples INTEGER NOT NULL,
    ci_lower REAL NOT NULL,
    ci_upper REAL NOT NULL,
    spy_beat_rate REAL,
    qqq_beat_rate REAL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (size_tier, year)
);
```

### Expected Performance

Walk-forward base rates are honest out-of-sample estimates:
- **Micro-cap segments**: 55-65% peer-beat rates (strongest signal)
- **Large-cap segments**: 48-52% peer-beat rates (near baseline)
- **Segment suppression threshold**: n ≥ 60 (below this, degrades to tier-level)

Historical performance reflects actual forward returns, not fitted model metrics. No AUC/Brier reported because base rates are frequencies, not predictions from a discriminative model.

### Known Issues

1. **Segment sparsity**: MIN_SEGMENT_SIZE = 60 may suppress legitimate signals in smaller sector/size combinations. Monitor suppression rate in production.

2. **Market cap filtering**: Requirement for `market_cap_asof <= signal_date` may exclude valid peers. Review companies table to ensure market_cap_asof is populated correctly.

3. **Performance**: Price panel build takes ~60 seconds. For real-time scoring, cache the panel and reload only when prices are updated.

### Next Steps

1. Integrate `score_cluster()` into MCP bridge
2. Monitor segment suppression rate in production (tier-level fallback frequency)
3. Consider lowering MIN_SEGMENT_SIZE to 40-50 if suppression is too aggressive
4. Monthly retraining via `.github/workflows/monthly-snapshot.yml` (already wired)
