# Product Requirements

## Problem Statement

Geopolitical prediction is bottlenecked by human working memory — analysts draw on 3-5 salient historical cases rather than the full universe of structurally similar precedents. This system replaces that bottleneck with a self-improving agent that systematically retrieves historical analogues, generates calibrated predictions, and improves its retrieval logic through a fast offline training loop on thousands of resolved questions.

## Users

The system itself — an autonomous agent whose job-to-be-done is to improve its own Brier score on geopolitical forecasting questions over time.

## Requirements

### Must Have (v1)

- [ ] **Historical question ingestion**: Fetch all resolved geopolitical questions from Metaculus API (question text, resolution date, resolution value, community probability at close)
- [ ] **Historical event corpus**: Ingest a structured corpus of historical geopolitical events with metadata (actors, event type, outcome, date, region) — starting with Correlates of War + Wikipedia summaries
- [ ] **Analogue retrieval**: Given a question, retrieve the N most structurally similar historical events using a defined similarity function (v1: embedding similarity + metadata filters)
- [ ] **Prediction synthesis**: Generate a probability estimate + rationale from retrieved analogues using an LLM call
- [ ] **Scoring**: Calculate Brier score for each prediction against known resolution
- [ ] **Offline training loop**: Batch runner that iterates over resolved questions, generates predictions, scores them, and logs results — runnable end-to-end without human intervention
- [ ] **Weighting/retrieval update**: Mechanism to adjust analogue retrieval based on which features correlated with better Brier scores (v1: prompt-tuning or re-ranking weights; v2: fine-tuning)
- [ ] **Experiment tracking**: Log each run's predictions, analogues used, scores, and retrieval config so iterations are comparable

### Nice to Have (v2)

- [ ] Live prediction mode: run against active (unresolved) Metaculus questions and store predictions for future scoring
- [ ] Fine-tuning pipeline: use consistently wrong live predictions as additional training signal
- [ ] Richer corpus: add GDELT event stream, ACLED conflict data, declassified diplomatic cables

### Out of Scope

- Any user-facing interface or dashboard (no humans in the loop for v1)
- Monetization, APIs, or external integrations beyond data sources
- Non-geopolitical question types (defer until geopolitical loop is proven)
- Real-time event ingestion (offline batch is sufficient for v1)

## User Stories

Since the "user" is the system itself, these are framed as agent capabilities:

1. As the training loop, I want to fetch all resolved Metaculus geopolitical questions so I have a ground-truth dataset to train and evaluate against.
2. As the retrieval engine, I want to find historically similar events given a question so I can ground predictions in structural precedent rather than parametric LLM knowledge.
3. As the prediction engine, I want to synthesize a calibrated probability from retrieved analogues so each prediction has an explicit, auditable reasoning chain.
4. As the optimization loop, I want to compare Brier scores across retrieval configurations so I can identify which analogue features most improve accuracy.
5. As the experiment tracker, I want to log every prediction run with full context so performance trends are visible and iterations are reproducible.

---

## Open Questions

- **Similarity function design**: Start with embedding similarity (question text → event description)? Or use structured metadata (regime type, conflict type, actors) as primary signal with embeddings as fallback? This is the core research question — both approaches should be tested in the offline loop.
- **Corpus size vs. quality tradeoff**: A large but noisy corpus (GDELT) vs. a smaller but well-structured one (Correlates of War)? Start small and high-quality, expand if retrieval quality plateaus.

## Decisions Made

- **Offline-first training loop** — use resolved Metaculus questions as both training and validation set; eliminates waiting for live events to resolve, enables fast iteration
- **Analogue retrieval is the core problem** — v1 investment goes here; synthesis and scoring are comparatively straightforward
- **No fine-tuning in v1** — weighting updates via prompt-tuning or re-ranking; full fine-tuning deferred to v2

---

## Engineering Handoff

**Core entities:**
- `Question` — Metaculus question (id, text, resolution_date, resolution_value, community_probability, tags)
- `HistoricalEvent` — event in the corpus (id, description, actors, event_type, outcome, date, region, embedding)
- `Analogue` — a (question, historical_event) pair with a similarity score and features used
- `Prediction` — (question_id, analogues[], probability_estimate, rationale, run_id)
- `Score` — (prediction_id, brier_score, resolved_value)
- `RunConfig` — retrieval hyperparameters for a given experiment run (similarity function, top-k, filters)
- `RunResult` — aggregate stats for a run (mean Brier score, n predictions, config_id)

**Key actions:**
- `ingest_metaculus_questions()` — fetch and store resolved geopolitical questions via Metaculus API
- `ingest_event_corpus()` — load and embed historical event corpus
- `retrieve_analogues(question, config)` → `Analogue[]` — core retrieval function
- `synthesize_prediction(question, analogues)` → `Prediction` — LLM call with structured output
- `score_prediction(prediction, resolution)` → `Score`
- `run_offline_loop(config)` — batch runner over all resolved questions
- `compare_runs(run_ids[])` — surface which config produced the best Brier scores

**Integrations required:**
- Metaculus API (public, no auth required for read)
- OpenAI / Anthropic API (LLM calls for synthesis)
- Correlates of War dataset (public download)
- Vector store for embeddings (Chroma or pgvector — local for v1)

**Performance/scale constraints:**
- Offline loop must process ~2,000 resolved questions per run; acceptable if it takes hours, not days
- Embedding and retrieval should be fast enough that the bottleneck is LLM synthesis calls
- All data local for v1 — no cloud infra required

**Auth model:**
- None — fully local system; API keys stored in env vars

**Deployment target:**
- Local Python environment, v1. Single machine. No containerization required initially — add Docker once the loop is stable.
