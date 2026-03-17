"""Ingest stock fundamentals from yfinance for the prediction engine.

Two purposes:
A) Historical returns corpus — synthetic events describing historical company
   profiles + outcomes for each of the top-50 S&P 500 constituents, covering
   5 years of annual snapshots.  These are the analogues Claude retrieves.
B) Current snapshot — live fundamental data for each ticker, used to form the
   forecast question and populate the {current_profile} prompt variable.

Data source: yfinance (Yahoo Finance API, no auth required).
Rate limiting: 0.5 s sleep between tickers to avoid 429s.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# S&P 500 ticker universe (tiered by market cap)
# ---------------------------------------------------------------------------

# Tier 1: Top-50 by market cap — original training set
TOP_50_SP500: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "LLY", "AVGO",
    "JPM", "V", "UNH", "XOM", "MA", "HD", "PG", "COST", "JNJ", "NFLX",
    "CRM", "BAC", "ABBV", "WMT", "MRK", "CVX", "ORCL", "AMD", "TMO", "ACN",
    "MCD", "GE", "NOW", "ISRG", "TXN", "QCOM", "IBM", "CAT", "AMGN", "INTU",
    "SPGI", "BKNG", "GS", "AXP", "BLK", "RTX", "T", "DHR", "NEE", "LOW",
]

# Tier 2: Tickers 51-250 by approximate market cap (large-cap S&P 500 members)
# Expanding corpus from 250 → 1,250 training samples (5 years × 250 tickers).
# Run `make fetch-snapshots ARGS="--tickers SP500_EXTENDED"` — or pass the
# expanded list directly: `make fetch-snapshots` after setting DEFAULT_TICKERS.
SP500_NEXT_200: list[str] = [
    # Large-cap tech / communication
    "PLTR", "CSCO", "TMUS", "VZ", "ANET", "MU", "PANW", "ADI", "AMAT", "KLAC",
    "LRCX", "SNPS", "CDNS", "ADSK", "MSI", "HPQ", "HPE", "NTAP",
    # Financials / insurance
    "MS", "WFC", "USB", "COF", "SCHW", "ICE", "CME", "MCO", "FISV", "AON",
    "MMC", "HIG", "CB", "AFL", "ALL", "TROW", "WTW", "PGR",
    # Healthcare / pharma / biotech
    "ABT", "GILD", "REGN", "VRTX", "MRNA", "BMY", "BIIB", "IDXX", "DXCM",
    "IQV", "EW", "SYK", "ZTS", "BDX", "GEHC", "HCA", "CI", "CVS", "ELV", "MCK",
    # Consumer / retail / food & bev
    "KO", "PEP", "PM", "MO", "MDLZ", "CL", "KMB", "SBUX", "NKE", "TJX",
    "COST",  # already in TOP_50, dedup handled by set logic
    "DHI", "LEN",
    # Industrials / defense / aero
    "HON", "ETN", "DE", "PH", "EMR", "ITW", "ROK", "MMM", "PCAR", "CTAS",
    "GWW", "FAST", "FTV", "ROP", "TT", "NSC", "UNP", "CSX", "ODFL", "FDX",
    "UPS", "JBHT", "XPO", "PWR", "LMT", "GD", "NOC", "TDG", "RTX",  # RTX already in TOP_50
    # Energy
    "COP", "SLB", "EOG", "OXY", "PSX", "MPC", "HAL", "EQT", "DVN",
    # Real estate / REITs
    "PLD", "SPG", "AMT", "CCI", "DLR", "IRM", "AVB", "O", "WELL",
    # Utilities
    "SO", "DUK", "SRE", "D", "EXC", "AEP", "XEL", "WEC", "CEG", "PCG", "VST",
    # Materials
    "SHW", "ECL", "NUE", "STLD", "FCX",
    # Miscellaneous large-cap
    "ADP", "VRSK", "FICO", "WM", "MCK", "OKE", "PYPL", "UBER",
]

# Full expanded universe: TOP_50 + NEXT_200, deduplicated
SP500_EXTENDED: list[str] = list(dict.fromkeys(TOP_50_SP500 + SP500_NEXT_200))

# S&P 500 benchmark ticker (yfinance)
_SP500_TICKER = "^GSPC"

# Number of historical years to include in the corpus
_HISTORY_YEARS = 5

# Sector median P/E ratios (2025 estimates — used for relative valuation)
_SECTOR_MEDIAN_PE = {
    "Technology": 28.0,
    "Healthcare": 22.0,
    "Financials": 13.0,
    "Consumer Discretionary": 25.0,
    "Consumer Staples": 22.0,
    "Industrials": 20.0,
    "Energy": 12.0,
    "Materials": 16.0,
    "Real Estate": 35.0,
    "Utilities": 18.0,
    "Communication Services": 20.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe(val, default=None):
    """Return val if it is not None/NaN, else default."""
    if val is None:
        return default
    try:
        import math
        if math.isnan(float(val)):
            return default
    except (TypeError, ValueError):
        pass
    return val


def _pct(val) -> str:
    """Format a float as +12.3% or N/A."""
    v = _safe(val)
    if v is None:
        return "N/A"
    return f"{float(v):+.1f}%"


def _fmt(val, fmt=".1f") -> str:
    """Format a numeric value or return 'N/A'."""
    v = _safe(val)
    if v is None:
        return "N/A"
    return format(float(v), fmt)


def _analyst_label(recommendation: str | None) -> str:
    """Normalise yfinance recommendation string to a clean label."""
    if not recommendation:
        return "neutral"
    r = str(recommendation).lower().strip()
    mapping = {
        "strong_buy": "Strong Buy",
        "strongbuy": "Strong Buy",
        "buy": "Buy",
        "outperform": "Buy",
        "overweight": "Buy",
        "hold": "Hold",
        "neutral": "Hold",
        "market perform": "Hold",
        "marketperform": "Hold",
        "underperform": "Sell",
        "sell": "Sell",
        "strong_sell": "Strong Sell",
        "strongsell": "Strong Sell",
        "underweight": "Sell",
    }
    return mapping.get(r, recommendation.title())


# ---------------------------------------------------------------------------
# Historical corpus builder
# ---------------------------------------------------------------------------


def _fetch_annual_returns(ticker_obj, sp500_obj, years: int) -> list[dict]:
    """Return a list of annual snapshot dicts for ticker vs S&P 500.

    Each dict has: year, stock_return, sp500_return, delta.
    We use January 1st close prices as year-start anchors.
    """
    import pandas as pd

    snapshots = []
    try:
        # Fetch 6 years of monthly data to cover 5 full annual return windows
        history = ticker_obj.history(period="6y", interval="1mo")
        sp_history = sp500_obj.history(period="6y", interval="1mo")

        if history.empty or sp_history.empty:
            return []

        # Resample to annual close (last trading close of each calendar year)
        annual = history["Close"].resample("YE").last()
        sp_annual = sp_history["Close"].resample("YE").last()

        # Align on common years
        common_years = annual.index.intersection(sp_annual.index)
        if len(common_years) < 2:
            return []

        annual = annual.loc[common_years]
        sp_annual = sp_annual.loc[common_years]

        # Compute year-over-year returns (year N price / year N-1 price - 1)
        for i in range(1, min(len(annual), years + 1)):
            prev_price = annual.iloc[i - 1]
            curr_price = annual.iloc[i]
            prev_sp = sp_annual.iloc[i - 1]
            curr_sp = sp_annual.iloc[i]

            if prev_price <= 0 or prev_sp <= 0:
                continue

            stock_ret = (curr_price / prev_price - 1) * 100
            sp_ret = (curr_sp / prev_sp - 1) * 100
            delta = stock_ret - sp_ret
            year = annual.index[i].year

            snapshots.append(
                {
                    "year": year,
                    "stock_return": round(stock_ret, 2),
                    "sp500_return": round(sp_ret, 2),
                    "delta": round(delta, 2),
                }
            )

    except Exception as exc:
        logger.debug("Error fetching annual returns: %s", exc)

    return snapshots


def _fetch_financials_for_year(ticker_obj, year: int) -> dict:
    """Extract revenue growth, gross margin for a given calendar year from financials.

    yfinance returns annual financials as a DataFrame indexed by fiscal year end date.
    We pick the row whose year matches and the one before it for growth.
    """
    result: dict = {"revenue_growth": None, "gross_margin": None}
    try:
        fin = ticker_obj.financials  # columns = fiscal year end dates, rows = metrics
        if fin is None or fin.empty:
            return result

        # Transpose so dates are index, metrics are columns
        fin = fin.T
        fin.index = fin.index.map(lambda d: d.year if hasattr(d, "year") else None)
        fin = fin[fin.index.notnull()]

        # Revenue keys vary
        rev_key = None
        for k in ("Total Revenue", "Revenue", "Net Revenue"):
            if k in fin.columns:
                rev_key = k
                break

        gross_key = None
        for k in ("Gross Profit", "Gross Income"):
            if k in fin.columns:
                gross_key = k
                break

        if rev_key and year in fin.index:
            rev_curr = _safe(fin.loc[year, rev_key])
            # Find closest prior year
            prior_years = [y for y in fin.index if y < year]
            if prior_years and rev_curr is not None:
                prior_year = max(prior_years)
                rev_prior = _safe(fin.loc[prior_year, rev_key])
                if rev_prior and rev_prior != 0:
                    result["revenue_growth"] = round(
                        (float(rev_curr) / float(rev_prior) - 1) * 100, 2
                    )

            if gross_key and rev_curr and float(rev_curr) != 0:
                gross = _safe(fin.loc[year, gross_key])
                if gross is not None:
                    result["gross_margin"] = round(
                        float(gross) / float(rev_curr) * 100, 2
                    )

    except Exception as exc:
        logger.debug("Error extracting financials for year %d: %s", year, exc)

    return result


def _fetch_momentum_for_year(ticker_obj, year: int) -> float | None:
    """Compute 12-1 month momentum as of the start of a given calendar year.

    Uses monthly closes: price at the start of (year - 12 months) divided by
    price at the start of (year - 1 month), minus 1.  Both anchored to Jan 1
    of the target year, looking back 13 months of monthly data.
    """
    try:
        import pandas as pd

        # Fetch 14 months of monthly data ending at Jan of `year`
        end = pd.Timestamp(f"{year}-02-01")
        start = pd.Timestamp(f"{year - 2}-01-01")
        history = ticker_obj.history(start=str(start.date()), end=str(end.date()), interval="1mo")
        if history.empty or len(history) < 13:
            return None

        # Keep only rows before Feb 1 of `year` (i.e., through Jan)
        history = history[history.index < end]
        if len(history) < 13:
            return None

        price_12m_ago = float(history["Close"].iloc[-13])  # ~12 months before target year start
        price_1m_ago = float(history["Close"].iloc[-2])    # ~1 month before target year start
        if price_1m_ago <= 0:
            return None
        return round((price_12m_ago / price_1m_ago - 1) * 100, 2)
    except Exception as exc:
        logger.debug("Error computing historical momentum for year %d: %s", year, exc)
        return None


def _fetch_pe_for_year(ticker_obj, year: int) -> float | None:
    """Approximate P/E at the start of year from historical price + EPS in financials."""
    try:
        # Try to get EPS from income statement
        fin = ticker_obj.financials
        if fin is None or fin.empty:
            return None
        fin = fin.T
        fin.index = fin.index.map(lambda d: d.year if hasattr(d, "year") else None)
        fin = fin[fin.index.notnull()]

        eps_key = None
        for k in ("Diluted EPS", "Basic EPS", "EPS"):
            if k in fin.columns:
                eps_key = k
                break

        if eps_key is None or year not in fin.index:
            return None

        eps = _safe(fin.loc[year, eps_key])
        if not eps or float(eps) <= 0:
            return None

        # Get price at start of that year
        history = ticker_obj.history(
            start=f"{year}-01-01",
            end=f"{year}-03-31",
            interval="1mo",
        )
        if history.empty:
            return None
        price = history["Close"].iloc[0]
        if price and float(eps) > 0:
            return round(float(price) / float(eps), 1)

    except Exception as exc:
        logger.debug("Error computing P/E for year %d: %s", year, exc)

    return None


def _build_corpus_event(
    ticker: str,
    company_name: str,
    year: int,
    stock_return: float,
    sp500_return: float,
    delta: float,
    revenue_growth: float | None,
    gross_margin: float | None,
    pe_ratio: float | None,
    analyst_consensus: str = "Hold",
    target_pct: float | None = None,
    momentum_12_1: float | None = None,
    pe_vs_sector_hist: float | None = None,
) -> dict:
    """Build a single corpus event dict in the standard schema."""
    outcome_label = (
        f"outperformed S&P 500 by {delta:+.1f}%"
        if delta >= 0
        else f"underperformed S&P 500 by {delta:+.1f}%"
    )
    rel_word = "outperformed" if delta >= 0 else "underperformed"

    rev_str = _pct(revenue_growth) if revenue_growth is not None else "N/A"
    gm_str = f"{gross_margin:.1f}%" if gross_margin is not None else "N/A"
    pe_str = f"{pe_ratio:.1f}x" if pe_ratio is not None else "N/A"
    tgt_str = _pct(target_pct) if target_pct is not None else "N/A"

    description = (
        f"In {year}, {company_name} ({ticker}) had revenue growth of {rev_str} YoY, "
        f"gross margin of {gm_str}, and traded at a P/E of {pe_str}. "
        f"Over the following 12 months, the stock {rel_word} the S&P 500 by "
        f"{delta:+.1f}% (stock: {stock_return:+.1f}%, S&P: {sp500_return:+.1f}%). "
        f"Analyst consensus was {analyst_consensus} with a mean price target "
        f"{tgt_str} from current price."
    )

    # Append momentum signal if available
    if momentum_12_1 is not None:
        mom_dir = "positive" if momentum_12_1 >= 0 else "negative"
        description += (
            f" 12-1 month price momentum was {momentum_12_1:+.1f}% ({mom_dir})."
        )

    # Append relative valuation signal if available
    if pe_vs_sector_hist is not None:
        val_label = "expensive" if pe_vs_sector_hist > 1.0 else "cheap"
        description += (
            f" P/E vs sector median was {pe_vs_sector_hist:.2f}x ({val_label} relative to sector)."
        )

    return {
        "id": f"fundamental-{ticker}-{year}",
        "description": description,
        "actors": [ticker, company_name],
        "event_type": "stock_performance",
        "outcome": outcome_label,
        "date": f"{year}-01-01",
        "region": "North America",
        "num_mentions": 1,
    }


# ---------------------------------------------------------------------------
# Momentum + earnings revision helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FRED macro regime helpers
# ---------------------------------------------------------------------------

# In-memory cache so all tickers for the same year share one FRED fetch
_FRED_CACHE: dict[int | None, dict] = {}

_FRED_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _fetch_fred_series(series_id: str, obs_date: str | None = None) -> list[tuple[str, float]]:
    """Fetch a FRED CSV series and return list of (date_str, value) tuples.

    If obs_date is given (YYYY-MM-DD), attempt the vintage_date parameter first;
    if that fails (some series don't support it), fall back to full-series fetch.
    Always returns the full list — caller filters by date.
    """
    import csv
    import io
    import requests

    def _parse_csv(text: str) -> list[tuple[str, float]]:
        rows = []
        reader = csv.reader(io.StringIO(text))
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < 2:
                continue
            date_s, val_s = row[0].strip(), row[1].strip()
            if val_s in (".", "", "NA"):
                continue
            try:
                rows.append((date_s, float(val_s)))
            except ValueError:
                continue
        return rows

    # Try vintage_date first (faster — smaller payload)
    if obs_date:
        try:
            url = f"{_FRED_BASE_URL}?id={series_id}&vintage_date={obs_date}"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200 and resp.text.strip():
                parsed = _parse_csv(resp.text)
                if parsed:
                    return parsed
        except Exception:
            pass

    # Fall back to full series
    url = f"{_FRED_BASE_URL}?id={series_id}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return _parse_csv(resp.text)


def _last_obs_on_or_before(rows: list[tuple[str, float]], cutoff: str) -> float | None:
    """Return the value of the last observation on or before cutoff date (YYYY-MM-DD)."""
    result = None
    for date_s, val in rows:
        if date_s <= cutoff:
            result = val
        else:
            break
    return result


def _fetch_fred_snapshot(year: int | None) -> dict:
    """Fetch FRED macro indicators as of Jan 1 of year (or current if year is None).

    Returns continuous signals plus legacy binary flags for backward compat.
    """
    import time as _time

    # Determine the observation cutoff date
    if year is None:
        from datetime import date as _date
        cutoff = str(_date.today())
        prior_cutoff = None  # used for fed_funds YoY comparison
        prior_year_cutoff = None
    else:
        cutoff = f"{year}-01-01"
        prior_cutoff = f"{year - 1}-01-01"  # 1 year ago for rate direction
        prior_year_cutoff = f"{year - 1}-01-01"

    result: dict = {
        "yield_curve_slope": None,
        "fed_funds_rate": None,
        "hy_spread": None,
        "vix": None,
        "cpi_yoy": None,
        "market_trend": "unknown",
        "rate_env": "unknown",
    }

    series_map = {
        "yield_curve_slope": "T10Y2Y",
        "fed_funds_rate": "FEDFUNDS",
        "hy_spread": "BAMLH0A0HYM2",
        "vix": "VIXCLS",
    }

    prior_fed_funds: float | None = None

    for field, series_id in series_map.items():
        try:
            rows = _fetch_fred_series(series_id, obs_date=cutoff)
            val = _last_obs_on_or_before(rows, cutoff)
            result[field] = round(val, 4) if val is not None else None

            # For fed_funds rate direction: also grab prior year value
            if field == "fed_funds_rate" and prior_cutoff and rows:
                pval = _last_obs_on_or_before(rows, prior_cutoff)
                prior_fed_funds = round(pval, 4) if pval is not None else None

            _time.sleep(0.5)
        except Exception as exc:
            logger.warning("FRED fetch failed for %s: %s", series_id, exc)

    # CPI YoY: need CPIAUCSL for 13 months ending at cutoff
    try:
        rows_cpi = _fetch_fred_series("CPIAUCSL", obs_date=cutoff)
        cpi_now = _last_obs_on_or_before(rows_cpi, cutoff)
        # 12 months prior
        if year is None:
            from datetime import date as _date, timedelta
            dt = _date.today()
            prior_dt = _date(dt.year - 1, dt.month, dt.day)
            cpi_prior_cutoff = str(prior_dt)
        else:
            cpi_prior_cutoff = f"{year - 1}-01-01"
        cpi_prior = _last_obs_on_or_before(rows_cpi, cpi_prior_cutoff)
        if cpi_now is not None and cpi_prior is not None and cpi_prior != 0:
            result["cpi_yoy"] = round((cpi_now / cpi_prior - 1) * 100, 2)
        _time.sleep(0.5)
    except Exception as exc:
        logger.warning("FRED fetch failed for CPIAUCSL: %s", exc)

    # Derive legacy binary flags from continuous values
    yc_slope = result["yield_curve_slope"]
    hy_val = result["hy_spread"]
    fed_val = result["fed_funds_rate"]

    # market_trend: bear if hy_spread > 4.5 OR yield_curve_slope < -0.3
    if hy_val is not None and yc_slope is not None:
        if hy_val > 4.5 or yc_slope < -0.3:
            result["market_trend"] = "bear"
        else:
            result["market_trend"] = "bull"
    elif hy_val is not None:
        result["market_trend"] = "bear" if hy_val > 4.5 else "bull"
    elif yc_slope is not None:
        result["market_trend"] = "bear" if yc_slope < -0.3 else "bull"

    # rate_env: compare current fed_funds to prior year
    if fed_val is not None and prior_fed_funds is not None:
        if fed_val > prior_fed_funds:
            result["rate_env"] = "rising"
        elif fed_val < prior_fed_funds:
            result["rate_env"] = "falling"
        else:
            result["rate_env"] = "stable"

    return result


def fetch_macro_regime(year: int | None = None) -> dict:
    """Return continuous FRED macro signals for a given year (or current).

    If year is None, fetches live current FRED values.
    If year is an int, fetches FRED values as of Jan 1 of that year.

    Returns dict with:
        yield_curve_slope : float | None  — T10Y2Y (e.g. -0.05)
        fed_funds_rate    : float | None  — FEDFUNDS (e.g. 4.33)
        hy_spread         : float | None  — BAMLH0A0HYM2 (e.g. 3.87)
        vix               : float | None  — VIXCLS (e.g. 23.1)
        cpi_yoy           : float | None  — CPIAUCSL YoY % (e.g. 8.2)
        market_trend      : "bull" | "bear" | "unknown"
        rate_env          : "rising" | "falling" | "stable" | "unknown"
    """
    global _FRED_CACHE
    if year in _FRED_CACHE:
        return _FRED_CACHE[year]

    data = _fetch_fred_snapshot(year)
    _FRED_CACHE[year] = data
    return data


def load_fred_macro_from_db(session) -> dict[int, dict]:
    """Load all fred_macro rows into a year → macro_dict lookup.

    Used at training/backtest time to inject FRED continuous signals into
    snapshot dicts before feature extraction, without re-fetching yfinance data.
    """
    from src.db.models import FredMacro
    rows = session.query(FredMacro).all()
    result = {}
    for row in rows:
        result[row.year] = {
            "yield_curve_slope": row.yield_curve_slope,
            "fed_funds_rate":    row.fed_funds_rate,
            "hy_spread":         row.hy_spread,
            "vix":               row.vix,
            "cpi_yoy":           row.cpi_yoy,
            "market_trend":      row.market_trend or "unknown",
            "rate_env":          row.rate_env or "unknown",
            "skew":              row.skew,
        }
    return result


def _fetch_momentum_12_1(ticker_obj) -> float | None:
    """Compute classic 12-1 month price momentum as a percentage.

    Returns (price_12_months_ago / price_1_month_ago) - 1, expressed as %.
    The most-recent month is excluded to avoid reversal contamination.
    """
    try:
        history = ticker_obj.history(period="13mo")
        if history.empty or len(history) < 23:
            return None
        # price roughly 12 months ago (index 0 is oldest in 13-month window)
        price_12m = float(history["Close"].iloc[0])
        # price roughly 1 month ago (approx 22 trading days from end)
        price_1m = float(history["Close"].iloc[-22])
        if price_1m <= 0:
            return None
        return round((price_12m / price_1m - 1) * 100, 2)
    except Exception as exc:
        logger.debug("Error computing momentum: %s", exc)
        return None


def _fetch_earnings_revision(ticker_obj) -> str:
    """Return "up", "down", or "neutral" based on recent analyst rating changes.

    Uses recommendations_summary if available; falls back to individual
    recommendations history filtered to the last 3 months.
    """
    try:
        summary = ticker_obj.recommendations_summary
        if summary is not None and not summary.empty:
            # recommendations_summary has columns: period, strongBuy, buy, hold, sell, strongSell
            # Use the most recent period (index 0)
            row = summary.iloc[0]
            upgrades = int(_safe(row.get("strongBuy"), 0) or 0) + int(_safe(row.get("buy"), 0) or 0)
            downgrades = int(_safe(row.get("sell"), 0) or 0) + int(_safe(row.get("strongSell"), 0) or 0)
            if upgrades > downgrades:
                return "up"
            if downgrades > upgrades:
                return "down"
            return "neutral"
    except Exception:
        pass

    # Fallback: scan recommendations history for last 3 months
    try:
        import pandas as pd
        recs = ticker_obj.recommendations
        if recs is None or recs.empty:
            return "neutral"
        cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=3)
        if recs.index.tz is None:
            recs.index = recs.index.tz_localize("UTC")
        recent = recs[recs.index >= cutoff]
        if recent.empty:
            return "neutral"

        grade_col = None
        for col in ("To Grade", "toGrade", "Action", "action"):
            if col in recent.columns:
                grade_col = col
                break
        if grade_col is None:
            return "neutral"

        up_words = {"buy", "strong buy", "outperform", "overweight", "upgrade"}
        down_words = {"sell", "strong sell", "underperform", "underweight", "downgrade"}
        upgrades = recent[grade_col].str.lower().isin(up_words).sum()
        downgrades = recent[grade_col].str.lower().isin(down_words).sum()
        if upgrades > downgrades:
            return "up"
        if downgrades > upgrades:
            return "down"
    except Exception as exc:
        logger.debug("Error computing earnings revision: %s", exc)

    return "neutral"


# ---------------------------------------------------------------------------
# Current snapshot
# ---------------------------------------------------------------------------


def get_current_snapshots(tickers: list[str]) -> list[dict]:
    """Return current fundamental data for each ticker for use in forecast questions.

    Each returned dict has:
        ticker, company_name, current_price, analyst_target_mean,
        analyst_target_high, analyst_target_low, analyst_recommendation,
        revenue_growth_ttm, gross_margin, pe_ratio, market_cap, sector,
        price_52w_high, price_52w_low, analyst_count
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError(
            "yfinance is required. Install it with: pip install yfinance"
        )

    snapshots: list[dict] = []

    # Fetch macro regime once — same for all tickers (current market conditions)
    current_macro_regime = fetch_macro_regime(year=None)

    for i, ticker in enumerate(tickers):
        logger.info("[%d/%d] Fetching current snapshot for %s", i + 1, len(tickers), ticker)
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}

            current_price = _safe(
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
            )

            analyst_target_mean = _safe(info.get("targetMeanPrice"))
            analyst_target_high = _safe(info.get("targetHighPrice"))
            analyst_target_low = _safe(info.get("targetLowPrice"))
            analyst_recommendation = _analyst_label(
                info.get("recommendationKey") or info.get("recommendation")
            )
            analyst_count = _safe(info.get("numberOfAnalystOpinions"), default=0)

            pe_ratio = _safe(
                info.get("trailingPE") or info.get("forwardPE")
            )
            market_cap = _safe(info.get("marketCap"))
            sector = info.get("sector", "Unknown")
            company_name = info.get("longName") or info.get("shortName") or ticker

            price_52w_high = _safe(info.get("fiftyTwoWeekHigh"))
            price_52w_low = _safe(info.get("fiftyTwoWeekLow"))

            # Revenue growth TTM: compare TTM revenue to prior year
            revenue_growth_ttm = None
            gross_margin = _safe(info.get("grossMargins"))
            if gross_margin is not None:
                gross_margin = round(float(gross_margin) * 100, 2)

            # Try to derive TTM revenue growth from financials
            try:
                fin = t.financials
                if fin is not None and not fin.empty:
                    fin = fin.T
                    rev_key = None
                    for k in ("Total Revenue", "Revenue", "Net Revenue"):
                        if k in fin.columns:
                            rev_key = k
                            break
                    if rev_key and len(fin) >= 2:
                        rev_curr = _safe(fin.iloc[0][rev_key])
                        rev_prior = _safe(fin.iloc[1][rev_key])
                        if rev_curr and rev_prior and float(rev_prior) != 0:
                            revenue_growth_ttm = round(
                                (float(rev_curr) / float(rev_prior) - 1) * 100, 2
                            )
            except Exception:
                pass

            # --- New signals ---

            # Price momentum (12-1 month)
            momentum_12_1 = _fetch_momentum_12_1(t)

            # Earnings revision trend
            earnings_revision = _fetch_earnings_revision(t)

            # Valuation vs sector: current P/E divided by sector median P/E
            pe_vs_sector: float | None = None
            if pe_ratio is not None and sector in _SECTOR_MEDIAN_PE:
                sector_median = _SECTOR_MEDIAN_PE[sector]
                if sector_median > 0:
                    pe_vs_sector = round(float(pe_ratio) / sector_median, 3)

            # Quality metrics
            roe = _safe(info.get("returnOnEquity"))
            if roe is not None:
                roe = round(float(roe) * 100, 2)  # convert to percentage

            debt_to_equity = _safe(info.get("debtToEquity"))

            # Short interest
            short_percent_float = _safe(info.get("shortPercentOfFloat"))

            # Beta and dividend yield (added in Iteration 7)
            beta = _safe(info.get("beta"))
            div_yield_raw = info.get("dividendYield")
            dividend_yield = round(float(div_yield_raw) * 100, 3) if div_yield_raw else None

            macro_regime = current_macro_regime

            snapshots.append(
                {
                    "ticker": ticker,
                    "company_name": company_name,
                    "current_price": current_price,
                    "analyst_target_mean": analyst_target_mean,
                    "analyst_target_high": analyst_target_high,
                    "analyst_target_low": analyst_target_low,
                    "analyst_recommendation": analyst_recommendation,
                    "revenue_growth_ttm": revenue_growth_ttm,
                    "gross_margin": gross_margin,
                    "pe_ratio": pe_ratio,
                    "market_cap": market_cap,
                    "sector": sector,
                    "price_52w_high": price_52w_high,
                    "price_52w_low": price_52w_low,
                    "analyst_count": int(analyst_count) if analyst_count else 0,
                    # New signals
                    "momentum_12_1": momentum_12_1,
                    "earnings_revision": earnings_revision,
                    "pe_vs_sector": pe_vs_sector,
                    "roe": roe,
                    "debt_to_equity": debt_to_equity,
                    "short_percent_float": short_percent_float,
                    "beta": beta,
                    "dividend_yield": dividend_yield,
                    "macro_regime": macro_regime,
                }
            )

        except Exception as exc:
            logger.warning("Failed to fetch snapshot for %s: %s", ticker, exc)
            snapshots.append(
                {
                    "ticker": ticker,
                    "company_name": ticker,
                    "current_price": None,
                    "analyst_target_mean": None,
                    "analyst_target_high": None,
                    "analyst_target_low": None,
                    "analyst_recommendation": "Hold",
                    "revenue_growth_ttm": None,
                    "gross_margin": None,
                    "pe_ratio": None,
                    "market_cap": None,
                    "sector": "Unknown",
                    "price_52w_high": None,
                    "price_52w_low": None,
                    "analyst_count": 0,
                    # New signals — all None on error
                    "momentum_12_1": None,
                    "earnings_revision": "neutral",
                    "beta": None,
                    "dividend_yield": None,
                    "pe_vs_sector": None,
                    "roe": None,
                    "debt_to_equity": None,
                    "short_percent_float": None,
                }
            )

        time.sleep(0.5)

    return snapshots


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_fundamentals_corpus(tickers: list[str] | None = None) -> list[dict]:
    """Build historical returns corpus for the given tickers.

    For each ticker, fetches up to _HISTORY_YEARS years of annual snapshots
    from yfinance and constructs narrative event dicts in the standard corpus
    schema ready for embed_and_store_events().

    Parameters
    ----------
    tickers:
        List of ticker symbols to include.  Defaults to TOP_50_SP500.

    Returns
    -------
    list[dict]
        Events with keys: id, description, actors, event_type, outcome,
        date, region, num_mentions.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError(
            "yfinance is required. Install it with: pip install yfinance"
        )

    if tickers is None:
        tickers = TOP_50_SP500

    events: list[dict] = []
    sp500 = yf.Ticker(_SP500_TICKER)

    for i, ticker in enumerate(tickers):
        logger.info(
            "[%d/%d] Building corpus for %s", i + 1, len(tickers), ticker
        )
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            company_name = (
                info.get("longName") or info.get("shortName") or ticker
            )

            # Fetch annual return snapshots
            annual_snapshots = _fetch_annual_returns(t, sp500, _HISTORY_YEARS)

            # Fetch info once for sector lookup
            sector = info.get("sector", "Unknown")

            for snap in annual_snapshots:
                year = snap["year"]

                # Fetch financial metrics for that year
                fin_data = _fetch_financials_for_year(t, year)
                pe = _fetch_pe_for_year(t, year)

                # Compute historical momentum (12-1 month) as of Jan 1 of that year
                momentum_12_1 = _fetch_momentum_for_year(t, year)

                # Compute P/E vs sector median for that year
                pe_vs_sector_hist: float | None = None
                if pe is not None and sector in _SECTOR_MEDIAN_PE:
                    sector_median = _SECTOR_MEDIAN_PE[sector]
                    if sector_median > 0:
                        pe_vs_sector_hist = round(float(pe) / sector_median, 3)

                event = _build_corpus_event(
                    ticker=ticker,
                    company_name=company_name,
                    year=year,
                    stock_return=snap["stock_return"],
                    sp500_return=snap["sp500_return"],
                    delta=snap["delta"],
                    revenue_growth=fin_data.get("revenue_growth"),
                    gross_margin=fin_data.get("gross_margin"),
                    pe_ratio=pe,
                    analyst_consensus="Hold",  # Historical consensus not available via yfinance
                    target_pct=None,
                    momentum_12_1=momentum_12_1,
                    pe_vs_sector_hist=pe_vs_sector_hist,
                )
                events.append(event)

        except Exception as exc:
            logger.warning("Failed to build corpus for %s: %s", ticker, exc)

        time.sleep(0.5)

    logger.info(
        "Fundamentals corpus: %d events for %d tickers",
        len(events),
        len(tickers),
    )
    return events
