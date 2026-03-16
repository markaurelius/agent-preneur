"""CLI entry point for running the offline evaluation loop.

Usage:
    python scripts/run.py --config experiments/v1.yaml
    python scripts/run.py --config experiments/v1.yaml --dry-run --max-questions 10
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure the repo root (/app or wherever the project lives) is on sys.path so
# that `src` is importable when this script is run directly (e.g. from /app/scripts/).
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline forecasting evaluation loop."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to a YAML RunConfig file (e.g. experiments/v1.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Override config dry_run=True (no real API calls)",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=None,
        help="Override config max_questions",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Logging setup
    # ------------------------------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Load config (with CLI overrides)
    # ------------------------------------------------------------------
    try:
        from src.config.schema import load_config, RunConfig

        config = load_config(args.config)

        # Apply CLI overrides — rebuild since RunConfig is frozen
        overrides: dict = {}
        if args.dry_run:
            overrides["dry_run"] = True
        if args.max_questions is not None:
            overrides["max_questions"] = args.max_questions

        if overrides:
            config = RunConfig(**{**config.model_dump(), **overrides})

        logger.info(
            "Loaded config: name=%s dry_run=%s max_questions=%s",
            config.name,
            config.dry_run,
            config.max_questions,
        )
    except Exception:
        logger.exception("Failed to load config from %s", args.config)
        return 1

    # ------------------------------------------------------------------
    # Build external clients
    # ------------------------------------------------------------------
    try:
        import chromadb  # type: ignore[import]

        chroma_path = os.environ["CHROMA_PATH"]
        chroma_client = chromadb.PersistentClient(path=chroma_path)
        logger.info("ChromaDB client initialised at %s", chroma_path)
    except KeyError:
        logger.error("CHROMA_PATH environment variable is not set")
        return 1
    except Exception:
        logger.exception("Failed to initialise ChromaDB client")
        return 1

    try:
        import anthropic  # type: ignore[import]

        anthropic_client = anthropic.Anthropic()
        logger.info("Anthropic client initialised")
    except Exception:
        logger.exception("Failed to initialise Anthropic client")
        return 1

    # ------------------------------------------------------------------
    # Run the loop
    # ------------------------------------------------------------------
    try:
        from src.db.session import get_session
        from src.runner.offline_loop import run_offline_loop

        with get_session() as session:
            run_result = run_offline_loop(
                config=config,
                session=session,
                chroma_client=chroma_client,
                anthropic_client=anthropic_client,
            )
    except Exception:
        logger.exception("Fatal error during offline loop")
        return 1

    # ------------------------------------------------------------------
    # Print final summary
    # ------------------------------------------------------------------
    mean_brier = (
        f"{run_result.mean_brier_score:.4f}"
        if run_result.mean_brier_score is not None
        else "N/A"
    )
    cost = run_result.cost_usd if run_result.cost_usd is not None else 0.0

    print(
        f"\n=== Run complete ===\n"
        f"  run_id:          {run_result.id}\n"
        f"  n_predictions:   {run_result.n_predictions}\n"
        f"  mean_brier:      {mean_brier}  (use for relative comparison only — see compare.py)\n"
        f"  cost_usd:        ${cost:.6f}\n"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
