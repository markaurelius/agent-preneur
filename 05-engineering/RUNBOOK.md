# Prediction Engine Runbook

Quick reference for running, evaluating, and iterating on the prediction engine.
All commands run inside Docker — prefix every command with `docker compose run --rm engine`.

---

## The Iteration Loop

The core workflow: **run eval → read results → change one thing → repeat**.

```
╔══════════════════════════════════════════════════════╗
║  ITERATION LOOP                                      ║
║                                                      ║
║  1. docker compose build engine        (after edits) ║
║  2. evaluate.py --run-eval --diagnose  (run + score) ║
║  3. Read diagnosis, make one change                  ║
║  4. Go to 1                                          ║
╚══════════════════════════════════════════════════════╝
```

**Fixed eval set** (locked — never change, ensures apples-to-apples comparison):
- Tickers: AAPL MSFT GOOGL AMZN META NVDA JPM XOM JNJ BRK-B
- Years: 2022 (bear), 2023 (bull), 2024 (bull)
- ~30 predictions

| Config | Model | Workers | Time | Cost |
|--------|-------|---------|------|------|
| `stock-v1.yaml` | Sonnet | 1 | ~10 min | ~$0.30 |
| `stock-v1-fast.yaml` | Haiku | 5 | **~1 min** | ~$0.02 |

**Always use `stock-v1-fast.yaml` for iteration.** Use `stock-v1.yaml` only for a final
validation run once you have a config worth committing to.

---

## Core Commands

### Rebuild (required after any code/config change)
```bash
docker compose build engine
```

### Run + Evaluate in one command
```bash
# Run fixed eval set with Haiku (fast, ~1 min), print report, ask Claude for next step
docker compose run --rm engine python scripts/evaluate.py \
  --run-eval --config experiments/stock-v1-fast.yaml --diagnose
```

### Evaluate only (no new backtest run)
```bash
# Most recent run, human-readable report
docker compose run --rm engine python scripts/evaluate.py

# Most recent run, JSON output (for Claude to parse programmatically)
docker compose run --rm engine python scripts/evaluate.py --json

# Specific run by name prefix
docker compose run --rm engine python scripts/evaluate.py --run backtest-stock-v1-live-2026-03-16

# Compare two runs side-by-side
docker compose run --rm engine python scripts/evaluate.py \
  --run backtest-stock-v1-live-2026-03-16-1200 \
  --compare backtest-stock-v1-live-2026-03-16-1430
```

### Run backtest manually (more control)
```bash
# Fixed eval set (same as --run-eval above)
docker compose run --rm engine python scripts/backtest_stocks.py \
  --config experiments/stock-v1.yaml \
  --tickers AAPL,MSFT,GOOGL,AMZN,META,NVDA,JPM,XOM,JNJ,BRK-B \
  --years 2022,2023,2024

# Dry run — validates pipeline without API calls (~10s, free)
docker compose run --rm engine python scripts/backtest_stocks.py \
  --config experiments/stock-v1.yaml \
  --tickers AAPL,MSFT,GOOGL \
  --years 2023 \
  --dry-run

# Broader run (all 50 tickers × 4 years ≈ 200 predictions, ~20 min, ~$2)
docker compose run --rm engine python scripts/backtest_stocks.py \
  --config experiments/stock-v1.yaml
```

### Run live forecast (current predictions, no scoring)
```bash
docker compose run --rm engine python scripts/stock_forecast.py \
  --config experiments/stock-v1.yaml

# Specific tickers only
docker compose run --rm engine python scripts/stock_forecast.py \
  --config experiments/stock-v1.yaml \
  --tickers AAPL,NVDA,MSFT
```

### Corpus ingestion
```bash
# Fundamentals (S&P 500 historical snapshots — required for stock predictions)
docker compose run --rm engine python scripts/ingest.py --source fundamentals

# GDELT news events (geopolitics corpus)
docker compose run --rm engine python scripts/ingest.py --source gdelt --days 7

# SEC EDGAR filings (corporate events corpus)
docker compose run --rm engine python scripts/ingest.py --source edgar
```

---

## Experiment Configs (`experiments/`)

| Config | Corpus | Domain | Notes |
|--------|--------|--------|-------|
| `stock-v1.yaml` | `fundamentals` | Finance | **Active** — stock outperformance |
| `v3-gdelt.yaml` | `gdelt_events` | Geopolitics | GDELT news corpus |
| `finance-v4-edgar.yaml` | `edgar_events` | Finance | SEC filings corpus |

To create a new experiment: copy `stock-v1.yaml`, change `name` + any params, run backtest.

---

## Success Metrics

| Metric | Random baseline | Good | Great |
|--------|----------------|------|-------|
| Overall Brier | 0.2500 | < 0.2300 | < 0.2000 |
| High-confidence bucket Brier | 0.2500 | < 0.2200 | < 0.1800 |
| Mean prediction | — | 45–55% | 48–52% |
| Regime gap (2022 vs 2023) | — | < 0.05 | < 0.02 |

**North star: High-confidence bucket Brier < 0.20** — this is the only bucket that matters
for acting on signals. Low/Med confidence predictions are noise.

---

## What Claude Can Change Autonomously

When running the iteration loop, Claude will propose and implement changes. Here's the scope:

| Change | Auto | Ask first |
|--------|------|-----------|
| Edit prompt (`src/synthesis/prompts/stock-v1.txt`) | ✓ | |
| New prompt version (copy + edit) | ✓ | |
| Experiment YAML (top_k, similarity_type, weights) | ✓ | |
| Add/remove signals in `fundamentals.py` | | ✓ |
| Change analyst bias correction constant | ✓ | |
| New corpus source (new ingestion script) | | ✓ |
| DB schema changes | | ✓ always |

---

## Interpreting Results

**Brier score**: `(probability - actual_outcome)²`. Lower = better. A model that always
predicts 50% scores 0.25. A model predicting 80% on a 50-50 question scores 0.09 if
correct, 0.64 if wrong — high confidence is punished hard for errors.

**Confidence calibration**: If the model says 70%, the actual outcome rate should be ~70%.
If it says 70% but stocks only outperform 45% of the time, the model is overconfident.

**Regime sensitivity**: If 2022 (bear market) Brier >> 2023 (bull market) Brier, the
model learned bull-market patterns and fails in downturns. Fix: add macro regime context
to the corpus or prompt.

**Analogue quality gap**: If correct predictions have higher mean similarity than wrong
predictions, the corpus is working. If no gap, the corpus is not driving accuracy.

---

## Troubleshooting

**"ChromaDB collection does not exist"** → run `ingest.py --source fundamentals` first

**"No completed backtest runs found"** → run `backtest_stocks.py` first

**All predictions are bearish** → mean output well below 50%; check `evaluate.py` bias
warning. May need to adjust `_ANALYST_BIAS` in `stock_forecast.py` or rewrite prompt.

**Retrieval errors in backtest** → collection may have been created with wrong distance
metric. Delete chroma volume and re-ingest: `docker compose down -v && ingest.py --source fundamentals`
