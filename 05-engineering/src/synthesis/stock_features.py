"""Feature extraction from stock fundamental snapshots.

Converts a snapshot dict (as produced by fundamentals.get_current_snapshots or
backtest_stocks._build_historical_snapshot) into a fixed-length numeric vector
suitable for LightGBM training and inference.
"""
from __future__ import annotations

import math

STOCK_FEATURE_NAMES: list[str] = [
    "pe_ratio",              # trailing P/E, winsorized to [0, 100]
    "pe_vs_sector",          # P/E / sector median P/E  (1.0 = in-line with sector)
    "revenue_growth_ttm",    # TTM revenue growth %
    "gross_margin",          # gross margin %
    "momentum_12_1",         # 12-1 month price momentum %
    "macro_bull",            # 1.0 if bull market environment
    "macro_bear",            # 1.0 if bear market environment
    "macro_rate_rising",     # 1.0 if rising rate environment
    "macro_rate_falling",    # 1.0 if falling rate environment
    "earnings_rev_up",       # 1.0 if analyst revision trend is up
    "earnings_rev_down",     # 1.0 if analyst revision trend is down
    "roe",                   # return on equity %
    "debt_to_equity",        # debt-to-equity ratio
    "short_pct_float",       # short % of float
    "beta",                  # 5Y monthly beta (>1 = high-vol growth, <1 = defensive)
    "dividend_yield",        # annual dividend yield % (high = defensive/value)
    # NOTE: price_vs_52w_high and price_vs_52w_low removed in Iteration 3.
    # They dominated feature importance (85%+ gain) but caused regime-change
    # mispredictions (T 2022: prob=0.01, actual BEAT+23.8%). The model was
    # overfitting to momentum which doesn't transfer across bear/bull transitions.
    # Sector one-hot flags (enables sector-rotation learning)
    "sector_technology",
    "sector_healthcare",
    "sector_financials",
    "sector_consumer_disc",
    "sector_consumer_staples",
    "sector_industrials",
    "sector_energy",
    "sector_communication",
]

_WINSOR_PE_MAX = 100.0
_PRICE_VS_LOW_MAX = 3.0  # cap to reduce outlier dominance

# yfinance sector string → feature flag name
_SECTOR_FLAG_MAP: dict[str, str] = {
    "Technology": "sector_technology",
    "Healthcare": "sector_healthcare",
    "Health Care": "sector_healthcare",
    "Financial Services": "sector_financials",
    "Financials": "sector_financials",
    "Consumer Cyclical": "sector_consumer_disc",
    "Consumer Discretionary": "sector_consumer_disc",
    "Consumer Defensive": "sector_consumer_staples",
    "Consumer Staples": "sector_consumer_staples",
    "Industrials": "sector_industrials",
    "Energy": "sector_energy",
    "Communication Services": "sector_communication",
}


def _safe(val: object, default: float = 0.0) -> float:
    """Convert val to float; return default if None/NaN/inf."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def extract_stock_features(snapshot: dict) -> dict[str, float]:
    """Return a named feature dict from a stock snapshot.

    Missing values are filled with neutral defaults so the model ignores them
    rather than treating them as signal.
    """
    macro = snapshot.get("macro_regime") or {}

    # Valuation
    pe_raw = _safe(snapshot.get("pe_ratio"), default=0.0)
    pe_ratio = min(max(pe_raw, 0.0), _WINSOR_PE_MAX)
    pe_vs_sector = _safe(snapshot.get("pe_vs_sector"), default=1.0)

    # Growth
    revenue_growth = _safe(snapshot.get("revenue_growth_ttm"), default=0.0)
    gross_margin = _safe(snapshot.get("gross_margin"), default=0.0)

    # Momentum
    momentum = _safe(snapshot.get("momentum_12_1"), default=0.0)

    # Macro regime flags
    market_trend = macro.get("market_trend", "unknown")
    macro_bull = 1.0 if market_trend == "bull" else 0.0
    macro_bear = 1.0 if market_trend == "bear" else 0.0

    rate_env = macro.get("rate_env", "unknown")
    macro_rate_rising = 1.0 if rate_env == "rising" else 0.0
    macro_rate_falling = 1.0 if rate_env == "falling" else 0.0

    # Earnings revision
    earnings_rev = snapshot.get("earnings_revision", "neutral")
    earnings_rev_up = 1.0 if earnings_rev == "up" else 0.0
    earnings_rev_down = 1.0 if earnings_rev == "down" else 0.0

    # Quality
    roe = _safe(snapshot.get("roe"), default=0.0)
    debt_to_equity = _safe(snapshot.get("debt_to_equity"), default=0.0)
    short_pct = _safe(snapshot.get("short_percent_float"), default=0.0)
    beta = _safe(snapshot.get("beta"), default=1.0)  # 1.0 = market beta as neutral default
    div_yield = _safe(snapshot.get("dividend_yield"), default=0.0)

    # Sector one-hot flags
    sector = snapshot.get("sector") or "Unknown"
    sector_flag = _SECTOR_FLAG_MAP.get(sector, None)
    sector_features = {
        "sector_technology": 0.0,
        "sector_healthcare": 0.0,
        "sector_financials": 0.0,
        "sector_consumer_disc": 0.0,
        "sector_consumer_staples": 0.0,
        "sector_industrials": 0.0,
        "sector_energy": 0.0,
        "sector_communication": 0.0,
    }
    if sector_flag and sector_flag in sector_features:
        sector_features[sector_flag] = 1.0

    return {
        "pe_ratio": pe_ratio,
        "pe_vs_sector": pe_vs_sector,
        "revenue_growth_ttm": revenue_growth,
        "gross_margin": gross_margin,
        "momentum_12_1": momentum,
        "macro_bull": macro_bull,
        "macro_bear": macro_bear,
        "macro_rate_rising": macro_rate_rising,
        "macro_rate_falling": macro_rate_falling,
        "earnings_rev_up": earnings_rev_up,
        "earnings_rev_down": earnings_rev_down,
        "roe": roe,
        "debt_to_equity": debt_to_equity,
        "short_pct_float": short_pct,
        "beta": beta,
        "dividend_yield": div_yield,
        **sector_features,
    }


def features_to_vector(features: dict[str, float]) -> list[float]:
    """Convert named feature dict to ordered list matching STOCK_FEATURE_NAMES."""
    return [features.get(name, 0.0) for name in STOCK_FEATURE_NAMES]
