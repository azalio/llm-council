"""Tests for the optional first-turn clarification gate."""

import importlib
import json
import sys
import threading
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.council import (
    CLARIFICATION_FEEDBACK_TYPE,
    SUGGESTION_PROVIDE_CLARIFICATION,
    SUGGESTION_RETRY_REFINED,
    build_clarification_result,
    run_full_council,
    stage_minus_1_intent_check,
)


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "council.db"
    monkeypatch.setenv("LLM_COUNCIL_ROOT", str(tmp_path))

    import backend.config as backend_config

    monkeypatch.setattr(backend_config, "DB_PATH", str(db_path))

    storage = importlib.import_module("backend.storage")
    existing_conn = getattr(storage._local, "conn", None)
    if existing_conn is not None:
        existing_conn.close()
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    storage._local = threading.local()
    storage._ensure_schema()

    if "backend.main" in sys.modules:
        backend_main = importlib.reload(sys.modules["backend.main"])
    else:
        backend_main = importlib.import_module("backend.main")

    with TestClient(backend_main.app) as client:
        yield client, backend_main

    conn = getattr(storage._local, "conn", None)
    if conn is not None:
        conn.close()
    storage._local = threading.local()


@pytest.mark.asyncio
async def test_stage_minus_1_parses_ambiguous_classifier_output():
    with patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        return_value={
            "content": "AMBIGUOUS: Which deployment target should I optimize for?",
            "_debug": {"ok": True},
        },
    ):
        clarification = await stage_minus_1_intent_check("Make it faster")

    assert clarification is not None
    assert clarification["needed"] is True
    assert clarification["question"] == "Which deployment target should I optimize for?"
    assert clarification["model"]


@pytest.mark.asyncio
async def test_stage_minus_1_emits_structured_interpretations():
    classifier_output = (
        "AMBIGUOUS: Which deployment target should I optimize for?\n"
        "INTERPRETATION: How do I optimize this for AWS Lambda cold starts?\n"
        "INTERPRETATION: How do I optimize this for a long-running container?"
    )
    with patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        return_value={"content": classifier_output, "_debug": {"ok": True}},
    ):
        clarification = await stage_minus_1_intent_check("Make it faster")

    assert clarification is not None
    assert clarification["question"] == "Which deployment target should I optimize for?"

    recovery = clarification["recovery_feedback"]
    assert recovery["type"] == CLARIFICATION_FEEDBACK_TYPE
    assert recovery["message"] == clarification["question"]

    retry_suggestions = [
        s for s in recovery["suggestions"] if s["type"] == SUGGESTION_RETRY_REFINED
    ]
    assert [s["parameters"]["question"] for s in retry_suggestions] == [
        "How do I optimize this for AWS Lambda cold starts?",
        "How do I optimize this for a long-running container?",
    ]
    # A terminal human-in-the-loop suggestion always trails the interpretations.
    assert recovery["suggestions"][-1]["type"] == SUGGESTION_PROVIDE_CLARIFICATION
    assert (
        recovery["suggestions"][-1]["parameters"]["clarifying_question"]
        == clarification["question"]
    )


@pytest.mark.asyncio
async def test_stage_minus_1_without_interpretations_still_has_recovery():
    with patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        return_value={
            "content": "AMBIGUOUS: Which deployment target should I optimize for?",
            "_debug": {"ok": True},
        },
    ):
        clarification = await stage_minus_1_intent_check("Make it faster")

    recovery = clarification["recovery_feedback"]
    assert [s["type"] for s in recovery["suggestions"]] == [
        SUGGESTION_PROVIDE_CLARIFICATION
    ]


def test_build_clarification_result_carries_recovery_feedback():
    recovery = {"type": CLARIFICATION_FEEDBACK_TYPE, "message": "Which one?", "suggestions": []}
    with_recovery = build_clarification_result(
        {"question": "Which one?", "recovery_feedback": recovery}
    )
    assert with_recovery["recovery_feedback"] == recovery

    # Injected clarifications without structured feedback stay backward compatible.
    without_recovery = build_clarification_result({"question": "Which one?"})
    assert "recovery_feedback" not in without_recovery


def test_mcp_format_recovery_feedback_renders_block():
    from mcp_server import server

    recovery = {
        "type": CLARIFICATION_FEEDBACK_TYPE,
        "message": "Which one?",
        "suggestions": [
            {"type": SUGGESTION_PROVIDE_CLARIFICATION, "parameters": {"clarifying_question": "Which one?"}}
        ],
    }
    rendered = server.format_recovery_feedback({"recovery_feedback": recovery})
    assert "```json" in rendered
    assert CLARIFICATION_FEEDBACK_TYPE in rendered
    # Normal answers (no structured feedback) render nothing.
    assert server.format_recovery_feedback({"response": "final answer"}) == ""


@pytest.mark.asyncio
async def test_stage_minus_1_clear_output_continues_to_council():
    with patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        return_value={"content": "CLEAR", "_debug": {"ok": True}},
    ):
        clarification = await stage_minus_1_intent_check(
            "Compare SQLite and Postgres for this FastAPI app."
        )

    assert clarification is None


@pytest.mark.asyncio
async def test_run_full_council_short_circuits_when_clarification_needed():
    clarification = {
        "needed": True,
        "question": "Which part of the answer should the council focus on?",
        "model": "cheap-model",
    }

    with patch(
        "backend.council.stage_minus_1_intent_check",
        new_callable=AsyncMock,
        return_value=clarification,
    ), patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
    ) as stage1:
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            "Improve this",
            clarify_when_unclear=True,
        )

    assert stage1_results == []
    assert stage2_results == []
    assert stage3_result["model"] == "clarification-gate"
    assert stage3_result["response"] == clarification["question"]
    assert metadata["clarification"] == clarification
    stage1.assert_not_called()


@pytest.mark.asyncio
async def test_run_full_council_skips_clarification_gate_for_followups():
    with patch(
        "backend.council.stage_minus_1_intent_check",
        new_callable=AsyncMock,
    ) as clarification_check, patch(
        "backend.council.stage0_reformulate",
        new_callable=AsyncMock,
        return_value="Standalone follow-up question",
    ), patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=([{"model": "m1", "response": "answer"}], {"stage": "stage1"}),
    ), patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
        return_value=(
            [
                {
                    "model": "m1",
                    "ranking": "FINAL RANKING:\n1. Response A",
                    "parsed_ranking": ["Response A"],
                }
            ],
            {"Response A": "m1"},
            {"stage": "stage2"},
        ),
    ), patch(
        "backend.council.stage3_synthesize_final",
        new_callable=AsyncMock,
        return_value=({"model": "chairman", "response": "final"}, {"stage": "stage3"}),
    ):
        _, _, stage3_result, metadata = await run_full_council(
            "What about that?",
            conversation_context={"recent_turns": [{"user": "Earlier", "assistant": "Answer"}]},
            clarify_when_unclear=True,
        )

    clarification_check.assert_not_called()
    assert stage3_result["response"] == "final"
    assert "clarification" not in metadata


def test_direct_message_persists_clarification_metadata(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]
    clarification = {
        "needed": True,
        "question": "Which repository should I review?",
        "model": "cheap-model",
    }
    metadata = {"clarification": clarification}

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [],
            [],
            {"model": "clarification-gate", "response": clarification["question"]},
            metadata,
        ),
    ) as run_mock, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Clarify repo",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ) as summary_mock:
        response = client.post(
            f"/api/conversations/{conversation_id}/message",
            json={"content": "Review it", "clarify_when_unclear": True},
        )

    assert response.status_code == 200
    assert run_mock.call_args.kwargs["clarify_when_unclear"] is True
    summary_mock.assert_not_called()

    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assistant_message = stored["messages"][1]
    assert assistant_message["metadata"]["clarification"] == clarification


def test_stream_message_short_circuits_with_clarification_event(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]
    clarification = {
        "needed": True,
        "question": "Which audience should the explanation target?",
        "model": "cheap-model",
    }

    with patch.object(
        backend_main,
        "stage_minus_1_intent_check",
        new_callable=AsyncMock,
        return_value=clarification,
    ), patch.object(
        backend_main,
        "stage1_collect_responses",
        new_callable=AsyncMock,
    ) as stage1, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Clarify audience",
    ):
        response = client.post(
            f"/api/conversations/{conversation_id}/message/stream",
            json={"content": "Explain this", "clarify_when_unclear": True},
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events if event["type"].startswith("stage_minus_1")] == [
        "stage_minus_1_start",
        "stage_minus_1_complete",
    ]
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    assert stage3_event["data"]["response"] == clarification["question"]
    assert stage3_event["metadata"]["clarification"] == clarification
    stage1.assert_not_called()

    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert stored["messages"][1]["metadata"]["clarification"] == clarification


@pytest.mark.asyncio
async def test_mcp_tool_schema_exposes_clarification_flag_but_not_context():
    from mcp_server import server

    server = importlib.reload(server)
    tools = await server.mcp.list_tools()
    ask_tool = next(tool for tool in tools if tool.name == "ask_council")

    assert "clarify_when_unclear" in ask_tool.inputSchema["properties"]
    assert "ctx" not in ask_tool.inputSchema["properties"]
