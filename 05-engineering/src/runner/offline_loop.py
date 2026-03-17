"""Offline loop runner: batch execution engine for geopolitical forecasting runs."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy.orm import Session
from tqdm import tqdm

from src.config.schema import RunConfig
from src.db.models import Prediction, RunConfig as RunConfigModel, RunResult, Score
from src.db.models import Question
from src.ingestion.finance_filter import _is_finance
from src.retrieval.retriever import retrieve_analogues
from src.scoring.scorer import ScoreResult, compute_run_stats, score_prediction
from src.db.session import get_session as _default_get_session
from src.synthesis.predictor import synthesize_prediction
from src.synthesis.ml_predictor import AnaloguAggregator, MLPredictor

logger = logging.getLogger(__name__)


def run_offline_loop(
    config: RunConfig,
    session: Session,
    chroma_client,  # chromadb.ClientAPI
    anthropic_client,  # anthropic.Anthropic
    _worker_session_factory=None,  # injectable for testing; defaults to get_session
) -> RunResult:
    """Run the full offline evaluation loop.

    1. Persist the RunConfig to the DB and get a run_id.
    2. Create a RunResult row immediately.
    3. Fetch all resolved questions (optionally limited by config.max_questions).
    4. For each question: retrieve analogues, synthesize prediction, score it.
    5. After all questions: compute aggregate stats, update RunResult, and return it.
    """
    # ------------------------------------------------------------------
    # Step 1: Persist RunConfig
    # ------------------------------------------------------------------
    run_config_row = RunConfigModel(
        name=config.name,
        top_k=config.top_k,
        similarity_type=config.similarity_type,
        embedding_weight=config.embedding_weight,
        metadata_weight=config.metadata_weight,
        metadata_filters=config.metadata_filters,
        prompt_version=config.prompt_version,
        model=config.model,
        predictor_type=config.predictor_type,
        max_questions=config.max_questions,
        dry_run=config.dry_run,
    )
    session.add(run_config_row)
    session.commit()
    run_id_config = run_config_row.id
    logger.info("Persisted RunConfig id=%s name=%s", run_id_config, config.name)

    # ------------------------------------------------------------------
    # Step 2: Create RunResult row immediately
    # ------------------------------------------------------------------
    run_result = RunResult(
        config_id=run_id_config,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run_result)
    session.commit()
    run_id = run_result.id
    logger.info("Created RunResult id=%s", run_id)

    # ------------------------------------------------------------------
    # Step 3: Fetch resolved questions
    # ------------------------------------------------------------------
    all_questions = (
        session.query(Question)
        .filter(Question.resolution_value.isnot(None))
        .all()
    )
    # Filter by domain
    domain_filter = {"finance": _is_finance}.get(config.domain, _is_finance)
    questions = [q for q in all_questions if domain_filter(q.text or "")]
    logger.info(
        "domain=%s: filtered to %d questions out of %d total resolved",
        config.domain, len(questions), len(all_questions),
    )
    # Optionally restrict to questions resolving on or after a given year
    if config.min_resolution_year is not None:
        before = len(questions)
        questions = [
            q for q in questions
            if q.resolution_date and q.resolution_date.year >= config.min_resolution_year
        ]
        logger.info(
            "min_resolution_year=%d: kept %d questions (dropped %d)",
            config.min_resolution_year,
            len(questions),
            before - len(questions),
        )
    if config.max_questions is not None:
        questions = questions[: config.max_questions]

    total = len(questions)
    logger.info("Running against %d questions (max_questions=%s)", total, config.max_questions)

    # ------------------------------------------------------------------
    # Step 4: Per-question loop
    # ------------------------------------------------------------------

    # Initialise the predictor once — thread-safe (stateless after init)
    if config.predictor_type == "analogue_aggregator":
        _predictor = AnaloguAggregator()
    elif config.predictor_type == "ml":
        if not config.model_path:
            raise ValueError("predictor_type='ml' requires model_path in the config")
        _predictor = MLPredictor(config.model_path)
    else:
        _predictor = None  # will use synthesize_prediction (Claude)

    score_results: list[ScoreResult] = []
    score_lock = Lock()

    _get_session = _worker_session_factory or _default_get_session

    def _process_question(question: Question) -> ScoreResult | None:
        """Process a single question in its own DB session. Thread-safe."""
        with _get_session() as worker_session:
            # Idempotency check
            existing = (
                worker_session.query(Prediction)
                .filter_by(run_id=run_id, question_id=question.id)
                .first()
            )
            if existing is not None:
                if existing.score is not None and existing.score.brier_score is not None:
                    return ScoreResult(
                        brier_score=existing.score.brier_score,
                        resolved_value=existing.score.resolved_value or 0.0,
                        community_brier_score=existing.score.community_brier_score,
                    )
                return None

            analogues = retrieve_analogues(question, config, chroma_client, worker_session, anthropic_client)

            if _predictor is not None:
                pred_result = _predictor.predict(question, analogues)
            else:
                pred_result = synthesize_prediction(question, analogues, config, anthropic_client)

            prediction = Prediction(
                run_id=run_id,
                question_id=question.id,
                probability_estimate=pred_result.probability,
                rationale=pred_result.rationale,
                analogues_used=[
                    {"event_id": a.event.id, "similarity_score": a.similarity_score}
                    for a in analogues
                ],
                features=pred_result.features,
                prompt_version=config.prompt_version,
                model=config.model,
                tokens_used=pred_result.tokens_used,
                latency_ms=pred_result.latency_ms,
            )
            worker_session.add(prediction)
            worker_session.flush()

            score_result = score_prediction(prediction, question)
            worker_session.add(Score(
                prediction_id=prediction.id,
                brier_score=score_result.brier_score,
                resolved_value=score_result.resolved_value,
                community_brier_score=score_result.community_brier_score,
            ))
            worker_session.commit()
            return score_result

    pbar = tqdm(total=total, desc="Running predictions")
    workers = config.workers if hasattr(config, "workers") else 1

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_question, q): q for q in questions}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result is not None:
                    with score_lock:
                        score_results.append(result)
                        current_mean = sum(s.brier_score for s in score_results) / len(score_results)
                        pbar.set_description(f"Brier: {current_mean:.4f}")
            except Exception:
                q = futures[future]
                logger.error("Error processing question id=%s — skipping", q.id, exc_info=True)
            pbar.update(1)

    pbar.close()

    # ------------------------------------------------------------------
    # Step 5: Compute aggregate stats and update RunResult
    # ------------------------------------------------------------------
    # Re-query scores for this run to be safe (handles idempotency restarts)
    all_predictions = session.query(Prediction).filter_by(run_id=run_id).all()
    all_score_results: list[ScoreResult] = []
    total_tokens = 0

    for pred in all_predictions:
        if pred.score is not None and pred.score.brier_score is not None:
            all_score_results.append(
                ScoreResult(
                    brier_score=pred.score.brier_score,
                    resolved_value=pred.score.resolved_value or 0.0,
                    community_brier_score=pred.score.community_brier_score,
                )
            )
        if pred.tokens_used is not None:
            total_tokens += pred.tokens_used

    stats = compute_run_stats(all_score_results)
    cost_usd = max(0.0, total_tokens * 0.000003)

    run_result.mean_brier_score = stats["mean_brier"]
    run_result.median_brier_score = stats["median_brier"]
    run_result.n_predictions = stats["n"]
    run_result.completed_at = datetime.now(timezone.utc)
    run_result.cost_usd = cost_usd

    session.add(run_result)
    session.commit()

    logger.info(
        "Run id=%s complete: n_predictions=%d mean_brier=%s cost_usd=%.6f",
        run_id,
        stats["n"],
        f"{stats['mean_brier']:.4f}" if stats["mean_brier"] is not None else "N/A",
        cost_usd,
    )

    return run_result
