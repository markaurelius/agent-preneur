"""Bulk snapshot fetcher for the full S&P 500 extended universe.

Key optimization over fetch_snapshots.py:
- Uses yf.download() to fetch ALL price history in ONE network call
  (instead of one call per ticker per year window)
- Caches the raw price DataFrame in memory; derives all per-ticker/year
  features (annual return, momentum, 52-week range) from the cached data
- Only makes individual yf.Ticker() calls for fundamentals (financials,
  sector info) which cannot be batched — and does these concurrently

Result: ~10× faster than the sequential approach for 250+ tickers.

Usage:
    python scripts/fetch_snapshots_extended.py
    python scripts/fetch_snapshots_extended.py --years 2021,2022,2023,2024
    python scripts/fetch_snapshots_extended.py --tickers AAPL,MSFT,NVDA
    python scripts/fetch_snapshots_extended.py --refresh  # re-fetch even if cached
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_DEFAULT_YEARS = [2020, 2021, 2022, 2023, 2024]
_SECTOR_MEDIAN_PE = {
    "Technology": 28.0,
    "Healthcare": 22.0,
    "Health Care": 22.0,
    "Financial Services": 13.0,
    "Financials": 13.0,
    "Consumer Cyclical": 25.0,
    "Consumer Discretionary": 25.0,
    "Consumer Defensive": 22.0,
    "Consumer Staples": 22.0,
    "Industrials": 20.0,
    "Energy": 12.0,
    "Materials": 16.0,
    "Real Estate": 35.0,
    "Utilities": 18.0,
    "Communication Services": 20.0,
}


def _safe(val, default=None):
    if val is None:
        return default
    try:
        import math
        if math.isnan(float(val)):
            return default
    except (TypeError, ValueError):
        pass
    return val


def _normalize_index(series):
    """Return a tz-naive copy of a Series index for date comparisons."""
    import pandas as pd
    s = series.copy()
    if s.index.tz is not None:
        s.index = s.index.tz_convert("UTC").tz_localize(None)
    return s


def _get_close_series(price_df, ticker: str):
    """Safely extract Close price series for ticker from a multi-ticker DataFrame."""
    try:
        # yf.download multi-ticker: MultiIndex columns (field, ticker)
        return _normalize_index(price_df["Close"][ticker].dropna())
    except Exception:
        return None


def _price_at_year_start(price_df, ticker: str, year: int) -> float | None:
    """Return first available close price on or after Jan 1 of year."""
    try:
        import pandas as pd
        col = _get_close_series(price_df, ticker)
        if col is None or col.empty:
            return None
        jan1 = pd.Timestamp(f"{year}-01-01")
        after = col[col.index >= jan1]
        return float(after.iloc[0]) if not after.empty else None
    except Exception:
        return None


def _annual_return_from_prices(price_df, ticker: str, year: int) -> float | None:
    """Compute annual return for ticker in year from bulk price DataFrame."""
    p_start = _price_at_year_start(price_df, ticker, year)
    p_end = _price_at_year_start(price_df, ticker, year + 1)
    if p_start is None or p_end is None or p_start <= 0:
        return None
    return (p_end / p_start) - 1


def _52w_range_from_prices(price_df, ticker: str, year: int) -> tuple[float | None, float | None]:
    """Return (high_52w, low_52w) over the 12 months prior to Jan 1 of year."""
    try:
        import pandas as pd
        col_high = _normalize_index(price_df["High"][ticker].dropna())
        col_low = _normalize_index(price_df["Low"][ticker].dropna())
        start = pd.Timestamp(f"{year - 1}-01-01")
        end = pd.Timestamp(f"{year}-01-15")
        window_h = col_high[(col_high.index >= start) & (col_high.index <= end)]
        window_l = col_low[(col_low.index >= start) & (col_low.index <= end)]
        if window_h.empty or window_l.empty:
            return None, None
        return float(window_h.max()), float(window_l.min())
    except Exception:
        return None, None


def _momentum_from_prices(price_df, ticker: str, year: int) -> float | None:
    """Compute 12-1 month momentum as of Jan 1 of year from bulk price DataFrame."""
    try:
        import pandas as pd
        col = _get_close_series(price_df, ticker)
        if col is None or col.empty:
            return None
        # 12 months ago relative to Jan 1 of year (tz-naive)
        ref_date = pd.Timestamp(f"{year}-01-01")
        date_12m = ref_date - pd.DateOffset(months=12)
        date_1m = ref_date - pd.DateOffset(months=1)
        # Find closest prices
        after_12m = col[col.index >= date_12m]
        after_1m = col[col.index >= date_1m]
        if after_12m.empty or after_1m.empty:
            return None
        p_12m = float(after_12m.iloc[0])
        p_1m = float(after_1m.iloc[0])
        if p_1m <= 0:
            return None
        return round((p_12m / p_1m - 1) * 100, 2)
    except Exception:
        return None


def _fetch_ticker_fundamentals(ticker: str, years: list[int]) -> dict:
    """Fetch per-ticker fundamentals (financials, sector, info).

    Returns dict with keys: info, financials_by_year
    where financials_by_year[year] = {revenue_growth, gross_margin, pe_ratio, pe_vs_sector}
    """
    import yfinance as yf
    result = {"info": {}, "financials_by_year": {}}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        result["info"] = info
        sector = info.get("sector", "Unknown")

        # Load financials once
        fin = None
        try:
            fin = t.financials
        except Exception:
            pass

        for year in years:
            data: dict = {
                "revenue_growth": None,
                "gross_margin": None,
                "pe_ratio": None,
                "pe_vs_sector": None,
                "roe": None,
                "debt_to_equity": None,
                "short_pct_float": None,
                "earnings_revision": "neutral",
            }

            if fin is not None and not fin.empty:
                try:
                    fin_t = fin.T
                    fin_t.index = fin_t.index.map(lambda d: d.year if hasattr(d, "year") else None)
                    fin_t = fin_t[fin_t.index.notnull()]

                    rev_key = next((k for k in ("Total Revenue", "Revenue", "Net Revenue") if k in fin_t.columns), None)
                    gross_key = next((k for k in ("Gross Profit", "Gross Income") if k in fin_t.columns), None)
                    eps_key = next((k for k in ("Diluted EPS", "Basic EPS", "EPS") if k in fin_t.columns), None)

                    target_year = year - 1
                    prior_year = year - 2
                    if rev_key and target_year in fin_t.index and prior_year in fin_t.index:
                        rev_curr = _safe(fin_t.loc[target_year, rev_key])
                        rev_prior = _safe(fin_t.loc[prior_year, rev_key])
                        if rev_curr and rev_prior and float(rev_prior) != 0:
                            data["revenue_growth"] = round(
                                (float(rev_curr) / float(rev_prior) - 1) * 100, 2
                            )
                        if gross_key and rev_curr and float(rev_curr) != 0:
                            gross = _safe(fin_t.loc[target_year, gross_key]) if target_year in fin_t.index else None
                            if gross is not None:
                                data["gross_margin"] = round(float(gross) / float(rev_curr) * 100, 2)

                    if eps_key and (year - 1) in fin_t.index:
                        eps = _safe(fin_t.loc[year - 1, eps_key])
                        # P/E will be computed with historical price below
                        data["_eps"] = float(eps) if eps and float(eps) > 0 else None
                    else:
                        data["_eps"] = None

                except Exception:
                    pass

            # Quality metrics from info (current, used as proxy for historical)
            roe = _safe(info.get("returnOnEquity"))
            if roe is not None:
                data["roe"] = round(float(roe) * 100, 2)
            data["debt_to_equity"] = _safe(info.get("debtToEquity"))
            short_pct = _safe(info.get("shortPercentOfFloat"))
            data["short_pct_float"] = short_pct
            data["beta"] = _safe(info.get("beta"))  # 5Y monthly beta (low=defensive, high=growth)
            div_yield = info.get("dividendYield")
            data["dividend_yield"] = round(float(div_yield) * 100, 3) if div_yield else None
            data["sector"] = sector

            result["fundamentals_by_year"] = result.get("fundamentals_by_year", {})
            result["fundamentals_by_year"][year] = data

        # Brief pause to respect rate limits
        time.sleep(0.2)
    except Exception as exc:
        logger.warning("Fundamentals fetch error for %s: %s", ticker, exc)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-fetch historical stock snapshots using yf.download() for speed"
    )
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers (default: SP500_EXTENDED)")
    parser.add_argument("--years", default=",".join(str(y) for y in _DEFAULT_YEARS))
    parser.add_argument("--refresh", action="store_true", help="Re-fetch and overwrite existing")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers for fundamentals fetch")
    args = parser.parse_args()

    import yfinance as yf

    from src.db.models import StockSnapshot
    from src.db.session import get_session
    from src.ingestion.fundamentals import SP500_EXTENDED, TOP_50_SP500
    from src.synthesis.stock_features import extract_stock_features
    from src.ingestion.fundamentals import fetch_macro_regime

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else SP500_EXTENDED
    )
    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]

    logger.info("Universe: %d tickers × %d years = up to %d snapshots", len(tickers), len(years), len(tickers) * len(years))

    # -----------------------------------------------------------------------
    # Step 1: Determine which (ticker, year) pairs still need fetching
    # -----------------------------------------------------------------------
    with get_session() as session:
        if not args.refresh:
            existing_ids = set(
                row.id for row in session.query(StockSnapshot.id).all()
            )
        else:
            existing_ids = set()

    needed = [
        (t, y) for t in tickers for y in years
        if f"snapshot-{t}-{y}" not in existing_ids
    ]
    if not needed:
        logger.info("All snapshots already cached. Use --refresh to re-fetch.")
        return
    logger.info("%d snapshots to fetch (%d already cached)", len(needed), len(tickers) * len(years) - len(needed))

    needed_tickers = list(dict.fromkeys(t for t, _ in needed))

    # -----------------------------------------------------------------------
    # Step 2: Bulk price download — ALL tickers, ALL years in ONE call
    # -----------------------------------------------------------------------
    fetch_start_year = min(years) - 2  # need 2 prior years for momentum
    fetch_end_year = max(years) + 1   # need year+1 start price for annual return

    # Always include SPY in the bulk download for benchmark returns
    download_tickers = list(dict.fromkeys(["SPY"] + needed_tickers))

    logger.info(
        "Bulk price download: %d tickers (incl. SPY benchmark), %d-%d … (one network call)",
        len(download_tickers), fetch_start_year, fetch_end_year,
    )
    price_df = yf.download(
        tickers=download_tickers,
        start=f"{fetch_start_year}-01-01",
        end=f"{fetch_end_year + 1}-01-01",
        interval="1d",
        auto_adjust=True,
        progress=True,
    )
    logger.info("Bulk price download complete. Shape: %s", price_df.shape)

    # SPY benchmark returns from the same bulk DataFrame
    import pandas as pd
    spy_returns: dict[int, float | None] = {}
    for yr in years:
        spy_returns[yr] = _annual_return_from_prices(price_df, "SPY", yr)
        logger.info("  SPY %d: %s", yr, f"{spy_returns[yr]:+.1%}" if spy_returns[yr] is not None else "N/A")

    # Normalize MultiIndex for lookup
    if hasattr(price_df.columns, "levels"):
        # MultiIndex: (metric, ticker) or (ticker, metric)
        level_vals = [set(price_df.columns.get_level_values(i)) for i in range(price_df.columns.nlevels)]
        logger.debug("Price DataFrame column levels: %s", [sorted(lv)[:5] for lv in level_vals])

    # -----------------------------------------------------------------------
    # Step 3: Fetch fundamentals in parallel (still per-ticker, but concurrent)
    # -----------------------------------------------------------------------
    logger.info("Fetching fundamentals for %d tickers (workers=%d) …", len(needed_tickers), args.workers)
    fundamentals: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_fetch_ticker_fundamentals, t, years): t for t in needed_tickers}
        for i, future in enumerate(as_completed(futures)):
            ticker = futures[future]
            try:
                fundamentals[ticker] = future.result()
            except Exception as exc:
                logger.warning("Failed fundamentals for %s: %s", ticker, exc)
                fundamentals[ticker] = {"info": {}, "fundamentals_by_year": {}}
            if (i + 1) % 50 == 0:
                logger.info("  Fundamentals: %d/%d tickers done", i + 1, len(needed_tickers))

    # Pre-fetch macro regimes
    macro_by_year = {yr: fetch_macro_regime(yr) for yr in years}

    # -----------------------------------------------------------------------
    # Step 4: Build and persist snapshots
    # -----------------------------------------------------------------------
    fetched = skipped = errors = 0

    with get_session() as session:
        for ticker in needed_tickers:
            fund = fundamentals.get(ticker, {})
            info = fund.get("info", {})
            company_name = info.get("longName") or info.get("shortName") or ticker
            sector = info.get("sector", "Unknown")

            for year in years:
                snap_id = f"snapshot-{ticker}-{year}"
                if snap_id in existing_ids and not args.refresh:
                    skipped += 1
                    continue

                spy_return = spy_returns.get(year)
                stock_return = _annual_return_from_prices(price_df, ticker, year)

                if stock_return is None or spy_return is None:
                    errors += 1
                    continue

                label = 1.0 if stock_return > spy_return else 0.0

                # Price-derived features
                current_price = _price_at_year_start(price_df, ticker, year)
                high_52w, low_52w = _52w_range_from_prices(price_df, ticker, year)
                momentum = _momentum_from_prices(price_df, ticker, year)

                # Fundamentals for this year
                fd = (fund.get("fundamentals_by_year") or {}).get(year, {})
                eps = fd.get("_eps")
                pe_ratio = None
                if eps and current_price:
                    pe_ratio = round(float(current_price) / float(eps), 1)
                pe_vs_sector = None
                if pe_ratio and sector in _SECTOR_MEDIAN_PE:
                    pe_vs_sector = round(pe_ratio / _SECTOR_MEDIAN_PE[sector], 3)

                snap = {
                    "ticker": ticker,
                    "company_name": company_name,
                    "current_price": current_price,
                    "analyst_target_mean": None,
                    "analyst_target_high": None,
                    "analyst_target_low": None,
                    "analyst_recommendation": "Hold",
                    "revenue_growth_ttm": fd.get("revenue_growth"),
                    "gross_margin": fd.get("gross_margin"),
                    "pe_ratio": pe_ratio,
                    "market_cap": None,
                    "sector": sector,
                    "price_52w_high": high_52w,
                    "price_52w_low": low_52w,
                    "analyst_count": 0,
                    "momentum_12_1": momentum,
                    "earnings_revision": "neutral",
                    "pe_vs_sector": pe_vs_sector,
                    "roe": fd.get("roe"),
                    "debt_to_equity": fd.get("debt_to_equity"),
                    "short_percent_float": fd.get("short_pct_float"),
                    "beta": fd.get("beta"),
                    "dividend_yield": fd.get("dividend_yield"),
                    "macro_regime": macro_by_year.get(year, {}),
                }

                features = extract_stock_features(snap)

                if args.refresh:
                    session.query(StockSnapshot).filter_by(id=snap_id).delete()

                row = StockSnapshot(
                    id=snap_id,
                    ticker=ticker,
                    year=year,
                    snapshot_json=snap,
                    features_json=features,
                    label=label,
                    stock_return=round(stock_return * 100, 4),
                    spy_return=round(spy_return * 100, 4),
                    fetched_at=datetime.now(timezone.utc),
                )
                session.add(row)
                fetched += 1

            session.commit()

    logger.info("")
    logger.info("=== fetch_snapshots_extended complete ===")
    logger.info("  Fetched (new)  : %d", fetched)
    logger.info("  Already cached : %d", skipped)
    logger.info("  Errors/missing : %d", errors)
    logger.info("  Total in cache : ~%d", fetched + skipped)
    logger.info("")
    logger.info("Run 'make iterate' to train on the expanded dataset.")


if __name__ == "__main__":
    main()
