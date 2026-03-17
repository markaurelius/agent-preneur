"""Print predictions from the most recent backtest run."""
import argparse
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from src.db.session import get_session
from src.db.models import Prediction, RunResult, Question, Score
from sqlalchemy import desc


def _bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, int(round(pct / 5))))
    return "█" * filled + "░" * (width - filled)


parser = argparse.ArgumentParser()
parser.add_argument("--run", default=None, help="Run name prefix or id (default: most recent)")
parser.add_argument("--sort", default="brier", choices=["brier", "ticker", "year"], help="Sort order")
args = parser.parse_args()

with get_session() as s:
    query = s.query(RunResult).order_by(desc(RunResult.started_at))
    if args.run:
        from src.db.models import RunConfig
        run_result = (
            query.join(RunConfig, RunResult.config_id == RunConfig.id)
            .filter(RunConfig.name.like(f"{args.run}%"))
            .first()
        )
    else:
        run_result = query.filter(RunResult.completed_at.isnot(None)).first()

    if not run_result:
        print("No completed runs found.")
        sys.exit(1)

    brier_str = f"{run_result.mean_brier_score:.4f}" if run_result.mean_brier_score else "N/A"
    print(f"\n{'='*70}")
    print(f"  Run: {run_result.id[:8]}...  |  n={run_result.n_predictions}  |  Mean Brier: {brier_str}")
    print(f"  Random baseline: 0.2500  |  Sorted by: {args.sort}")
    print(f"{'='*70}\n")

    rows = (
        s.query(Prediction, Question, Score)
        .join(Question, Question.id == Prediction.question_id)
        .join(Score, Score.prediction_id == Prediction.id)
        .filter(Prediction.run_id == run_result.id)
        .all()
    )

    sort_key = {
        "brier": lambda x: x[2].brier_score or 0,
        "ticker": lambda x: x[1].id.split("-")[-2],
        "year": lambda x: x[1].id.split("-")[-1],
    }[args.sort]
    rows = sorted(rows, key=sort_key, reverse=(args.sort == "brier"))

    correct_count = 0
    for p, q, sc in rows:
        parts = q.id.split("-")
        ticker = parts[-2]
        year = parts[-1]
        prob = p.probability_estimate or 0.5
        prob_pct = prob * 100
        actual = sc.resolved_value or 0.0
        actual_pct = actual * 100
        brier = sc.brier_score or 0.0

        is_correct = (prob >= 0.5 and actual == 1.0) or (prob < 0.5 and actual == 0.0)
        if is_correct:
            correct_count += 1
        marker = "✓" if is_correct else "✗"
        result_str = "BEAT SPY" if actual == 1.0 else "MISS SPY"

        rationale_short = ""
        if p.rationale:
            clean = p.rationale.replace("\n", " ").strip()
            first = next((sent.strip() for sent in clean.split(".") if len(sent.strip()) > 20), "")
            rationale_short = first[:160] + "." if first else ""

        print(f"{marker} [{ticker} {year}]  Brier: {brier:.4f}")
        print(f"  Model   : {prob_pct:5.1f}%  {_bar(prob_pct)}")
        print(f"  Actual  : {actual_pct:5.1f}%  {_bar(actual_pct)}  ({result_str})")
        if rationale_short:
            print(f"  Reason  : {rationale_short}")
        print()

    n = len(rows)
    print(f"{'='*70}")
    print(f"  Accuracy: {correct_count}/{n} ({correct_count/n*100:.1f}%)  |  Mean Brier: {brier_str}")
    print(f"{'='*70}\n")
