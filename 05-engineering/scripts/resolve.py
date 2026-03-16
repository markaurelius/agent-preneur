"""Check resolution status of live predictions and score them.

Usage:
    python scripts/resolve.py

Finds all questions in the DB that were created by forecast.py (tagged "live",
no resolution yet), checks the Metaculus API for their current status, and
scores any that have resolved.

NOTE: The Metaculus API only returns resolution values for questions you have
personally predicted on. Make sure you've submitted a prediction on Metaculus
for each question before running this script.

Set METACULUS_API_KEY in your .env file.
"""

import logging
import os
import sys

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


def _fetch_question_status(metaculus_id: int, token: str) -> dict | None:
    """Fetch a single question from Metaculus API. Returns None on error."""
    headers = {"Authorization": f"Token {token}"}
    try:
        resp = httpx.get(
            f"{METACULUS_API}/posts/{metaculus_id}/",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch metaculus id=%s: %s", metaculus_id, exc)
        return None


def _extract_resolution(api_row: dict) -> float | None:
    """Return 0.0 or 1.0 if resolved, None if still open."""
    status = api_row.get("status") or ""
    if status not in ("resolved", "closed"):
        return None

    resolution = api_row.get("resolution")
    if resolution is None:
        resolution = (api_row.get("question") or {}).get("resolution")
    if resolution in (1, 1.0, "yes", True):
        return 1.0
    if resolution in (0, 0.0, "no", False):
        return 0.0
    return None  # ambiguous / annulled


def main() -> None:
    token = os.environ.get("METACULUS_API_KEY", "").strip()
    if not token:
        logger.error("METACULUS_API_KEY not set. Add it to your .env file.")
        sys.exit(1)

    from src.db.session import get_session
    from src.db.models import Prediction, Question, Score
    from src.scoring.scorer import score_prediction

    with get_session() as session:
        # Find all live questions with no resolution yet
        live_questions = (
            session.query(Question)
            .filter(
                Question.resolution_value.is_(None),
                Question.tags.contains(["live"]),
            )
            .all()
        )

        if not live_questions:
            print("No unresolved live questions found. Run forecast.py first.")
            return

        logger.info("Checking %d unresolved live questions ...", len(live_questions))

        resolved_count = 0
        scored_count = 0

        for question in live_questions:
            # Extract Metaculus numeric ID from our "metaculus-12345" format
            qid_parts = question.id.split("-")
            if len(qid_parts) < 2 or not qid_parts[-1].isdigit():
                logger.debug("Cannot extract Metaculus ID from %s — skipping", question.id)
                continue

            metaculus_id = int(qid_parts[-1])
            api_row = _fetch_question_status(metaculus_id, token)
            if api_row is None:
                continue

            resolution = _extract_resolution(api_row)
            if resolution is None:
                logger.debug("Question %s still open — skipping", question.id)
                continue

            # Update question with resolution
            question.resolution_value = resolution
            session.flush()
            resolved_count += 1
            logger.info("Question %s resolved → %.0f", question.id, resolution)

            # Score all predictions for this question that don't have a score yet
            predictions = session.query(Prediction).filter_by(question_id=question.id).all()
            for prediction in predictions:
                already_scored = session.query(Score).filter_by(
                    prediction_id=prediction.id
                ).first()
                if already_scored:
                    continue

                score_result = score_prediction(prediction, question)
                score_row = Score(
                    prediction_id=prediction.id,
                    brier_score=score_result.brier_score,
                    resolved_value=score_result.resolved_value,
                    community_brier_score=score_result.community_brier_score,
                )
                session.add(score_row)
                scored_count += 1

            session.commit()

        # Summary
        print("\n" + "=" * 60)
        print(f"  Resolved this run : {resolved_count} questions")
        print(f"  Predictions scored: {scored_count}")

        if scored_count > 0:
            # Print Brier scores for newly scored predictions
            all_scores = (
                session.query(Score)
                .join(Prediction)
                .join(Question)
                .filter(Question.tags.contains(["live"]))
                .all()
            )
            brier_values = [s.brier_score for s in all_scores if s.brier_score is not None]
            if brier_values:
                mean_brier = sum(brier_values) / len(brier_values)
                print(f"\n  Live predictions scored so far: {len(brier_values)}")
                print(f"  Mean Brier (live):  {mean_brier:.4f}")
                print(f"  Offline baseline:   0.1558")
                print(f"  Random baseline:    0.2500")

                print("\n  Per-question breakdown:")
                for score in all_scores:
                    if score.brier_score is None:
                        continue
                    q = session.query(Question).filter_by(id=score.prediction.question_id).first()
                    pred = score.prediction
                    print(
                        f"    {q.text[:60]:<60}"
                        f"  p={pred.probability_estimate:.2f}"
                        f"  actual={score.resolved_value:.0f}"
                        f"  brier={score.brier_score:.4f}"
                    )

        print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
