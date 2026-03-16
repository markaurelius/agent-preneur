# Engineering Spec — Analogue Prediction Engine

> Authoritative technical reference for `/build` tasks.
> Last updated: 2026-03-15

## Vision

A domain-agnostic self-improving prediction agent. Given any question about the future, it finds structurally similar historical events, uses Claude to reason from those analogues, and scores predictions against known resolutions (Brier score).

The key insight: Claude's knowledge has a training cutoff. For questions that resolve *after* that cutoff, Claude cannot simply recall the answer — it must reason. The better the historical analogues, the better the reasoning. This creates a tight feedback loop: better corpora → better retrieval → lower Brier score.

**v1 domain: geopolitics.** The architecture is built to be domain-agnostic from day one.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  CORPUS (one-time setup per domain)                             │
│  Historical events → embed → ChromaDB + SQLite                  │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  OFFLINE LOOP (evaluation / training)                           │
│  Resolved questions → retrieve analogues → Claude → Brier score │
│  Iterate on prompts, corpora, retrieval strategies              │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  LIVE FORECASTING                                               │
│  Open questions → retrieve analogues → Claude → store prediction│
│  Score when questions resolve                                   │
└─────────────────────────────────────────────────────────────────┘
```

All data local. No web UI. No cloud infra. Runs in Docker.

---

## Project Structure

```
05-engineering/
├── src/
│   ├── db/
│   │   ├── models.py          — SQLAlchemy ORM (6 tables)
│   │   ├── session.py         — engine + session factory (WAL mode)
│   │   └── migrations/        — Alembic migration files
│   ├── ingestion/
│   │   ├── metaculus.py       — questions from HuggingFace dataset + live API
│   │   └── corpus.py          — CoW MID event parser + batch embedding + ChromaDB
│   ├── retrieval/
│   │   └── retriever.py       — embedding / metadata / hybrid retrieval modes
│   ├── synthesis/
│   │   ├── predictor.py       — Claude tool_use structured prediction
│   │   └── prompts/
│   │       └── v1.txt         — prompt template v1
│   ├── scoring/
│   │   └── scorer.py          — Brier score calculation
│   ├── runner/
│   │   └── offline_loop.py    — batch runner over resolved questions
│   └── config/
│       └── schema.py          — Pydantic RunConfig + YAML loader
├── experiments/
│   └── v1.yaml                — v1 baseline config
├── scripts/
│   ├── run.py                 — CLI: offline evaluation loop
│   ├── ingest.py              — CLI: ingest questions or corpus
│   ├── forecast.py            — CLI: predict on live open questions
│   └── resolve.py             — CLI: score predictions when questions close
├── data/                      — gitignored; Docker bind mount
│   └── corpus/                — raw dataset CSVs
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

---

## Data Model (SQLite)

### `questions`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `metaculus-{id}` |
| text | TEXT | Question text |
| resolution_date | DATETIME | When it resolves |
| resolution_value | REAL | 0.0 or 1.0; NULL = still open |
| community_probability | REAL | Community forecast (nullable) |
| tags | JSON | `["live"]` for forecast.py questions |
| created_at | DATETIME | |

### `historical_events`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `{source}-{id}` e.g. `cow-4021-RUS` |
| description | TEXT | Narrative text — the embedded field |
| actors | JSON | `["RUS", "UKR"]` |
| event_type | TEXT | `conflict \| diplomacy \| other` |
| outcome | TEXT | How it resolved |
| date | TEXT | `YYYY-MM-DD` |
| region | TEXT | |
| chroma_id | TEXT | Matches ChromaDB document ID |
| created_at | DATETIME | |

### `run_configs`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| name | TEXT | Human-readable (from YAML) |
| top_k | INTEGER | Analogues to retrieve |
| similarity_type | TEXT | `embedding \| hybrid \| metadata` |
| embedding_weight | REAL | For hybrid mode |
| metadata_weight | REAL | For hybrid mode |
| metadata_filters | JSON | Optional event_type / region filters |
| prompt_version | TEXT | Which prompts/*.txt to use |
| model | TEXT | Anthropic model ID |
| max_questions | INTEGER | Dev cap; NULL = all |
| min_resolution_year | INTEGER | Exclude questions resolving before this year |
| dry_run | BOOLEAN | Skip LLM calls, use dummy 0.5 |

### `run_results`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| config_id | TEXT FK | → run_configs |
| n_predictions | INTEGER | |
| mean_brier_score | REAL | Primary metric |
| median_brier_score | REAL | |
| cost_usd | REAL | Estimated API cost |
| started_at | DATETIME | |
| completed_at | DATETIME | NULL = incomplete |

### `predictions`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| run_id | TEXT FK | → run_results |
| question_id | TEXT FK | → questions |
| probability_estimate | REAL | 0.0–1.0, clamped to [0.01, 0.99] |
| rationale | TEXT | Claude's reasoning |
| analogues_used | JSON | `[{event_id, similarity_score}]` |
| prompt_version | TEXT | Snapshot at prediction time |
| model | TEXT | |
| tokens_used | INTEGER | |
| latency_ms | INTEGER | |

### `scores`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | UUID |
| prediction_id | TEXT FK | → predictions (unique) |
| brier_score | REAL | `(probability - resolution)²` |
| resolved_value | REAL | Ground truth |
| community_brier_score | REAL | Nullable baseline comparison |

---

## Vector Store (ChromaDB)

**Collection per corpus source** (currently: `historical_events`)

Each document:
- `id`: matches `historical_events.chroma_id`
- `document`: `historical_events.description` — the embedded text
- `metadata`: `{event_id, event_type, region, date, actors_json}`

Embeddings computed once on ingest. Idempotent: re-runs skip existing IDs.
Embedding provider: Voyage AI (`voyage-3`) → OpenAI (`text-embedding-3-small`) fallback.

---

## Key Design Decisions

**Why Brier score?** Proper scoring rule — rewards calibration, not just direction. Random = 0.25. Perfect = 0.0. v1 baseline: **0.1558**.

**Why offline-first?** 1,543 resolved geopolitics questions available immediately as training data. Fast iteration without waiting for live resolutions.

**Why `min_resolution_year`?** Claude's training cutoff (~Aug 2025) means it likely knows the answers to older questions. Filtering to post-cutoff questions ensures the analogues are actually doing work, not masking memorization.

**Why tool_use for structured output?** Forces Claude to return `{probability, rationale}` schema — no fragile text parsing.

---

## Retrieval Modes

| Mode | How | Best for |
|------|-----|---------|
| `embedding` | ChromaDB cosine similarity on question text | Default; semantic matching |
| `metadata` | SQL filter on event_type/region + date proximity | When domain/region is known |
| `hybrid` | Embedding pool → metadata re-rank | Best quality; slower |

Retriever fetches `top_k * 4` candidates, then prefers analogues with known outcomes over "Unknown" before returning top_k.

---

## CLI

```bash
# Ingest historical corpus
python scripts/ingest.py --source corpus --path data/corpus/cow.csv

# Ingest resolved questions (historical, from HuggingFace)
python scripts/ingest.py --source metaculus

# Run offline evaluation
python scripts/run.py --config experiments/v1.yaml
python scripts/run.py --config experiments/v1.yaml --dry-run --max-questions 10

# Live forecasting (open questions)
python scripts/forecast.py --config experiments/v1.yaml --limit 20

# Score resolved live predictions
python scripts/resolve.py
```

---

## Prompt Iteration Workflow

```
experiments/v1.yaml  →  src/synthesis/prompts/v1.txt  →  mean_brier: 0.1558  (baseline)
experiments/v2.yaml  →  src/synthesis/prompts/v2.txt  →  mean_brier: ?
```

To run a new experiment:
1. Copy `prompts/v1.txt` → `prompts/v2.txt`, edit the prompt
2. Copy `experiments/v1.yaml` → `experiments/v2.yaml`, set `prompt_version: v2`
3. Run: `python scripts/run.py --config experiments/v2.yaml`
4. Compare Brier scores between runs

---

## Environment Variables

```
ANTHROPIC_API_KEY=
METACULUS_API_KEY=     # required for forecast.py + resolve.py
VOYAGE_API_KEY=        # optional; better embeddings than OpenAI
OPENAI_API_KEY=        # optional; fallback if VOYAGE_API_KEY not set
DATABASE_URL=sqlite:////app/data/engine.db
CHROMA_PATH=/app/chroma
```
