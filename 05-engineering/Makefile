.PHONY: build run ingest ingest-corpus shell jupyter lint test clean \
        train-stocks backtest-stocks backtest-stocks-ml forecast-stocks forecast-stocks-ml

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

## Step 0 (ONE-TIME) — Fetch historical snapshots from yfinance, cache in SQLite
## Re-run with: make fetch-snapshots ARGS="--refresh" to update
fetch-snapshots:
	docker compose run --rm engine python scripts/fetch_snapshots.py $(ARGS)

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
		--config experiments/stock-v1.yaml --dry-run

## -----------------------------------------------------------------------
## Claude-based stock workflow (original, kept for comparison)
## -----------------------------------------------------------------------

## Back-test using Claude + analogue retrieval
backtest-stocks:
	docker compose run --rm engine python scripts/backtest_stocks.py \
		--config experiments/stock-v1.yaml

## Forward-looking forecast using Claude
forecast-stocks:
	docker compose run --rm engine python scripts/stock_forecast.py \
		--config experiments/stock-v1.yaml

## -----------------------------------------------------------------------
## Infrastructure / utilities
## -----------------------------------------------------------------------

## Ingest fundamentals corpus into ChromaDB
ingest-corpus:
	docker compose run --rm engine python scripts/ingest.py --source corpus

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

## Run tests
test:
	docker compose run --rm engine pytest tests/ -v

## Remove the ChromaDB volume (WARNING: destroys all embeddings — requires re-ingestion)
clean-chroma:
	docker compose down -v
	@echo "ChromaDB volume removed. Run 'make ingest-corpus' to rebuild."

# Legacy targets kept for backward compatibility
run:
	docker compose run --rm engine python scripts/run.py --config experiments/v1.yaml
dry-run:
	docker compose run --rm engine python scripts/run.py --config experiments/v1.yaml --dry-run --max-questions 5
ingest:
	docker compose run --rm engine python scripts/ingest.py --source metaculus
