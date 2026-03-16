# Data Plan

## North Star Metric

**Mean Brier score across all predictions in a run, trending down over successive runs.**

Brier score = mean((predicted_probability - resolution)²). Lower is better. Ranges 0–1; random guessing = 0.25, perfect = 0.

## Supporting Metrics

| Metric | Definition | Target (90 days) |
|---|---|---|
| Brier score by prompt version | Per-prompt-version mean Brier score | Identify at least one prompt variant that beats baseline by >10% |
| Score distribution | % predictions in [0–0.05], [0.05–0.1], [0.1–0.25], [0.25+] buckets | Shift mass toward lower buckets over time |
| Worst-prediction rate | % predictions with Brier score > 0.2 | Reduce by 30% from run 1 to run 10 |
| Analogue utilization | How often retrieved analogues are actually cited in the rationale | Diagnostic — low utilization = retrieval isn't helping synthesis |
| Run-over-run delta | Brier score change between consecutive runs | Should trend negative; flat = stuck, positive = regressing |

## Instrumentation Plan

All events logged to SQLite. No external analytics tool.

| Event | Trigger | Properties |
|---|---|---|
| `run_started` | Beginning of offline loop | `run_id`, `config_version`, `prompt_version`, `timestamp` |
| `prediction_generated` | After LLM synthesis call | `run_id`, `question_id`, `analogues_used[]`, `prompt_version`, `probability_estimate`, `latency_ms`, `tokens_used` |
| `prediction_scored` | After scoring against resolution | `run_id`, `question_id`, `brier_score`, `resolved_value`, `predicted_probability` |
| `run_completed` | End of offline loop | `run_id`, `n_predictions`, `mean_brier`, `duration_s`, `cost_usd` |
| `prompt_version_registered` | When a new prompt template is added | `prompt_version`, `description`, `diff_from_previous` |

**Analytics tool:** SQLite + Jupyter notebook. No external tooling for v1.

## Experiment Design

**Primary experiment: prompt version comparison**

**Question:** Which prompt framing produces better-calibrated predictions — (A) presenting analogues as direct precedents ("these events are most similar to the current scenario") vs. (B) presenting them as contrasting cases ("here is what happened in similar situations, and here is what's different")?

**Hypothesis:** Contrastive framing (B) forces the model to reason about disanalogies, producing better-calibrated probabilities rather than anchoring too heavily on the most salient historical outcome.

**Metric:** Mean Brier score per prompt version across the same resolved question set.

**Minimum sample size:** 200 questions per variant (sufficient for meaningful Brier score comparison at this scale).

## Data Infrastructure

- **Storage:** SQLite for all structured data (predictions, scores, run logs); ChromaDB for embeddings — both in a Docker volume, persisted across container runs
- **Querying:** Direct SQL for structured queries; pandas in Jupyter for analysis
- **Alerting:** None for v1 — researcher reviews `compare.py` output after each run
- **Cost tracking:** Log `tokens_used` per prediction call; compute `cost_usd` at run level so API spend is visible

---

## Open Questions

- None blocking — data plan is sufficient for v1.

## Decisions Made

- **Prompting is the primary tuning lever** — retrieval assumed adequate; Brier score improvement comes from better synthesis framing
- **Versioned prompts with direct Brier score attribution** — every prompt template gets a version ID, every prediction logs which prompt was used
- **No external analytics tool** — SQLite + Jupyter is sufficient; add PostHog or similar only if this becomes a multi-user system
