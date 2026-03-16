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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "src" / "synthesis" / "prompts"

# S&P 500 proxy
_SPY_TICKER = "SPY"

# Default years to back-test
_DEFAULT_YEARS = [2021, 2022, 2023, 2024]

# Sector median P/E map — must match fundamentals.py
_SECTOR_MEDIAN_PE = {
    "Technology": 28.0,
    "Healthcare": 22.0,
    "Financials": 13.0,
    "Consumer Discretionary": 25.0,
    "Consumer Staples": 22.0,
    "Industrials": 20.0,
    "Energy": 12.0,
    "Materials": 16.0,
    "Real Estate": 35.0,
    "Utilities": 18.0,
    "Communication Services": 20.0,
}


# ---------------------------------------------------------------------------
# Data helpers
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


def _pct(val) -> str:
    v = _safe(val)
    if v is None:
        return "N/A"
    return f"{float(v):+.1f}%"


def _fetch_price_at_jan1(ticker_obj, year: int) -> float | None:
    """Return the first available close price on or after Jan 1 of year."""
    try:
        history = ticker_obj.history(
            start=f"{year}-01-01",
            end=f"{year}-03-01",
            interval="1d",
        )
        if history.empty:
            return None
        return float(history["Close"].iloc[0])
    except Exception as exc:
        logger.debug("Price fetch error for %d: %s", year, exc)
        return None


def _fetch_annual_return(ticker_obj, year: int) -> float | None:
    """Return stock return for the calendar year: (price_jan1_year+1 / price_jan1_year) - 1."""
    p_start = _fetch_price_at_jan1(ticker_obj, year)
    p_end = _fetch_price_at_jan1(ticker_obj, year + 1)
    if p_start is None or p_end is None or p_start <= 0:
        return None
    return (p_end / p_start) - 1


def _fetch_spy_annual_return(spy_obj, year: int) -> float | None:
    """Return SPY return for the calendar year."""
    return _fetch_annual_return(spy_obj, year)


def _build_historical_snapshot(ticker_obj, ticker: str, year: int, info: dict) -> dict:
    """Build a 'current profile' dict using data available at Jan 1 of year.

    Mirrors the structure of get_current_snapshots() but anchored to a
    historical date so no future data leaks in.
    """
    import pandas as pd

    company_name = info.get("longName") or info.get("shortName") or ticker
    sector = info.get("sector", "Unknown")

    # Current price at Jan 1 of year
    current_price = _fetch_price_at_jan1(ticker_obj, year)

    # 52-week range: high/low over the prior 12 months
    price_52w_high: float | None = None
    price_52w_low: float | None = None
    try:
        hist_52w = ticker_obj.history(
            start=f"{year - 1}-01-01",
            end=f"{year}-01-15",
            interval="1d",
        )
        if not hist_52w.empty:
            price_52w_high = float(hist_52w["High"].max())
            price_52w_low = float(hist_52w["Low"].min())
    except Exception:
        pass

    # Revenue growth and gross margin: use financials for (year-1) vs (year-2)
    revenue_growth_ttm: float | None = None
    gross_margin: float | None = None
    try:
        fin = ticker_obj.financials
        if fin is not None and not fin.empty:
            fin = fin.T
            fin.index = fin.index.map(lambda d: d.year if hasattr(d, "year") else None)
            fin = fin[fin.index.notnull()]

            rev_key = None
            for k in ("Total Revenue", "Revenue", "Net Revenue"):
                if k in fin.columns:
                    rev_key = k
                    break

            gross_key = None
            for k in ("Gross Profit", "Gross Income"):
                if k in fin.columns:
                    gross_key = k
                    break

            target_year = year - 1
            prior_year = year - 2
            if rev_key and target_year in fin.index and prior_year in fin.index:
                rev_curr = _safe(fin.loc[target_year, rev_key])
                rev_prior = _safe(fin.loc[prior_year, rev_key])
                if rev_curr and rev_prior and float(rev_prior) != 0:
                    revenue_growth_ttm = round(
                        (float(rev_curr) / float(rev_prior) - 1) * 100, 2
                    )
                if gross_key and rev_curr and target_year in fin.index:
                    gross = _safe(fin.loc[target_year, gross_key])
                    if gross is not None and float(rev_curr) != 0:
                        gross_margin = round(float(gross) / float(rev_curr) * 100, 2)
    except Exception:
        pass

    # P/E at start of year: approximate from historical price + prior-year EPS
    pe_ratio: float | None = None
    try:
        fin = ticker_obj.financials
        if fin is not None and not fin.empty:
            fin_t = fin.T
            fin_t.index = fin_t.index.map(lambda d: d.year if hasattr(d, "year") else None)
            fin_t = fin_t[fin_t.index.notnull()]

            eps_key = None
            for k in ("Diluted EPS", "Basic EPS", "EPS"):
                if k in fin_t.columns:
                    eps_key = k
                    break

            target_eps_year = year - 1
            if eps_key and target_eps_year in fin_t.index:
                eps = _safe(fin_t.loc[target_eps_year, eps_key])
                if eps and float(eps) > 0 and current_price:
                    pe_ratio = round(float(current_price) / float(eps), 1)
    except Exception:
        pass

    # Momentum (12-1 month) as of Jan 1 of year
    momentum_12_1: float | None = None
    try:
        end_date = pd.Timestamp(f"{year}-02-01")
        start_date = pd.Timestamp(f"{year - 2}-01-01")
        hist_mom = ticker_obj.history(
            start=str(start_date.date()),
            end=str(end_date.date()),
            interval="1mo",
        )
        if hist_mom is not None and not hist_mom.empty:
            hist_mom = hist_mom[hist_mom.index < end_date]
            if len(hist_mom) >= 13:
                price_12m_ago = float(hist_mom["Close"].iloc[-13])
                price_1m_ago = float(hist_mom["Close"].iloc[-2])
                if price_1m_ago > 0:
                    momentum_12_1 = round((price_12m_ago / price_1m_ago - 1) * 100, 2)
    except Exception:
        pass

    # P/E vs sector
    pe_vs_sector: float | None = None
    if pe_ratio is not None and sector in _SECTOR_MEDIAN_PE:
        sector_median = _SECTOR_MEDIAN_PE[sector]
        if sector_median > 0:
            pe_vs_sector = round(float(pe_ratio) / sector_median, 3)

    # Macro regime for the prediction year
    from src.ingestion.fundamentals import fetch_macro_regime
    macro_regime = fetch_macro_regime(year)

    return {
        "ticker": ticker,
        "company_name": company_name,
        "current_price": current_price,
        "analyst_target_mean": None,    # not available historically
        "analyst_target_high": None,
        "analyst_target_low": None,
        "analyst_recommendation": "Hold",
        "revenue_growth_ttm": revenue_growth_ttm,
        "gross_margin": gross_margin,
        "pe_ratio": pe_ratio,
        "market_cap": None,
        "sector": sector,
        "price_52w_high": price_52w_high,
        "price_52w_low": price_52w_low,
        "analyst_count": 0,
        "momentum_12_1": momentum_12_1,
        "earnings_revision": "neutral",   # historical revisions not available
        "pe_vs_sector": pe_vs_sector,
        "roe": None,
        "debt_to_equity": None,
        "short_percent_float": None,
        "macro_regime": macro_regime,
    }


# ---------------------------------------------------------------------------
# Time-filtered analogue retrieval
# ---------------------------------------------------------------------------


def _retrieve_analogues_time_filtered(
    question,
    config,
    chroma_client,
    session,
    anthropic_client,
    cutoff_year: int,
    top_k_multiplier: int = 10,
):
    """Retrieve analogues, then discard any whose corpus date >= cutoff year.

    ChromaDB does not support range filters natively, so we over-fetch by
    top_k_multiplier and filter in Python.
    """
    from src.retrieval.retriever import retrieve_analogues

    # Temporarily inflate top_k to get enough candidates after date filtering
    wide_config = config.model_copy(update={"top_k": config.top_k * top_k_multiplier})

    all_analogues = retrieve_analogues(
        question, wide_config, chroma_client, session, anthropic_client
    )

    cutoff_date_str = f"{cutoff_year}-01-01"
    filtered = [
        a for a in all_analogues
        if (a.event.date or "") < cutoff_date_str
    ]

    # Return only top_k after filtering
    return filtered[:config.top_k]


# ---------------------------------------------------------------------------
# Profile formatter (shared with stock_forecast.py pattern)
# ---------------------------------------------------------------------------


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
# Synthesis (mirrors stock_forecast.py)
# ---------------------------------------------------------------------------


def _load_prompt_template(prompt_version: str) -> str:
    path = PROMPTS_DIR / f"{prompt_version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text()


def _format_analogues_block(analogues: list) -> str:
    lines = []
    for n, analogue in enumerate(analogues, start=1):
        event = analogue.event
        lines.append(
            f"[{n}]. {event.description}\n"
            f"Outcome: {event.outcome}\n"
            f"Similarity: {analogue.similarity_score:.2f}"
        )
    return "\n\n".join(lines) if lines else "[No historical analogues found in corpus.]"


def _synthesize_backtest_prediction(
    question_text: str,
    current_profile: str,
    resolution_date_str: str,
    analogues: list,
    config,
    anthropic_client,
    dry_run: bool = False,
) -> tuple[float, str, int, int]:
    """Return (probability, rationale, tokens_used, latency_ms)."""
    if dry_run:
        return 0.5, "[dry run]", 0, 0

    from src.synthesis.predictor import PredictionResult

    template = _load_prompt_template(config.prompt_version)
    analogues_block = _format_analogues_block(analogues)

    prompt = template.format(
        question_text=question_text,
        current_profile=current_profile,
        resolution_date=resolution_date_str,
        analogues_block=analogues_block,
    )

    tool = {
        "name": "submit_forecast",
        "description": "Submit your probability forecast and reasoning",
        "input_schema": {
            "type": "object",
            "properties": {
                "probability": {
                    "type": "number",
                    "description": (
                        "Probability 0.0–1.0 that this stock outperforms the S&P 500 "
                        "over the next 12 months"
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": "Reasoning referencing comparable cases",
                },
            },
            "required": ["probability", "rationale"],
        },
    }

    start = time.monotonic()
    response = anthropic_client.messages.create(
        model=config.model,
        max_tokens=1024,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_forecast"},
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    tool_block = next(b for b in response.content if b.type == "tool_use")
    raw_prob = float(tool_block.input["probability"])
    rationale = tool_block.input.get("rationale", "")
    probability = max(0.01, min(0.99, raw_prob))
    tokens_used = response.usage.input_tokens + response.usage.output_tokens

    return probability, rationale, tokens_used, elapsed_ms


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
    else:
        print("NOTE: Claude's training knowledge may leak for pre-2025 data.")
        print("Use for calibration measurement, not absolute accuracy claims.")
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
        help="Skip Claude API calls; return 0.5 for all predictions (validates pipeline)",
    )
    args = parser.parse_args()

    # Deferred imports so sys.path manipulation takes effect first
    from src.config.schema import load_config
    from src.db.session import get_session
    from src.db.models import Prediction, RunConfig as RunConfigModel, RunResult, Score
    from src.ingestion.fundamentals import TOP_50_SP500

    import anthropic
    import chromadb
    import yfinance as yf

    config = load_config(args.config)
    dry_run = config.dry_run or args.dry_run
    if args.dry_run and not config.dry_run:
        config = config.model_copy(update={"dry_run": True})

    # Resolve ticker list
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = TOP_50_SP500
    if config.max_questions:
        tickers = tickers[: config.max_questions]

    # Resolve year list
    if args.years:
        years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
    else:
        years = _DEFAULT_YEARS

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    if dry_run:
        anthropic_client = None
        logger.info("DRY RUN mode — Claude API calls skipped")
    else:
        anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    logger.info(
        "Back-testing %d tickers x %d years = up to %d predictions",
        len(tickers), len(years), len(tickers) * len(years),
    )

    # Pre-fetch SPY for benchmark returns
    logger.info("Fetching SPY data for benchmark returns ...")
    spy = yf.Ticker(_SPY_TICKER)

    # Cache SPY annual returns
    spy_returns: dict[int, float | None] = {}
    for yr in years:
        spy_returns[yr] = _fetch_spy_annual_return(spy, yr)
        logger.debug("SPY %d: %s", yr, spy_returns[yr])

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
            max_questions=len(tickers) * len(years),
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
        print(f"  Tickers: {len(tickers)}  |  Years: {years}")
        if dry_run:
            print("  MODE: DRY RUN (all predictions = 0.5)")
        print("=" * 64)

        scored_records: list[dict] = []
        skipped_no_data = 0

        # ---------------------------------------------------------------
        # Phase 1: Data preparation (sequential — yfinance + DB reads)
        # Build all work items before any API calls.
        # ---------------------------------------------------------------
        work_items: list[dict] = []

        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                info = t.info or {}
            except Exception as exc:
                logger.warning("Cannot fetch info for %s: %s — skipping", ticker, exc)
                continue

            for year in years:
                label = f"{ticker} {year}"

                stock_return = _fetch_annual_return(t, year)
                spy_return = spy_returns.get(year)
                if stock_return is None or spy_return is None:
                    skipped_no_data += 1
                    continue

                resolution = 1.0 if stock_return > spy_return else 0.0
                actual_delta_pct = (stock_return - spy_return) * 100

                try:
                    snap = _build_historical_snapshot(t, ticker, year, info)
                except Exception as exc:
                    logger.warning("%s — snapshot error: %s", label, exc)
                    skipped_no_data += 1
                    continue

                question = _upsert_backtest_question(snap, year, session)
                if question.resolution_value is None:
                    question.resolution_value = resolution
                session.commit()

                # Re-use if already predicted in this run
                existing_pred = session.query(Prediction).filter_by(
                    run_id=run_id, question_id=question.id
                ).first()
                if existing_pred:
                    prob = existing_pred.probability_estimate or 0.5
                    scored_records.append({
                        "ticker": ticker, "year": year,
                        "probability": prob, "resolution": resolution,
                        "brier_score": (prob - resolution) ** 2,
                        "actual_delta_pct": actual_delta_pct,
                    })
                    continue

                try:
                    analogues = _retrieve_analogues_time_filtered(
                        question=question, config=config,
                        chroma_client=chroma_client, session=session,
                        anthropic_client=anthropic_client, cutoff_year=year,
                    )
                except Exception as exc:
                    logger.warning("%s — retrieval error: %s", label, exc)
                    analogues = []

                work_items.append({
                    "ticker": ticker,
                    "year": year,
                    "question_id": question.id,
                    "question_text": question.text,
                    "current_profile": _format_current_profile(snap),
                    "resolution_date_str": f"{year + 1}-01-01",
                    "analogues": analogues,
                    "resolution": resolution,
                    "actual_delta_pct": actual_delta_pct,
                })

        logger.info(
            "Data prep complete. %d predictions to synthesize, %d already done, %d skipped.",
            len(work_items), len(scored_records), skipped_no_data,
        )

        # ---------------------------------------------------------------
        # Phase 2: Parallel synthesis (Claude API calls)
        # ---------------------------------------------------------------
        n_workers = config.workers
        total_work = len(work_items)
        completed_count = 0

        def _synthesize_item(item: dict) -> dict:
            probability, rationale, tokens_used, latency_ms = _synthesize_backtest_prediction(
                question_text=item["question_text"],
                current_profile=item["current_profile"],
                resolution_date_str=item["resolution_date_str"],
                analogues=item["analogues"],
                config=config,
                anthropic_client=anthropic_client,
                dry_run=dry_run,
            )
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
