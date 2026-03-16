"""Compare Brier scores across runs.

Usage:
    python scripts/compare.py                        # compare all runs
    python scripts/compare.py --domain geopolitics   # filter by domain
    python scripts/compare.py --last 5               # most recent 5 runs
    python scripts/compare.py --runs <id1> <id2>     # specific run IDs

NOTE: Absolute Brier scores are inflated by training-data leakage on historical
questions. Use this tool for RELATIVE comparison only — lower is better, and
differences between runs are meaningful even if the absolute values are not.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.WARNING)

RANDOM_BRIER = 0.25


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare prediction runs by Brier score")
    parser.add_argument("--runs", nargs="+", help="Specific run IDs (prefix match)")
    parser.add_argument("--domain", help="Filter by domain (geopolitics, finance)")
    parser.add_argument("--last", type=int, default=10, help="Show N most recent runs (default 10)")
    args = parser.parse_args()

    from src.db.session import get_session
    from src.db.models import RunResult, RunConfig as RC, Prediction, Score, Question

    with get_session() as session:
        query = (
            session.query(RunResult, RC)
            .join(RC, RunResult.config_id == RC.id)
            .filter(RunResult.completed_at.isnot(None))
            .filter(RunResult.n_predictions > 0)
            .filter(RC.dry_run == False)
        )

        if args.runs:
            from sqlalchemy import or_
            query = query.filter(
                or_(*[RunResult.id.startswith(r) for r in args.runs])
            )

        if args.domain:
            query = query.filter(RC.name.contains(args.domain))

        rows = query.order_by(RunResult.completed_at.desc()).limit(args.last).all()

        if not rows:
            print("No completed runs found.")
            return

        # Reverse so oldest is first (better for comparison reading)
        rows = list(reversed(rows))

        # Header
        print()
        print("=" * 90)
        print("  RUN COMPARISON  —  scores are relative only; leakage inflates all absolute values")
        print("=" * 90)
        print(f"  {'Run name':<35} {'Retrieval':<12} {'n':>5}  {'Mean Brier':>10}  {'vs Random':>10}  {'Cost':>8}")
        print("-" * 90)

        best_brier = min(r.mean_brier_score for r, _ in rows if r.mean_brier_score is not None)

        for run_result, cfg in rows:
            if run_result.mean_brier_score is None:
                continue

            brier = run_result.mean_brier_score
            vs_random = brier - RANDOM_BRIER  # negative = better than random
            is_best = abs(brier - best_brier) < 1e-6

            marker = " ◀ best" if is_best else ""
            cost_str = f"${run_result.cost_usd:.3f}" if run_result.cost_usd else "  —"

            print(
                f"  {cfg.name:<35}"
                f" {cfg.similarity_type:<12}"
                f" {run_result.n_predictions:>5}"
                f"  {brier:>10.4f}"
                f"  {vs_random:>+10.4f}"
                f"  {cost_str:>8}"
                f"{marker}"
            )

        print("-" * 90)
        print(f"  {'Random baseline':<35} {'—':<12} {'—':>5}  {RANDOM_BRIER:>10.4f}  {'±0.0000':>10}  {'—':>8}")
        print("=" * 90)

        # Detail view for best run
        best_run, best_cfg = min(
            ((r, c) for r, c in rows if r.mean_brier_score is not None),
            key=lambda x: x[0].mean_brier_score,
        )

        print(f"\n  Best run: {best_cfg.name}  (id: {best_run.id[:8]}...)")
        print(f"  Prompt: {best_cfg.prompt_version}  |  top_k: {best_cfg.top_k}  |  model: {best_cfg.model}")
        if getattr(best_cfg, "min_resolution_year", None):
            print(f"  min_resolution_year: {best_cfg.min_resolution_year}")

        # Score distribution for best run
        scores = (
            session.query(Score)
            .join(Prediction, Score.prediction_id == Prediction.id)
            .filter(Prediction.run_id == best_run.id)
            .all()
        )
        if scores:
            briers = sorted(s.brier_score for s in scores if s.brier_score is not None)
            n = len(briers)
            buckets = {"0.00–0.05": 0, "0.05–0.10": 0, "0.10–0.20": 0, "0.20–0.25": 0, ">0.25": 0}
            for b in briers:
                if b < 0.05: buckets["0.00–0.05"] += 1
                elif b < 0.10: buckets["0.05–0.10"] += 1
                elif b < 0.20: buckets["0.10–0.20"] += 1
                elif b <= 0.25: buckets["0.20–0.25"] += 1
                else: buckets[">0.25"] += 1

            print(f"\n  Score distribution ({n} predictions):")
            for label, count in buckets.items():
                bar = "█" * int(count / n * 40)
                print(f"    {label}  {bar:<40} {count:>3} ({count/n*100:.0f}%)")

        print()


if __name__ == "__main__":
    main()
