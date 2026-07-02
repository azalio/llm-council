"""Focused tests for resiliency and request-scoped debug metadata."""

import logging
import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import backend.openrouter as openrouter
from backend.council import run_full_council
from backend.observability import bind_request_id, reset_request_id
from mcp_server.server import _execute_council_deliberation


@pytest.mark.asyncio
async def test_openrouter_timeout_returns_typed_failure(monkeypatch):
    """Provider timeouts should return a typed debug payload."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", FakeClient)

    _, token = bind_request_id("req-timeout")
    try:
        response = await openrouter._query_openrouter(
            "openai/test-model",
            [{"role": "user", "content": "Hello"}],
            timeout=1.0,
        )
    finally:
        reset_request_id(token)

    assert response["_debug"]["ok"] is False
    assert response["_debug"]["failure_type"] == "timeout"
    assert response["_debug"]["request_id"] == "req-timeout"


@pytest.mark.asyncio
async def test_run_full_council_exposes_partial_failure_debug_metadata(caplog):
    """Partial failures should still produce a final answer and debug metadata."""
    stage1_responses = {
        "alpha": {
            "content": "Alpha answer",
            "_debug": {"ok": True, "provider": "openrouter"},
        },
        "beta": {
            "content": None,
            "_debug": {"ok": False, "provider": "openrouter", "failure_type": "timeout"},
        },
        "gamma": {
            "content": "Gamma answer",
            "_debug": {"ok": True, "provider": "openrouter"},
        },
    }
    stage2_responses = {
        "alpha": {
            "content": "Review\n\nFINAL RANKING:\n1. Response A\n2. Response B",
            "_debug": {"ok": True, "provider": "openrouter"},
        },
        "beta": {
            "content": None,
            "_debug": {
                "ok": False,
                "provider": "openrouter",
                "failure_type": "http_status",
                "status_code": 503,
            },
        },
        "gamma": {
            "content": "Review\n\nFINAL RANKING:\n1. Response B\n2. Response A",
            "_debug": {"ok": True, "provider": "openrouter"},
        },
    }

    caplog.set_level(logging.INFO)

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta", "gamma"]), \
         patch("backend.council.query_models_parallel", new_callable=AsyncMock, side_effect=[stage1_responses, stage2_responses]), \
         patch(
             "backend.council.query_model",
             new_callable=AsyncMock,
             return_value={"content": "Final synthesis", "_debug": {"ok": True, "provider": "openrouter"}},
         ):
        stage1, stage2, stage3, metadata = await run_full_council("Why does testing matter?")

    assert [item["model"] for item in stage1] == ["alpha", "gamma"]
    assert len(stage2) == 2
    assert stage3["response"] == "Final synthesis"

    debug = metadata["debug"]
    run_status = metadata["run_status"]
    assert debug["successful_council_models"] == 2
    assert debug["failed_council_models"] == 1
    assert debug["stages"]["stage1"]["failed_models"][0]["model"] == "beta"
    assert debug["stages"]["stage1"]["failed_models"][0]["failure_type"] == "timeout"
    assert debug["stages"]["stage2"]["failed_models"][0]["failure_type"] == "http_status"
    assert debug["stages"]["stage2"]["failed_models"][0]["status_code"] == 503
    assert run_status["degraded"] is True
    assert run_status["summary"] == "2 of 3 council members responded."
    assert run_status["stages"]["stage1"]["failed_models"] == [
        {"model": "beta", "failure_type": "timeout"}
    ]
    assert run_status["stages"]["stage2"]["failed_models"] == [
        {"model": "beta", "failure_type": "http_status"}
    ]

    request_ids = {
        match.group(1)
        for record in caplog.records
        for match in [re.search(r'"request_id": "([^"]+)"', record.getMessage())]
        if match
    }
    assert request_ids == {debug["request_id"]}
    assert any('"stage": "stage1"' in record.getMessage() for record in caplog.records)
    assert any('"stage": "stage2"' in record.getMessage() for record in caplog.records)
    assert any('"stage": "stage3"' in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_execute_council_deliberation_appends_debug_section():
    """MCP output should include debug details only when requested."""
    metadata = {
        "label_to_model": {"Response A": "alpha"},
        "aggregate_rankings": [{"model": "alpha", "average_rank": 1.0, "rankings_count": 1}],
        "debug": {
            "request_id": "req-123",
            "duration_ms": 42.5,
            "successful_council_models": 2,
            "failed_council_models": 1,
            "stages": {
                "stage1": {
                    "successful_models": 2,
                    "requested_models": 3,
                    "duration_ms": 12.0,
                    "failed_models": [{"model": "beta", "failure_type": "timeout"}],
                }
            },
        },
    }

    with patch("mcp_server.server.storage_create"), \
         patch("mcp_server.server.storage_add_user"), \
         patch("mcp_server.server.storage_add_assistant"), \
         patch("mcp_server.server.storage_update_title"), \
         patch("mcp_server.server.storage_update_summary"), \
         patch("mcp_server.server.generate_conversation_title", new_callable=AsyncMock, return_value="Test Title"), \
         patch("mcp_server.server.generate_conversation_summary", new_callable=AsyncMock, return_value=None), \
         patch(
             "mcp_server.server.run_full_council",
             new_callable=AsyncMock,
             return_value=(
                 [{"model": "alpha", "response": "Alpha answer"}],
                 [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}],
                 {"model": "chairman", "response": "Final answer"},
                 metadata,
             ),
         ):
        result = await _execute_council_deliberation(
            "What should we test?",
            include_debug=True,
        )

    assert "Final answer" in result
    assert "## Debug" in result
    assert "req-123" in result
    assert "beta (timeout)" in result
    assert "Council conversation:" in result
