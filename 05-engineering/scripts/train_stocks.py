"""Train LightGBM stock outperformance predictor from cached snapshots.

Reads from the stock_snapshots DB table (populated by fetch_snapshots.py).
Zero yfinance calls — training completes in seconds.

Two bias fixes applied automatically:
  1. scale_pos_weight: corrects for class imbalance in training labels
  2. CalibratedClassifierCV (isotonic): forces mean output close to base rate (~50%)

Walk-forward cross-validation is used to avoid data leakage across time.

Usage:
    python scripts/train_stocks.py
    python scripts/train_stocks.py --years 2021,2022,2023,2024
    python scripts/train_stocks.py --output data/models/lgbm_stock_v1.pkl --cv-window 3
"""

from __future__ import annotations

import argparse
import logging
import sys
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


def _brier(y_true: list[float], y_pred: list[float]) -> float:
    return sum((p - t) ** 2 for p, t in zip(y_pred, y_true)) / len(y_true)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train LightGBM stock predictor from cached DB snapshots"
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Restrict to specific years (comma-separated); default: all cached years",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Restrict to specific tickers (comma-separated); default: all cached",
    )
    parser.add_argument(
        "--output",
        default="data/models/lgbm_stock_v1.pkl",
        help="Output path for trained model pipeline",
    )
    parser.add_argument(
        "--cv-window",
        type=int,
        default=3,
        help="Walk-forward: number of prior years to train on per test fold",
    )
    args = parser.parse_args()

    import joblib
    import numpy as np

    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("lightgbm not installed. Rebuild Docker image: make build")
        sys.exit(1)

    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    from src.db.models import StockSnapshot
    from src.db.session import get_session
    from src.ingestion.fundamentals import load_fred_macro_from_db
    from src.synthesis.stock_features import STOCK_FEATURE_NAMES, extract_stock_features, features_to_vector

    # -----------------------------------------------------------------------
    # Load from DB cache — fast, no network calls
    # -----------------------------------------------------------------------
    with get_session() as session:
        query = session.query(StockSnapshot).filter(StockSnapshot.label.isnot(None))

        if args.years:
            year_list = [int(y.strip()) for y in args.years.split(",") if y.strip()]
            query = query.filter(StockSnapshot.year.in_(year_list))

        if args.tickers:
            ticker_list = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
            query = query.filter(StockSnapshot.ticker.in_(ticker_list))

        rows = query.all()

        # Load FRED macro once inside the session so continuous signals override
        # the old binary-only macro_regime JSON stored in snapshot_json.
        fred_by_year = load_fred_macro_from_db(session)

    if fred_by_year:
        logger.info(
            "FRED macro loaded: %d years cached (%s)",
            len(fred_by_year), sorted(fred_by_year.keys()),
        )
    else:
        logger.warning(
            "fred_macro table is empty — FRED signals will use neutral defaults. "
            "Run 'make populate-fred' to populate."
        )

    if not rows:
        logger.error(
            "No cached snapshots found. Run 'make fetch-snapshots' first."
        )
        sys.exit(1)

    logger.info(
        "Loaded %d cached snapshots from DB  (tickers: %d, years: %s)",
        len(rows),
        len(set(r.ticker for r in rows)),
        sorted(set(r.year for r in rows)),
    )

    def _snap_with_fred(snap: dict, yr: int) -> dict:
        """Return snapshot with FRED macro injected if available for year."""
        if yr in fred_by_year:
            snap = dict(snap)  # shallow copy — never mutate the DB object
            snap["macro_regime"] = fred_by_year[yr]
        return snap

    # -----------------------------------------------------------------------
    # Build feature matrix
    # -----------------------------------------------------------------------
    data = [
        {
            "ticker": r.ticker,
            "year": r.year,
            # Re-extract from snapshot_json so any new features in extract_stock_features
            # are included without needing to re-fetch from yfinance.
            # Inject FRED continuous macro signals (yield_curve_slope, fed_funds_rate,
            # hy_spread, vix, cpi_yoy) so the model trains on real values rather than
            # the neutral defaults that were stored in snapshot_json.
            "vec": features_to_vector(extract_stock_features(_snap_with_fred(r.snapshot_json, r.year))),
            "label": float(r.label),
        }
        for r in rows
    ]

    y_all_labels = [d["label"] for d in data]
    n_pos = sum(y_all_labels)
    n_neg = len(y_all_labels) - n_pos
    pct_pos = n_pos / len(y_all_labels) * 100
    scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0

    logger.info(
        "Label balance: %.1f%% outperformed SPY  (pos=%d, neg=%d)  "
        "→ scale_pos_weight=%.2f",
        pct_pos, int(n_pos), int(n_neg), scale_pos_weight,
    )

    # -----------------------------------------------------------------------
    # Walk-forward cross-validation
    # -----------------------------------------------------------------------
    sorted_years = sorted(set(d["year"] for d in data))
    wf_window = args.cv_window
    fold_briers: list[float] = []

    if len(sorted_years) > wf_window:
        test_years = sorted_years[wf_window:]
        logger.info(
            "Walk-forward CV: expanding window (all years from earliest) | test folds: %s", test_years
        )

        for test_year in test_years:
            train_years = [y for y in sorted_years if y < test_year]  # expanding window from earliest year
            train_data = [d for d in data if d["year"] in train_years]
            test_data = [d for d in data if d["year"] == test_year]

            if not train_data or not test_data:
                continue

            import pandas as pd
            X_tr = pd.DataFrame([d["vec"] for d in train_data], columns=STOCK_FEATURE_NAMES)
            y_tr = np.array([d["label"] for d in train_data])
            X_te = pd.DataFrame([d["vec"] for d in test_data], columns=STOCK_FEATURE_NAMES)
            y_te = [d["label"] for d in test_data]

            # Compute class weight for this fold
            n_pos_f = y_tr.sum()
            n_neg_f = len(y_tr) - n_pos_f
            spw_f = n_neg_f / n_pos_f if n_pos_f > 0 else 1.0

            fold_model = CalibratedClassifierCV(
                lgb.LGBMClassifier(
                    objective="binary",
                    n_estimators=100,
                    learning_rate=0.05,
                    num_leaves=15,
                    min_child_samples=5,
                    scale_pos_weight=spw_f,
                    random_state=42,
                    verbose=-1,
                ),
                cv=min(3, len(set(y_tr.astype(int)))),
                method="isotonic",
            )
            fold_model.fit(X_tr, y_tr)
            y_pred = fold_model.predict_proba(X_te)[:, 1].tolist()
            brier = _brier(y_te, y_pred)
            mean_pred = sum(y_pred) / len(y_pred) * 100
            fold_briers.append(brier)

            logger.info(
                "  fold test=%d  train=%s  n=%d/%d  Brier=%.4f  mean_pred=%.1f%%",
                test_year, train_years,
                len(train_data), len(test_data),
                brier, mean_pred,
            )

        if fold_briers:
            mean_b = sum(fold_briers) / len(fold_briers)
            std_b = (
                sum((b - mean_b) ** 2 for b in fold_briers) / len(fold_briers)
            ) ** 0.5
            logger.info(
                "Walk-forward CV  Brier: %.4f ± %.4f  (random baseline: 0.2500)",
                mean_b, std_b,
            )
    else:
        logger.warning(
            "Only %d year(s) of data — walk-forward CV needs > %d years. "
            "Skipping CV, training final model only.",
            len(sorted_years), wf_window,
        )

    # -----------------------------------------------------------------------
    # Train final model on all data
    # -----------------------------------------------------------------------
    import pandas as pd
    X_all = pd.DataFrame([d["vec"] for d in data], columns=STOCK_FEATURE_NAMES)
    y_all = np.array([d["label"] for d in data])

    logger.info("Fitting final model on %d examples …", len(X_all))

    # StandardScaler is a no-op for LightGBM but keeps pipeline interface
    # consistent with StockMLPredictor.predict()
    final_pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                CalibratedClassifierCV(
                    lgb.LGBMClassifier(
                        objective="binary",
                        n_estimators=200,
                        learning_rate=0.05,
                        num_leaves=15,
                        min_child_samples=5,
                        scale_pos_weight=scale_pos_weight,  # fix class imbalance
                        random_state=42,
                        verbose=-1,
                    ),
                    cv=5,
                    method="isotonic",  # non-parametric calibration → fixes output bias
                ),
            ),
        ]
    )
    final_pipeline.fit(X_all, y_all)

    # Sanity check: mean output on training data should be close to 50%
    train_preds = final_pipeline.predict_proba(X_all)[:, 1]
    mean_output = train_preds.mean() * 100
    logger.info(
        "Calibration check (train set): mean output = %.1f%%  "
        "(expected ~50%% after calibration)",
        mean_output,
    )
    if mean_output > 60 or mean_output < 40:
        logger.warning(
            "Mean output still off (%.1f%%). "
            "Consider adding more tickers/years for a balanced dataset.",
            mean_output,
        )

    # Feature importances (from inner LightGBM of first calibration fold)
    try:
        inner_lgb = final_pipeline.named_steps["clf"].calibrated_classifiers_[0].estimator
        importances = inner_lgb.feature_importances_
        logger.info("Feature importances (gain-based, top 10):")
        for name, imp in sorted(
            zip(STOCK_FEATURE_NAMES, importances), key=lambda x: -x[1]
        )[:10]:
            logger.info("  %-30s  %d", name, imp)
    except Exception:
        pass

    # -----------------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------------
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_pipeline, output_path)
    logger.info("Model saved → %s", output_path)
    logger.info("Next: make backtest-stocks-ml  or  make forecast-stocks-ml")


if __name__ == "__main__":
    main()
