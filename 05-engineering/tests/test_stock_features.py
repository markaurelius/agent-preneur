"""Tests for stock feature extraction (stock_features.py).

All tests are pure Python — no DB, no network, no model.
"""
import math

import pytest

from src.synthesis.stock_features import (
    STOCK_FEATURE_NAMES,
    _safe,
    extract_stock_features,
    features_to_vector,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_SNAPSHOT = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "current_price": 150.0,
    "pe_ratio": 25.0,
    "pe_vs_sector": 0.9,
    "revenue_growth_ttm": 12.5,
    "gross_margin": 45.0,
    "momentum_12_1": 8.3,
    "earnings_revision": "up",
    "roe": 18.0,
    "debt_to_equity": 0.5,
    "short_percent_float": 0.02,
    "price_52w_high": 180.0,
    "price_52w_low": 120.0,
    "macro_regime": {"market_trend": "bull", "rate_env": "rising"},
}

EMPTY_SNAPSHOT: dict = {}


# ---------------------------------------------------------------------------
# _safe helper
# ---------------------------------------------------------------------------


class TestSafeHelper:
    def test_none_returns_default(self):
        assert _safe(None, default=5.0) == 5.0

    def test_nan_returns_default(self):
        assert _safe(float("nan"), default=99.0) == 99.0

    def test_inf_returns_default(self):
        assert _safe(float("inf"), default=0.0) == 0.0

    def test_valid_float_returned(self):
        assert _safe(3.14) == 3.14

    def test_string_int_coerced(self):
        assert _safe("42") == 42.0

    def test_non_numeric_string_returns_default(self):
        assert _safe("abc", default=-1.0) == -1.0

    def test_zero_is_valid(self):
        assert _safe(0.0, default=1.0) == 0.0


# ---------------------------------------------------------------------------
# extract_stock_features — happy path
# ---------------------------------------------------------------------------


class TestExtractFullSnapshot:
    def setup_method(self):
        self.features = extract_stock_features(FULL_SNAPSHOT)

    def test_returns_all_feature_names(self):
        assert set(self.features.keys()) == set(STOCK_FEATURE_NAMES)

    def test_pe_ratio(self):
        assert self.features["pe_ratio"] == 25.0

    def test_pe_vs_sector(self):
        assert self.features["pe_vs_sector"] == pytest.approx(0.9)

    def test_revenue_growth(self):
        assert self.features["revenue_growth_ttm"] == pytest.approx(12.5)

    def test_gross_margin(self):
        assert self.features["gross_margin"] == pytest.approx(45.0)

    def test_momentum(self):
        assert self.features["momentum_12_1"] == pytest.approx(8.3)

    def test_macro_bull_set(self):
        assert self.features["macro_bull"] == 1.0
        assert self.features["macro_bear"] == 0.0

    def test_macro_rate_rising_set(self):
        assert self.features["macro_rate_rising"] == 1.0
        assert self.features["macro_rate_falling"] == 0.0

    def test_earnings_rev_up(self):
        assert self.features["earnings_rev_up"] == 1.0
        assert self.features["earnings_rev_down"] == 0.0

    def test_roe(self):
        assert self.features["roe"] == pytest.approx(18.0)

    def test_debt_to_equity(self):
        assert self.features["debt_to_equity"] == pytest.approx(0.5)

    def test_short_pct_float(self):
        assert self.features["short_pct_float"] == pytest.approx(0.02)

    def test_price_vs_52w_high(self):
        # 150 / 180 = 0.833...
        assert self.features["price_vs_52w_high"] == pytest.approx(150 / 180)

    def test_price_vs_52w_low(self):
        # 150 / 120 = 1.25
        assert self.features["price_vs_52w_low"] == pytest.approx(150 / 120)


# ---------------------------------------------------------------------------
# extract_stock_features — empty / missing values → neutral defaults
# ---------------------------------------------------------------------------


class TestExtractEmptySnapshot:
    def setup_method(self):
        self.features = extract_stock_features(EMPTY_SNAPSHOT)

    def test_pe_ratio_default_zero(self):
        assert self.features["pe_ratio"] == 0.0

    def test_pe_vs_sector_default_one(self):
        # 1.0 = "in-line with sector" — the neutral assumption
        assert self.features["pe_vs_sector"] == 1.0

    def test_macro_flags_all_zero(self):
        assert self.features["macro_bull"] == 0.0
        assert self.features["macro_bear"] == 0.0
        assert self.features["macro_rate_rising"] == 0.0
        assert self.features["macro_rate_falling"] == 0.0

    def test_earnings_rev_neutral(self):
        assert self.features["earnings_rev_up"] == 0.0
        assert self.features["earnings_rev_down"] == 0.0

    def test_price_vs_range_defaults_to_one(self):
        # No price data → ratios default to 1.0 (neutral)
        assert self.features["price_vs_52w_high"] == 1.0
        assert self.features["price_vs_52w_low"] == 1.0

    def test_numeric_defaults_to_zero(self):
        for key in ("revenue_growth_ttm", "gross_margin", "momentum_12_1",
                    "roe", "debt_to_equity", "short_pct_float"):
            assert self.features[key] == 0.0, f"{key} should default to 0.0"


# ---------------------------------------------------------------------------
# PE ratio winsorization
# ---------------------------------------------------------------------------


class TestPERatioWinsorization:
    def test_negative_pe_clamped_to_zero(self):
        snap = {"pe_ratio": -5.0}
        f = extract_stock_features(snap)
        assert f["pe_ratio"] == 0.0

    def test_extreme_pe_clamped_to_100(self):
        snap = {"pe_ratio": 500.0}
        f = extract_stock_features(snap)
        assert f["pe_ratio"] == 100.0

    def test_normal_pe_unchanged(self):
        snap = {"pe_ratio": 22.5}
        f = extract_stock_features(snap)
        assert f["pe_ratio"] == pytest.approx(22.5)

    def test_exactly_100_not_clamped(self):
        snap = {"pe_ratio": 100.0}
        f = extract_stock_features(snap)
        assert f["pe_ratio"] == 100.0


# ---------------------------------------------------------------------------
# Macro regime flag permutations
# ---------------------------------------------------------------------------


class TestMacroRegimeFlags:
    def test_bear_market(self):
        snap = {"macro_regime": {"market_trend": "bear", "rate_env": "stable"}}
        f = extract_stock_features(snap)
        assert f["macro_bull"] == 0.0
        assert f["macro_bear"] == 1.0
        assert f["macro_rate_rising"] == 0.0
        assert f["macro_rate_falling"] == 0.0

    def test_falling_rates(self):
        snap = {"macro_regime": {"market_trend": "flat", "rate_env": "falling"}}
        f = extract_stock_features(snap)
        assert f["macro_rate_falling"] == 1.0
        assert f["macro_rate_rising"] == 0.0
        assert f["macro_bull"] == 0.0
        assert f["macro_bear"] == 0.0

    def test_unknown_trend_all_zero(self):
        snap = {"macro_regime": {"market_trend": "unknown", "rate_env": "unknown"}}
        f = extract_stock_features(snap)
        assert f["macro_bull"] == 0.0
        assert f["macro_bear"] == 0.0

    def test_none_macro_regime(self):
        snap = {"macro_regime": None}
        f = extract_stock_features(snap)
        assert f["macro_bull"] == 0.0
        assert f["macro_bear"] == 0.0


# ---------------------------------------------------------------------------
# Earnings revision flags
# ---------------------------------------------------------------------------


class TestEarningsRevisionFlags:
    def test_revision_down(self):
        snap = {"earnings_revision": "down"}
        f = extract_stock_features(snap)
        assert f["earnings_rev_down"] == 1.0
        assert f["earnings_rev_up"] == 0.0

    def test_revision_neutral(self):
        snap = {"earnings_revision": "neutral"}
        f = extract_stock_features(snap)
        assert f["earnings_rev_up"] == 0.0
        assert f["earnings_rev_down"] == 0.0

    def test_missing_revision_neutral(self):
        f = extract_stock_features({})
        assert f["earnings_rev_up"] == 0.0
        assert f["earnings_rev_down"] == 0.0


# ---------------------------------------------------------------------------
# features_to_vector
# ---------------------------------------------------------------------------


class TestFeaturesToVector:
    def test_length_matches_feature_names(self):
        f = extract_stock_features(FULL_SNAPSHOT)
        v = features_to_vector(f)
        assert len(v) == len(STOCK_FEATURE_NAMES)

    def test_ordering_matches_feature_names(self):
        f = extract_stock_features(FULL_SNAPSHOT)
        v = features_to_vector(f)
        for i, name in enumerate(STOCK_FEATURE_NAMES):
            assert v[i] == pytest.approx(f[name]), f"Position {i} ({name}) mismatch"

    def test_empty_dict_fills_zeros(self):
        v = features_to_vector({})
        assert all(x == 0.0 for x in v)

    def test_no_nan_in_full_snapshot_vector(self):
        f = extract_stock_features(FULL_SNAPSHOT)
        v = features_to_vector(f)
        assert not any(math.isnan(x) for x in v)

    def test_no_nan_in_empty_snapshot_vector(self):
        f = extract_stock_features(EMPTY_SNAPSHOT)
        v = features_to_vector(f)
        assert not any(math.isnan(x) for x in v)
