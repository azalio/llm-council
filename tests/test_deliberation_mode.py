"""Tests for quick/standard/deep deliberation mode routing."""

import importlib
import json
import sys
import threading
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.council import (
    classify_deliberation_mode,
    resolve_deliberation_mode,
    run_full_council,
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
async def test_auto_mode_routes_simple_question_to_quick_without_classifier_call():
    with patch("backend.council.query_model", new_callable=AsyncMock) as query_model:
        selection = await classify_deliberation_mode("What is 2 + 2?")

    assert selection["selected_mode"] == "quick"
    assert selection["source"] == "heuristic"
    query_model.assert_not_called()


@pytest.mark.asyncio
async def test_auto_mode_falls_back_to_standard_when_classifier_fails():
    with patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        side_effect=RuntimeError("offline"),
    ):
        selection = await classify_deliberation_mode("Why should we test metrics?")

    assert selection["selected_mode"] == "standard"
    assert selection["source"] == "fallback"


@pytest.mark.asyncio
async def test_thorough_alias_resolves_to_deep_mode():
    selection = await resolve_deliberation_mode("Explain consensus", thorough=True)

    assert selection["selected_mode"] == "deep"
    assert selection["source"] == "thorough_alias"


@pytest.mark.asyncio
async def test_explicit_standard_mode_overrides_thorough_alias():
    selection = await resolve_deliberation_mode(
        "Explain consensus",
        mode="standard",
        thorough=True,
    )

    assert selection["selected_mode"] == "standard"
    assert selection["source"] == "explicit"


@pytest.mark.asyncio
async def test_run_full_council_quick_mode_skips_peer_ranking_and_persists_mode():
    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
    ) as stage1, patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
    ) as stage2, patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        return_value={"content": "Four.", "_debug": {"ok": True}},
    ):
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            "What is 2 + 2?",
            mode="quick",
        )

    assert stage1_results == [{"model": stage3_result["model"], "response": "Four.", "mode": "quick"}]
    assert stage2_results == []
    assert stage3_result["response"] == "Four."
    assert metadata["deliberation_mode"]["selected_mode"] == "quick"
    assert metadata["debug"]["deliberation_mode"] == "quick"
    assert metadata["run_status"]["summary"] == "Quick mode answered with the chairman model only."
    stage1.assert_not_called()
    stage2.assert_not_called()


def test_direct_message_passes_mode_and_persists_mode_metadata(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]
    mode_metadata = {
        "requested_mode": "quick",
        "selected_mode": "quick",
        "confidence": 1.0,
        "reason": "test",
        "source": "explicit",
    }
    metadata = {"deliberation_mode": mode_metadata, "run_status": {"deliberation_mode": "quick"}}

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "chairman", "response": "Four.", "mode": "quick"}],
            [],
            {"model": "chairman", "response": "Four.", "mode": "quick"},
            metadata,
        ),
    ) as run_mock, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Quick math",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(
            f"/api/conversations/{conversation_id}/message",
            json={"content": "What is 2 + 2?", "mode": "quick"},
        )

    assert response.status_code == 200
    assert run_mock.call_args.kwargs["mode"] == "quick"

    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert stored["messages"][1]["metadata"]["deliberation_mode"] == mode_metadata


def test_stream_message_quick_mode_returns_final_answer_without_stage2(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]
    mode_metadata = {
        "requested_mode": "quick",
        "selected_mode": "quick",
        "confidence": 1.0,
        "reason": "test",
        "source": "explicit",
    }
    quick_debug = {
        "stage": "quick_answer",
        "duration_ms": 5.0,
        "requested_models": 1,
        "successful_models": 1,
        "failed_models_count": 0,
        "failed_models": [],
    }

    with patch.object(
        backend_main,
        "resolve_deliberation_mode",
        new_callable=AsyncMock,
        return_value=mode_metadata,
    ), patch.object(
        backend_main,
        "stage_quick_answer",
        new_callable=AsyncMock,
        return_value=({"model": "chairman", "response": "Four.", "mode": "quick"}, quick_debug),
    ), patch.object(
        backend_main,
        "stage2_collect_rankings",
        new_callable=AsyncMock,
    ) as stage2, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Quick math",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(
            f"/api/conversations/{conversation_id}/message/stream",
            json={"content": "What is 2 + 2?", "mode": "quick"},
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert any(event["type"] == "mode_selection_complete" for event in events)
    assert not any(event["type"] == "stage2_start" for event in events)
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    assert stage3_event["data"]["response"] == "Four."
    assert stage3_event["metadata"]["deliberation_mode"] == mode_metadata
    stage2.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_schema_exposes_mode_and_hides_context():
    from mcp_server import server

    server = importlib.reload(server)
    tools = await server.mcp.list_tools()
    ask_tool = next(tool for tool in tools if tool.name == "ask_council")

    assert "mode" in ask_tool.inputSchema["properties"]
    assert "ctx" not in ask_tool.inputSchema["properties"]
