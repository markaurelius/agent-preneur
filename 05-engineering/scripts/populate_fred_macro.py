"""Fetch FRED macro indicators for each training year and store in fred_macro table.

Runs 8 network calls (one per year, 2018–2025), each call fetching 5 FRED series.
Also populates CBOE SKEW index (via yfinance ^SKEW, with hardcoded fallback).
Safe to re-run: for existing rows, updates the skew column if it is NULL.

Usage:
    docker compose run --rm engine python scripts/populate_fred_macro.py
    # or via Makefile:
    make populate-fred
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from src.ingestion.fundamentals import fetch_macro_regime
from src.db.session import get_session
from src.db.models import FredMacro

YEARS = list(range(2018, 2026))  # 2018–2025 inclusive

# Hardcoded CBOE SKEW fallback values (derived from historical CBOE SKEW data, Jan 1 of year).
# SKEW measures tail risk: cost of OTM puts vs calls.
# High SKEW = sophisticated investors hedging downside. Normal range ~115–150.
_SKEW_FALLBACK: dict[int, float] = {
    2015: 127.0,
    2016: 129.0,
    2017: 132.0,
    2018: 133.0,  # elevated — smart money hedging after long bull run
    2019: 130.0,
    2020: 131.0,
    2021: 147.0,  # very elevated — options market pricing significant tail risk
    2022: 140.0,  # still elevated Jan 1 2022, before rate-hike crash
    2023: 123.0,  # normalized post-crash
    2024: 130.0,
    2025: 132.0,
}


def _fetch_skew_annual(year: int) -> float:
    """Fetch CBOE SKEW index value near Jan 1 of year.

    Tries yfinance ^SKEW first; falls back to hardcoded historical values.
    Returns the first valid trading day close in the first 10 days of the year.
    """
    fallback = _SKEW_FALLBACK.get(year, 130.0)
    try:
        import yfinance as yf
        df = yf.download(
            "^SKEW",
            start=f"{year}-01-01",
            end=f"{year}-01-15",
            progress=False,
            auto_adjust=True,
        )
        if df is not None and not df.empty:
            close_col = "Close"
            if hasattr(df.columns, "get_level_values"):
                # MultiIndex columns from yfinance ≥ 0.2.x
                try:
                    val = float(df[close_col].iloc[0])
                    if val > 50:  # sanity: SKEW is always > 100 in practice
                        log.info("  %d: SKEW fetched from yfinance: %.1f", year, val)
                        return val
                except Exception:
                    pass
            # Flat columns
            if close_col in df.columns:
                val = float(df[close_col].iloc[0])
                if val > 50:
                    log.info("  %d: SKEW fetched from yfinance: %.1f", year, val)
                    return val
    except Exception as exc:
        log.debug("  %d: yfinance ^SKEW fetch failed (%s), using fallback", year, exc)

    log.info("  %d: SKEW using hardcoded fallback: %.1f", year, fallback)
    return fallback


def main() -> None:
    with get_session() as session:
        for year in YEARS:
            existing = session.query(FredMacro).filter_by(year=year).first()
            if existing:
                # Row already exists — update skew if it is NULL
                if existing.skew is None:
                    skew_val = _fetch_skew_annual(year)
                    existing.skew = skew_val
                    session.commit()
                    log.info(
                        "  %d: already cached, backfilled skew=%.1f",
                        year, skew_val,
                    )
                else:
                    log.info(
                        "  %d: already cached (curve=%.2f  fed=%.2f  hy=%.2f  "
                        "vix=%.1f  cpi=%.1f%%  trend=%s  rate=%s  skew=%.1f), skipping",
                        year,
                        existing.yield_curve_slope or 0,
                        existing.fed_funds_rate or 0,
                        existing.hy_spread or 0,
                        existing.vix or 0,
                        existing.cpi_yoy or 0,
                        existing.market_trend or "?",
                        existing.rate_env or "?",
                        existing.skew,
                    )
                continue

            log.info("  Fetching FRED macro for %d ...", year)
            macro = fetch_macro_regime(year)
            skew_val = _fetch_skew_annual(year)

            row = FredMacro(
                year=year,
                yield_curve_slope=macro.get("yield_curve_slope"),
                fed_funds_rate=macro.get("fed_funds_rate"),
                hy_spread=macro.get("hy_spread"),
                vix=macro.get("vix"),
                cpi_yoy=macro.get("cpi_yoy"),
                market_trend=macro.get("market_trend"),
                rate_env=macro.get("rate_env"),
                skew=skew_val,
            )
            session.add(row)
            session.commit()

            yc = macro.get("yield_curve_slope")
            ff = macro.get("fed_funds_rate")
            hy = macro.get("hy_spread")
            vx = macro.get("vix")
            cp = macro.get("cpi_yoy")
            log.info(
                "  %d: curve=%s  fed=%s  hy=%s  vix=%s  cpi=%s%%  "
                "trend=%s  rate=%s  skew=%.1f",
                year,
                f"{yc:.2f}" if yc is not None else "None",
                f"{ff:.2f}" if ff is not None else "None",
                f"{hy:.2f}" if hy is not None else "None",
                f"{vx:.1f}" if vx is not None else "None",
                f"{cp:.1f}" if cp is not None else "None",
                macro.get("market_trend", "?"),
                macro.get("rate_env", "?"),
                skew_val,
            )

    log.info("Done. fred_macro table populated for years %s.", YEARS)


if __name__ == "__main__":
    main()
