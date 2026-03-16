"""Tests for the synthesis engine (predictor.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.schema import RunConfig
from src.synthesis.predictor import PredictionResult, synthesize_prediction


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_question(
    text: str = "Will country X invade country Y by end of 2025?",
    resolution_date: datetime | None = datetime(2025, 12, 31, tzinfo=timezone.utc),
) -> SimpleNamespace:
    return SimpleNamespace(
        id="q1",
        text=text,
        resolution_date=resolution_date,
        resolution_value=None,
        community_probability=None,
        tags=None,
    )


def _make_event(
    description: str = "Border skirmish between nations A and B in 1999",
    outcome: str = "Conflict escalated to full war within six months",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="e1",
        description=description,
        outcome=outcome,
        actors=None,
        event_type="conflict",
        date="1999-06-01",
        region="Eastern Europe",
        chroma_id=None,
    )


@dataclass
class _Analogue:
    """Minimal stand-in for src.retrieval.retriever.Analogue."""

    event: HistoricalEvent
    similarity_score: float
    features_used: dict


def _make_analogue(similarity_score: float = 0.85) -> _Analogue:
    return _Analogue(event=_make_event(), similarity_score=similarity_score, features_used={})


def _make_config(**kwargs) -> RunConfig:
    defaults = dict(name="test-run")
    defaults.update(kwargs)
    return RunConfig(**defaults)


def _make_tool_use_response(probability: float, rationale: str) -> MagicMock:
    """Build a mock Anthropic messages.create response with a tool_use block."""
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.input = {"probability": probability, "rationale": rationale}

    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 50

    response = MagicMock()
    response.content = [tool_use_block]
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_stub_without_api_call(self):
        config = _make_config(dry_run=True)
        client = MagicMock()
        question = _make_question()
        analogues = [_make_analogue()]

        result = synthesize_prediction(question, analogues, config, client)

        client.messages.create.assert_not_called()
        assert result.probability == 0.5
        assert result.rationale == "[dry run]"
        assert result.tokens_used == 0
        assert result.latency_ms == 0

    def test_dry_run_returns_prediction_result_type(self):
        config = _make_config(dry_run=True)
        result = synthesize_prediction(_make_question(), [], config, MagicMock())
        assert isinstance(result, PredictionResult)


class TestHappyPath:
    def test_returns_valid_prediction_result(self):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.72, "Strong historical precedent.")
        question = _make_question()
        analogues = [_make_analogue(0.85), _make_analogue(0.70)]

        result = synthesize_prediction(question, analogues, config, client)

        assert isinstance(result, PredictionResult)
        assert 0.0 <= result.probability <= 1.0
        assert isinstance(result.rationale, str) and len(result.rationale) > 0
        assert result.tokens_used == 150  # 100 + 50
        assert result.latency_ms >= 0

    def test_api_called_with_correct_model(self):
        config = _make_config(model="claude-opus-4-5")
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.6, "Reasoning here.")

        synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-opus-4-5"

    def test_tool_choice_forces_submit_forecast(self):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "Balanced.")

        synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "submit_forecast"}

    def test_tools_include_submit_forecast(self):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "Balanced.")

        synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        call_kwargs = client.messages.create.call_args[1]
        tool_names = [t["name"] for t in call_kwargs["tools"]]
        assert "submit_forecast" in tool_names


class TestProbabilityClamping:
    @pytest.mark.parametrize("raw,expected", [
        (0.0, 0.01),
        (0.005, 0.01),
        (1.0, 0.99),
        (1.5, 0.99),
        (0.5, 0.5),
        (0.01, 0.01),
        (0.99, 0.99),
    ])
    def test_probability_clamped(self, raw: float, expected: float):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(raw, "Rationale.")

        result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        assert result.probability == pytest.approx(expected)

    def test_probability_always_in_bounds(self):
        config = _make_config()
        client = MagicMock()
        for raw in [0.0, 0.3, 0.7, 1.0]:
            client.messages.create.return_value = _make_tool_use_response(raw, "R.")
            result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)
            assert 0.01 <= result.probability <= 0.99


class TestPromptSubstitution:
    def test_question_text_in_prompt(self):
        question_text = "Will the UN Security Council pass resolution Z?"
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        synthesize_prediction(_make_question(text=question_text), [_make_analogue()], config, client)

        call_kwargs = client.messages.create.call_args[1]
        messages = call_kwargs["messages"]
        prompt_content = messages[0]["content"]
        assert question_text in prompt_content

    def test_resolution_date_in_prompt(self):
        resolution_date = datetime(2026, 6, 15, tzinfo=timezone.utc)
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        synthesize_prediction(_make_question(resolution_date=resolution_date), [_make_analogue()], config, client)

        call_kwargs = client.messages.create.call_args[1]
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "2026-06-15" in prompt_content

    def test_analogue_description_in_prompt(self):
        description = "Unique event description for testing XYZ"
        analogue = _Analogue(
            event=_make_event(description=description),
            similarity_score=0.75,
            features_used={},
        )
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        synthesize_prediction(_make_question(), [analogue], config, client)

        call_kwargs = client.messages.create.call_args[1]
        prompt_content = call_kwargs["messages"][0]["content"]
        assert description in prompt_content

    def test_analogue_similarity_score_in_prompt(self):
        analogue = _Analogue(
            event=_make_event(),
            similarity_score=0.83,
            features_used={},
        )
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        synthesize_prediction(_make_question(), [analogue], config, client)

        call_kwargs = client.messages.create.call_args[1]
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "0.83" in prompt_content

    def test_analogue_outcome_in_prompt(self):
        outcome = "Distinctive outcome text for testing ABC"
        analogue = _Analogue(
            event=_make_event(outcome=outcome),
            similarity_score=0.60,
            features_used={},
        )
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        synthesize_prediction(_make_question(), [analogue], config, client)

        call_kwargs = client.messages.create.call_args[1]
        prompt_content = call_kwargs["messages"][0]["content"]
        assert outcome in prompt_content

    def test_null_resolution_date_shows_unknown(self):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        synthesize_prediction(_make_question(resolution_date=None), [_make_analogue()], config, client)

        call_kwargs = client.messages.create.call_args[1]
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "unknown" in prompt_content


class TestPromptTemplateLoading:
    def test_missing_template_raises_file_not_found(self):
        config = _make_config(prompt_version="nonexistent_version")
        client = MagicMock()

        with pytest.raises(FileNotFoundError) as exc_info:
            synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        assert "nonexistent_version" in str(exc_info.value)

    def test_custom_prompt_version_is_loaded(self, tmp_path, monkeypatch):
        """Verify that a custom prompt_version loads the correct template file."""
        custom_template = "Custom prompt: {question_text} {resolution_date} {analogues_block}"
        custom_prompts_dir = tmp_path / "prompts"
        custom_prompts_dir.mkdir()
        (custom_prompts_dir / "custom_v.txt").write_text(custom_template)

        import src.synthesis.predictor as predictor_module

        monkeypatch.setattr(predictor_module, "PROMPTS_DIR", custom_prompts_dir)

        config = _make_config(prompt_version="custom_v")
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)
        assert isinstance(result, PredictionResult)


class TestToolUseResponseParsing:
    def test_tool_use_probability_extracted(self):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.63, "Clear precedent found.")

        result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        assert result.probability == pytest.approx(0.63)

    def test_tool_use_rationale_extracted(self):
        expected_rationale = "This is a very specific rationale string 12345."
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, expected_rationale)

        result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        assert result.rationale == expected_rationale

    def test_tokens_used_summed_correctly(self):
        config = _make_config()
        client = MagicMock()
        response = _make_tool_use_response(0.5, "r")
        response.usage.input_tokens = 200
        response.usage.output_tokens = 75
        client.messages.create.return_value = response

        result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        assert result.tokens_used == 275

    def test_latency_ms_is_positive_integer(self):
        config = _make_config()
        client = MagicMock()
        client.messages.create.return_value = _make_tool_use_response(0.5, "r")

        result = synthesize_prediction(_make_question(), [_make_analogue()], config, client)

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0
