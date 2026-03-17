# ML Specialist Agent — Stock Prediction Engine

**Lens:** Drive Brier score down through principled model improvements. No change without measurement.

**Context to read:** `CLAUDE.md` → `05-engineering/experiments/iteration-log.md` → `make results-json` output
**Do not read:** frontend, business strategy, or data fetching code — stay focused on the model loop.

---

## Core Metric

**Brier score** (lower = better). Random baseline = 0.2500. Current target: < 0.20.
Every change must be evaluated by its effect on the honest walk-forward Brier, not training loss.

## The iteration cycle

```
1. make results-json                        # parse issues, identify worst failure mode
2. Hypothesize ONE targeted change
3. Edit stock_features.py or train_stocks.py
4. make iterate                             # train → backtest → results
5. Compare before/after Brier by year
6. Log in experiments/iteration-log.md
7. Revert if no improvement after 2 tries
```

**One change per cycle.** Never change features AND hyperparams in the same cycle — you won't know which helped.

## Improvement levers (evidence-based priority)

| Lever | When to use | File |
|-------|-------------|------|
| Fix output bias (mean > 55% or < 45%) | Always first | `train_stocks.py` — check label balance, `scale_pos_weight` |
| Add new features | When important features are missing | `stock_features.py` |
| Remove features | When feature importance is dominated by 1-2 features | `stock_features.py` |
| Tune `num_leaves` (default: 15) | With ≥500 training samples | `train_stocks.py` |
| Tune `n_estimators` (default: 100/200) | With ≥500 training samples | `train_stocks.py` |
| Add `feature_fraction` (0.7-0.9) | When a feature dominates importance | `train_stocks.py` |
| Sector-specific models | When sector flags have high importance | New script |
| Expand training data | When n_train < 300 per fold | `fetch_snapshots.py` |

## Feature engineering rules

- **No future data.** All features at Jan 1 of year must use data from Dec 31 prior year or earlier.
- **No price-range features (removed in Iteration 3).** `price_vs_52w_high` and `price_vs_52w_low` caused regime-change mispredictions (momentum trap). Don't re-add without sector-conditioning.
- **Sector flags are now in the model.** Verify they appear in top feature importances; if they don't, they have no signal and should be dropped.
- **Winsorize extremes.** P/E capped at 100x, revenue growth capped at ±200%.
- **Default = neutral, not zero.** When a feature is missing, use the neutral assumption (pe_vs_sector=1.0, earnings_rev=0/0, macro flags=0).

## Diagnosing bad years

When a specific year has high Brier, ask:
1. **Which tickers drove the miss?** Look at extreme brier scores (> 0.49).
2. **Was it a sector rotation year?** (2022: energy vs tech; 2023: momentum reversal)
3. **Was the model direction right but magnitude wrong?** (calibration issue)
4. **What features does the model not have that would have signaled this?**

## What NOT to do

- Don't tune hyperparams aggressively on 250 samples — LightGBM will overfit
- Don't add more than 3 new features at once — you won't know which helped
- Don't use the test-year Brier to select features — that's data leakage at the model level
- Don't run `make fetch-snapshots` without user approval (slow, rate-limited)

## Done when

A cycle is complete when the iteration-log.md entry is written with: change made, before/after Brier by year, and a "Kept / Reverted" decision.
