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
    # NOTE: macro_bull, macro_bear, macro_rate_rising, macro_rate_falling removed in Iteration 13.
    # All four had near-zero gain (≤50) in Iter 11/12 final model — beta_x_spread and divy_x_rate
    # now encode macro regime via interactions with stock-level features, making raw binary flags
    # redundant noise. Feature count: 28 → 24.
    # NOTE: earnings_rev_up, earnings_rev_down removed in Iteration 14 (0 gain each).
    # earnings_revision field in snapshots is nearly always "neutral" across the corpus;
    # the signal is absent, not just weak. Feature count: 24 → 21.
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
    # NOTE: sector_consumer_disc removed in Iteration 14 (0 gain). Consumer Discretionary tickers
    # in the TOP_50 corpus are too few and return too varied to provide a reliable sector signal.
    "sector_consumer_staples",
    "sector_industrials",
    "sector_energy",
    "sector_communication",
    # FRED interaction terms (Iteration 11)
    # Raw FRED values are year-level constants that LightGBM can't split on within a fold.
    # These interactions multiply a FRED year value by a stock-level feature, giving each
    # stock a different value within the same year → within-year split surface for LightGBM.
    "pe_x_rate",             # pe_ratio × fed_funds_rate  (multiple compression: high-PE hurt by rising rates)
    "energy_x_cpi",          # sector_energy × cpi_yoy   (commodity pass-through: energy outperforms when CPI high)
    "beta_x_spread",         # beta × hy_spread           (credit amplification: high-beta hurt more in risk-off)
    "divy_x_rate",           # dividend_yield × fed_funds_rate (yield competition: divvy stocks face bond competition)
    # Momentum decomposition (Iteration 18b)
    # momentum_3_1 stored in every snapshot from corpus re-fetch.
    # Sign convention: negative = stock rose, positive = stock fell (matches momentum_12_1 convention).
    "momentum_3_1",          # 3-month price momentum (short-term trend)
    "momentum_decel",        # 12M − 3M momentum: captures rolling-over stocks (peaked mid-year)
                             # e.g. NVDA 2022: 12M=−58.3%, 3M=−31.1% → decel=−27.2pp
                             # e.g. XOM  2022: 12M=−34.6%, 3M=−5.4%  → decel=−29.2pp
    # CBOE SKEW interaction (Iteration 23)
    # SKEW measures tail risk (OTM put vs call pricing). Normalized: (SKEW − 130) / 10
    # so 130=neutral(0), 140=elevated(+1), 120=subdued(−1).
    # beta_x_skew = beta × skew_norm: high-beta stock in elevated-SKEW env = bearish signal.
    # 2021 SKEW=147 (+1.7), 2022 SKEW=140 (+1.0) → high-beta names penalized going into crash.
    # 2023 SKEW=123 (−0.7) → recovery signal: fewer options-market tail-risk concerns.
    "beta_x_skew",           # beta × (SKEW − 130) / 10
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
    momentum_3 = _safe(snapshot.get("momentum_3_1"), default=0.0)
    # Deceleration: 12M − 3M (both in inverted sign convention)
    # Rolling-over stock: strong 12M momentum but weaker/reversing 3M → large negative decel
    momentum_decel = momentum - momentum_3

    # Quality
    roe = _safe(snapshot.get("roe"), default=0.0)
    debt_to_equity = _safe(snapshot.get("debt_to_equity"), default=0.0)
    short_pct = _safe(snapshot.get("short_percent_float"), default=0.0)
    beta = _safe(snapshot.get("beta"), default=1.0)  # 1.0 = market beta as neutral default
    div_yield = _safe(snapshot.get("dividend_yield"), default=0.0)

    # FRED macro values — injected by train_stocks.py / backtest_stocks.py via fred_macro table.
    # NOT added as standalone features (year-level constants have zero within-year variance for LightGBM).
    # Used only to compute interaction terms below.
    fed_funds_rate = _safe(macro.get("fed_funds_rate"), default=2.5)          # 2.5 = historical avg
    hy_spread      = _safe(macro.get("hy_spread"),      default=4.0)          # 4.0 = long-run avg
    cpi_yoy        = _safe(macro.get("cpi_yoy"),        default=2.5)          # 2.5 = Fed target
    skew           = _safe(macro.get("skew"),           default=130.0)        # 130 = roughly neutral SKEW

    # Sector one-hot flags
    sector = snapshot.get("sector") or "Unknown"
    sector_flag = _SECTOR_FLAG_MAP.get(sector, None)
    sector_features = {
        "sector_technology": 0.0,
        "sector_healthcare": 0.0,
        "sector_financials": 0.0,
        "sector_consumer_staples": 0.0,
        "sector_industrials": 0.0,
        "sector_energy": 0.0,
        "sector_communication": 0.0,
    }
    if sector_flag and sector_flag in sector_features:
        sector_features[sector_flag] = 1.0

    # Interaction features (Iteration 11)
    # Each term = FRED year-level value × stock-level feature → unique value per stock per year.
    # energy_x_cpi must be computed after sector_features to access sector_energy.
    pe_x_rate     = pe_ratio                           * fed_funds_rate  # multiple compression: high-PE stocks hurt by rising rates
    energy_x_cpi  = sector_features["sector_energy"]  * cpi_yoy          # commodity pass-through: energy outperforms when CPI is high
    beta_x_spread = beta      * hy_spread         # credit amplification: high-beta names hurt more in risk-off
    divy_x_rate   = div_yield * fed_funds_rate   # yield competition: dividend stocks face bond competition in high-rate env

    # CBOE SKEW interaction (Iteration 23)
    # Normalize SKEW: (raw - 130) / 10 → 0 at neutral, +1 at elevated (140), −1 at subdued (120)
    skew_norm  = (skew - 130.0) / 10.0
    beta_x_skew = beta * skew_norm  # high-beta stock × elevated SKEW = bearish

    return {
        "pe_ratio": pe_ratio,
        "pe_vs_sector": pe_vs_sector,
        "revenue_growth_ttm": revenue_growth,
        "gross_margin": gross_margin,
        "momentum_12_1": momentum,
        "roe": roe,
        "debt_to_equity": debt_to_equity,
        "short_pct_float": short_pct,
        "beta": beta,
        "dividend_yield": div_yield,
        **sector_features,
        "pe_x_rate":      pe_x_rate,
        "energy_x_cpi":   energy_x_cpi,
        "beta_x_spread":  beta_x_spread,
        "divy_x_rate":    divy_x_rate,
        "momentum_3_1":   momentum_3,
        "momentum_decel": momentum_decel,
        "beta_x_skew":    beta_x_skew,
    }


def features_to_vector(features: dict[str, float]) -> list[float]:
    """Convert named feature dict to ordered list matching STOCK_FEATURE_NAMES."""
    return [features.get(name, 0.0) for name in STOCK_FEATURE_NAMES]
