"""Generate 12-month outperformance forecasts for S&P 500 stocks.

Fetches current fundamental snapshots via yfinance, retrieves structurally
similar historical cases from the fundamentals corpus in ChromaDB, and asks
Claude to estimate the probability each stock outperforms the S&P 500 over
the next 12 months.

Usage:
    python scripts/stock_forecast.py --config experiments/stock-v1.yaml
    python scripts/stock_forecast.py --config experiments/stock-v1.yaml --tickers AAPL,MSFT,NVDA
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

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
# Analyst consensus → community probability
# ---------------------------------------------------------------------------


def _analyst_upside_to_prob(
    current_price: float | None,
    target_mean: float | None,
) -> float | None:
    """Convert analyst mean price target to an outperformance probability.

    Analysts are systematically bullish — the average S&P 500 stock has a
    consensus target ~15% above current price even in flat markets. We correct
    for this by treating 15% analyst upside as the break-even (50%), not 10%.

    Mapping:
        relative_upside = analyst_upside - ANALYST_BIAS (0.15)
        prob = clamp(0.5 + relative_upside * 1.0, 0.05, 0.95)

    So a 15% target → 50%, 30% target → 65%, 5% target → 40%.
    Multiplier of 1.0 (not 2.0) keeps large targets (40-60% upside common
    for large caps) from saturating at 95% and washing out differentiation.
    """
    if current_price is None or target_mean is None:
        return None
    if float(current_price) <= 0:
        return None
    analyst_upside = (float(target_mean) - float(current_price)) / float(current_price)
    # Correct for known sell-side optimism bias: ~15% avg upside regardless of outlook
    _ANALYST_BIAS = 0.15
    relative_upside = analyst_upside - _ANALYST_BIAS
    prob = 0.5 + relative_upside * 1.0
    return round(min(0.95, max(0.05, prob)), 4)


# ---------------------------------------------------------------------------
# Format current profile for prompt
# ---------------------------------------------------------------------------


def _format_current_profile(snap: dict) -> str:
    """Build a human-readable current profile string for the prompt."""

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

    def _fmt_cap(val) -> str:
        if val is None:
            return "N/A"
        v = float(val)
        if v >= 1e12:
            return f"${v / 1e12:.1f}T"
        if v >= 1e9:
            return f"${v / 1e9:.1f}B"
        return f"${v / 1e6:.1f}M"

    lines = [
        f"Ticker: {snap['ticker']} | Company: {snap['company_name']} | Sector: {snap.get('sector', 'Unknown')}",
        f"Current price: {_fmt_price(snap.get('current_price'))} | "
        f"52w range: {_fmt_price(snap.get('price_52w_low'))}–{_fmt_price(snap.get('price_52w_high'))}",
        f"P/E ratio: {_fmt_pe(snap.get('pe_ratio'))} | "
        f"Market cap: {_fmt_cap(snap.get('market_cap'))}",
        f"Revenue growth (TTM): {_fmt_pct(snap.get('revenue_growth_ttm'))} | "
        f"Gross margin: {_fmt_pct(snap.get('gross_margin'))}",
        f"Analyst consensus: {snap.get('analyst_recommendation', 'N/A')} | "
        f"Mean target: {_fmt_price(snap.get('analyst_target_mean'))} | "
        f"Analysts covering: {snap.get('analyst_count', 'N/A')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _upsert_stock_question(snap: dict, session) -> "Question":  # noqa: F821
    """Insert or return an existing Question row for a stock forecast."""
    from src.db.models import Question

    question_id = f"stock-12m-{snap['ticker']}"
    existing = session.query(Question).filter_by(id=question_id).first()
    if existing:
        return existing

    community_prob = _analyst_upside_to_prob(
        snap.get("current_price"),
        snap.get("analyst_target_mean"),
    )

    resolution_date = datetime.now(timezone.utc) + timedelta(days=365)

    q = Question(
        id=question_id,
        text=(
            f"Will {snap['ticker']} ({snap['company_name']}) outperform the "
            f"S&P 500 over the next 12 months?"
        ),
        resolution_value=None,
        resolution_date=resolution_date,
        community_probability=community_prob,
        tags=["stock", "fundamentals", "12m-outperformance"],
    )
    session.add(q)
    session.flush()
    return q


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _bar(pct: float, width: int = 20) -> str:
    filled = int(round(pct / 5))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _fmt_price(val) -> str:
    if val is None:
        return "N/A"
    return f"${float(val):,.2f}"


def _fmt_cap(val) -> str:
    if val is None:
        return "N/A"
    v = float(val)
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    return f"${v / 1e6:.1f}M"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forecast 12-month S&P 500 outperformance for top stocks"
    )
    parser.add_argument("--config", required=True, help="Path to experiment YAML")
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated list of tickers (default: top-50 S&P 500)",
    )
    args = parser.parse_args()

    # Deferred imports so sys.path manipulation takes effect first
    from src.config.schema import load_config
    from src.db.session import get_session
    from src.db.models import Prediction, RunConfig as RunConfigModel, RunResult
    from src.ingestion.fundamentals import TOP_50_SP500, get_current_snapshots

    config = load_config(args.config)

    if config.predictor_type != "ml":
        raise ValueError("Only predictor_type='ml' is supported. Update your config YAML.")
    if not config.model_path:
        raise ValueError("predictor_type='ml' requires model_path in the config YAML")

    # Resolve ticker list
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = TOP_50_SP500
    if config.max_questions:
        tickers = tickers[: config.max_questions]

    from src.synthesis.stock_predictor import StockMLPredictor, confidence_label
    ml_predictor = StockMLPredictor(config.model_path)
    logger.info("ML mode — LightGBM predictor loaded from %s", config.model_path)

    logger.info("Fetching current fundamental snapshots for %d tickers …", len(tickers))
    snapshots = get_current_snapshots(tickers)
    logger.info("%d snapshots fetched", len(snapshots))

    resolution_date_str = (
        datetime.now(timezone.utc) + timedelta(days=365)
    ).strftime("%Y-%m-%d")

    with get_session() as session:
        run_name = f"stock-v1-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        run_config_row = RunConfigModel(
            name=run_name,
            top_k=config.top_k,
            similarity_type=config.similarity_type,
            embedding_weight=config.embedding_weight,
            metadata_weight=config.metadata_weight,
            metadata_filters=config.metadata_filters,
            prompt_version=config.prompt_version,
            model=config.model,
            max_questions=len(snapshots),
            dry_run=config.dry_run,
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

        print("\n" + "=" * 70)
        print(f"  STOCK FORECAST — {run_name}  (run_id: {run_id[:8]}...)")
        print("=" * 70)

        all_signals: list[dict] = []

        for i, snap in enumerate(snapshots, start=1):
            ticker = snap["ticker"]
            company_name = snap["company_name"]

            question = _upsert_stock_question(snap, session)
            session.commit()

            # Skip if already predicted in this run
            existing_pred = session.query(Prediction).filter_by(
                run_id=run_id, question_id=question.id
            ).first()
            if existing_pred:
                continue

            try:
                # Pure ML path — no API calls, no ChromaDB
                analogues = []
                pred_result = ml_predictor.predict(snap)

                prediction = Prediction(
                    run_id=run_id,
                    question_id=question.id,
                    probability_estimate=pred_result.probability,
                    rationale=pred_result.rationale,
                    analogues_used=[
                        {
                            "event_id": a.event.id,
                            "similarity_score": a.similarity_score,
                        }
                        for a in analogues
                    ],
                    prompt_version=config.prompt_version,
                    model=config.model,
                    tokens_used=pred_result.tokens_used,
                    latency_ms=pred_result.latency_ms,
                )
                session.add(prediction)
                session.commit()

                # --- Build display strings ---
                our_pct = pred_result.probability * 100
                our_bar = _bar(our_pct)

                community_prob = question.community_probability
                if community_prob is not None:
                    com_pct = community_prob * 100
                    com_bar = _bar(com_pct)
                    delta_str = f"  Δ {our_pct - com_pct:+.1f}%"
                    analyst_label = snap.get("analyst_recommendation", "N/A")
                    analyst_count = snap.get("analyst_count", 0)
                    target_str = _fmt_price(snap.get("analyst_target_mean"))
                    analyst_display = (
                        f"(target: {target_str}, {analyst_count} analysts, {analyst_label})"
                    )
                else:
                    com_pct = None
                    com_bar = "N/A"
                    delta_str = ""
                    analyst_display = "(no analyst data)"

                # Confidence display (ML path)
                conf_level = confidence_label(pred_result.probability)
                conf_pct = (abs(pred_result.probability - 0.5) + 0.5) * 100
                conf_bar = _bar(conf_pct)
                conf_str = f"{conf_level:6s}  {conf_bar}  (ML confidence)"

                # Price range display
                lo = snap.get("price_52w_low")
                hi = snap.get("price_52w_high")
                rng = f"{_fmt_price(lo)}–{_fmt_price(hi)}" if lo and hi else "N/A"
                pe_display = (
                    f"{float(snap['pe_ratio']):.1f}x"
                    if snap.get("pe_ratio")
                    else "N/A"
                )
                cap_display = _fmt_cap(snap.get("market_cap"))
                price_display = _fmt_price(snap.get("current_price"))

                # First sentence of rationale
                rationale_short = pred_result.rationale.split(".")[0].strip() + "."

                # Track for summary
                all_signals.append({
                    "ticker": ticker,
                    "our_pct": our_pct,
                    "com_pct": com_pct,
                    "delta": (our_pct - com_pct) if com_pct is not None else None,
                    "conf_pct": conf_pct,
                    "conf_level": conf_level,
                })

                # Flag high-confidence divergences inline
                signal_flag = ""
                is_high_conf = conf_level == "high"
                if com_pct is not None and is_high_conf and abs(our_pct - com_pct) >= 15:
                    signal_flag = "  *** SIGNAL ***"

                print(f"\n[{i}/{len(snapshots)}] {ticker} — {company_name}{signal_flag}")
                print(
                    f"  Current     :  {price_display}  |  52w: {rng}  |  "
                    f"P/E: {pe_display}  |  Mkt cap: {cap_display}"
                )
                if com_pct is not None:
                    print(f"  Analyst     :  {com_pct:5.1f}%  {com_bar}  {analyst_display}")
                print(f"  Our model   :  {our_pct:5.1f}%  {our_bar}{delta_str}")
                print(f"  Confidence  :  {conf_str}")
                print(f"  Reasoning   : {rationale_short}")
                print(f"  URL         : https://finance.yahoo.com/quote/{ticker}")

            except Exception:
                logger.error(
                    "Error forecasting %s — skipping", ticker, exc_info=True
                )
                session.rollback()
                continue

        # Finalise run record
        run_result.completed_at = datetime.now(timezone.utc)
        run_result.n_predictions = (
            session.query(Prediction).filter_by(run_id=run_id).count()
        )
        session.add(run_result)
        session.commit()

        # --- Divergence signals ---
        # High confidence + large delta from analyst consensus = interesting signal
        # High confidence: conf_level == "high" (|prob - 0.5| >= 0.20)
        signals = [
            s for s in all_signals
            if s["delta"] is not None
            and abs(s["delta"]) >= 15
            and s.get("conf_level") == "high"
        ]
        signals.sort(key=lambda s: abs(s["delta"]), reverse=True)

        # --- Calibration summary ---
        scored = [s for s in all_signals if s["com_pct"] is not None]
        mean_model = sum(s["our_pct"] for s in all_signals) / len(all_signals) if all_signals else None
        mean_analyst = sum(s["com_pct"] for s in scored) / len(scored) if scored else None
        n_bullish = sum(1 for s in all_signals if s["our_pct"] > 50)
        n_bearish = sum(1 for s in all_signals if s["our_pct"] < 50)

        print("\n" + "=" * 70)
        print(f"  {run_result.n_predictions} predictions stored  (run_id: {run_id[:8]}...)")
        print(f"\n  CALIBRATION CHECK")
        if mean_model is not None:
            print(f"  Mean model output  : {mean_model:.1f}%  (expected ~50% at base rate)")
            print(f"  Mean analyst prob  : {mean_analyst:.1f}%  (after bias correction)")
            print(f"  Bullish / Bearish  : {n_bullish} / {n_bearish}")
            if mean_model < 40:
                print(
                    f"\n  ⚠  BIAS WARNING: mean model output {mean_model:.1f}% is well below 50%."
                    f"\n     ~50% of stocks outperform the index by definition."
                    f"\n     High bearish skew suggests systematic bias, not signal."
                    f"\n     Treat bearish divergences with extra skepticism this run."
                )
            elif mean_model > 60:
                print(
                    f"\n  ⚠  BIAS WARNING: mean model output {mean_model:.1f}% is well above 50%."
                    f"\n     Bullish skew may reflect corpus or prompt bias."
                )
            else:
                print(f"\n  ✓  Mean output near 50% — no obvious directional bias detected.")

        # --- Model's own rankings (independent of analyst comparison) ---
        ranked = sorted(all_signals, key=lambda s: s["our_pct"], reverse=True)
        if ranked:
            print(f"\n  MODEL RANKINGS  (model's view, independent of analyst consensus)")
            top5 = ranked[:5]
            bot5 = ranked[-5:]
            print("  Most bullish:")
            for s in top5:
                print(
                    f"    {s['ticker']:6s}  {s['our_pct']:5.1f}%  {_bar(s['our_pct'])}  conf {s['conf_pct']:.0f}%"
                )
            print("  Most bearish:")
            for s in reversed(bot5):
                print(
                    f"    {s['ticker']:6s}  {s['our_pct']:5.1f}%  {_bar(s['our_pct'])}  conf {s['conf_pct']:.0f}%"
                )

        # --- High-confidence divergences ---
        if signals:
            print(f"\n  HIGH-CONFIDENCE DIVERGENCES ({len(signals)} found)")
            print("  conf≥50% + |Δ|≥15% — where the model most disagrees with analysts")
            print("  NOTE: verify against calibration check above before acting on signals\n")
            for s in signals:
                direction = "BULLISH" if s["delta"] > 0 else "BEARISH"
                print(
                    f"  {s['ticker']:6s}  model {s['our_pct']:5.1f}%  "
                    f"analysts {s['com_pct']:5.1f}%  "
                    f"Δ {s['delta']:+.1f}%  conf {s['conf_pct']:.0f}%  "
                    f"← {direction}"
                )
        else:
            print("\n  No high-confidence divergences (conf≥50%, |Δ|≥15%) found.")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
