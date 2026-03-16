.PHONY: build run ingest ingest-corpus shell jupyter lint test clean

## Build the Docker image
build:
	docker compose build

## Run the offline loop with default config
run:
	docker compose run --rm engine python scripts/run.py --config experiments/v1.yaml

## Run a dry-run with a cap (cheap sanity check)
dry-run:
	docker compose run --rm engine python scripts/run.py --config experiments/v1.yaml --dry-run --max-questions 5

## Ingest fresh questions from Metaculus
ingest:
	docker compose run --rm engine python scripts/ingest.py --source metaculus

## Ingest corpus (pass path via COW_PATH env or edit directly)
ingest-corpus:
	docker compose run --rm engine python scripts/ingest.py --source corpus --path data/corpus/cow.csv

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

## Run tests
test:
	docker compose run --rm engine pytest tests/ -v

## Remove the ChromaDB volume (WARNING: destroys all embeddings — requires re-ingestion)
clean-chroma:
	docker compose down -v
	@echo "ChromaDB volume removed. Run 'make ingest-corpus' to rebuild."
