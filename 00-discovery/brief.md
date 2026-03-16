# Discovery Brief

## The Problem

Geopolitical analysts pattern-match to 3-5 memorable historical cases when reasoning about current events — often the wrong ones. The full universe of relevant precedents is scattered across thousands of books, academic papers, declassified documents, and case studies. No single human can hold all of it simultaneously, so predictions are systematically biased toward salient/famous cases rather than the most structurally similar ones. The result: poor calibration, missed analogues, and expensive teams of historians to do what should be a computational problem.

## The Users

The system itself. This is a self-improving research tool — an agent that generates geopolitical predictions from historical analogues, tracks how events actually unfold, and updates its analogue-selection and weighting model based on prediction accuracy. No human end-user in v1.

## Why Now

Three things converged:
1. **LLMs can now synthesize qualitative, narrative historical data** — previously only structured data was computationally tractable; the richest historical knowledge lived in prose
2. **Event databases and outcome trackers exist** — GDELT, ACLED, Correlates of War, Wikipedia event logs, and real-time news APIs provide both historical training material and live validation signals
3. **Cost of iteration is near zero** — what previously required a team of historians and political scientists can now be run, evaluated, and iterated on by a single agent in a feedback loop

## What Success Looks Like

At 12 months:
- The system's geopolitical predictions show **improving calibration over time** — measured against Metaculus/Polymarket as a baseline for human+market consensus
- The analogue retrieval improves measurably: later predictions draw on structurally better-matched historical cases than early ones (evaluable by retrospective scoring)
- The feedback loop is self-sustaining: the system surfaces new scenarios, makes predictions, and validates them without manual intervention

---

## Open Questions

- What historical event taxonomy works best for structural matching — geographic/temporal features, regime type, conflict type, or something else? Start with hypothesis, validate empirically.
- How do we handle prediction timeframes? Short-horizon (weeks) vs. long-horizon (years) require different analogue types.
- What's the right granularity for a "geopolitical scenario" as input?

## Decisions Made

- Domain: geopolitical events (v1 scope)
- Architecture: self-improving agent, not a human-facing product
- Feedback loop: real-world outcome validation via news/event APIs, benchmarked against prediction markets
