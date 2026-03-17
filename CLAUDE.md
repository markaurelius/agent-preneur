# Stock Prediction Engine — AI Context

> Single source of truth for Claude Code. Update this file as decisions are made.
> Read this first before working in 05-engineering/.

---

## Status

**Current Phase:** 05-engineering — ML pipeline operational, iteration loop ready
**Domain:** Finance / stock outperformance vs S&P 500
**Stack:** Python 3.11 / LightGBM / SQLite / Docker
**Last Updated:** 2026-03-16

---

## Autonomous Agent Mode

When running inside Docker as the autonomous agent (via `make agent`), follow the
protocol in `05-engineering/AGENT_TASK.md`. Key differences from interactive mode:

### Use `make agent-iterate` (not `make iterate`)
Inside the agent container there is no Docker socket — run Python scripts directly:
```
make agent-iterate    # train + backtest + results (direct Python)
make agent-results    # results summary only
```

### Notification triggers
Use `python scripts/notify.py` for all user communication:
- **Before every iteration**: summary of what you're about to try
- **After 5 iterations**: checkpoint message, then exit
- **On 3 consecutive regressions**: urgent stop message, then exit
- **On any true blocker**: describe the blocker, then exit

### Git workflow (agent mode)
- Always start from `main`
- Branch per iteration: `iter-N-<slug>`
- Push on improvement; delete branch and stay on main on regression
- Never commit to main directly

### Coexistence with interactive mode
The agent uses `docker-compose.agent.yml`; interactive dev uses `docker-compose.yml`.
**Do not run both simultaneously** — they share `./data/engine.db`.

---

## Active Workflows

### Fast iteration loop (seconds per cycle, no network after step 0)

```
make build               # once after any pyproject.toml change
make fetch-snapshots     # ONCE — caches (ticker, year) snapshots in SQLite
make train-stocks        # seconds — reads from DB, no network
make backtest-stocks-ml  # seconds — reads from DB, no network
make results             # parse results, identify next improvement
```

### Forward-looking predictions
```
make forecast-stocks-ml  # live fundamentals → LightGBM → probability vs analyst targets
```

---

## Architecture: How It Works

```
yfinance (one-time)
    ↓
stock_snapshots table (SQLite cache)
    ↓
extract_stock_features()  →  16-dim numeric vector
    ↓
LightGBM + CalibratedClassifierCV  →  P(outperform SPY over 12 months)
    ↓
Brier score vs actual outcome  →  iterate
```

**Key files:**
- `src/synthesis/stock_features.py` — snapshot dict → 16-feature vector
- `src/synthesis/stock_predictor.py` — StockMLPredictor, confidence_label
- `scripts/fetch_snapshots.py` — one-time yfinance → SQLite cache
- `scripts/train_stocks.py` — LightGBM training from DB cache
- `scripts/backtest_stocks.py` — walk-forward backtest (ML or Claude path)
- `scripts/stock_forecast.py` — live forward-looking predictions
- `scripts/results_summary.py` — machine-readable run results for agent iteration

---

## Agent Iteration Protocol

Claude is authorized to run the iteration loop autonomously. The goal is to drive
mean Brier score down across runs. Each iteration follows this protocol:

### One iteration cycle

1. **Read results**: `make results` (or `make results-json`) — parse issues list
2. **Identify the single highest-leverage change** from the issues list
3. **Make one targeted change** (see levers below)
4. **Retrain**: `make train-stocks`
5. **Backtest**: `make backtest-stocks-ml`
6. **Compare**: `make results` — did Brier improve?
7. **Log the iteration** in `05-engineering/experiments/iteration-log.md`:
   - What changed and why
   - Before/after Brier scores
   - Whether the change was kept or reverted

### Improvement levers (in priority order)

| Priority | Lever | File to change |
|----------|-------|---------------|
| 1 | Fix bias (mean output far from 50%) | `train_stocks.py` — check label balance, calibration |
| 2 | Add/remove features | `stock_features.py` — add interaction terms, sector encoding |
| 3 | Tune model hyperparams | `train_stocks.py` — num_leaves, n_estimators, learning_rate |
| 4 | Expand training data | `fetch_snapshots.py` — add years, add tickers |
| 5 | Sector-specific models | New: train separate model per sector |
| 6 | Different target engineering | Different outperformance thresholds (e.g., >5% vs SPY) |
| 7 | Ensemble | Combine multiple model outputs |

### Success criteria

| Metric | Target | Current status |
|--------|--------|---------------|
| Mean Brier | < 0.20 (vs random 0.25) | TBD |
| Bias check | mean output 45–55% | TBD |
| High-conf accuracy | > 60% | TBD |
| High-conf Brier | < 0.18 | TBD |

### Authorized autonomous actions

Claude may autonomously:
- Edit `src/synthesis/stock_features.py` (features)
- Edit `scripts/train_stocks.py` (hyperparams, calibration)
- Run `make train-stocks`, `make backtest-stocks-ml`, `make results`
- Create new experiment YAMLs in `experiments/`
- Log results in `experiments/iteration-log.md`

Claude must ask before:
- Changing the DB schema (new alembic migration)
- Adding new data sources
- Running `make fetch-snapshots` (takes 10-15 min, costs yfinance rate limit)
- Changing `scripts/stock_forecast.py` (affects live predictions)

---

## Technical Decisions

### Database: SQLite (deliberate, with documented upgrade trigger)

**Current choice: SQLite is the right database for this stage.**

Reasoning:
- S&P 500 (500 tickers) × 5 years × annual snapshots = 2,500 rows — trivial
- Russell 3000 × 10 years × annual = 30,000 rows — still trivial for SQLite
- SQLAlchemy abstracts the DB layer, so migration is a connection string change
- Single writer (Docker container) — no concurrency issue
- No network overhead — DB is a file in `./data/`, bind-mounted into container

**Upgrade trigger: migrate to PostgreSQL when ANY of these are true:**
- Daily snapshot granularity at >1,000 tickers (>1M rows/year)
- Multiple concurrent writers (e.g., distributed training agents)
- Need for full-text search or advanced time-series queries
- Total DB size exceeds 5GB

**How to migrate:** Change `DATABASE_URL` in `.env` from `sqlite:///...` to `postgresql://...`.
SQLAlchemy + Alembic handle the rest.

### ML model: LightGBM + CalibratedClassifierCV

**Why LightGBM over Logistic Regression:**
- Handles non-linear feature interactions (P/E × momentum, sector × macro regime)
- Tree-based: robust to outliers, no need for feature scaling
- Fast training (seconds on 2,500 rows)
- Feature importance is interpretable

**Why isotonic calibration:**
- LightGBM outputs are not calibrated probabilities
- Isotonic (non-parametric) calibration forces mean output ≈ base rate (~50%)
- `scale_pos_weight` in LightGBM also corrects for label imbalance in training data
- Together these fix the "78.4% mean output" bias observed in early runs

**Why walk-forward CV (not k-fold):**
- k-fold would allow future data to leak into training (data leakage)
- Walk-forward: train on years N-3:N-1, test on year N — mimics real deployment

### No Claude in the prediction loop

Claude is only used as:
1. **Orchestrator** — runs the iteration loop, reads results, decides what to change
2. **Live corpus labeling** (one-time, via `label_outcomes.py` with Haiku) — not active focus

Every prediction is: snapshot → LightGBM → probability. Zero API calls, zero latency, zero cost.

### ChromaDB / analogue retrieval: retained but not in hot path

ChromaDB and the fundamentals corpus are still available for the Claude backtest path
(`experiments/stock-v1.yaml`). This lets us compare ML vs Claude predictions.
Not used in `predictor_type: ml` path.

---

## Stack

- **Language:** Python 3.11+
- **ML:** LightGBM + scikit-learn (calibration, pipeline)
- **Storage:** SQLite (relational) + ChromaDB (vector, Claude path only)
- **Data:** yfinance (fundamentals), FRED (macro), SEC EDGAR (8-K filings)
- **Hosting:** Docker (local), `docker compose run --rm engine <cmd>`
- **Auth:** env vars (`ANTHROPIC_API_KEY`, optionally `FRED_API_KEY`)
- **LLM:** Claude Sonnet 4.6 (orchestration only, not prediction loop)

---

## Decisions Log

*(newest at top)*

- [05] **SQLite adequate for current and near-term scale** — S&P 500 annual snapshots is 2,500 rows; upgrade trigger documented above; SQLAlchemy abstracts migration
- [05] **Snapshot cache in SQLite** — `stock_snapshots` table caches (ticker, year) → features + label; fetch once via `make fetch-snapshots`, train/backtest in seconds with no network calls
- [05] **LightGBM replaces Logistic Regression** — better for non-linear feature interactions; still sklearn Pipeline compatible; same `StockMLPredictor` interface
- [05] **Bias fix: scale_pos_weight + isotonic calibration** — detected 78.4% mean output in early run; root cause is TOP-50 mega-caps outperforming in 2020-2024 bull market; fix: weight underperformers during training + post-hoc calibration
- [05] **Agent iteration protocol documented** — Claude authorized to autonomously run train/backtest loop, edit features and hyperparams, log results; must ask before schema changes or data fetches
- [05] **Geopolitics artifacts deleted** — `scripts/forecast.py`, `resolve.py`, `polymarket_forecast.py`, `src/ingestion/gdelt.py`, `scripts/train.py` (analogue-based), `src/synthesis/feature_extractor.py`, deprecated prompt templates and experiment YAMLs
- [05] **Pivoted domain to finance/stock outperformance** — geopolitics (Metaculus, CoW, GDELT, Polymarket) fully deprecated
- [05] **ML predictor replaces Claude for per-prediction synthesis** — `predictor_type: analogue_aggregator | ml | claude`
- [04] **North star: mean Brier score trending down across runs** — lower is better; target measurable improvement by run 10
- [03] **Docker-first** — containerized from day one; data persisted in Docker volumes
- [03] **Stack: Python + Claude API + ChromaDB + SQLite** — local-first, no cloud infra, runs on a laptop
- [02] **Offline-first training loop** — fast iteration without waiting for live events
- [00] **Self-improving agent, not a product** — agent generates predictions, validates against outcomes, updates its own model

---

## Phase Docs

- [00-discovery/brief.md](00-discovery/brief.md)
- [01-strategy/business-case.md](01-strategy/business-case.md)
- [02-product/prd.md](02-product/prd.md)
- [03-design/design-brief.md](03-design/design-brief.md)
- [04-data/data-plan.md](04-data/data-plan.md)
- [05-engineering/spec.md](05-engineering/spec.md)
