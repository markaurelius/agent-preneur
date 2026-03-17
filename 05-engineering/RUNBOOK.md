# Prediction Engine Runbook

Quick reference for running, evaluating, and iterating on the LightGBM stock
outperformance prediction engine.

---

## Architecture in one line

```
yfinance snapshots (SQLite)  →  LightGBM + CalibratedClassifierCV  →  P(beat SPY over 12 months)
```

No API calls in the prediction loop. Train and backtest run in seconds from cached DB.

---

## Interactive dev workflow (the fast loop)

```
make iterate          # train → backtest → results  (seconds, no network)
make results          # just re-read last results
make forecast-stocks-ml  # live 2026 predictions (fetches fresh yfinance data)
```

After any `pyproject.toml` change: `make build` first.

---

## Core commands

### Data setup (one-time or on-demand)

```bash
# Populate FRED macro table (2018–2025 FRED signals, ~30s, 8 network calls)
make populate-fred

# Fetch stock snapshots for all 187 tickers × 8 years (~10 min, one-time)
make fetch-snapshots-extended

# Fetch EDGAR XBRL fundamentals (one-time, ~2 min)
make fetch-edgar
```

### Fast iteration loop

```bash
make train-stocks          # Step 1 — train LightGBM from DB cache (seconds)
make backtest-stocks-ml    # Step 2 — walk-forward backtest, 187 tickers × 4 years
make results               # Step 3 — human-readable summary
make iterate               # Steps 1-3 in one command
```

### Live forecast

```bash
make forecast-stocks-ml    # Current Jan 2026 fundamentals → P(beat SPY)
```

### Utilities

```bash
make shell                 # bash inside the container
make test                  # run test suite
make lint                  # ruff linter
make compare RUN1=<id> RUN2=<id>   # diff two backtest runs
```

---

## Key files

| File | Purpose |
|------|---------|
| `src/synthesis/stock_features.py` | Snapshot dict → 24-feature vector |
| `src/synthesis/stock_predictor.py` | StockMLPredictor, confidence_label |
| `scripts/train_stocks.py` | LightGBM training, saves `data/models/lgbm_stock_v1.pkl` |
| `scripts/backtest_stocks.py` | Expanding walk-forward backtest |
| `scripts/stock_forecast.py` | Live predictions → stored in DB |
| `scripts/fetch_snapshots_extended.py` | yfinance → SQLite (187 tickers × N years) |
| `scripts/populate_fred_macro.py` | FRED macro signals → `fred_macro` table |
| `src/db/models.py` | ORM: StockSnapshot, FredMacro, EdgarFundamentals, Prediction |
| `experiments/iteration-log.md` | Every iteration, before/after Brier, decisions |
| `tasks/backlog.md` | Prioritized improvement ideas |

---

## Success metrics

| Metric | Random baseline | Current (Iter 23) | Target |
|--------|----------------|-------------------|--------|
| Overall Brier | 0.2500 | **0.2373** | < 0.2200 |
| 2022 Brier | 0.2500 | 0.2501 | < 0.2400 |
| 2023 Brier | 0.2500 | 0.2129 | — |
| 2024 Brier | 0.2500 | 0.2157 | — |
| Mean prediction | — | 48.6% | 45–55% |

Brier score: `(probability − actual)²`. Lower is better. Random guesser = 0.2500.

---

## What Claude can change autonomously

| Change | Auto | Ask first |
|--------|------|-----------|
| Edit `stock_features.py` (add/remove features) | ✓ | |
| Edit `train_stocks.py` (hyperparams, calibration) | ✓ | |
| Run `make iterate` / `make agent-iterate` | ✓ | |
| Log results in `iteration-log.md` | ✓ | |
| Create branches, commit, push | ✓ | |
| Run `make fetch-snapshots-year YEAR=YYYY` | ✓ | |
| DB schema changes (new Alembic migration) | | ✓ always |
| Add new data sources | | ✓ |
| Run `make fetch-snapshots-extended` (full re-fetch) | | ✓ |
| Change `stock_forecast.py` (affects live predictions) | | ✓ |

---

## Autonomous agent

Run the agent once to iterate hands-free through the backlog. It notifies you via
push notification before each iteration and when it stops.

### One-time setup

**1. WhatsApp bridge (Baileys)**

The bridge runs as a sidecar container and maintains a persistent WebSocket connection
to WhatsApp's servers using your personal account — no third-party service, no Twilio.

```bash
# Build the bridge image
make agent-build

# Add your number to .env
echo "WHATSAPP_TO=+12125551234" >> .env

# Start the bridge and scan the QR code (same as linking WhatsApp Web)
make whatsapp-setup
```

Scan the QR with WhatsApp → Linked Devices → Link a Device. The session is saved to
the `whatsapp_auth` Docker volume — you only scan once. After that:

```bash
make whatsapp-start   # keeps the bridge running in the background
make whatsapp-logs    # confirm "[wa] Connected ✓"
```

To re-link (e.g. after a logout): `docker volume rm 05-engineering_whatsapp_auth` then
`make whatsapp-setup` again.

**ntfy.sh fallback (optional)**
If the WhatsApp bridge is unreachable, `notify.py` automatically falls back to ntfy.sh.
To enable: pick a secret slug, install the [ntfy app](https://ntfy.sh), subscribe to
the topic, and add `NTFY_TOPIC=your-slug` to `.env`.

**2. GitHub PAT**
- Go to GitHub → Settings → Developer Settings → Personal Access Tokens → Fine-grained
- Permissions needed: `Contents: Read & Write` (to push branches)
- Add to `.env`:
  ```
  GITHUB_TOKEN=ghp_xxxxxxxxxxxx
  GITHUB_REPO=markstoughton/agent-preneur
  ```

**3. Build the agent image (once)**
```bash
make agent-build
```

### Running the agent

```bash
make agent
```

The agent will:
1. Read `experiments/iteration-log.md` and `tasks/backlog.md`
2. Send a notification before each iteration: what it's trying, current Brier, backlog count
3. Create branch `iter-N-<slug>`, make one change, run `make agent-iterate`
4. Push branch on improvement; delete branch and stay on main on regression
5. After 5 iterations (or on 3 consecutive regressions): send notification and exit

Run `make agent` again to continue after the 5-iteration checkpoint.

### Stop conditions

| Condition | Notification |
|-----------|-------------|
| 3 consecutive regressions | Urgent — review `iteration-log.md` |
| 5 iterations complete | High — run `make agent` to continue |
| Backlog exhausted | High — time to add new ideas |
| Brier < 0.220 | Urgent — target reached |

### Important: don't run agent and interactive dev simultaneously

Both use `./data/engine.db`. Running `make agent` and `make iterate` at the same
time will corrupt the DB. The agent uses `docker-compose.agent.yml`; interactive
dev uses `docker-compose.yml` — they are otherwise independent.

---

## Troubleshooting

**`make iterate` shows no improvement after feature change**
→ Verify the feature is in both `STOCK_FEATURE_NAMES` and the return dict in `extract_stock_features()`. Length mismatch will silently use wrong feature ordering.

**"no such table: fred_macro"**
→ Run `make populate-fred` and `alembic upgrade head`.

**All predictions near 50% / high Brier**
→ Check calibration: mean prediction should be 45–55%. If training data has no bear-market examples, the model will predict 50% for everything in downturns.

**Agent exits immediately**
→ Check that `ANTHROPIC_API_KEY` is set in `.env` and `AGENT_TASK.md` exists.

**Agent can't push to GitHub**
→ Verify `GITHUB_TOKEN` has `Contents: Write` on the correct repo. Check `GITHUB_REPO` matches `owner/repo` exactly.

**`make fetch-snapshots-extended` fails partway through**
→ It's idempotent — re-run and it skips already-cached rows. Use `--years YYYY` to retry a specific year.
