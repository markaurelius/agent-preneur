# Data Corpus Specialist Agent — Stock Prediction Engine

**Lens:** Expand and diversify the training data. More tickers, more years, better features.
The model is data-starved (250 samples). This agent's work unlocks all model improvement.

**Context to read:** `CLAUDE.md` → `05-engineering/src/ingestion/fundamentals.py` → `05-engineering/scripts/fetch_snapshots_extended.py`

---

## Data sources (in priority order)

### 1. yfinance — operational, already used
- `yf.download()` bulk price download: ALL tickers, ALL years, ONE network call
- Per-ticker `.info` and `.financials` via `ThreadPoolExecutor` (8 workers)
- Script: `scripts/fetch_snapshots_extended.py`
- Status: ~250 tickers in `SP500_EXTENDED`, fetch ongoing

### 2. FRED API — free, no auth required for many series
- Base URL: `https://fred.stlouisfed.org/graph/fredgraph.csv?id={SERIES}`
- No API key required for basic series; free key at fred.stlouisfed.org for bulk
- Key series to add:
  - `T10Y2Y` — 10Y-2Y yield curve slope (recession signal)
  - `FEDFUNDS` — Fed funds rate (cost of capital)
  - `BAMLH0A0HYM2` — High-yield credit spread (risk appetite)
  - `VIXCLS` — VIX level (fear/opportunity signal)
  - `CPIAUCSL` — CPI (inflation shock detector)
- Integration point: `src/ingestion/fundamentals.py::fetch_macro_regime()` — replace binary flags with continuous values from FRED at each snapshot year-start

### 3. SEC EDGAR XBRL API — free, no auth
- Structured financial statements for all US public companies
- URL pattern: `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`
- Provides: revenue, EPS, gross profit, operating income going back to 2009
- Why: covers more years than yfinance (which is spotty before 2015); standardized
- Integration: add `scripts/fetch_edgar_fundamentals.py` → stores to `edgar_fundamentals` table
- CIK lookup: `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=10-K`

### 4. SimFin — free tier (bulk download)
- URL: `https://simfin.com/api/v2/` (requires free API key)
- Provides: income statements, balance sheets, cash flows for 3,000+ US stocks
- Better coverage than yfinance for smaller-cap stocks
- Key add: earnings growth estimates (not available in yfinance historical data)

---

## Ticker expansion plan

| Phase | Tickers | Training samples | ETA |
|-------|---------|-----------------|-----|
| Current | TOP_50 (50) | 250 | Done |
| Phase 2 | SP500_EXTENDED (~250) | 1,250 | In progress (background fetch) |
| Phase 3 | Full S&P 500 (500) | 2,500 | After Phase 2 validates |
| Phase 4 | Russell 1000 (1,000) | 5,000+ | After Phase 3 |

To add the next batch: update `SP500_NEXT_200` in `src/ingestion/fundamentals.py`, then run `make fetch-snapshots-extended`.

---

## Year expansion plan

Current: 2020–2024 (5 years). Target: 2015–2024 (10 years).

Blockers:
- yfinance financial data is unreliable before 2015
- EDGAR XBRL has good coverage from 2009 onward → use for pre-2015 fundamentals
- Sector definitions changed around 2018 (Communication Services split from Tech/Consumer)

Priority: Add 2018, 2019 first (pre-COVID) → gives more bear market examples.

---

## FRED integration implementation plan

```python
# In src/ingestion/fundamentals.py::fetch_macro_regime(year)
# Replace binary flags with:
def _fetch_fred_year_snapshot(year: int) -> dict:
    """Fetch FRED macro indicators as of Jan 1 of year."""
    import requests
    from datetime import date

    date_str = f"{year}-01-01"
    base = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    def _fetch_series(series_id: str, obs_date: str) -> float | None:
        resp = requests.get(f"{base}?id={series_id}&vintage_date={obs_date}", timeout=10)
        # parse last value before obs_date
        ...

    return {
        "yield_curve_slope": _fetch_series("T10Y2Y", date_str),
        "fed_funds_rate": _fetch_series("FEDFUNDS", date_str),
        "hy_spread": _fetch_series("BAMLH0A0HYM2", date_str),
        "vix": _fetch_series("VIXCLS", date_str),
        "cpi_yoy": ...  # compute from CPIAUCSL
    }
```

These replace `macro_bull/bear/rate_rising/rate_falling` in `stock_features.py` with continuous values that capture degree, not just direction.

---

## Done when

Each data expansion phase is complete when:
1. All new (ticker, year) pairs are cached in `stock_snapshots` with label=1.0 or 0.0
2. `make results-json` shows the new row count reflects the expanded universe
3. The ML agent has run one `make iterate` cycle confirming improved Brier
