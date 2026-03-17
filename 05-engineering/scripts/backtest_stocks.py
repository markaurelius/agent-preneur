"""Walk-forward back-test for stock outperformance predictions.

For each ticker and each historical year in the corpus, builds a "current
profile" snapshot using data available at the START of that year, retrieves
analogues from BEFORE that year (time-filtered), synthesizes a prediction,
and scores it against the actual 12-month return vs S&P 500.

Usage:
    python scripts/backtest_stocks.py --config experiments/stock-v1.yaml
    python scripts/backtest_stocks.py --config experiments/stock-v1.yaml --years 2022,2023
    python scripts/backtest_stocks.py --config experiments/stock-v1.yaml --tickers AAPL,MSFT
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Default years to back-test
_DEFAULT_YEARS = [2021, 2022, 2023, 2024]

# ---------------------------------------------------------------------------
# Profile formatter helpers
# ---------------------------------------------------------------------------


def _safe(val, default=None):
    """Return val if not None/NaN, else default."""
    if val is None:
        return default
    try:
        import math
        if math.isnan(float(val)):
            return default
    except (TypeError, ValueError):
        pass
    return val


def _format_current_profile(snap: dict) -> str:
    """Build human-readable current profile string for the prompt."""

    def _fmt_price(val) -> str:
        if val is None:
            return "N/A"
        return f"${float(val):,.2f}"

    def _fmt_pct(val) -> str:
        if val is None:
            return "N/A"
        return f"{float(val):+.1f}%"

    def _fmt_pe(val) -> str:
        if val is None:
            return "N/A"
        return f"{float(val):.1f}x"

    macro = snap.get("macro_regime") or {}
    lines = [
        f"Ticker: {snap['ticker']} | Company: {snap['company_name']} | Sector: {snap.get('sector', 'Unknown')}",
        f"Macro regime: {macro.get('description', 'unknown')}",
        f"Current price: {_fmt_price(snap.get('current_price'))} | "
        f"52w range: {_fmt_price(snap.get('price_52w_low'))}–{_fmt_price(snap.get('price_52w_high'))}",
        f"P/E ratio: {_fmt_pe(snap.get('pe_ratio'))} | "
        f"P/E vs sector: {_safe(snap.get('pe_vs_sector'), 'N/A')}",
        f"Revenue growth (TTM): {_fmt_pct(snap.get('revenue_growth_ttm'))} | "
        f"Gross margin: {_fmt_pct(snap.get('gross_margin'))}",
        f"12-1 month momentum: {_fmt_pct(snap.get('momentum_12_1'))}",
        f"Earnings revision trend: {snap.get('earnings_revision', 'N/A')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Results analysis
# ---------------------------------------------------------------------------


def _compute_stats(records: list[dict]) -> dict:
    """Compute aggregate stats from a list of scored prediction records.

    Each record must have: probability, resolution, brier_score, year, ticker.
    """
    if not records:
        return {}

    n = len(records)
    total_brier = sum(r["brier_score"] for r in records)
    mean_brier = total_brier / n
    correct = sum(
        1 for r in records
        if (r["probability"] >= 0.5 and r["resolution"] == 1.0)
        or (r["probability"] < 0.5 and r["resolution"] == 0.0)
    )
    accuracy = correct / n * 100

    # By confidence bucket
    high = [r for r in records if r["probability"] >= 0.70]
    med = [r for r in records if 0.50 <= r["probability"] < 0.70]
    low = [r for r in records if r["probability"] < 0.50]

    def _bucket_stats(bucket):
        if not bucket:
            return {"n": 0, "brier": None, "accuracy": None}
        b_n = len(bucket)
        b_brier = sum(r["brier_score"] for r in bucket) / b_n
        b_correct = sum(
            1 for r in bucket
            if (r["probability"] >= 0.5 and r["resolution"] == 1.0)
            or (r["probability"] < 0.5 and r["resolution"] == 0.0)
        )
        return {"n": b_n, "brier": b_brier, "accuracy": b_correct / b_n * 100}

    # By year
    years = sorted(set(r["year"] for r in records))
    by_year = {}
    for yr in years:
        yr_recs = [r for r in records if r["year"] == yr]
        by_year[yr] = _bucket_stats(yr_recs)

    # Mean prediction (bias check)
    mean_pred = sum(r["probability"] for r in records) / n

    return {
        "n": n,
        "mean_brier": mean_brier,
        "accuracy": accuracy,
        "high": _bucket_stats(high),
        "med": _bucket_stats(med),
        "low": _bucket_stats(low),
        "by_year": by_year,
        "mean_pred": mean_pred,
        "years": years,
        "n_tickers": len(set(r["ticker"] for r in records)),
    }


def _print_results(stats: dict, dry_run: bool = False) -> None:
    """Print formatted back-test results table."""
    print()
    print("=" * 64)
    print("BACK-TEST RESULTS (walk-forward, time-filtered corpus)")
    if dry_run:
        print("NOTE: dry-run mode — all predictions are 0.5 (pipeline validation only)")
    print()

    years_str = ", ".join(str(y) for y in stats.get("years", []))
    print(f"Years covered: {years_str}")
    print(
        f"Tickers: {stats['n_tickers']}  |  Total predictions: {stats['n']}"
    )
    print()

    brier_str = f"{stats['mean_brier']:.4f}"
    print(f"Overall Brier score : {brier_str}  (random baseline: 0.2500)")
    print(f"Accuracy (>50% -> YES): {stats['accuracy']:.1f}%")
    print()

    def _fmt_bucket(label: str, bucket: dict) -> str:
        if bucket["n"] == 0:
            return f"  {label}: n=0   (no predictions)"
        brier_s = f"{bucket['brier']:.4f}" if bucket["brier"] is not None else "N/A"
        acc_s = f"{bucket['accuracy']:.1f}" if bucket["accuracy"] is not None else "N/A"
        return (
            f"  {label}: n={bucket['n']:<4}  Brier={brier_s:>6}  Accuracy={acc_s:>5}%"
        )

    print("By confidence bucket:")
    h = stats["high"]
    m = stats["med"]
    lo = stats["low"]
    print(_fmt_bucket("High (>=70%) ", h))
    print(_fmt_bucket("Med  (50-70%)", m))
    print(_fmt_bucket("Low  (<50%)  ", lo))
    print()

    print("By year:")
    for yr, ys in stats.get("by_year", {}).items():
        if ys["n"] == 0:
            continue
        print(
            f"  {yr}: n={ys['n']:<3}  "
            f"Brier={ys['brier']:.4f}  "
            f"Accuracy={ys['accuracy']:.1f}%"
        )
    print()

    mean_pred_pct = stats["mean_pred"] * 100
    bias_flag = "  [OK] no directional bias" if 45 <= mean_pred_pct <= 55 else "  [!] possible bias"
    print(f"Bias check: mean prediction = {mean_pred_pct:.1f}%{bias_flag}")
    print("=" * 64)
    print()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _upsert_backtest_question(snap: dict, year: int, session) -> "Question":  # noqa: F821
    """Insert or return a backtest Question for (ticker, year)."""
    from src.db.models import Question

    question_id = f"backtest-stock-12m-{snap['ticker']}-{year}"
    existing = session.query(Question).filter_by(id=question_id).first()
    if existing:
        return existing

    resolution_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

    q = Question(
        id=question_id,
        text=(
            f"Will {snap['ticker']} ({snap['company_name']}) outperform the "
            f"S&P 500 over the 12 months starting January 1, {year}?"
        ),
        resolution_value=None,
        resolution_date=resolution_date,
        community_probability=None,
        tags=["stock", "fundamentals", "12m-outperformance", "backtest", str(year)],
    )
    session.add(q)
    session.flush()
    return q


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward back-test for stock outperformance predictions"
    )
    parser.add_argument("--config", required=True, help="Path to experiment YAML")
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated list of tickers (default: top-50 S&P 500)",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Comma-separated years to back-test, e.g. 2022,2023 (default: 2021-2024)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return 0.5 for all predictions (validates pipeline end-to-end)",
    )
    args = parser.parse_args()

    # Deferred imports so sys.path manipulation takes effect first
    from src.config.schema import load_config
    from src.db.session import get_session
    from src.db.models import Prediction, RunConfig as RunConfigModel, RunResult, Score

    config = load_config(args.config)
    dry_run = config.dry_run or args.dry_run
    if args.dry_run and not config.dry_run:
        config = config.model_copy(update={"dry_run": True})

    # Resolve ticker list
    # None means "use all tickers in DB" (resolved inside the session).
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = None  # deferred: resolved from DB inside ML branch
    if tickers is not None and config.max_questions:
        tickers = tickers[: config.max_questions]

    # Resolve year list
    if args.years:
        years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
    else:
        years = _DEFAULT_YEARS

    if config.predictor_type != "ml":
        raise ValueError("Only predictor_type='ml' is supported. Update your config YAML.")
    if not config.model_path:
        raise ValueError("predictor_type='ml' requires model_path in the config YAML")

    _ticker_count_str = str(len(tickers)) if tickers is not None else "all-DB"
    logger.info(
        "Back-testing %s tickers x %d years = up to %s predictions",
        _ticker_count_str, len(years),
        str(len(tickers) * len(years)) if tickers is not None else "?",
    )

    with get_session() as session:
        run_name = (
            f"backtest-stock-v1-{'dry' if dry_run else 'live'}-"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M')}"
        )
        run_config_row = RunConfigModel(
            name=run_name,
            top_k=config.top_k,
            similarity_type=config.similarity_type,
            embedding_weight=config.embedding_weight,
            metadata_weight=config.metadata_weight,
            metadata_filters=config.metadata_filters,
            prompt_version=config.prompt_version,
            model=config.model,
            max_questions=(len(tickers) if tickers is not None else 0) * len(years),
            dry_run=dry_run,
        )
        session.add(run_config_row)
        session.commit()

        run_result = RunResult(
            config_id=run_config_row.id,
            started_at=datetime.now(timezone.utc),
        )
        session.add(run_result)
        session.commit()
        run_id = run_result.id

        print()
        print("=" * 64)
        print(f"  STOCK BACK-TEST — {run_name}")
        print(f"  run_id: {run_id[:8]}...")
        print(f"  Tickers: {len(tickers) if tickers is not None else 'all-DB'}  |  Years: {years}")
        if dry_run:
            print("  MODE: DRY RUN (all predictions = 0.5)")
        print("=" * 64)

        scored_records: list[dict] = []
        skipped_no_data = 0

        # ---------------------------------------------------------------
        # Phase 1: Data preparation — reads from DB cache (instant, no network)
        # ---------------------------------------------------------------
        work_items: list[dict] = []

        if True:  # ML path only
            # --- Walk-forward ML path: train per-fold models inline ---
            # This avoids data leakage from the pre-trained model (which was
            # trained on ALL years). For each test year Y we train a fresh
            # LightGBM+calibration pipeline on years < Y only.
            from src.db.models import StockSnapshot
            from src.synthesis.stock_features import STOCK_FEATURE_NAMES, extract_stock_features, features_to_vector

            import numpy as np
            try:
                import lightgbm as lgb
            except ImportError:
                logger.error("lightgbm not installed. Rebuild Docker image: make build")
                raise SystemExit(1)
            from sklearn.calibration import CalibratedClassifierCV

            # Load FRED macro once for all years — injected into snapshot dicts
            # before feature extraction so continuous signals override the old
            # binary-only macro_regime JSON stored in snapshot_json.
            from src.ingestion.fundamentals import load_fred_macro_from_db
            fred_by_year = load_fred_macro_from_db(session)
            if fred_by_year:
                logger.info(
                    "FRED macro loaded: %d years cached (%s)",
                    len(fred_by_year), sorted(fred_by_year.keys()),
                )
            else:
                logger.warning(
                    "fred_macro table is empty — FRED signals will use defaults. "
                    "Run 'make populate-fred' to populate."
                )

            # Resolve tickers from DB if not explicitly specified (ML path default).
            # This uses the full universe rather than just TOP_50, giving 3-4× more
            # evaluation rows per fold for stable Brier estimation.
            if tickers is None:
                tickers = sorted(set(
                    r.ticker for r in session.query(StockSnapshot.ticker)
                    .filter(StockSnapshot.label.isnot(None))
                    .distinct()
                    .all()
                ))
                logger.info(
                    "ML full-universe backtest: resolved %d tickers from DB",
                    len(tickers),
                )

            # Load ALL cached snapshots (all years with labels) for walk-forward training
            all_cached = (
                session.query(StockSnapshot)
                .filter(
                    StockSnapshot.ticker.in_(tickers),
                    StockSnapshot.label.isnot(None),
                )
                .all()
            )
            if not all_cached:
                logger.error(
                    "No cached snapshots found for ML backtest. "
                    "Run 'make fetch-snapshots' first."
                )
                raise SystemExit(1)

            logger.info(
                "Walk-forward ML path: loaded %d total snapshots from DB cache",
                len(all_cached),
            )

            # Group all rows by year
            all_years_in_db = sorted(set(r.year for r in all_cached))
            logger.info("Years available in DB: %s", all_years_in_db)

            # Determine which test years are evaluable:
            # need at least 2 prior years of training data
            _MIN_TRAIN_YEARS = 2
            _MIN_TRAIN_EXAMPLES = 30

            # Build a lookup dict: year -> list of rows
            rows_by_year: dict[int, list] = {}
            for r in all_cached:
                rows_by_year.setdefault(r.year, []).append(r)

            # Pre-compute walk-forward predictions for each test year
            wf_predictions: dict[tuple[str, int], float] = {}  # (ticker, year) -> prob

            test_years_evaluated = []
            for test_year in years:
                prior_years = [y for y in all_years_in_db if y < test_year]
                if len(prior_years) < _MIN_TRAIN_YEARS:
                    logger.warning(
                        "Skipping test_year=%d: only %d prior year(s) available "
                        "(need at least %d)",
                        test_year, len(prior_years), _MIN_TRAIN_YEARS,
                    )
                    continue

                train_rows = [r for y in prior_years for r in rows_by_year.get(y, [])]
                if len(train_rows) < _MIN_TRAIN_EXAMPLES:
                    logger.warning(
                        "Skipping test_year=%d: only %d training examples "
                        "(need at least %d)",
                        test_year, len(train_rows), _MIN_TRAIN_EXAMPLES,
                    )
                    continue

                test_rows = rows_by_year.get(test_year, [])
                if not test_rows:
                    logger.warning("Skipping test_year=%d: no test rows in DB", test_year)
                    continue

                # Build train arrays — re-extract features from snapshot_json so any
                # new features in extract_stock_features are included without re-fetching.
                # Inject FRED continuous macro signals before feature extraction so the
                # model sees continuous values (yield_curve_slope, fed_funds_rate, etc.)
                # rather than the old binary-only macro_regime stored in snapshot_json.
                # NOTE: year comes from r.year (the ORM column), not from snapshot_json
                # (which stores the profile dict and does not include a "year" key).
                def _snap_with_fred(snap: dict, yr: int) -> dict:
                    """Return snapshot with FRED macro injected if available for year."""
                    if yr in fred_by_year:
                        snap = dict(snap)  # shallow copy — never mutate the DB object
                        snap["macro_regime"] = fred_by_year[yr]
                    return snap

                X_tr = np.array([features_to_vector(extract_stock_features(_snap_with_fred(r.snapshot_json, r.year))) for r in train_rows])
                y_tr = np.array([float(r.label) for r in train_rows])

                n_pos_f = float(y_tr.sum())
                n_neg_f = len(y_tr) - n_pos_f
                spw_f = n_neg_f / n_pos_f if n_pos_f > 0 else 1.0

                logger.info(
                    "Walk-forward: test_year=%d  train_years=%s  n_train=%d  "
                    "n_test=%d  scale_pos_weight=%.2f",
                    test_year, prior_years, len(train_rows), len(test_rows), spw_f,
                )

                fold_model = CalibratedClassifierCV(
                    lgb.LGBMClassifier(
                        objective="binary",
                        n_estimators=100,
                        learning_rate=0.05,
                        num_leaves=15,
                        min_child_samples=20,  # iter 27: 5→20 to prevent leaf-group overfitting on small folds
                        scale_pos_weight=spw_f,
                        random_state=42,
                        verbose=-1,
                    ),
                    cv=min(3, len(set(y_tr.astype(int)))),
                    method="isotonic",
                )
                import pandas as pd
                fold_model.fit(
                    pd.DataFrame(X_tr, columns=STOCK_FEATURE_NAMES), y_tr
                )

                # Predict for all test rows
                X_te = np.array([features_to_vector(extract_stock_features(_snap_with_fred(r.snapshot_json, r.year))) for r in test_rows])
                probs = fold_model.predict_proba(
                    pd.DataFrame(X_te, columns=STOCK_FEATURE_NAMES)
                )[:, 1].tolist()

                for row, prob in zip(test_rows, probs):
                    prob_clipped = max(0.01, min(0.99, prob))
                    wf_predictions[(row.ticker, row.year)] = prob_clipped

                test_years_evaluated.append(test_year)

            if not wf_predictions:
                logger.error(
                    "Walk-forward produced no predictions. "
                    "Check that snapshots cover at least %d prior years before the test years.",
                    _MIN_TRAIN_YEARS,
                )
                raise SystemExit(1)

            logger.info(
                "Walk-forward complete: %d predictions across years %s",
                len(wf_predictions), test_years_evaluated,
            )

            # Now build work_items using the pre-computed walk-forward probabilities.
            # Only include (ticker, year) pairs that were evaluated.
            cached_test = (
                session.query(StockSnapshot)
                .filter(
                    StockSnapshot.ticker.in_(tickers),
                    StockSnapshot.year.in_(test_years_evaluated),
                    StockSnapshot.label.isnot(None),
                )
                .all()
            )

            for row in cached_test:
                key = (row.ticker, row.year)
                if key not in wf_predictions:
                    continue  # was skipped (e.g. not enough training data)

                snap = row.snapshot_json
                resolution = float(row.label)
                stock_ret = (row.stock_return or 0.0) / 100
                spy_ret = (row.spy_return or 0.0) / 100
                actual_delta_pct = (stock_ret - spy_ret) * 100

                question = _upsert_backtest_question(snap, row.year, session)
                if question.resolution_value is None:
                    question.resolution_value = resolution
                session.commit()

                existing_pred = session.query(Prediction).filter_by(
                    run_id=run_id, question_id=question.id
                ).first()
                if existing_pred:
                    prob = existing_pred.probability_estimate or 0.5
                    scored_records.append({
                        "ticker": row.ticker, "year": row.year,
                        "probability": prob, "resolution": resolution,
                        "brier_score": (prob - resolution) ** 2,
                        "actual_delta_pct": actual_delta_pct,
                    })
                    continue

                # Use pre-computed walk-forward probability (not ml_predictor.predict())
                wf_prob = wf_predictions[key]
                conf = "high" if abs(wf_prob - 0.5) >= 0.20 else "medium" if abs(wf_prob - 0.5) >= 0.10 else "low"
                direction = "bullish" if wf_prob >= 0.5 else "bearish"
                features = row.features_json or {}
                rationale = (
                    f"Walk-forward LightGBM {direction} ({wf_prob:.1%}) — "
                    f"confidence: {conf}  |  "
                    f"pe_vs_sector={features.get('pe_vs_sector', 0.0):.2f}, "
                    f"momentum={features.get('momentum_12_1', 0.0):+.1f}%, "
                    f"revenue_growth={features.get('revenue_growth_ttm', 0.0):+.1f}%"
                )

                work_items.append({
                    "ticker": row.ticker,
                    "year": row.year,
                    "question_id": question.id,
                    "question_text": question.text,
                    "current_profile": _format_current_profile(snap),
                    "resolution_date_str": f"{row.year + 1}-01-01",
                    "snap": snap,
                    "analogues": [],
                    "resolution": resolution,
                    "actual_delta_pct": actual_delta_pct,
                    # Pre-computed walk-forward probability and rationale
                    "wf_probability": wf_prob,
                    "wf_rationale": rationale,
                })

        logger.info(
            "Data prep complete. %d predictions to synthesize, %d already done, %d skipped.",
            len(work_items), len(scored_records), skipped_no_data,
        )

        # ---------------------------------------------------------------
        # Phase 2: Parallel synthesis (ML inference — no API calls)
        # ---------------------------------------------------------------
        n_workers = config.workers
        total_work = len(work_items)
        completed_count = 0

        def _synthesize_item(item: dict) -> dict:
            # Use pre-computed walk-forward probability (no data leakage).
            # wf_probability was computed by a model trained only on years
            # strictly before item["year"].
            probability = item["wf_probability"]
            rationale = item["wf_rationale"]
            tokens_used = 0
            latency_ms = 0
            return {**item, "probability": probability, "rationale": rationale,
                    "tokens_used": tokens_used, "latency_ms": latency_ms}

        synthesis_results: list[dict] = []
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_synthesize_item, item): item for item in work_items}
            for future in as_completed(futures):
                completed_count += 1
                try:
                    result = future.result()
                    synthesis_results.append(result)
                    brier = (result["probability"] - result["resolution"]) ** 2
                    outcome_str = "BEAT" if result["resolution"] == 1.0 else "MISS"
                    logger.info(
                        "[%d/%d] %s %d  prob=%.2f  actual=%s%+.1f%%  brier=%.4f",
                        completed_count, total_work,
                        result["ticker"], result["year"], result["probability"],
                        outcome_str, result["actual_delta_pct"], brier,
                    )
                except Exception as exc:
                    item = futures[future]
                    logger.warning("%s %d — synthesis error: %s", item["ticker"], item["year"], exc)

        # ---------------------------------------------------------------
        # Phase 3: Sequential DB writes (SQLite is not thread-safe for writes)
        # ---------------------------------------------------------------
        for result in synthesis_results:
            brier = (result["probability"] - result["resolution"]) ** 2
            pred_row = Prediction(
                run_id=run_id,
                question_id=result["question_id"],
                probability_estimate=result["probability"],
                rationale=result["rationale"],
                analogues_used=[
                    {"event_id": a.event.id, "similarity_score": a.similarity_score}
                    for a in result["analogues"]
                ],
                prompt_version=config.prompt_version,
                model=config.model,
                tokens_used=result["tokens_used"],
                latency_ms=result["latency_ms"],
            )
            session.add(pred_row)
            session.flush()

            score_row = Score(
                prediction_id=pred_row.id,
                brier_score=brier,
                resolved_value=result["resolution"],
                community_brier_score=None,
            )
            session.add(score_row)
            session.commit()

            scored_records.append({
                "ticker": result["ticker"],
                "year": result["year"],
                "probability": result["probability"],
                "resolution": result["resolution"],
                "brier_score": brier,
                "actual_delta_pct": result["actual_delta_pct"],
            })

        # --- Finalise run record ---
        n_preds = len(scored_records)
        mean_brier = (
            sum(r["brier_score"] for r in scored_records) / n_preds
            if n_preds > 0
            else None
        )
        run_result.completed_at = datetime.now(timezone.utc)
        run_result.n_predictions = n_preds
        run_result.mean_brier_score = mean_brier
        session.add(run_result)
        session.commit()

        if skipped_no_data:
            logger.info(
                "%d (ticker, year) pairs skipped due to missing return data",
                skipped_no_data,
            )

        # --- Print results ---
        if scored_records:
            stats = _compute_stats(scored_records)
            _print_results(stats, dry_run=dry_run)
        else:
            print("\nNo scored predictions — check data availability and corpus.")


if __name__ == "__main__":
    main()
