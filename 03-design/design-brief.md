# Design Brief

> This is a headless agent system — no UI. "Design" here means interface shape, output formats, and developer experience.

## Design Principles

1. **Reproducibility over convenience** — every run must be fully reconstructable from logged config and data; no silent defaults
2. **Inspect everything** — predictions, analogues, scores, and reasoning chains are always readable by a human reviewing results; black boxes are a bug
3. **Fast iteration above all** — the offline loop should be trivially runnable; adding a new retrieval experiment should take minutes, not hours

## Key Flows

### Flow 1: Offline training run

1. Researcher runs `python run.py --config experiments/v1.yaml`
2. Ingestion checks local DB; fetches any new resolved Metaculus questions and corpus events
3. For each question: retrieve analogues → synthesize prediction → score against resolution
4. Results written to SQLite + a run log (JSON lines)
5. `python analyze.py --run <run_id>` opens a Jupyter notebook with Brier score breakdown, analogue quality stats, worst predictions

### Flow 2: Experiment comparison

1. Two or more run configs defined in `experiments/`
2. Both runs executed (can be parallelized)
3. `python compare.py --runs v1 v2` outputs a side-by-side table: mean Brier, score distribution, which questions improved/worsened
4. Researcher identifies which retrieval config performed better and promotes it

### Flow 3: Live prediction (v2)

1. Cron or manual trigger: `python predict_live.py`
2. Fetches active unresolved Metaculus geopolitical questions
3. Generates and stores predictions with current timestamp
4. When questions resolve (detected on next run), scores are computed and logged automatically

## Component Inventory

No UI components. System interfaces:

- [ ] **CLI runner** (`run.py`) — accepts config file, executes offline loop, reports progress
- [ ] **Config schema** (YAML) — defines retrieval strategy, top-k, corpus filters, model params
- [ ] **SQLite schema** — Questions, HistoricalEvents, Analogues, Predictions, Scores, RunConfigs, RunResults
- [ ] **Analysis notebook** (`analyze.ipynb`) — Brier score breakdown, analogue quality, worst-case predictions
- [ ] **Comparison script** (`compare.py`) — side-by-side run comparison output to terminal table

## Design References

- Inspired by ML experiment tracking tools (MLflow, Weights & Biases) in spirit — but no external dependency; local-first
- Output format: structured JSON lines for machine readability + human-readable summary printed to stdout

## Responsive / Platform Notes

- Local Python environment only; no web, no mobile
- Target: runs comfortably on a MacBook Pro; GPU not required (embeddings via API, not local model)
- Docker-ready structure from the start, even if not containerized in v1

---

## Stack Recommendation

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Ecosystem fit: LLM SDKs, data tools, Jupyter all native |
| LLM | Anthropic Claude (claude-sonnet-4-6) | Default; configurable via env var to swap |
| Embeddings | Voyage AI or OpenAI embeddings | High quality; API-based so no local GPU needed |
| Vector store | ChromaDB (local) | Zero-infra, persistent, good Python SDK; swap to pgvector later |
| Relational store | SQLite | Sufficient for v1 scale (~2k questions); no server needed |
| Config | YAML + Pydantic | Human-readable configs with validation |
| Analysis | Jupyter + pandas + matplotlib | Standard; no custom tooling |
| Packaging | `uv` + `pyproject.toml` | Fast, modern Python packaging |

## Open Questions

- Embeddings provider: Voyage AI has better retrieval quality for long-form text; OpenAI is simpler. Start with OpenAI, benchmark against Voyage if retrieval quality plateaus.

## Decisions Made

- **Anthropic Claude as default LLM** — configurable via env var
- **Local-first, no cloud infra for v1** — SQLite + ChromaDB, runs on a laptop
- **CLI + Jupyter, no web UI** — output is for the researcher/agent, not end users
