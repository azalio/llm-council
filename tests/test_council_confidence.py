"""Tests for council ranking confidence and low-confidence surfacing."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.council import (
    build_persisted_message_metadata,
    compute_council_confidence,
    run_full_council,
    stage3_synthesize_final,
)
from mcp_server.server import format_council_output


def test_compute_council_confidence_marks_unanimous_rankings_normal():
    confidence = compute_council_confidence(
        [
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B"},
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B"},
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B"},
        ],
        {"Response A": "alpha", "Response B": "beta"},
    )

    assert confidence["available"] is True
    assert confidence["low_confidence"] is False
    assert confidence["status"] == "normal"
    assert confidence["top1_stability"] == 1.0
    assert confidence["rank_agreement"] == 1.0
    assert confidence["disagreement_score"] == 0.0
    assert confidence["top_model"] == "alpha"


def test_compute_council_confidence_marks_split_top_votes_low():
    confidence = compute_council_confidence(
        [
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"},
            {"ranking": "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A"},
            {"ranking": "FINAL RANKING:\n1. Response C\n2. Response A\n3. Response B"},
        ],
        {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"},
    )

    assert confidence["available"] is True
    assert confidence["low_confidence"] is True
    assert confidence["status"] == "low"
    assert confidence["top1_stability"] == 0.33
    assert confidence["disagreement_score"] > 0
    assert "Council rankings were split" in confidence["summary"]


def test_compute_council_confidence_is_unavailable_with_one_ranking():
    confidence = compute_council_confidence(
        [{"ranking": "FINAL RANKING:\n1. Response A\n2. Response B"}],
        {"Response A": "alpha", "Response B": "beta"},
    )

    assert confidence["available"] is False
    assert confidence["status"] == "unavailable"
    assert confidence["low_confidence"] is False


def test_compute_council_confidence_ignores_incomplete_rankings():
    confidence = compute_council_confidence(
        [
            {"ranking": "FINAL RANKING:\n1. Response A"},
            {"ranking": "FINAL RANKING:\n1. Response A"},
        ],
        {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"},
    )

    assert confidence["available"] is False
    assert confidence["status"] == "unavailable"
    assert confidence["incomplete_ranking_count"] == 2


def test_compute_council_confidence_marks_mostly_incomplete_rankings_low():
    confidence = compute_council_confidence(
        [
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"},
            {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"},
            {"ranking": "FINAL RANKING:\n1. Response A"},
            {"ranking": "FINAL RANKING:\n1. Response A"},
            {"ranking": "FINAL RANKING:\n1. Response A"},
        ],
        {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"},
    )

    assert confidence["available"] is True
    assert confidence["status"] == "low"
    assert confidence["low_confidence"] is True
    assert confidence["top1_stability"] == 1.0
    assert confidence["incomplete_ranking_count"] == 3
    assert "ranking evidence was weak" in confidence["summary"]


@pytest.mark.asyncio
async def test_run_full_council_feeds_low_confidence_to_chairman_and_metadata():
    stage1_responses = {
        "alpha": {"content": "Alpha answer", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": "Beta answer", "_debug": {"ok": True, "provider": "openrouter"}},
        "gamma": {"content": "Gamma answer", "_debug": {"ok": True, "provider": "openrouter"}},
    }
    stage2_responses = {
        "alpha": {"content": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A", "_debug": {"ok": True, "provider": "openrouter"}},
        "gamma": {"content": "FINAL RANKING:\n1. Response C\n2. Response A\n3. Response B", "_debug": {"ok": True, "provider": "openrouter"}},
    }
    chairman_messages = []

    async def fake_query_model(model, messages, **kwargs):
        chairman_messages.append(messages)
        return {
            "content": "Council was split on this answer. Final synthesis",
            "_debug": {"ok": True, "provider": "openrouter"},
        }

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta", "gamma"]), \
         patch(
             "backend.council.query_models_parallel",
             new_callable=AsyncMock,
             side_effect=[stage1_responses, stage2_responses],
         ), \
         patch("backend.council.query_model", side_effect=fake_query_model):
        _, _, stage3, metadata = await run_full_council("Which migration path is safest?")

    confidence = metadata["council_confidence"]
    assert confidence["low_confidence"] is True
    assert metadata["run_status"]["degraded"] is False
    assert stage3["response"].startswith("Council was split")

    chairman_prompt = chairman_messages[0][0]["content"]
    assert "COUNCIL CONFIDENCE SIGNAL" in chairman_prompt
    assert "Status: LOW" in chairman_prompt
    assert "start with a one-sentence warning" in chairman_prompt

    persisted = build_persisted_message_metadata(metadata)
    assert persisted["council_confidence"] == confidence
    assert "debug" not in persisted


@pytest.mark.asyncio
async def test_auto_standard_low_confidence_escalates_to_revision_stages():
    stage1_results = [
        {"model": "alpha", "response": "Alpha answer"},
        {"model": "beta", "response": "Beta answer"},
        {"model": "gamma", "response": "Gamma answer"},
    ]
    stage2_results = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "parsed_ranking": ["Response A", "Response B", "Response C"]},
        {"model": "beta", "ranking": "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A", "parsed_ranking": ["Response B", "Response C", "Response A"]},
        {"model": "gamma", "ranking": "FINAL RANKING:\n1. Response C\n2. Response A\n3. Response B", "parsed_ranking": ["Response C", "Response A", "Response B"]},
    ]
    label_to_model = {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"}
    stage2a_results = [{"model": "alpha", "critiques": "Critique A"}]
    stage2b_results = [
        {"model": "alpha", "original_label": "Response A", "revision": "Revised Alpha"},
    ]
    mode_selection = {
        "requested_mode": "auto",
        "selected_mode": "standard",
        "confidence": 0.55,
        "reason": "Classifier selected standard.",
        "source": "model",
    }

    with patch("backend.council.resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, return_value=(stage1_results, {"stage": "stage1", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})), \
         patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, return_value=(stage2_results, label_to_model, {"stage": "stage2", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})), \
         patch("backend.council.stage2a_collect_critiques", new_callable=AsyncMock, return_value=(stage2a_results, {"stage": "stage2a", "requested_models": 3, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})) as critiques, \
         patch("backend.council.stage2b_collect_revisions", new_callable=AsyncMock, return_value=(stage2b_results, {"stage": "stage2b", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})) as revisions, \
         patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Final from revisions"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})) as synthesize:
        _, _, _, metadata = await run_full_council("Which migration path is safest?", mode="auto")

    assert critiques.await_count == 1
    assert revisions.await_count == 1
    assert synthesize.await_args.kwargs["stage2b_results"] == stage2b_results
    assert metadata["confidence_escalation"]["triggered"] is True
    assert metadata["debug"]["thorough"] is True
    assert "stage2a" in metadata["debug"]["stages"]
    persisted = build_persisted_message_metadata(metadata)
    assert persisted["confidence_escalation"]["triggered"] is True
    assert persisted["run_status"]["confidence_escalation"]["triggered"] is True


@pytest.mark.asyncio
async def test_explicit_standard_low_confidence_does_not_escalate():
    stage1_results = [
        {"model": "alpha", "response": "Alpha answer"},
        {"model": "beta", "response": "Beta answer"},
        {"model": "gamma", "response": "Gamma answer"},
    ]
    stage2_results = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "parsed_ranking": ["Response A", "Response B", "Response C"]},
        {"model": "beta", "ranking": "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A", "parsed_ranking": ["Response B", "Response C", "Response A"]},
        {"model": "gamma", "ranking": "FINAL RANKING:\n1. Response C\n2. Response A\n3. Response B", "parsed_ranking": ["Response C", "Response A", "Response B"]},
    ]
    mode_selection = {
        "requested_mode": "standard",
        "selected_mode": "standard",
        "confidence": 1.0,
        "reason": "Caller explicitly selected standard mode.",
        "source": "explicit",
    }

    with patch("backend.council.resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, return_value=(stage1_results, {"stage": "stage1", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})), \
         patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, return_value=(stage2_results, {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"}, {"stage": "stage2", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})), \
         patch("backend.council.stage2a_collect_critiques", new_callable=AsyncMock) as critiques, \
         patch("backend.council.stage2b_collect_revisions", new_callable=AsyncMock) as revisions, \
         patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Standard final"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})):
        _, _, _, metadata = await run_full_council("Which migration path is safest?", mode="standard")

    critiques.assert_not_awaited()
    revisions.assert_not_awaited()
    assert "confidence_escalation" not in metadata
    assert metadata["debug"]["thorough"] is False


def test_mcp_full_output_renders_council_confidence_summary():
    output = format_council_output(
        [{"model": "alpha", "response": "Alpha answer"}],
        {
            "aggregate_rankings": [
                {"model": "alpha", "average_rank": 1.0, "rankings_count": 2}
            ],
            "council_confidence": {
                "available": True,
                "low_confidence": True,
                "summary": "Council rankings were split: Response A received 1 of 3 top votes (33%).",
                "top1_stability": 0.33,
                "rank_agreement": 0.5,
                "disagreement_score": 0.58,
            },
        },
        {"model": "chairman", "response": "Final answer"},
    )

    assert "### Low confidence" in output
    assert "Council rankings were split" in output
    assert "Disagreement score: 0.58" in output


def test_mcp_full_output_renders_confidence_escalation():
    output = format_council_output(
        [{"model": "alpha", "response": "Alpha answer"}],
        {
            "aggregate_rankings": [],
            "confidence_escalation": {
                "triggered": True,
                "reason": "Auto mode escalated from standard to deep critique/revision.",
            },
        },
        {"model": "chairman", "response": "Final answer"},
    )

    assert "### Confidence escalation" in output
    assert "Auto mode escalated" in output


def test_mcp_full_output_labels_unavailable_confidence():
    output = format_council_output(
        [{"model": "alpha", "response": "Alpha answer"}],
        {
            "aggregate_rankings": [],
            "council_confidence": {
                "available": False,
                "low_confidence": False,
                "status": "unavailable",
                "summary": "Fewer than two peer rankings were available.",
            },
        },
        {"model": "chairman", "response": "Final answer"},
    )

    assert "### Confidence unavailable" in output
    assert "Fewer than two peer rankings" in output


@pytest.mark.asyncio
async def test_stage3_prompt_omits_none_metrics_when_confidence_unavailable():
    chairman_messages = []

    async def fake_query_model(model, messages, **kwargs):
        chairman_messages.append(messages)
        return {"content": "Final synthesis", "_debug": {"ok": True}}

    with patch("backend.council.query_model", side_effect=fake_query_model):
        await stage3_synthesize_final(
            "Question?",
            [{"model": "alpha", "response": "Alpha answer"}],
            [],
            {"Response A": "alpha"},
            council_confidence={
                "available": False,
                "status": "unavailable",
                "summary": "Fewer than two peer rankings were available.",
            },
        )

    chairman_prompt = chairman_messages[0][0]["content"]
    assert "Status: UNAVAILABLE" in chairman_prompt
    assert "Top-1 stability:" not in chairman_prompt
    assert "None" not in chairman_prompt
