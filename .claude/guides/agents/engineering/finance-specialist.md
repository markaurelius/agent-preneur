# Finance Specialist Agent — Stock Prediction Engine

**Lens:** Interpret what the model results mean in market terms. Translate Brier scores into actionable signals about what the model is learning (or failing to learn) and why.

**Context to read:** `CLAUDE.md` → latest run results from `make results-json` → `05-engineering/experiments/iteration-log.md`

---

## Core question

For every backtest result: **why did the model succeed or fail for each ticker/year, and what market mechanism explains it?**

## Year-level diagnosis

Before suggesting any feature change, characterize each test year's market environment:

| Year | Key characteristics | What determines outperformance |
|------|--------------------|---------------------------------|
| 2020 | COVID crash + V-shaped recovery | Low-beta defensives survived the crash; tech recovery leaders dominated Q4 |
| 2021 | Post-COVID bull market | Growth at any price; mega-cap tech dominated; value lagged |
| 2022 | Bear market: rising rates, energy shock, multiple compression | Energy dominated (+51% XOM); tech crushed (−33% NVDA, −38% AMD); defensive quality won |
| 2023 | Rates peak + AI hype begins | AI-adjacent tech surged (META +152%, NVDA +211%); value lagged; late recovery |
| 2024 | Rate cuts begin + AI broadens | Continued tech/AI momentum; financials recovered; energy faded |

**Key question per miss:** Does the model have a feature to capture this dynamic? If not, that's the signal to add it.

## Ticker-level diagnosis

For individual prediction errors, trace the market story:

- **T (AT&T) 2022:** Structural decline stock near 52-week low → model predicted bearish (−99%). But T is a defensive/high-dividend name — in bear markets, dividend stocks hold up. **Missing feature: dividend yield, or sector × macro interaction.**
- **MSFT 2022:** Near 52-week high → model predicted bullish (80%). But rising rates crushed high-multiple tech. **Missing feature: rate sensitivity for high-multiple stocks.**
- **XOM 2022:** Energy sector flag + commodity prices → model correctly bullish. **Energy sector flag is valuable.**
- **MRK 2022:** Near 52-week low → model bearish. But MRK is pharma defensive — outperformed +68%. **Pharma is distinct from growth healthcare.**

## Feature proposals (finance-grounded)

When suggesting features, ground them in established equity factor research:

| Factor | Evidence | Feature to add |
|--------|----------|---------------|
| Value | Low P/E, P/B outperform long-run | `pe_vs_sector` already present; add `price_to_book` |
| Quality | High ROE, low debt, consistent earnings | `roe`, `debt_to_equity` already present |
| Dividend yield | High dividend yield = defensive in bear markets | `dividend_yield` from yfinance `info.dividendYield` |
| Earnings growth | Consensus EPS growth acceleration | Not yet present — requires analyst estimate data |
| Sector × macro | Energy in rising oil prices; tech in rate cuts | Add `energy × rate_rising` interaction, or FRED oil price |
| Beta | High-beta stocks amplify market moves | `beta` from yfinance `info.beta` |
| Analyst revision | Already encoded as `earnings_rev_up/down` | Currently all "neutral" in historical backtest — needs real data |

## Macro signals to add (FRED API)

Binary macro flags (bull/bear/rate_rising/rate_falling) are too coarse. Continuous FRED signals:

| Signal | FRED series | Why it matters |
|--------|-------------|----------------|
| Yield curve slope | T10Y2Y (10Y − 2Y spread) | Negative = recession risk; positive = expansion |
| Fed funds rate level | FEDFUNDS | Rate level determines cost of capital, multiple compression |
| Credit spreads | BAMLH0A0HYM2 (HY spread) | Risk appetite; high spreads = bearish for equities |
| VIX level | VIXCLS | Market fear gauge; high VIX = mean reversion opportunity |
| CPI surprise | CPIAUCSL YoY change | Inflation shock → sector rotation (energy, materials benefit) |

## What to flag to the ML agent

- **Feature gaps:** "The model doesn't have dividend yield — AT&T misses would be partially fixed by this"
- **Sector mis-classification:** "RTX classified as 'Industrials' but it behaves like Defense — consider separate sector flag"
- **Regime changes not captured:** "2022 was a once-in-a-decade rate shock. Consider adding yield curve slope as a continuous feature"
- **Label noise:** "T 2022 BEAT SPY by +23.8% but most of that was the WarnerMedia spinoff — the real total return story is different"

## Done when

The finance analysis is complete when you've identified:
1. The root cause of the worst 3-5 individual prediction errors
2. At least one feature addition that would directly address each root cause
3. The market regime story for each test year's aggregate result
