# Business Case

## Market

Not applicable as a traditional market — this is a self-improving research agent, not a product sold to users. The relevant "market" is the space of geopolitical prediction problems that are:
- Publicly posed with resolution criteria (Metaculus, Polymarket, ACLED events)
- Resolvable within a 6-24 month window (enough feedback signal to iterate)
- Structurally comparable to historical events in the record

There are ~500-2,000 active geopolitical questions on Metaculus at any time. That's the initial problem set.

## Competition

| Competitor | What they do | Why we're different |
|---|---|---|
| Metaculus / Good Judgment Project | Aggregate human forecasters; track calibration over time | Human-bounded — forecasters draw on personal knowledge, not full historical record; not self-improving |
| Polymarket / Manifold | Prediction markets; price reflects crowd wisdom | Price-driven, not explanation-driven; no analogue reasoning; no self-improvement loop |
| RAND / Brookings / think tanks | Expert analyst reports on geopolitical scenarios | Expensive, slow, not scalable, not measurable, no feedback loop |
| GPT/LLM prompted directly | Can reason about history if prompted well | No systematic analogue retrieval; no prediction tracking; no self-improvement; biased by training recency |
| Superforecasters (Tetlock) | Trained humans who beat expert consensus | Still human-bounded; slow; expensive to scale; not self-improving |

**Core differentiation:** systematic structural matching across the full historical record + a closed feedback loop that improves analogue selection over time. No existing system combines both.

## Revenue Model

- Model: N/A — self-contained research tool, no monetization in scope
- The value produced is prediction accuracy and the improving model itself
- Future optionality: insights could be licensed, or the system could be a backend for a forecasting product — but this is explicitly out of scope for v1

## OKRs — First 12 Months

**Objective 1: Build a prediction engine that makes measurable geopolitical forecasts**
- KR1: System autonomously generates predictions on ≥50 active Metaculus geopolitical questions within 90 days
- KR2: Each prediction includes ≥3 historical analogues with structural similarity rationale

**Objective 2: Demonstrate self-improvement through the feedback loop**
- KR1: Calibration score (Brier score) improves by ≥15% from month 3 to month 12
- KR2: Analogue retrieval quality improves measurably — later predictions draw on structurally closer historical matches than early ones (scored retrospectively)

---

## Open Questions

- What historical event corpus to start with? Options: Wikipedia structured events, Correlates of War dataset, GDELT, hand-curated case library. Likely: start with CoW + GDELT for structure, Wikipedia for narrative depth.
- How to define "structural similarity" between a current scenario and a historical one? This is the core research problem — likely needs iteration.

## Decisions Made

- Benchmark: Metaculus geopolitical questions as the primary evaluation set; Brier score as the calibration metric
- Revenue: none in scope; this is a research tool
