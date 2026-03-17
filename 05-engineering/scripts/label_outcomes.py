"""One-time script: use Claude to label historical event outcomes as binary.

Sets historical_events.outcome_binary = 1.0 (positive/escalatory outcome) or
0.0 (negative/de-escalatory outcome) based on the event's outcome text.

This is the one Claude-assisted step in the ML pipeline — run once per corpus.
Results are cached in the DB; re-runs skip already-labeled events.

Usage:
    python scripts/label_outcomes.py [--limit 500] [--dry-run] [--batch-size 20]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Prompt sent to Claude for each batch of outcomes
_SYSTEM = (
    "You are labeling historical geopolitical event outcomes as binary features for a forecasting model. "
    "For each event, return outcome_binary=1 if the primary action succeeded / conflict escalated / "
    "the more aggressive or assertive outcome occurred. Return 0 if it failed / de-escalated / "
    "the more cautious or status-quo outcome occurred. "
    "Return null only if the outcome is genuinely ambiguous or missing."
)

_TOOL = {
    "name": "submit_labels",
    "description": "Submit binary outcome labels for a batch of historical events",
    "input_schema": {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "outcome_binary": {
                            "type": ["number", "null"],
                            "description": "1=positive/escalatory, 0=negative/de-escalatory, null=ambiguous",
                        },
                    },
                    "required": ["event_id", "outcome_binary"],
                },
            }
        },
        "required": ["labels"],
    },
}


def _label_batch(client, events: list) -> list[dict]:
    """Ask Claude to label a batch of events. Returns [{event_id, outcome_binary}]."""
    lines = []
    for ev in events:
        lines.append(
            f"event_id: {ev.id}\n"
            f"description: {ev.description[:300]}\n"
            f"outcome: {ev.outcome or '(none)'}\n"
        )
    prompt = "Label the outcome_binary for each event below:\n\n" + "\n---\n".join(lines)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest model — labeling is simple
        max_tokens=1024,
        system=_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_labels"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_block = next(b for b in response.content if b.type == "tool_use")
    return tool_block.input.get("labels", [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Label historical event outcomes with Claude")
    parser.add_argument("--limit", type=int, default=None, help="Max events to label")
    parser.add_argument("--batch-size", type=int, default=20, help="Events per Claude call")
    parser.add_argument("--dry-run", action="store_true", help="Print first batch, no DB writes")
    args = parser.parse_args()

    import anthropic
    from tqdm import tqdm

    from src.db.session import get_session
    from src.db.models import HistoricalEvent

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    with get_session() as session:
        # Only fetch events that haven't been labeled yet
        unlabeled = (
            session.query(HistoricalEvent)
            .filter(HistoricalEvent.outcome_binary.is_(None))
            .filter(HistoricalEvent.outcome.isnot(None))
            .all()
        )

    if args.limit:
        unlabeled = unlabeled[: args.limit]

    logger.info("%d unlabeled events to process (batch_size=%d)", len(unlabeled), args.batch_size)

    if args.dry_run:
        batch = unlabeled[: args.batch_size]
        labels = _label_batch(client, batch)
        print(json.dumps(labels, indent=2))
        return

    labeled_count = 0
    batches = [unlabeled[i : i + args.batch_size] for i in range(0, len(unlabeled), args.batch_size)]

    for batch in tqdm(batches, desc="Labeling batches"):
        try:
            labels = _label_batch(client, batch)
        except Exception as exc:
            logger.warning("Batch failed: %s — skipping", exc)
            continue

        id_to_label = {item["event_id"]: item["outcome_binary"] for item in labels}

        with get_session() as session:
            for ev in batch:
                label = id_to_label.get(ev.id)
                if label is not None:
                    db_ev = session.get(HistoricalEvent, ev.id)
                    if db_ev is not None:
                        db_ev.outcome_binary = float(label)
                        labeled_count += 1
            session.commit()

    logger.info(
        "Done. Labeled %d / %d events (%.1f%%)",
        labeled_count,
        len(unlabeled),
        100 * labeled_count / len(unlabeled) if unlabeled else 0,
    )


if __name__ == "__main__":
    main()
