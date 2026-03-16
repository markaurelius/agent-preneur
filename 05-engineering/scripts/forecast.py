"""Fetch open geopolitics questions from Metaculus and generate predictions.

Usage:
    python scripts/forecast.py --config experiments/v1.yaml
    python scripts/forecast.py --config experiments/v1.yaml --limit 10

After running, visit each question URL printed below and submit your own
prediction on Metaculus. This is required so the Metaculus API will return
resolution values once the question closes.

Set METACULUS_API_KEY in your .env file.
"""

import argparse
import logging
import os
import sys
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

METACULUS_API = "https://www.metaculus.com/api"


# ---------------------------------------------------------------------------
# Metaculus API helpers
# ---------------------------------------------------------------------------


_SEARCH_TERMS = {
    "geopolitics": [
        "war", "invasion", "military", "sanctions", "nuclear", "ceasefire",
        "geopolitics", "nato", "ukraine", "taiwan", "iran", "conflict",
    ],
    "finance": [
        "federal reserve", "interest rate", "inflation", "recession",
        "gdp", "unemployment", "bitcoin", "stock market", "yield curve",
        "earnings", "fed funds",
    ],
}


def _is_binary(row: dict) -> bool:
    """Return True only for binary (Yes/No) questions."""
    q = row.get("question") or {}
    # Metaculus v3 API nests type inside question{}
    qtype = q.get("type") or row.get("type") or ""
    return qtype.lower() == "binary"


def _fetch_open_questions(token: str, limit: int, domain: str = "geopolitics") -> list[dict]:
    """Fetch open binary questions from Metaculus search."""
    headers = {"Authorization": f"Token {token}"}
    seen_ids: set = set()
    results: list[dict] = []
    search_terms = _SEARCH_TERMS.get(domain, _SEARCH_TERMS["geopolitics"])

    now = datetime.now(timezone.utc)
    one_year = now + timedelta(days=365)

    for term in search_terms:
        if len(results) >= limit * 3:
            break
        params = {
            "status": "open",
            "type": "forecast",
            "limit": 20,
            "search": term,
            "scheduled_resolve_time__gt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scheduled_resolve_time__lt": one_year.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            resp = httpx.get(
                f"{METACULUS_API}/posts/", headers=headers, params=params, timeout=30
            )
            resp.raise_for_status()
            for row in resp.json().get("results", []):
                rid = row.get("id")
                if rid and rid not in seen_ids and _is_binary(row):
                    seen_ids.add(rid)
                    results.append(row)
        except Exception as exc:
            logger.warning("Search term '%s' failed: %s", term, exc)

    return results


def _extract_community_prob(row: dict) -> float | None:
    """Pull the current Metaculus community probability from an API row."""
    q = row.get("question") or {}

    # v3 API: question.aggregations.recency_weighted.latest.means[0]
    for container in (q, row):
        try:
            rw = container["aggregations"]["recency_weighted"]
            # prefer "latest" snapshot, fall back to last history entry
            latest = rw.get("latest") or (rw.get("history") or [None])[-1]
            if latest:
                means = latest.get("means") or []
                if means:
                    return float(means[0])
        except (KeyError, TypeError, IndexError):
            pass

    # Older API shape: community_prediction.full.q2
    try:
        return float(row["community_prediction"]["full"]["q2"])
    except (KeyError, TypeError):
        pass

    return None


def _parse_api_row(row: dict) -> dict | None:
    """Normalise a Metaculus API result row to our internal format."""
    text = (
        row.get("title")
        or (row.get("question") or {}).get("title")
        or ""
    )
    if not text:
        return None

    mid = row.get("id")
    if not mid:
        return None
    qid = f"metaculus-{mid}"

    close_raw = (
        row.get("scheduled_resolve_time")
        or row.get("scheduled_close_time")
        or (row.get("question") or {}).get("scheduled_resolve_time")
    )

    # Drop questions resolving outside the next 12 months
    if close_raw:
        try:
            close_dt = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if close_dt <= now or close_dt > now + timedelta(days=365):
                return None
        except ValueError:
            pass

    url = (
        row.get("url")
        or row.get("page_url")
        or f"https://www.metaculus.com/questions/{mid}/"
    )

    return {
        "id": qid,
        "metaculus_id": mid,
        "text": text,
        "resolution_value": None,
        "resolution_date_raw": close_raw,
        "community_probability": _extract_community_prob(row),
        "url": url,
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _upsert_live_question(parsed: dict, session) -> "Question":  # noqa: F821
    from src.db.models import Question
    from src.ingestion.metaculus import _parse_date

    existing = session.query(Question).filter_by(id=parsed["id"]).first()
    if existing:
        return existing

    q = Question(
        id=parsed["id"],
        text=parsed["text"],
        resolution_value=None,
        resolution_date=_parse_date(parsed["resolution_date_raw"]),
        community_probability=None,
        tags=["live"],
    )
    session.add(q)
    session.flush()
    return q


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Forecast open Metaculus questions")
    parser.add_argument("--config", required=True, help="Path to experiment YAML")
    parser.add_argument("--limit", type=int, default=50, help="Max questions to fetch")
    args = parser.parse_args()

    token = os.environ.get("METACULUS_API_KEY", "").strip()
    if not token:
        logger.error("METACULUS_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    from src.config.schema import load_config
    from src.db.session import get_session
    from src.db.models import Prediction, RunConfig as RunConfigModel, RunResult
    from src.ingestion.metaculus import _is_geopolitics
    from src.ingestion.finance_filter import _is_finance
    from src.retrieval.retriever import retrieve_analogues
    from src.synthesis.predictor import synthesize_prediction

    import anthropic
    import chromadb

    config = load_config(args.config)
    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    chroma_client = chromadb.PersistentClient(path=chroma_path)
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    domain_filter = {"geopolitics": _is_geopolitics, "finance": _is_finance}.get(
        config.domain, _is_geopolitics
    )

    # Fetch open binary questions from Metaculus
    logger.info("Fetching open binary %s questions from Metaculus ...", config.domain)
    raw_rows = _fetch_open_questions(token, limit=args.limit * 5, domain=config.domain)
    logger.info("Received %d binary rows from API", len(raw_rows))


    parsed_rows = [_parse_api_row(r) for r in raw_rows]
    domain_rows = [
        p for p in parsed_rows
        if p and domain_filter(p["text"])
    ][: args.limit]

    if not domain_rows:
        logger.warning("No %s questions found in the fetched batch.", config.domain)
        sys.exit(0)

    logger.info("%d %s questions to forecast", len(domain_rows), config.domain)

    with get_session() as session:
        # Create a run record for this forecast session
        run_name = f"live-{config.domain}-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        run_config_row = RunConfigModel(
            name=run_name,
            top_k=config.top_k,
            similarity_type=config.similarity_type,
            embedding_weight=config.embedding_weight,
            metadata_weight=config.metadata_weight,
            metadata_filters=config.metadata_filters,
            prompt_version=config.prompt_version,
            model=config.model,
            max_questions=len(domain_rows),
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
        print(f"  LIVE FORECAST RUN — {run_name}  (run_id: {run_id[:8]}...)")
        print("=" * 70)

        for i, parsed in enumerate(domain_rows, start=1):
            question = _upsert_live_question(parsed, session)
            session.commit()

            # Skip if already predicted in any run
            existing_pred = session.query(Prediction).filter_by(
                run_id=run_id, question_id=question.id
            ).first()
            if existing_pred:
                continue

            try:
                analogues = retrieve_analogues(question, config, chroma_client, session, anthropic_client)
                pred_result = synthesize_prediction(question, analogues, config, anthropic_client)

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

                # Print result
                our_pct = pred_result.probability * 100
                community_prob = parsed.get("community_probability")

                our_bar  = "█" * int(our_pct / 5) + "░" * (20 - int(our_pct / 5))
                if community_prob is not None:
                    com_pct = community_prob * 100
                    com_bar = "█" * int(com_pct / 5) + "░" * (20 - int(com_pct / 5))
                    delta = our_pct - com_pct
                    delta_str = f"  Δ {delta:+.1f}%"
                else:
                    com_pct = None
                    com_bar = "N/A"
                    delta_str = ""

                print(f"\n[{i}/{len(domain_rows)}] {question.text}")
                if com_pct is not None:
                    print(f"  Metaculus   : {com_pct:5.1f}%  {com_bar}")
                print(f"  Our model   : {our_pct:5.1f}%  {our_bar}{delta_str}")
                print(f"  Rationale   : {pred_result.rationale[:200].strip()}")
                print(f"  URL         : {parsed['url']}")

            except Exception:
                logger.error("Error on question %s — skipping", question.id, exc_info=True)
                session.rollback()
                continue

        # Mark run complete (no Brier score — questions haven't resolved)
        run_result.completed_at = datetime.now(timezone.utc)
        run_result.n_predictions = (
            session.query(Prediction).filter_by(run_id=run_id).count()
        )
        session.add(run_result)
        session.commit()

        print("\n" + "=" * 70)
        print(f"  {run_result.n_predictions} predictions stored.")
        print("  Visit each URL above and submit your prediction on Metaculus.")
        print("  Then run `python scripts/resolve.py` to score resolved questions.")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
