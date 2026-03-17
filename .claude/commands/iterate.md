Run one full ML iteration cycle using specialist agents.

Arguments: $ARGUMENTS
  (empty)         — full cycle: finance diagnosis + ML improvement + log
  "ml"            — ML agent only: run make iterate, log results
  "finance"       — Finance agent only: interpret latest results
  "data"          — Data agent only: expand corpus or add data sources
  "status"        — print current Brier, feature importances, open issues

## Protocol

### 1. Read current state
Always start here:
```
make results-json    → parse latest Brier, bias, confidence buckets, issues list
```
Read `05-engineering/experiments/iteration-log.md` — what's been tried, what worked.

### 2. Route based on argument or auto-detect priority

**If argument is "finance" or auto-detect detects a known bad-ticker pattern:**
  Read `.claude/guides/agents/engineering/finance-specialist.md`
  Analyze the worst 3-5 prediction errors from `make results-json`
  Identify root causes (sector rotation? macro regime change? missing feature?)
  Output: list of specific feature additions that would fix each root cause

**If argument is "ml" or finance agent has produced recommendations:**
  Read `.claude/guides/agents/engineering/ml-specialist.md`
  Select ONE highest-leverage change from the recommendations
  Implement it in `stock_features.py` or `train_stocks.py`
  Run `make iterate` (train → backtest → results)
  Compare before/after Brier by year
  Write iteration-log entry

**If argument is "data":**
  Read `.claude/guides/agents/engineering/data-corpus.md`
  Check how many snapshots are in the DB vs target
  If extended fetch needed: `make fetch-snapshots-extended`
  If FRED integration needed: implement `_fetch_fred_year_snapshot()` in fundamentals.py

**If argument is "status":**
  Run `make results-json`
  Print: current Brier, top features, issues, iteration count

### 3. Full cycle (no argument)
Run sequentially:
1. Finance agent → diagnosis
2. ML agent → implement top recommendation + run make iterate
3. Log results in iteration-log.md
4. Print: change made, before→after Brier, next recommended action

## Rules

- **One change per cycle** — never change features AND hyperparams simultaneously
- **Revert if no improvement after 2 tries** — log the failure with reasoning
- **Never run make fetch-snapshots without confirming** — it takes 20+ minutes
- **Log every cycle** in `05-engineering/experiments/iteration-log.md` — format: `date | change | before→after Brier | kept?`
- **Compare by year** — overall Brier can mask a year getting worse; check all years
