"""Synthesis engine: generates calibrated probability predictions using Claude."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.retrieval.retriever import Analogue

from src.config.schema import RunConfig
from src.db.models import Question

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class PredictionResult:
    probability: float  # 0.0–1.0
    rationale: str
    tokens_used: int
    latency_ms: int


def _load_prompt_template(prompt_version: str) -> str:
    """Load a prompt template by version name."""
    template_path = PROMPTS_DIR / f"{prompt_version}.txt"
    if not template_path.exists():
        raise FileNotFoundError(
            f"Prompt template not found: {template_path}. "
            f"Expected a file at src/synthesis/prompts/{prompt_version}.txt"
        )
    return template_path.read_text()


def _format_analogues_block(analogues: list) -> str:
    """Format a list of Analogue objects into the prompt block."""
    lines = []
    for n, analogue in enumerate(analogues, start=1):
        event = analogue.event
        similarity = analogue.similarity_score
        lines.append(
            f"[{n}]. {event.description}\n"
            f"Outcome: {event.outcome}\n"
            f"Similarity: {similarity:.2f}"
        )
    return "\n\n".join(lines)


def _synthesize_combined(
    question: Question,
    config: RunConfig,
    anthropic_client,
) -> PredictionResult:
    """Single-call retrieval + synthesis for similarity_type='claude'.

    Asks Claude to identify analogues AND estimate probability in one pass,
    halving API calls and latency compared to the two-step approach.
    """
    template = _load_prompt_template(config.prompt_version)
    resolution_date_str = (
        question.resolution_date.strftime("%Y-%m-%d")
        if question.resolution_date is not None
        else "unknown"
    )

    tool = {
        "name": "submit_forecast",
        "description": "Submit historical analogues and your probability forecast",
        "input_schema": {
            "type": "object",
            "properties": {
                "analogues": {
                    "type": "array",
                    "maxItems": config.top_k,
                    "description": "Historical events most structurally similar to this question",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title":       {"type": "string"},
                            "description": {"type": "string"},
                            "outcome":     {"type": "string"},
                        },
                        "required": ["title", "description", "outcome"],
                    },
                },
                "probability": {
                    "type": "number",
                    "description": "Probability 0.0–1.0 that the question resolves YES",
                },
                "rationale": {
                    "type": "string",
                    "description": "Reasoning linking the analogues to your probability estimate",
                },
            },
            "required": ["analogues", "probability", "rationale"],
        },
    }

    # Build an analogues_block placeholder instructing Claude to generate them
    analogues_placeholder = (
        f"[You will identify {config.top_k} historical analogues as part of your response. "
        f"Do NOT draw on knowledge of how this specific question resolved.]"
    )
    prompt = template.format(
        question_text=question.text,
        resolution_date=resolution_date_str,
        analogues_block=analogues_placeholder,
    )

    start = time.monotonic()
    response = anthropic_client.messages.create(
        model=config.model,
        max_tokens=2048,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_forecast"},
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    tool_block = next(b for b in response.content if b.type == "tool_use")
    raw_probability = float(tool_block.input["probability"])
    rationale = tool_block.input["rationale"]
    probability = max(0.01, min(0.99, raw_probability))
    tokens_used = response.usage.input_tokens + response.usage.output_tokens

    return PredictionResult(
        probability=probability,
        rationale=rationale,
        tokens_used=tokens_used,
        latency_ms=elapsed_ms,
    )


def synthesize_prediction(
    question: Question,
    analogues: list,  # list[Analogue]
    config: RunConfig,
    anthropic_client,  # anthropic.Anthropic
) -> PredictionResult:
    """Generate a calibrated probability estimate for a geopolitical question.

    Uses Claude with tool_use to enforce structured output containing a probability
    and rationale.
    """
    if config.dry_run:
        return PredictionResult(
            probability=0.5,
            rationale="[dry run]",
            tokens_used=0,
            latency_ms=0,
        )

    # Combined retrieval + synthesis in a single call for claude retrieval mode
    if config.similarity_type == "claude" and not analogues:
        return _synthesize_combined(question, config, anthropic_client)

    # Load and fill prompt template
    template = _load_prompt_template(config.prompt_version)
    resolution_date_str = (
        question.resolution_date.strftime("%Y-%m-%d")
        if question.resolution_date is not None
        else "unknown"
    )
    analogues_block = _format_analogues_block(analogues)
    prompt = template.format(
        question_text=question.text,
        resolution_date=resolution_date_str,
        analogues_block=analogues_block,
    )

    # Define structured-output tool
    tool = {
        "name": "submit_forecast",
        "description": "Submit your probability forecast and reasoning",
        "input_schema": {
            "type": "object",
            "properties": {
                "probability": {
                    "type": "number",
                    "description": "Probability 0.0–1.0 that question resolves YES",
                },
                "rationale": {
                    "type": "string",
                    "description": "Reasoning for this estimate",
                },
            },
            "required": ["probability", "rationale"],
        },
    }

    # Call Claude, measuring latency
    start = time.monotonic()
    response = anthropic_client.messages.create(
        model=config.model,
        max_tokens=1024,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_forecast"},
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Parse tool_use response
    tool_use_block = next(
        block for block in response.content if block.type == "tool_use"
    )
    tool_input = tool_use_block.input
    raw_probability: float = float(tool_input["probability"])
    rationale: str = tool_input["rationale"]

    # Clamp probability to [0.01, 0.99]
    probability = max(0.01, min(0.99, raw_probability))

    tokens_used = response.usage.input_tokens + response.usage.output_tokens

    return PredictionResult(
        probability=probability,
        rationale=rationale,
        tokens_used=tokens_used,
        latency_ms=elapsed_ms,
    )
