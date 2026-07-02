"""Tests for the explicit conversation_id/session-state contract in mcp_server.server.

llm-council is a Stateful Session Server layered under a Tool Orchestrator
(see CLAUDE.md's "State & Session Contract" section): conversation_id is real
state, but a plain-text MCP tool can't force a calling model to notice and
propagate it. These tests cover the concrete mitigations:

1. `_execute_council_deliberation`'s `on_conversation_resolved` callback fires
   with the resolved id (new or existing) as soon as it's known, and a
   callback failure never aborts the deliberation.
2. `start_council_async` + `poll_council_task` surface `conversation_id` as a
   structured JSON field — available before the run completes — instead of
   requiring the caller to parse it out of the prose `result` string.
"""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from mcp_server import server as mcp_server


def _fake_metadata():
    return {"debug": {"request_id": "req-1", "successful_council_models": 1, "failed_council_models": 0, "stages": {}}}


@contextmanager
def _no_title_or_summary_calls():
    """Title/summary generation are real network calls in the un-mocked path;
    every test below stubs them out so the suite stays offline and fast."""
    with patch(
        "mcp_server.server.generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Test Title",
    ), patch(
        "mcp_server.server.generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield


@pytest.mark.asyncio
async def test_execute_council_deliberation_invokes_callback_with_new_conversation_id():
    seen = []
    with _no_title_or_summary_calls(), patch(
        "mcp_server.server.run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "alpha", "response": "Alpha answer"}],
            [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}],
            {"model": "chairman", "response": "Final answer"},
            _fake_metadata(),
        ),
    ):
        result = await mcp_server._execute_council_deliberation(
            "What is the capital of France?",
            on_conversation_resolved=seen.append,
        )

    assert len(seen) == 1
    resolved_id = seen[0]
    assert resolved_id
    assert f"Council conversation: {resolved_id}" in result


@pytest.mark.asyncio
async def test_execute_council_deliberation_invokes_callback_with_existing_conversation_id():
    conversation_id = "existing-convo-id"
    mcp_server.storage_create(conversation_id)
    mcp_server.storage_add_user(conversation_id, "First question")
    mcp_server.storage_add_assistant(
        conversation_id,
        [{"model": "alpha", "response": "First answer"}],
        [],
        {"model": "chairman", "response": "First answer"},
    )

    seen = []
    with _no_title_or_summary_calls(), patch(
        "mcp_server.server.run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "alpha", "response": "Alpha answer"}],
            [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}],
            {"model": "chairman", "response": "Final answer"},
            _fake_metadata(),
        ),
    ):
        await mcp_server._execute_council_deliberation(
            "Follow-up question",
            conversation_id=conversation_id,
            on_conversation_resolved=seen.append,
        )

    assert seen == [conversation_id]


@pytest.mark.asyncio
async def test_execute_council_deliberation_swallows_callback_failure():
    def _broken_callback(_conversation_id):
        raise RuntimeError("boom")

    with _no_title_or_summary_calls(), patch(
        "mcp_server.server.run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "alpha", "response": "Alpha answer"}],
            [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}],
            {"model": "chairman", "response": "Final answer"},
            _fake_metadata(),
        ),
    ):
        result = await mcp_server._execute_council_deliberation(
            "What is the capital of France?",
            on_conversation_resolved=_broken_callback,
        )

    assert "Final answer" in result


@pytest.mark.asyncio
async def test_start_council_async_surfaces_conversation_id_before_run_completes(monkeypatch):
    """conversation_id must be visible via poll_council_task while status is
    still "running" — not just once status flips to "done"."""
    mode_selection = {"selected_mode": "quick", "confidence": 1.0, "reason": "test", "source": "explicit"}
    monkeypatch.setattr(
        mcp_server, "resolve_deliberation_mode", AsyncMock(return_value=mode_selection)
    )

    task_id_holder = {}

    async def _fake_run_full_council(*_args, **_kwargs):
        # By the time run_full_council would be invoked, the task store must
        # already carry the resolved conversation_id, proving it's set before
        # deliberation runs, not only after it completes.
        task_id = task_id_holder["task_id"]
        assert mcp_server._async_tasks[task_id]["conversation_id"] is not None
        assert mcp_server._async_tasks[task_id]["status"] == "running"
        return (
            [{"model": "chairman", "response": "Four.", "mode": "quick"}],
            [],
            {"model": "chairman", "response": "Four.", "mode": "quick"},
            _fake_metadata(),
        )

    monkeypatch.setattr(mcp_server, "run_full_council", _fake_run_full_council)

    with _no_title_or_summary_calls():
        raw = await mcp_server.start_council_async(question="What is 2 + 2?", mode="quick")
        task_id = json.loads(raw)["task_id"]
        task_id_holder["task_id"] = task_id

        poll_before = json.loads(await mcp_server.poll_council_task(task_id=task_id))
        assert poll_before["conversation_id"] is None  # background task hasn't started yet

        await mcp_server._run_task_background(
            task_id,
            question="What is 2 + 2?",
            full_output=False,
            thorough=False,
            mode="quick",
            conversation_id="",
            include_debug=False,
            clarify_when_unclear=False,
            bypass_cache=False,
        )

    poll_after = json.loads(await mcp_server.poll_council_task(task_id=task_id))
    assert poll_after["status"] == "done"
    assert poll_after["conversation_id"]
    assert poll_after["conversation_id"] not in (None, "")


@pytest.mark.asyncio
async def test_poll_council_task_has_conversation_id_field_before_background_runs(monkeypatch):
    mode_selection = {"selected_mode": "quick", "confidence": 1.0, "reason": "test", "source": "explicit"}
    monkeypatch.setattr(
        mcp_server, "resolve_deliberation_mode", AsyncMock(return_value=mode_selection)
    )

    async def _noop(*_args, **_kwargs):
        return

    monkeypatch.setattr(mcp_server, "_run_task_background", _noop)

    raw = await mcp_server.start_council_async(question="hi", mode="quick")
    task_id = json.loads(raw)["task_id"]
    poll = json.loads(await mcp_server.poll_council_task(task_id=task_id))
    assert "conversation_id" in poll
    assert poll["conversation_id"] is None
