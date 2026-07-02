"""Tests for chairman synthesis attribution discipline."""

from unittest.mock import patch

import pytest

from backend.council import (
    build_run_status,
    stage3_synthesize_final,
    validate_chairman_attribution,
)
from mcp_server.server import format_brief_attribution_output, format_council_output


def test_validate_chairman_attribution_flags_uncited_verifiable_claims():
    validation = validate_chairman_attribution(
        "The API returns HTTP 200 [A]. UnsupportedAPI returns HTTP 500. "
        "Plain qualitative guidance can stand without a marker.",
        {"Response A": "alpha", "Response B": "beta"},
    )

    assert validation["checked_claim_count"] == 2
    assert validation["unattributed_claim_count"] == 1
    assert validation["unattributed_claims"] == ["UnsupportedAPI returns HTTP 500."]
    assert validation["summary"] == "1 verifiable chairman claim lacks [A] style council attribution."


def test_validate_chairman_attribution_allows_explicit_abstention():
    validation = validate_chairman_attribution(
        "No council member discussed HTTP status behavior.",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 0
    assert validation["unattributed_claim_count"] == 0


def test_validate_chairman_attribution_catches_named_entities_and_flags():
    validation = validate_chairman_attribution(
        "Use --outDir for builds. The fastest provider is Anthropic.",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 2
    assert validation["unattributed_claim_count"] == 2
    assert validation["unattributed_claims"] == [
        "Use --outDir for builds.",
        "The fastest provider is Anthropic.",
    ]


def test_validate_chairman_attribution_allows_marker_after_sentence_punctuation():
    validation = validate_chairman_attribution(
        "The API returns HTTP 200. [A]",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 1
    assert validation["unattributed_claim_count"] == 0


def test_validate_chairman_attribution_splits_semicolon_claims():
    validation = validate_chairman_attribution(
        "The API returns HTTP 200 [A]; UnsupportedAPI returns HTTP 500.",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 2
    assert validation["unattributed_claim_count"] == 1
    assert validation["unattributed_claims"] == ["UnsupportedAPI returns HTTP 500."]


def test_validate_chairman_attribution_splits_attributed_and_clause():
    validation = validate_chairman_attribution(
        "The API returns HTTP 200 [A] and UnsupportedAPI returns HTTP 500.",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 2
    assert validation["unattributed_claim_count"] == 1
    assert validation["unattributed_claims"] == ["UnsupportedAPI returns HTTP 500."]


def test_validate_chairman_attribution_requires_marker_at_claim_end():
    validation = validate_chairman_attribution(
        "The API returns HTTP 200 [A] and the API returns HTTP 500.",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 1
    assert validation["unattributed_claim_count"] == 1


def test_validate_chairman_attribution_checks_facts_after_abstention_but_clause():
    validation = validate_chairman_attribution(
        "No council member discussed HTTP status behavior, but the API returns HTTP 500.",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 1
    assert validation["unattributed_claim_count"] == 1
    assert validation["unattributed_claims"] == ["the API returns HTTP 500."]


def test_validate_chairman_attribution_ignores_fenced_code_blocks():
    validation = validate_chairman_attribution(
        "Example [A]:\n```python\ndef run_full_council():\n    return HTTP_500\n```",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 0
    assert validation["unattributed_claim_count"] == 0


def test_validate_chairman_attribution_ignores_markdown_headings():
    validation = validate_chairman_attribution(
        "## Final Answer\n\nThe API returns HTTP 200 [A].",
        {"Response A": "alpha"},
    )

    assert validation["checked_claim_count"] == 1
    assert validation["unattributed_claim_count"] == 0


@pytest.mark.asyncio
async def test_stage3_prompt_requires_attribution_and_returns_validation():
    chairman_messages = []

    async def fake_query_model(model, messages, **kwargs):
        chairman_messages.append(messages)
        return {
            "content": "Use `run_full_council()` for orchestration [A]. The API returns HTTP 500.",
            "_debug": {"ok": True},
        }

    with patch("backend.council.query_model", side_effect=fake_query_model):
        stage3, debug = await stage3_synthesize_final(
            "How does orchestration work?",
            [
                {"model": "alpha", "response": "Use run_full_council() for orchestration."},
                {"model": "beta", "response": "Stage 2 ranks responses."},
            ],
            [],
            {"Response A": "alpha", "Response B": "beta"},
        )

    prompt = chairman_messages[0][0]["content"]
    assert "Attribution discipline" in prompt
    assert "Every verifiable claim" in prompt
    assert "using markers like [A], [B], or [A, B]" in prompt
    assert "[A, C]" not in prompt

    assert stage3["attribution"]["unattributed_claim_count"] == 1
    assert "The API returns HTTP 500." in stage3["attribution"]["unattributed_claims"]
    assert debug["attribution_validation"] == stage3["attribution"]


@pytest.mark.asyncio
async def test_stage3_prompt_uses_only_available_attribution_labels():
    chairman_messages = []

    async def fake_query_model(model, messages, **kwargs):
        chairman_messages.append(messages)
        return {"content": "Final answer [A].", "_debug": {"ok": True}}

    with patch("backend.council.query_model", side_effect=fake_query_model):
        await stage3_synthesize_final(
            "Question?",
            [{"model": "alpha", "response": "Alpha answer."}],
            [],
            {"Response A": "alpha"},
        )

    prompt = chairman_messages[0][0]["content"]
    assert "using markers like [A]." in prompt
    assert "[B]" not in prompt


def test_mcp_full_output_renders_attribution_key_and_warning():
    output = format_council_output(
        [{"model": "alpha", "response": "Alpha answer"}],
        {
            "label_to_model": {"Response A": "alpha", "Response B": "beta"},
            "aggregate_rankings": [],
        },
        {
            "model": "chairman",
            "response": "The API returns HTTP 500.",
            "attribution": {
                "unattributed_claim_count": 1,
                "unattributed_claims": ["The API returns HTTP 500."],
                "summary": "1 verifiable chairman claim lacks [A] style council attribution.",
            },
        },
    )

    assert "### Attribution Key" in output
    assert "`[A]` = alpha" in output
    assert "### Attribution Warning" in output
    assert "The API returns HTTP 500." in output


def test_mcp_brief_output_renders_attribution_key_and_warning():
    output = format_brief_attribution_output(
        {"label_to_model": {"Response A": "alpha", "Response B": "beta"}},
        {
            "attribution": {
                "unattributed_claim_count": 1,
                "unattributed_claims": ["The API returns HTTP 500."],
                "summary": "1 verifiable chairman claim lacks [A] style council attribution.",
            }
        },
    )

    assert "Attribution key: `[A]` = alpha; `[B]` = beta" in output
    assert "Attribution warning:" in output
    assert "The API returns HTTP 500." in output


def test_run_status_surfaces_chairman_attribution_validation():
    validation = {
        "unattributed_claim_count": 1,
        "summary": "1 verifiable chairman claim lacks [A] style council attribution.",
    }

    run_status = build_run_status(
        {
            "successful_council_models": 2,
            "failed_council_models": 0,
            "stages": {
                "stage1": {
                    "requested_models": 2,
                    "successful_models": 2,
                    "failed_models_count": 0,
                    "failed_models": [],
                },
                "stage3": {
                    "requested_models": 1,
                    "successful_models": 1,
                    "failed_models_count": 0,
                    "failed_models": [],
                    "attribution_validation": validation,
                },
            },
        }
    )

    assert run_status["chairman_attribution"] == validation
