# Iteration Log

> Maintained by the agent iteration loop. One entry per `make iterate` cycle.
> Format: date | change made | before → after Brier | kept?

---

## How to read this log

- **Brier lower is better** (random baseline: 0.2500)
- **Bias OK** = mean model output between 45–55%
- **High-conf accuracy** = accuracy on predictions with |prob − 0.5| ≥ 0.20

---

## Iterations

*(newest at top — agent appends here after each cycle)*

---

### Iteration 1 — 2026-03-17

**Issue: Data leakage in ML backtest (critical)**

The `scripts/backtest_stocks.py` ML path was loading a pre-trained model
(`lgbm_stock_v1.pkl`) that had been trained on ALL years 2020–2024, then
evaluating that same model on years 2021–2024. Because the test data was
in the training set, the model had already "seen" every example it was
scored on. This produced artificially perfect results:

- Before fix: Brier = 0.0673, Accuracy = 100.0% (fake — severe data leakage)

**Fix applied:**

1. **Walk-forward evaluation in `backtest_stocks.py`** — the ML backtest path
   now ignores the pre-trained model file entirely and trains a fresh
   LightGBM + CalibratedClassifierCV pipeline for each test year, using
   only snapshots from prior years as training data. Year 2021 is skipped
   (only 1 prior year available; minimum is 2). Years 2022, 2023, 2024 are
   evaluated honestly.

2. **Feature name warning fix in `stock_predictor.py`** — `predict_proba()`
   now receives a `pd.DataFrame(vec, columns=STOCK_FEATURE_NAMES)` instead
   of a bare numpy array, eliminating the sklearn feature-name mismatch
   warning. The pre-trained model pipeline (`forecast-stocks-ml` command)
   benefits from this fix.

**Honest walk-forward results (run `f7e95587`):**

| Year | n  | Brier  | Accuracy |
|------|----|--------|----------|
| 2022 | 50 | 0.3013 | 48.0%    |
| 2023 | 50 | 0.2646 | 46.0%    |
| 2024 | 50 | 0.2327 | 64.0%    |
| **Overall** | **150** | **0.2662** | **52.7%** |

- Random baseline: 0.2500
- Before fix (leaky): Brier = 0.0673 → **After fix (honest): Brier = 0.2662**
- The model is currently near random; leaky evaluation was hiding this fact
- High-confidence predictions (≥70%) are only 3 samples with poor accuracy (33%)
- Next focus: improve features or retrieval to push Brier below 0.2500

**Kept:** Yes (fix is mandatory — leaky evaluation is not a valid baseline)

---

## Baseline

Run `make fetch-snapshots` then `make iterate` to establish the baseline entry.
