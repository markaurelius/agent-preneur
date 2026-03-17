"""Print a structured summary of recent backtest runs.

Used by the agent iteration loop to assess model quality and identify
the highest-leverage improvement to make next.

Output modes:
  --format human   (default) — readable table for terminal
  --format json    — machine-readable JSON for agent parsing

Usage:
    python scripts/results_summary.py
    python scripts/results_summary.py --n 5 --format json
    python scripts/results_summary.py --run-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING)

_RANDOM_BRIER = 0.25  # binary: p=0.5, outcome ∈ {0,1}


def _pct_improvement(brier: float) -> float:
    """% improvement vs random-guess baseline (0.25)."""
    return (_RANDOM_BRIER - brier) / _RANDOM_BRIER * 100


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarise recent backtest run results"
    )
    parser.add_argument(
        "--n", type=int, default=10, help="Number of most recent runs to show"
    )
    parser.add_argument(
        "--run-id", default=None, help="Show detail for a specific run ID"
    )
    parser.add_argument(
        "--format",
        choices=["human", "json"],
        default="human",
        help="Output format",
    )
    args = parser.parse_args()

    from src.db.models import Prediction, RunConfig, RunResult, Score
    from src.db.session import get_session

    with get_session() as session:
        if args.run_id:
            runs = [session.query(RunResult).filter_by(id=args.run_id).first()]
            if not runs[0]:
                print(f"Run {args.run_id} not found.", file=sys.stderr)
                sys.exit(1)
        else:
            runs = (
                session.query(RunResult)
                .order_by(RunResult.started_at.desc())
                .limit(args.n)
                .all()
            )

        results = []
        for run in runs:
            if run is None:
                continue

            cfg = session.query(RunConfig).filter_by(id=run.config_id).first()

            # Per-prediction detail for confidence bucket analysis
            preds = (
                session.query(Prediction)
                .filter_by(run_id=run.id)
                .all()
            )

            bucket_high = []  # prob ≥ 0.70 or prob ≤ 0.30
            bucket_med = []   # 0.40 < prob < 0.70 boundary
            bucket_low = []   # near 0.5
            mean_pred_sum = 0.0
            n_scored = 0

            for pred in preds:
                p = pred.probability_estimate or 0.5
                mean_pred_sum += p
                if pred.score and pred.score.brier_score is not None:
                    n_scored += 1
                    record = {
                        "prob": p,
                        "brier": pred.score.brier_score,
                        "resolved": pred.score.resolved_value,
                    }
                    dist = abs(p - 0.5)
                    if dist >= 0.20:
                        bucket_high.append(record)
                    elif dist >= 0.10:
                        bucket_med.append(record)
                    else:
                        bucket_low.append(record)

            def _bucket_summary(bucket: list[dict]) -> dict:
                if not bucket:
                    return {"n": 0, "mean_brier": None, "accuracy": None}
                mean_b = sum(r["brier"] for r in bucket) / len(bucket)
                correct = sum(
                    1 for r in bucket
                    if (r["prob"] >= 0.5 and r["resolved"] == 1.0)
                    or (r["prob"] < 0.5 and r["resolved"] == 0.0)
                )
                return {
                    "n": len(bucket),
                    "mean_brier": round(mean_b, 4),
                    "accuracy_pct": round(correct / len(bucket) * 100, 1),
                }

            mean_pred = (mean_pred_sum / len(preds) * 100) if preds else None
            bias_ok = mean_pred is not None and 40 <= mean_pred <= 60

            entry = {
                "run_id": run.id[:8],
                "run_id_full": run.id,
                "name": cfg.name if cfg else "unknown",
                "predictor_type": cfg.predictor_type if cfg else "unknown",
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "n_predictions": run.n_predictions or 0,
                "mean_brier": round(run.mean_brier_score, 4) if run.mean_brier_score else None,
                "vs_random_pct": round(_pct_improvement(run.mean_brier_score), 1) if run.mean_brier_score else None,
                "mean_pred_pct": round(mean_pred, 1) if mean_pred else None,
                "bias_ok": bias_ok,
                "confidence_buckets": {
                    "high_ge70": _bucket_summary(bucket_high),
                    "medium_60_70": _bucket_summary(bucket_med),
                    "low_lt60": _bucket_summary(bucket_low),
                },
                "issues": _identify_issues(
                    run.mean_brier_score, mean_pred, bucket_high, n_scored
                ),
            }
            results.append(entry)

    if args.format == "json":
        print(json.dumps(results, indent=2, default=str))
        return

    # Human-readable output
    print()
    print("=" * 72)
    print("  BACKTEST RESULTS SUMMARY")
    print("=" * 72)

    for r in results:
        brier_str = f"{r['mean_brier']:.4f}" if r["mean_brier"] else "N/A"
        vs_str = (
            f"  {r['vs_random_pct']:+.1f}% vs random"
            if r["vs_random_pct"] is not None
            else ""
        )
        bias_str = (
            f"  bias OK ({r['mean_pred_pct']:.1f}%)"
            if r["bias_ok"]
            else f"  ⚠ BIAS {r['mean_pred_pct']:.1f}%"
        ) if r["mean_pred_pct"] else ""

        print(
            f"\n  [{r['run_id']}]  {r['name']}"
            f"\n  Brier: {brier_str}{vs_str}{bias_str}"
            f"\n  n={r['n_predictions']}  predictor={r['predictor_type']}"
            f"\n  Started: {r['started_at']}"
        )

        hb = r["confidence_buckets"]["high_ge70"]
        mb = r["confidence_buckets"]["medium_60_70"]
        lb = r["confidence_buckets"]["low_lt60"]
        print(f"\n  Confidence buckets:")
        for label, b in [("High (≥70%)", hb), ("Med (60-70%)", mb), ("Low (<60%)", lb)]:
            if b["n"] > 0:
                print(
                    f"    {label:12s}  n={b['n']:<4}  "
                    f"Brier={b['mean_brier']:.4f}  "
                    f"Accuracy={b['accuracy_pct']:.1f}%"
                )

        if r["issues"]:
            print(f"\n  Issues identified:")
            for issue in r["issues"]:
                print(f"    ⚠  {issue}")

    print()
    print("  Random baseline Brier: 0.2500")
    print("  Target: Brier < 0.20, bias_ok=True, high-conf accuracy > 60%")
    print("=" * 72)
    print()


def _identify_issues(
    mean_brier: float | None,
    mean_pred_pct: float | None,
    bucket_high: list,
    n_scored: int,
) -> list[str]:
    """Return a prioritised list of issues for agent to act on."""
    issues = []

    if mean_pred_pct is not None:
        if mean_pred_pct > 60:
            issues.append(
                f"BIAS HIGH: mean output {mean_pred_pct:.1f}% >> 50%. "
                "Check class imbalance in training data and calibration."
            )
        elif mean_pred_pct < 40:
            issues.append(
                f"BIAS LOW: mean output {mean_pred_pct:.1f}% << 50%. "
                "Check class imbalance and calibration."
            )

    if mean_brier is not None:
        if mean_brier >= 0.25:
            issues.append(
                f"BRIER >= RANDOM ({mean_brier:.4f}). "
                "Model is not beating a coin flip — check features and labels."
            )
        elif mean_brier >= 0.22:
            issues.append(
                f"BRIER MARGINAL ({mean_brier:.4f}). "
                "Some improvement vs random but weak. "
                "Consider adding features or more training data."
            )

    if bucket_high:
        high_brier = sum(r["brier"] for r in bucket_high) / len(bucket_high)
        correct = sum(
            1 for r in bucket_high
            if (r["prob"] >= 0.5 and r["resolved"] == 1.0)
            or (r["prob"] < 0.5 and r["resolved"] == 0.0)
        )
        high_acc = correct / len(bucket_high) * 100
        if high_acc < 55:
            issues.append(
                f"HIGH-CONF ACCURACY LOW ({high_acc:.1f}% on {len(bucket_high)} predictions). "
                "Model is overconfident. Improve calibration or remove noisy features."
            )

    if n_scored < 20:
        issues.append(
            f"SMALL SAMPLE ({n_scored} scored predictions). "
            "Results unreliable — add more tickers or years to fetch-snapshots."
        )

    return issues


if __name__ == "__main__":
    main()
