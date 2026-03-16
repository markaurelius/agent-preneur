"""Evaluate backtest results and diagnose improvement opportunities.

Reads prediction + score data from the DB, computes a structured breakdown,
and optionally asks Claude to propose the highest-leverage next change.

Usage:
    # Evaluate the most recent backtest run
    python scripts/evaluate.py

    # Evaluate a specific run by name prefix or full run_id
    python scripts/evaluate.py --run backtest-stock-v1-live-2026-03-16

    # Evaluate + ask Claude for a diagnosis and next-step recommendation
    python scripts/evaluate.py --diagnose

    # Compare two runs side-by-side
    python scripts/evaluate.py --run run-a --compare run-b

    # Fast eval: run the fixed 10-ticker eval set then evaluate
    python scripts/evaluate.py --run-eval --config experiments/stock-v1.yaml
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
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

# ---------------------------------------------------------------------------
# Fixed eval set — locked, never changes between runs
# ---------------------------------------------------------------------------

EVAL_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "XOM", "JNJ", "BRK-B"]
EVAL_YEARS = [2022, 2023, 2024]

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _load_run(session, run_ref: str | None):
    """Return the RunResult row matching run_ref (name prefix or full id).
    If run_ref is None, return the most recent completed backtest run.
    """
    from src.db.models import RunResult, RunConfig

    query = (
        session.query(RunResult, RunConfig)
        .join(RunConfig, RunResult.config_id == RunConfig.id)
        .filter(RunConfig.name.like("backtest-%"))
        .filter(RunResult.completed_at.isnot(None))
    )

    if run_ref:
        # Try exact id first, then name prefix
        exact = query.filter(RunResult.id == run_ref).first()
        if exact:
            return exact
        prefix = query.filter(RunConfig.name.like(f"{run_ref}%")).order_by(RunResult.started_at.desc()).first()
        if prefix:
            return prefix
        raise ValueError(f"No completed backtest run found matching '{run_ref}'")

    latest = query.order_by(RunResult.started_at.desc()).first()
    if not latest:
        raise ValueError("No completed backtest runs found in DB. Run backtest_stocks.py first.")
    return latest


def _load_predictions(session, run_id: str) -> list[dict]:
    """Load all predictions + scores + question metadata for a run."""
    from src.db.models import Prediction, Score, Question

    rows = (
        session.query(Prediction, Score, Question)
        .join(Score, Score.prediction_id == Prediction.id)
        .join(Question, Question.id == Prediction.question_id)
        .filter(Prediction.run_id == run_id)
        .all()
    )

    records = []
    for pred, score, question in rows:
        # Parse ticker and year from question ID: backtest-stock-12m-{TICKER}-{YEAR}
        parts = question.id.split("-")
        try:
            year = int(parts[-1])
            ticker = parts[-2]
        except (ValueError, IndexError):
            ticker = "UNKNOWN"
            year = 0

        records.append({
            "prediction_id": pred.id,
            "ticker": ticker,
            "year": year,
            "probability": pred.probability_estimate or 0.5,
            "rationale": pred.rationale or "",
            "analogues_used": pred.analogues_used or [],
            "tokens_used": pred.tokens_used or 0,
            "latency_ms": pred.latency_ms or 0,
            "brier_score": score.brier_score,
            "resolved_value": score.resolved_value,
            "prompt_version": pred.prompt_version or "unknown",
            "model": pred.model or "unknown",
        })

    return records


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------


def _bucket(prob: float) -> str:
    if prob >= 0.70:
        return "high"
    if prob >= 0.50:
        return "med"
    return "low"


def _compute_stats(records: list[dict]) -> dict:
    if not records:
        return {}

    n = len(records)
    briers = [r["brier_score"] for r in records if r["brier_score"] is not None]
    probs = [r["probability"] for r in records]
    resolutions = [r["resolved_value"] for r in records if r["resolved_value"] is not None]

    mean_brier = sum(briers) / len(briers) if briers else None
    mean_prob = sum(probs) / n
    base_rate = sum(resolutions) / len(resolutions) if resolutions else None

    # Directional accuracy
    correct = sum(
        1 for r in records
        if (r["probability"] >= 0.5 and r["resolved_value"] == 1.0)
        or (r["probability"] < 0.5 and r["resolved_value"] == 0.0)
    )
    accuracy = correct / n * 100

    # By confidence bucket
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        buckets[_bucket(r["probability"])].append(r)

    def _bucket_stats(recs: list[dict]) -> dict:
        if not recs:
            return {"n": 0, "mean_brier": None, "accuracy": None, "mean_prob": None}
        b = [r["brier_score"] for r in recs if r["brier_score"] is not None]
        c = sum(
            1 for r in recs
            if (r["probability"] >= 0.5 and r["resolved_value"] == 1.0)
            or (r["probability"] < 0.5 and r["resolved_value"] == 0.0)
        )
        return {
            "n": len(recs),
            "mean_brier": sum(b) / len(b) if b else None,
            "accuracy": c / len(recs) * 100,
            "mean_prob": sum(r["probability"] for r in recs) / len(recs),
        }

    # By year
    years = sorted(set(r["year"] for r in records))
    by_year = {yr: _bucket_stats([r for r in records if r["year"] == yr]) for yr in years}

    # By ticker
    tickers = sorted(set(r["ticker"] for r in records))
    by_ticker = {t: _bucket_stats([r for r in records if r["ticker"] == t]) for t in tickers}

    # Confidence calibration: does high confidence actually mean more accurate?
    # Split into deciles by probability
    sorted_by_prob = sorted(records, key=lambda r: r["probability"])
    decile_size = max(1, n // 5)
    calibration_buckets = []
    for i in range(0, n, decile_size):
        chunk = sorted_by_prob[i:i + decile_size]
        if not chunk:
            continue
        avg_prob = sum(r["probability"] for r in chunk) / len(chunk)
        avg_outcome = sum(r["resolved_value"] for r in chunk if r["resolved_value"] is not None) / len(chunk)
        calibration_buckets.append({"mean_prob": avg_prob, "mean_outcome": avg_outcome, "n": len(chunk)})

    # Analogue quality: mean similarity score for correct vs incorrect predictions
    correct_sims = []
    incorrect_sims = []
    for r in records:
        analogues = r["analogues_used"]
        if not analogues:
            continue
        mean_sim = sum(a.get("similarity_score", 0) for a in analogues) / len(analogues)
        is_correct = (
            (r["probability"] >= 0.5 and r["resolved_value"] == 1.0)
            or (r["probability"] < 0.5 and r["resolved_value"] == 0.0)
        )
        if is_correct:
            correct_sims.append(mean_sim)
        else:
            incorrect_sims.append(mean_sim)

    analogue_quality = {
        "mean_sim_correct": sum(correct_sims) / len(correct_sims) if correct_sims else None,
        "mean_sim_incorrect": sum(incorrect_sims) / len(incorrect_sims) if incorrect_sims else None,
        "n_no_analogues": sum(1 for r in records if not r["analogues_used"]),
    }

    # Worst predictions (highest Brier = most wrong)
    worst = sorted(
        [r for r in records if r["brier_score"] is not None],
        key=lambda r: r["brier_score"],
        reverse=True,
    )[:5]

    # Best predictions
    best = sorted(
        [r for r in records if r["brier_score"] is not None],
        key=lambda r: r["brier_score"],
    )[:5]

    return {
        "n": n,
        "mean_brier": mean_brier,
        "mean_prob": mean_prob,
        "base_rate": base_rate,
        "accuracy": accuracy,
        "by_bucket": {
            "high": _bucket_stats(buckets["high"]),
            "med": _bucket_stats(buckets["med"]),
            "low": _bucket_stats(buckets["low"]),
        },
        "by_year": by_year,
        "by_ticker": by_ticker,
        "calibration_buckets": calibration_buckets,
        "analogue_quality": analogue_quality,
        "worst": worst,
        "best": best,
        "years": years,
        "tickers": tickers,
    }


def _regime_analysis(by_year: dict) -> dict:
    """Identify regime sensitivity: how much does Brier vary by year?"""
    scored_years = {yr: s for yr, s in by_year.items() if s["mean_brier"] is not None}
    if len(scored_years) < 2:
        return {"regime_sensitive": False, "gap": 0}

    briers = [(yr, s["mean_brier"]) for yr, s in scored_years.items()]
    briers.sort(key=lambda x: x[1])
    best_yr, best_brier = briers[0]
    worst_yr, worst_brier = briers[-1]
    gap = worst_brier - best_brier

    return {
        "regime_sensitive": gap > 0.05,
        "gap": gap,
        "best_year": best_yr,
        "best_brier": best_brier,
        "worst_year": worst_yr,
        "worst_brier": worst_brier,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _fmt_brier(b: float | None) -> str:
    if b is None:
        return "  N/A  "
    flag = ""
    if b < 0.20:
        flag = " ✓✓"
    elif b < 0.25:
        flag = " ✓"
    elif b > 0.30:
        flag = " ✗"
    return f"{b:.4f}{flag}"


def _print_report(run_name: str, config_name: str, stats: dict, regime: dict) -> None:
    w = 68
    print()
    print("=" * w)
    print(f"  EVALUATION REPORT")
    print(f"  Run    : {run_name}")
    print(f"  Config : {config_name}")
    print(f"  Random baseline: 0.2500  |  Target: <0.2000 in High bucket")
    print("=" * w)

    print(f"\n  OVERALL")
    print(f"  Predictions : {stats['n']}")
    print(f"  Brier score : {_fmt_brier(stats['mean_brier'])}  (random = 0.2500)")
    print(f"  Accuracy    : {stats['accuracy']:.1f}%  (>50% → predicted YES)")
    print(f"  Mean output : {stats['mean_prob']*100:.1f}%  (base rate: {stats['base_rate']*100:.1f}% beat SPY)")

    bias_gap = stats["mean_prob"] - (stats["base_rate"] or 0.5)
    if abs(bias_gap) > 0.05:
        direction = "bearish" if bias_gap < 0 else "bullish"
        print(f"  ⚠  Bias: mean output {bias_gap*100:+.1f}% vs base rate — model skews {direction}")
    else:
        print(f"  ✓  No directional bias detected")

    print(f"\n  BY CONFIDENCE BUCKET")
    for label, key in [("High (≥70%)", "high"), ("Med  (50-70%)", "med"), ("Low  (<50%) ", "low")]:
        b = stats["by_bucket"][key]
        if b["n"] == 0:
            print(f"  {label}: n=0")
            continue
        print(
            f"  {label}: n={b['n']:<3}  Brier={_fmt_brier(b['mean_brier'])}  "
            f"Accuracy={b['accuracy']:.1f}%  mean_prob={b['mean_prob']*100:.1f}%"
        )

    print(f"\n  BY YEAR  (regime sensitivity)")
    for yr, ys in sorted(stats["by_year"].items()):
        if ys["n"] == 0:
            continue
        print(
            f"  {yr}: n={ys['n']:<3}  Brier={_fmt_brier(ys['mean_brier'])}  "
            f"Accuracy={ys['accuracy']:.1f}%"
        )
    if regime["regime_sensitive"]:
        print(
            f"  ⚠  Regime sensitive: {regime['worst_year']} Brier {regime['worst_brier']:.4f} "
            f"vs {regime['best_year']} {regime['best_brier']:.4f}  (gap={regime['gap']:.4f})"
        )
    else:
        print(f"  ✓  Regime stable (year-to-year Brier gap < 0.05)")

    print(f"\n  CONFIDENCE CALIBRATION  (does high prob → high outcome rate?)")
    for cb in stats["calibration_buckets"]:
        bar_len = int(cb["mean_outcome"] * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"  prob≈{cb['mean_prob']*100:4.1f}%  actual={cb['mean_outcome']*100:5.1f}%  "
            f"{bar}  n={cb['n']}"
        )

    aq = stats["analogue_quality"]
    print(f"\n  ANALOGUE QUALITY")
    if aq["mean_sim_correct"] is not None:
        print(f"  Mean similarity — correct predictions : {aq['mean_sim_correct']:.3f}")
        print(f"  Mean similarity — wrong predictions  : {aq['mean_sim_incorrect']:.3f}")
        gap = (aq["mean_sim_correct"] or 0) - (aq["mean_sim_incorrect"] or 0)
        if gap > 0.02:
            print(f"  ✓  Better analogues correlate with correct predictions (gap={gap:.3f})")
        else:
            print(f"  ⚠  No clear link between analogue quality and accuracy (gap={gap:.3f})")
    if aq["n_no_analogues"]:
        print(f"  ⚠  {aq['n_no_analogues']} predictions had no analogues (corpus miss)")

    print(f"\n  WORST PREDICTIONS  (highest Brier = most wrong)")
    for r in stats["worst"]:
        direction = "→YES" if r["probability"] >= 0.5 else "→NO"
        actual = "YES" if r["resolved_value"] == 1.0 else "NO"
        print(
            f"  {r['ticker']:6s} {r['year']}  prob={r['probability']*100:.0f}%{direction}  "
            f"actual={actual}  Brier={r['brier_score']:.4f}"
        )

    print(f"\n  BY TICKER  (hardest to predict)")
    ticker_briers = [
        (t, s) for t, s in stats["by_ticker"].items()
        if s["mean_brier"] is not None and s["n"] >= 2
    ]
    ticker_briers.sort(key=lambda x: x[1]["mean_brier"], reverse=True)
    for t, s in ticker_briers[:5]:
        print(f"  {t:6s}: Brier={_fmt_brier(s['mean_brier'])}  n={s['n']}")

    print("=" * w)


def _compare_runs(stats_a: dict, name_a: str, stats_b: dict, name_b: str) -> None:
    print(f"\n  COMPARISON: {name_a}  vs  {name_b}")
    print(f"  {'Metric':<28}  {'Run A':>8}  {'Run B':>8}  {'Δ':>8}")
    print(f"  {'-'*28}  {'-'*8}  {'-'*8}  {'-'*8}")

    def _row(label, a_val, b_val, fmt=".4f", lower_is_better=True):
        a_s = f"{a_val:{fmt}}" if a_val is not None else "   N/A"
        b_s = f"{b_val:{fmt}}" if b_val is not None else "   N/A"
        if a_val is not None and b_val is not None:
            delta = b_val - a_val
            symbol = "✓" if (delta < 0) == lower_is_better else "✗"
            d_s = f"{delta:+.4f} {symbol}"
        else:
            d_s = "   N/A"
        print(f"  {label:<28}  {a_s:>8}  {b_s:>8}  {d_s:>10}")

    _row("Overall Brier", stats_a["mean_brier"], stats_b["mean_brier"])
    _row("Accuracy (%)", stats_a["accuracy"], stats_b["accuracy"], fmt=".1f", lower_is_better=False)
    _row("Mean prediction (%)", stats_a["mean_prob"] * 100, stats_b["mean_prob"] * 100, fmt=".1f", lower_is_better=False)

    for bucket_key, label in [("high", "High bucket Brier"), ("med", "Med bucket Brier")]:
        _row(label, stats_a["by_bucket"][bucket_key]["mean_brier"], stats_b["by_bucket"][bucket_key]["mean_brier"])

    for yr in sorted(set(list(stats_a["by_year"].keys()) + list(stats_b["by_year"].keys()))):
        a_yr = stats_a["by_year"].get(yr, {}).get("mean_brier")
        b_yr = stats_b["by_year"].get(yr, {}).get("mean_brier")
        _row(f"  {yr} Brier", a_yr, b_yr)

    print()


# ---------------------------------------------------------------------------
# Claude diagnosis
# ---------------------------------------------------------------------------


def _build_diagnosis_prompt(stats: dict, regime: dict, run_name: str, config_name: str) -> str:
    """Format stats into a structured prompt for Claude to diagnose."""

    aq = stats["analogue_quality"]
    calibration_rows = "\n".join(
        f"  prob≈{cb['mean_prob']*100:.1f}% → actual {cb['mean_outcome']*100:.1f}%  (n={cb['n']})"
        for cb in stats["calibration_buckets"]
    )
    by_year_rows = "\n".join(
        f"  {yr}: Brier={s['mean_brier']:.4f}  acc={s['accuracy']:.1f}%  mean_prob={s['mean_prob']*100:.1f}%"
        for yr, s in sorted(stats["by_year"].items())
        if s["n"] > 0
    )
    worst_rows = "\n".join(
        f"  {r['ticker']} {r['year']}: prob={r['probability']*100:.0f}%  actual={'YES' if r['resolved_value']==1.0 else 'NO'}  "
        f"Brier={r['brier_score']:.4f}  rationale_start={r['rationale'][:120]!r}"
        for r in stats["worst"]
    )

    return f"""You are analyzing the results of a walk-forward stock outperformance prediction system.

The system predicts whether a stock will outperform the S&P 500 over a 12-month period.
It uses a fundamentals corpus (historical annual snapshots) + Claude LLM synthesis.
Loss function: Brier score. Lower = better. Random baseline = 0.2500. Target = <0.2000 in High bucket.

## Run: {run_name}  (config: {config_name})

### Overall
- N predictions: {stats['n']}
- Mean Brier: {stats['mean_brier']:.4f}
- Accuracy: {stats['accuracy']:.1f}%
- Mean model output: {stats['mean_prob']*100:.1f}%  (base rate: {(stats['base_rate'] or 0.5)*100:.1f}% beat SPY)

### By confidence bucket
- High (≥70%): n={stats['by_bucket']['high']['n']}  Brier={stats['by_bucket']['high']['mean_brier']}  acc={stats['by_bucket']['high']['accuracy']}%
- Med (50-70%): n={stats['by_bucket']['med']['n']}  Brier={stats['by_bucket']['med']['mean_brier']}  acc={stats['by_bucket']['med']['accuracy']}%
- Low (<50%):   n={stats['by_bucket']['low']['n']}  Brier={stats['by_bucket']['low']['mean_brier']}  acc={stats['by_bucket']['low']['accuracy']}%

### By year (regime sensitivity)
{by_year_rows}
Regime sensitive: {regime['regime_sensitive']}  (gap={regime['gap']:.4f})

### Confidence calibration  (does high prob → high actual outcome rate?)
{calibration_rows}

### Analogue quality
- Mean similarity for correct predictions: {aq['mean_sim_correct']}
- Mean similarity for wrong predictions: {aq['mean_sim_incorrect']}
- Predictions with no analogues: {aq['n_no_analogues']}

### Worst predictions (most wrong)
{worst_rows}

## Your task

Based on the above, identify:
1. **Root cause** (1-3 most likely issues causing high Brier score)
2. **Highest-leverage single change** to improve the system
3. **Specific next experiment** — one concrete, runnable change (prompt edit, corpus change, config change, or new signal) with expected impact
4. **Quick wins** — any easy fixes apparent from the worst predictions

Be specific and actionable. Prioritize changes testable in <5 minutes on the 10-ticker eval set.
"""


def _run_diagnosis(stats: dict, regime: dict, run_name: str, config_name: str) -> None:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n  ⚠  ANTHROPIC_API_KEY not set — skipping Claude diagnosis")
        return

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_diagnosis_prompt(stats, regime, run_name, config_name)

    print("\n" + "=" * 68)
    print("  CLAUDE DIAGNOSIS")
    print("=" * 68)
    print()

    start = time.monotonic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.monotonic() - start

    print(response.content[0].text)
    tokens = response.usage.input_tokens + response.usage.output_tokens
    cost = tokens / 1_000_000 * 3.0  # rough sonnet pricing
    print(f"\n  [{elapsed:.1f}s  {tokens} tokens  ~${cost:.3f}]")
    print("=" * 68)


# ---------------------------------------------------------------------------
# Fast eval runner
# ---------------------------------------------------------------------------


def _run_eval_set(config_path: str) -> str:
    """Run the fixed eval set and return the run name."""
    import subprocess

    tickers = ",".join(EVAL_TICKERS)
    years = ",".join(str(y) for y in EVAL_YEARS)
    cmd = [
        "python", "scripts/backtest_stocks.py",
        "--config", config_path,
        "--tickers", tickers,
        "--years", years,
    ]
    logger.info("Running eval set: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        raise RuntimeError(f"backtest_stocks.py exited with code {result.returncode}")

    # Return a name prefix to query
    return f"backtest-stock-v1-live-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate backtest results")
    parser.add_argument(
        "--run",
        default=None,
        help="Run name prefix or full run_id to evaluate (default: most recent backtest)",
    )
    parser.add_argument(
        "--compare",
        default=None,
        help="Second run name/id to compare against --run",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Ask Claude to diagnose results and propose the highest-leverage next change",
    )
    parser.add_argument(
        "--run-eval",
        action="store_true",
        help="Run the fixed 10-ticker eval set first, then evaluate",
    )
    parser.add_argument(
        "--config",
        default="experiments/stock-v1.yaml",
        help="Config path (used with --run-eval)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON to stdout (for programmatic consumption by Claude)",
    )
    args = parser.parse_args()

    from src.db.session import get_session

    if args.run_eval:
        run_name_hint = _run_eval_set(args.config)
    else:
        run_name_hint = args.run

    with get_session() as session:
        run_result, run_config = _load_run(session, run_name_hint)
        logger.info("Evaluating run: %s  (id: %s)", run_config.name, run_result.id[:8])

        records = _load_predictions(session, run_result.id)
        if not records:
            print(f"\nNo scored predictions found for run '{run_config.name}'.")
            print("Make sure backtest_stocks.py completed and wrote Score rows.")
            return

        stats = _compute_stats(records)
        regime = _regime_analysis(stats["by_year"])

        if args.json:
            import json as _json
            # Slim down for JSON: remove per-ticker noise, keep decision-relevant fields
            output = {
                "run_name": run_config.name,
                "run_id": run_result.id,
                "n": stats["n"],
                "mean_brier": stats["mean_brier"],
                "accuracy": stats["accuracy"],
                "mean_prob": stats["mean_prob"],
                "base_rate": stats["base_rate"],
                "bias_gap": stats["mean_prob"] - (stats["base_rate"] or 0.5),
                "by_bucket": {
                    k: {kk: vv for kk, vv in v.items()} for k, v in stats["by_bucket"].items()
                },
                "by_year": {
                    str(yr): {kk: vv for kk, vv in s.items()}
                    for yr, s in stats["by_year"].items()
                },
                "regime": regime,
                "analogue_quality": stats["analogue_quality"],
                "calibration_buckets": stats["calibration_buckets"],
                "worst": [
                    {
                        "ticker": r["ticker"],
                        "year": r["year"],
                        "probability": r["probability"],
                        "resolved_value": r["resolved_value"],
                        "brier_score": r["brier_score"],
                        "rationale_start": r["rationale"][:200],
                    }
                    for r in stats["worst"]
                ],
                "prompt_version": records[0]["prompt_version"] if records else None,
                "model": records[0]["model"] if records else None,
            }
            print(_json.dumps(output, indent=2))
            return

        _print_report(run_config.name, run_config.name, stats, regime)

        if args.compare:
            run_result_b, run_config_b = _load_run(session, args.compare)
            records_b = _load_predictions(session, run_result_b.id)
            if records_b:
                stats_b = _compute_stats(records_b)
                _compare_runs(stats, run_config.name, stats_b, run_config_b.name)
            else:
                print(f"\nNo predictions found for comparison run '{run_config_b.name}'")

    if args.diagnose:
        _run_diagnosis(stats, regime, run_config.name, run_config.name)


if __name__ == "__main__":
    main()
