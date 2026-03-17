.PHONY: build shell jupyter lint test \
        train-stocks backtest-stocks-ml forecast-stocks-ml \
        fetch-snapshots fetch-snapshots-extended fetch-edgar populate-fred \
        results results-json iterate compare \
        agent-build agent agent-iterate agent-results fetch-snapshots-year \
        whatsapp-setup

## Build the Docker image (run this after any pyproject.toml change)
build:
	docker compose build

## -----------------------------------------------------------------------
## Stock ML workflow — fast iteration loop
## -----------------------------------------------------------------------
## One-time setup (run once, or after adding new tickers/years):
##   make fetch-snapshots
##
## Rapid iteration loop (seconds each after fetch-snapshots):
##   make train-stocks  →  make backtest-stocks-ml  →  tweak  →  repeat
## -----------------------------------------------------------------------

## Fetch SEC EDGAR XBRL fundamentals for all tickers (one-time, ~2 min)
## Idempotent — skips tickers already cached; use ARGS="--refresh" to re-fetch all
fetch-edgar:
	docker compose run --rm engine python scripts/fetch_edgar_fundamentals.py $(ARGS)

## Step 0a (ONE-TIME) — Populate fred_macro table with FRED indicators for 2018–2025
## Safe to re-run: skips years already cached. (~8 network calls, ~30s)
populate-fred:
	docker compose run --rm engine python scripts/populate_fred_macro.py

## Step 0 (ONE-TIME) — Fetch historical snapshots from yfinance, cache in SQLite
## Re-run with: make fetch-snapshots ARGS="--refresh" to update
## Fetch only TOP-50:   make fetch-snapshots
## Fetch full extended: make fetch-snapshots-extended  (~2-3 hours, run once)
fetch-snapshots:
	docker compose run --rm engine python scripts/fetch_snapshots.py $(ARGS)

## Fetch full ~250-ticker S&P 500 extended universe (run once, ~2-3 hours)
## Idempotent — skips tickers already cached; use ARGS="--refresh" to re-fetch all
fetch-snapshots-extended:
	docker compose run --rm engine python scripts/fetch_snapshots_extended.py $(ARGS)

## Step 1 — Train LightGBM from cached snapshots (seconds, no network calls)
train-stocks:
	docker compose run --rm engine python scripts/train_stocks.py \
		--output data/models/lgbm_stock_v1.pkl $(ARGS)

## Step 2 — Back-test ML model on cached historical data (seconds)
backtest-stocks-ml:
	docker compose run --rm engine python scripts/backtest_stocks.py \
		--config experiments/stock-ml.yaml $(ARGS)

## Step 3 — Forward-looking forecast with ML model (compare to analyst targets)
forecast-stocks-ml:
	docker compose run --rm engine python scripts/stock_forecast.py \
		--config experiments/stock-ml.yaml $(ARGS)

## Back-test dry-run — validates pipeline end-to-end, all predictions = 0.5
backtest-stocks-dry:
	docker compose run --rm engine python scripts/backtest_stocks.py \
		--config experiments/stock-ml.yaml --dry-run

## -----------------------------------------------------------------------
## Infrastructure / utilities
## -----------------------------------------------------------------------

## Open a shell inside the container
shell:
	docker compose run --rm engine bash

## Launch Jupyter notebook (open http://localhost:8888)
jupyter:
	docker compose run --rm -p 8888:8888 engine \
		jupyter notebook --ip=0.0.0.0 --no-browser --allow-root --notebook-dir=notebooks

## Compare two runs: make compare RUN1=<id> RUN2=<id>
compare:
	docker compose run --rm engine python scripts/compare.py --runs $(RUN1) $(RUN2)

## Run linter
lint:
	docker compose run --rm engine ruff check src/ scripts/

## Show results summary (human-readable)
results:
	docker compose run --rm engine python scripts/results_summary.py --n 5

## Show results as JSON (for agent parsing)
results-json:
	docker compose run --rm engine python scripts/results_summary.py --n 5 --format json

## Full iteration cycle: train → backtest → results
iterate:
	docker compose run --rm engine python scripts/train_stocks.py --output data/models/lgbm_stock_v1.pkl
	docker compose run --rm engine python scripts/backtest_stocks.py --config experiments/stock-ml.yaml
	docker compose run --rm engine python scripts/results_summary.py --n 1

## -----------------------------------------------------------------------
## Autonomous agent (runs Claude Code CLI inside Docker)
## -----------------------------------------------------------------------

## Build the agent Docker image (run once after Dockerfile.agent changes)
agent-build:
	docker compose -f docker-compose.agent.yml build

## Run the autonomous agent (WhatsApp bridge starts automatically inside the container)
agent:
	docker compose -f docker-compose.agent.yml run --rm agent

## -----------------------------------------------------------------------
## WhatsApp bridge (Baileys — runs inside the agent container)
## -----------------------------------------------------------------------

## First-time setup: scan QR code to link your WhatsApp account.
## Run once — session is saved to the whatsapp_auth Docker volume.
## Ctrl+C when you see "[wa] Connected ✓"
whatsapp-setup:
	docker compose -f docker-compose.agent.yml run --rm -it -e WHATSAPP_SETUP=1 agent

## -----------------------------------------------------------------------
## Direct Python targets — used INSIDE the agent container (no docker nesting)
## -----------------------------------------------------------------------

## Full iteration cycle: train → backtest → results (direct Python, no Docker)
agent-iterate:
	python scripts/train_stocks.py --output data/models/lgbm_stock_v1.pkl
	python scripts/backtest_stocks.py --config experiments/stock-ml.yaml
	python scripts/results_summary.py --n 1

## Results summary (direct Python)
agent-results:
	python scripts/results_summary.py --n 5

## Fetch snapshots for a single year: make fetch-snapshots-year YEAR=2024
## Use inside the agent container to avoid rate-limiting (one year at a time)
fetch-snapshots-year:
	python scripts/fetch_snapshots_extended.py --years $(YEAR)

## Run tests
test:
	docker compose run --rm engine pytest tests/ -v

