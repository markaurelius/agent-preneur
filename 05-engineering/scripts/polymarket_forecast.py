"""Fetch open binary markets from Polymarket and generate predictions.

Usage:
    python scripts/polymarket_forecast.py --config experiments/v3-gdelt.yaml
    python scripts/polymarket_forecast.py --config experiments/v3-gdelt.yaml --limit 20
    python scripts/polymarket_forecast.py --config experiments/v3-gdelt.yaml --domain finance

No API key required — Polymarket Gamma API is public.

Predictions are stored in the DB using the same Prediction/RunConfig/RunResult
models as forecast.py, so they can be reviewed or scored later via resolve.py
if Polymarket adds a resolution mechanism.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

POLYMARKET_API = "https://gamma-api.polymarket.com"

# ---------------------------------------------------------------------------
# Domain keyword filters (client-side — Polymarket API has no search param)
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "geopolitics": [
        "war", "invasion", "military", "ukraine", "russia", "china", "taiwan",
        "iran", "nato", "sanctions", "ceasefire", "nuclear", "conflict",
        "treaty", "troops", "missile", "coup", "assassination", "geopolit",
        "diplomat", "referendum", "un security council", "peacekeeping",
        "airstrike", "armed forces", "occupied", "sovereignty",
    ],
    "finance": [
        "federal reserve", "fed funds", "fomc", "interest rate", "inflation",
        "gdp", "recession", "unemployment", "cpi", "pce", "earnings per share",
        "bitcoin", "nasdaq", "s&p 500", "dow jones", "bond yield", "treasury",
        "ipo", "merger", "acquisition", "bankruptcy", "rate cut", "rate hike",
        "basis points", "quantitative", "balance sheet",
    ],
}


def _matches_domain(question_text: str, domain: str) -> bool:
    """Return True if question_text contains at least one domain keyword."""
    keywords = _DOMAIN_KEYWORDS.get(domain, _DOMAIN_KEYWORDS["geopolitics"])
    lower = question_text.lower()
    return any(kw in lower for kw in keywords)


# ---------------------------------------------------------------------------
# Polymarket Gamma API helpers
# ---------------------------------------------------------------------------


def _fetch_open_markets(limit: int, domain: str) -> list[dict]:
    """Fetch open binary markets from Polymarket, filtered to domain keywords.

    Paginates through the API until *limit* domain-relevant markets are found
    or no more pages are available. Sleeps 0.2 s between page requests.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=365)

    results: list[dict] = []
    offset = 0
    page_size = 100  # Polymarket max per page

    while len(results) < limit:
        params = {
            "closed": "false",
            "limit": page_size,
            "offset": offset,
        }
        try:
            resp = httpx.get(
                f"{POLYMARKET_API}/markets",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            page: list[dict] = resp.json()
        except Exception as exc:
            logger.warning("Polymarket API request failed (offset=%d): %s", offset, exc)
            break

        if not page:
            logger.debug("Empty page at offset=%d — stopping pagination.", offset)
            break

        for market in page:
            parsed = _parse_market(market, now, cutoff)
            if parsed is None:
                continue
            if _matches_domain(parsed["text"], domain):
                results.append(parsed)
                if len(results) >= limit:
                    break

        logger.info(
            "Page offset=%d: %d raw markets, %d domain matches so far",
            offset, len(page), len(results),
        )

        if len(page) < page_size:
            # Last page
            break

        offset += page_size
        time.sleep(0.2)

    return results[:limit]


def _extract_yes_probability(market: dict) -> float | None:
    """Extract the YES (index 0) probability from outcomePrices."""
    outcome_prices = market.get("outcomePrices")
    if not outcome_prices:
        return None
    try:
        # outcomePrices is a list of strings: ["0.72", "0.28"]
        # index 0 = YES price
        if isinstance(outcome_prices, list) and len(outcome_prices) >= 1:
            return float(outcome_prices[0])
        # Sometimes it arrives as a JSON-encoded string: "[\"0.72\",\"0.28\"]"
        if isinstance(outcome_prices, str):
            import json
            parsed = json.loads(outcome_prices)
            if isinstance(parsed, list) and len(parsed) >= 1:
                return float(parsed[0])
    except (ValueError, TypeError, Exception):
        pass
    return None


def _parse_market(market: dict, now: datetime, cutoff: datetime) -> dict | None:
    """Normalise a Polymarket API market dict to our internal format.

    Returns None if the market should be skipped (non-binary, no question text,
    end date outside the 12-month window, or missing required fields).
    """
    # Question text
    text = (market.get("question") or market.get("title") or "").strip()
    if not text:
        return None

    market_id = market.get("id")
    if not market_id:
        return None

    # End date — must be within 12 months
    end_raw = market.get("endDate") or market.get("end_date") or market.get("endDateIso")
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            if end_dt <= now or end_dt > cutoff:
                return None
        except ValueError:
            pass  # If we can't parse, keep the market anyway
    else:
        # No end date — skip to avoid forecasting markets with no resolution horizon
        return None

    # URL
    url = market.get("url") or ""
    if not url:
        slug = market.get("slug") or ""
        if slug:
            url = f"https://polymarket.com/{slug}"
        else:
            url = f"https://polymarket.com/event/{market_id}"

    # YES community probability
    community_prob = _extract_yes_probability(market)

    return {
        "id": f"polymarket-{market_id}",
        "polymarket_id": str(market_id),
        "text": text,
        "resolution_value": None,
        "resolution_date_raw": end_raw,
        "community_probability": community_prob,
        "url": url,
        "liquidity": _safe_float(market.get("liquidity")),
        "volume": _safe_float(market.get("volume")),
    }


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _upsert_polymarket_question(parsed: dict, session) -> "Question":  # noqa: F821
    """Insert a Polymarket market as a Question row if not already present."""
    from src.db.models import Question

    existing = session.query(Question).filter_by(id=parsed["id"]).first()
    if existing:
        return existing

    resolution_date = None
    if parsed.get("resolution_date_raw"):
        try:
            resolution_date = datetime.fromisoformat(
                parsed["resolution_date_raw"].replace("Z", "+00:00")
            )
        except ValueError:
            pass

    q = Question(
        id=parsed["id"],
        text=parsed["text"],
        resolution_value=None,
        resolution_date=resolution_date,
        community_probability=parsed.get("community_probability"),
        tags=["polymarket", "live"],
    )
    session.add(q)
    session.flush()
    return q


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forecast open Polymarket binary markets"
    )
    parser.add_argument("--config", required=True, help="Path to experiment YAML")
    parser.add_argument(
        "--limit", type=int, default=50, help="Max domain-relevant markets to forecast"
    )
    parser.add_argument(
        "--domain",
        default="geopolitics",
        choices=list(_DOMAIN_KEYWORDS.keys()),
        help="Keyword domain filter (default: geopolitics)",
    )
    args = parser.parse_args()

    # Imports deferred so sys.path manipulation above takes effect first
    from src.config.schema import load_config
    from src.db.session import get_session
    from src.db.models import Prediction, RunConfig as RunConfigModel, RunResult
    from src.retrieval.retriever import retrieve_analogues
    from src.synthesis.predictor import synthesize_prediction

    import anthropic
    import chromadb

    config = load_config(args.config)

    # --domain CLI flag overrides the config file's domain when provided
    domain = args.domain

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    chroma_client = chromadb.PersistentClient(path=chroma_path)
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    logger.info(
        "Fetching open Polymarket %s markets (limit=%d) …", domain, args.limit
    )
    markets = _fetch_open_markets(limit=args.limit, domain=domain)
    logger.info("%d %s markets to forecast", len(markets), domain)

    if not markets:
        logger.warning(
            "No %s markets found closing within 12 months. "
            "Try --domain finance or increase --limit.",
            domain,
        )
        sys.exit(0)

    with get_session() as session:
        run_name = (
            f"polymarket-{domain}-"
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
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
            max_questions=len(markets),
            dry_run=False,
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
        print(f"  POLYMARKET FORECAST — {run_name}  (run_id: {run_id[:8]}...)")
        print("=" * 70)

        for i, parsed in enumerate(markets, start=1):
            question = _upsert_polymarket_question(parsed, session)
            session.commit()

            # Skip if already predicted in this run
            existing_pred = session.query(Prediction).filter_by(
                run_id=run_id, question_id=question.id
            ).first()
            if existing_pred:
                continue

            try:
                analogues = retrieve_analogues(
                    question, config, chroma_client, session, anthropic_client
                )
                pred_result = synthesize_prediction(
                    question, analogues, config, anthropic_client
                )

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
                our_bar = "█" * int(our_pct / 5) + "░" * (20 - int(our_pct / 5))

                community_prob = parsed.get("community_probability")
                if community_prob is not None:
                    com_pct = community_prob * 100
                    com_bar = "█" * int(com_pct / 5) + "░" * (20 - int(com_pct / 5))
                    delta = our_pct - com_pct
                    delta_str = f"  Δ {delta:+.1f}%"
                else:
                    com_pct = None
                    com_bar = "N/A"
                    delta_str = ""

                # Confidence = mean analogue similarity, or N/A for claude mode
                if analogues:
                    conf = sum(a.similarity_score for a in analogues) / len(analogues)
                    conf_pct = conf * 100
                    conf_bar = "█" * int(conf_pct / 5) + "░" * (20 - int(conf_pct / 5))
                    conf_str = f"{conf_pct:5.1f}%  {conf_bar}"
                else:
                    conf_str = "  N/A   (claude-generated analogues, no corpus)"

                # First sentence of rationale only
                rationale_short = pred_result.rationale.split(".")[0].strip() + "."

                print(f"\n[{i}/{len(markets)}] {question.text}")
                if com_pct is not None:
                    print(f"  Polymarket  : {com_pct:5.1f}%  {com_bar}")
                print(f"  Our model   : {our_pct:5.1f}%  {our_bar}{delta_str}")
                print(f"  Confidence  : {conf_str}")
                print(f"  Reasoning   : {rationale_short}")
                print(f"  URL         : {parsed['url']}")

            except Exception:
                logger.error(
                    "Error on market %s — skipping", question.id, exc_info=True
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

        print("\n" + "=" * 70)
        print(f"  {run_result.n_predictions} predictions stored  (run_id: {run_id[:8]}...)")
        print("  Predictions are in DB and can be scored later via resolve.py")
        print("  if Polymarket exposes a resolution endpoint.")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
