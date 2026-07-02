"""Tests for progress_callback wiring in run_full_council.

Covers two layers:

1. backend.council.run_full_council propagates per-stage progress to the
   supplied callback in the right order, with monotonic progress values, for
   default / multi-turn / thorough modes.
2. mcp_server.server._execute_council_deliberation forwards progress to the
   FastMCP Context and runs a heartbeat task that re-emits liveness
   notifications during long stages.

These tests use AsyncMock for stage internals and a recording fake for the
callback / Context. No real LLM calls are made.
"""

import asyncio
import importlib
import os
import tempfile
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Use an isolated test DB before importing storage (mirrors test_multi_turn.py).
_test_db_dir = tempfile.mkdtemp()
os.environ["LLM_COUNCIL_ROOT"] = _test_db_dir
os.makedirs(os.path.join(_test_db_dir, "data"), exist_ok=True)
_test_db_path = os.path.join(_test_db_dir, "data", "council.db")

import backend.config
backend.config.DB_PATH = _test_db_path

import backend.storage as storage
storage._local = __import__("threading").local()
storage._ensure_schema()

from backend.council import run_full_council  # noqa: E402


def _stage_debug(stage: str = "stage"):
    return {
        "stage": stage,
        "duration_ms": 1,
        "successful_models": 1,
        "requested_models": 1,
        "failed_models": [],
    }


def _mock_stage1_results():
    return [{"model": "m1", "response": "answer", "_debug": {"ok": True}}]


def _mock_stage2_results():
    return [{
        "model": "m1",
        "response": "1. Response A",
        "ranking": "FINAL RANKING:\n1. Response A",
        "parsed_ranking": ["Response A"],
    }]


def _mock_stage3_result():
    return {"model": "chairman", "response": "final"}


# 1. council.run_full_council per-stage progress


@pytest.mark.asyncio
async def test_progress_callback_invoked_for_each_stage_default():
    """Default mode emits 6 callbacks (start+done for stage1, stage2, stage3)."""
    events = []

    async def cb(progress: float, total: float, message: str):
        events.append((progress, total, message))

    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(_mock_stage1_results(), _stage_debug("stage1")),
    ), patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
        return_value=(_mock_stage2_results(), {"Response A": "m1"}, _stage_debug("stage2")),
    ), patch(
        "backend.council.stage3_synthesize_final",
        new_callable=AsyncMock,
        return_value=(_mock_stage3_result(), _stage_debug("stage3")),
    ):
        await run_full_council("test", progress_callback=cb)

    # Each stage emits one "starting" + one "done" event (3 stages * 2 = 6).
    assert len(events) == 6
    totals = {total for _, total, _ in events}
    assert totals == {3.0}  # All notifications share the same total.

    progresses = [p for p, _, _ in events]
    # Monotonically non-decreasing.
    assert progresses == sorted(progresses)
    # Final event reaches the total.
    assert progresses[-1] == 3.0
    # First event starts at 0 (Stage 1 starting).
    assert progresses[0] == 0.0


@pytest.mark.asyncio
async def test_progress_callback_invoked_for_each_stage_thorough():
    """Thorough mode emits 10 callbacks (5 stages * 2)."""
    events = []

    async def cb(progress: float, total: float, message: str):
        events.append((progress, total, message))

    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(_mock_stage1_results(), _stage_debug("stage1")),
    ), patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
        return_value=(_mock_stage2_results(), {"Response A": "m1"}, _stage_debug("stage2")),
    ), patch(
        "backend.council.stage2a_collect_critiques",
        new_callable=AsyncMock,
        return_value=([{"model": "m1", "response": "critique"}], _stage_debug("stage2a")),
    ), patch(
        "backend.council.stage2b_collect_revisions",
        new_callable=AsyncMock,
        return_value=([{"model": "m1", "response": "revised"}], _stage_debug("stage2b")),
    ), patch(
        "backend.council.stage3_synthesize_final",
        new_callable=AsyncMock,
        return_value=(_mock_stage3_result(), _stage_debug("stage3")),
    ):
        await run_full_council("test", thorough=True, progress_callback=cb)

    assert len(events) == 10
    totals = {total for _, total, _ in events}
    assert totals == {5.0}
    assert events[-1][0] == 5.0


@pytest.mark.asyncio
async def test_progress_callback_invoked_with_multi_turn():
    """Multi-turn (conversation_context) adds Stage 0, so default mode has 4 stages."""
    events = []

    async def cb(progress: float, total: float, message: str):
        events.append((progress, total, message))

    with patch(
        "backend.council.stage0_reformulate",
        new_callable=AsyncMock,
        return_value="standalone question",
    ), patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(_mock_stage1_results(), _stage_debug("stage1")),
    ), patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
        return_value=(_mock_stage2_results(), {"Response A": "m1"}, _stage_debug("stage2")),
    ), patch(
        "backend.council.stage3_synthesize_final",
        new_callable=AsyncMock,
        return_value=(_mock_stage3_result(), _stage_debug("stage3")),
    ):
        ctx = {"summary": "earlier", "recent_turns": [], "previous_final_answer": "x"}
        await run_full_council("follow-up", conversation_context=ctx, progress_callback=cb)

    # Stage 0 + 1 + 2 + 3 = 4 stages * 2 events.
    assert len(events) == 8
    assert events[-1][0] == 4.0
    # First event is Stage 0 "starting".
    assert "Reformulating" in events[0][2]


@pytest.mark.asyncio
async def test_progress_callback_failure_does_not_abort_run():
    """A raising callback must be logged but never break the council run."""
    call_count = {"n": 0}

    async def bad_cb(progress: float, total: float, message: str):
        call_count["n"] += 1
        raise RuntimeError("boom")

    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(_mock_stage1_results(), _stage_debug("stage1")),
    ), patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
        return_value=(_mock_stage2_results(), {"Response A": "m1"}, _stage_debug("stage2")),
    ), patch(
        "backend.council.stage3_synthesize_final",
        new_callable=AsyncMock,
        return_value=(_mock_stage3_result(), _stage_debug("stage3")),
    ):
        s1, s2, s3, metadata = await run_full_council("test", progress_callback=bad_cb)

    # Run still completes successfully.
    assert s3.get("response") == "final"
    # Callback was attempted multiple times (one per stage boundary).
    assert call_count["n"] >= 3


@pytest.mark.asyncio
async def test_progress_callback_cancelled_error_propagates():
    """Cancellation from a progress callback must not be swallowed."""

    async def cancel_cb(progress: float, total: float, message: str):
        raise asyncio.CancelledError()

    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(_mock_stage1_results(), _stage_debug("stage1")),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_full_council("test", progress_callback=cancel_cb)


@pytest.mark.asyncio
async def test_no_progress_callback_works_unchanged():
    """run_full_council without progress_callback runs as before (regression guard)."""
    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(_mock_stage1_results(), _stage_debug("stage1")),
    ), patch(
        "backend.council.stage2_collect_rankings",
        new_callable=AsyncMock,
        return_value=(_mock_stage2_results(), {"Response A": "m1"}, _stage_debug("stage2")),
    ), patch(
        "backend.council.stage3_synthesize_final",
        new_callable=AsyncMock,
        return_value=(_mock_stage3_result(), _stage_debug("stage3")),
    ):
        s1, s2, s3, metadata = await run_full_council("test")

    assert s3.get("response") == "final"


@pytest.mark.asyncio
async def test_progress_callback_emits_failure_notification_on_stage1_zero():
    """When Stage 1 returns no successful models, callback gets a final failure event."""
    events = []

    async def cb(progress: float, total: float, message: str):
        events.append((progress, total, message))

    # Stage1 returns empty results, so council aborts.
    with patch(
        "backend.council.stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=([], _stage_debug("stage1")),
    ):
        s1, s2, s3, metadata = await run_full_council("test", progress_callback=cb)

    assert s1 == []
    # At least one event should mention failure/abort.
    assert any("failed" in msg.lower() or "abort" in msg.lower() for _, _, msg in events)
    assert events[-1][0] == 1.0


# 2. mcp_server.server progress + heartbeat wiring


@pytest.mark.asyncio
async def test_mcp_server_forwards_progress_to_context():
    """_execute_council_deliberation must call ctx.report_progress per stage."""
    from mcp_server import server

    ctx = MagicMock()
    ctx.report_progress = AsyncMock()

    with patch.object(server, "run_full_council", new_callable=AsyncMock) as mock_run, \
         patch.object(server, "generate_conversation_title", new_callable=AsyncMock, return_value="title"), \
         patch.object(server, "generate_conversation_summary", new_callable=AsyncMock, return_value=None):

        async def fake_run_full_council(
            question,
            thorough=False,
            mode="auto",
            conversation_context=None,
            progress_callback=None,
            clarify_when_unclear=False,
        ):
            # Simulate the council reporting progress at three stage boundaries.
            if progress_callback is not None:
                await progress_callback(1.0, 3.0, "Stage 1 done")
                await progress_callback(2.0, 3.0, "Stage 2 done")
                await progress_callback(3.0, 3.0, "Stage 3 done")
            return (
                _mock_stage1_results(),
                _mock_stage2_results(),
                _mock_stage3_result(),
                {"debug": {"duration_ms": 1, "successful_council_models": 1}, "run_status": {}},
            )

        mock_run.side_effect = fake_run_full_council
        result = await server._execute_council_deliberation(
            "test question",
            ctx=ctx,
        )

    assert "final" in result
    # Initial 0% notification + 3 stage notifications = at least 4.
    assert ctx.report_progress.call_count >= 4
    # First call is the "Starting" 0/1 notification.
    first_call = ctx.report_progress.call_args_list[0]
    assert first_call.kwargs.get("progress") == 0.0
    # Stage progress values are forwarded faithfully.
    progress_values = [
        c.kwargs.get("progress")
        for c in ctx.report_progress.call_args_list
        if c.kwargs.get("progress") is not None
    ]
    assert 1.0 in progress_values and 2.0 in progress_values and 3.0 in progress_values


@pytest.mark.asyncio
async def test_mcp_server_heartbeat_keeps_emitting_during_long_stage():
    """During a long stage with no new progress, heartbeat re-emits liveness."""
    from mcp_server import server

    ctx = MagicMock()
    ctx.report_progress = AsyncMock()

    # Use a tiny heartbeat interval for the test so it actually fires.
    with patch.object(server, "HEARTBEAT_INTERVAL_SECONDS", 0.05), \
         patch.object(server, "run_full_council", new_callable=AsyncMock) as mock_run, \
         patch.object(server, "generate_conversation_title", new_callable=AsyncMock, return_value="title"), \
         patch.object(server, "generate_conversation_summary", new_callable=AsyncMock, return_value=None):

        async def slow_run(
            question,
            thorough=False,
            mode="auto",
            conversation_context=None,
            progress_callback=None,
            clarify_when_unclear=False,
        ):
            # Emit one progress event, then sleep long enough for several heartbeats.
            if progress_callback is not None:
                await progress_callback(1.0, 3.0, "Stage 1 done")
            await asyncio.sleep(0.3)  # ~6 heartbeat intervals
            return (
                _mock_stage1_results(),
                _mock_stage2_results(),
                _mock_stage3_result(),
                {"debug": {"duration_ms": 1, "successful_council_models": 1}, "run_status": {}},
            )

        mock_run.side_effect = slow_run
        await server._execute_council_deliberation("test", ctx=ctx)

    # We expect: initial 0% + 1 stage notification + at least 2 heartbeats.
    # Heartbeat re-emits include the "(still working...)" suffix.
    heartbeat_msgs = [
        c.kwargs.get("message", "")
        for c in ctx.report_progress.call_args_list
        if "still working" in c.kwargs.get("message", "")
    ]
    assert len(heartbeat_msgs) >= 2, (
        f"Expected at least 2 heartbeat re-emissions, got {len(heartbeat_msgs)}. "
        f"All calls: {ctx.report_progress.call_args_list}"
    )


@pytest.mark.asyncio
async def test_mcp_server_handles_missing_context_gracefully():
    """When ctx is None (non-MCP caller), no progress is emitted and run completes."""
    from mcp_server import server

    with patch.object(server, "run_full_council", new_callable=AsyncMock) as mock_run, \
         patch.object(server, "generate_conversation_title", new_callable=AsyncMock, return_value="title"), \
         patch.object(server, "generate_conversation_summary", new_callable=AsyncMock, return_value=None):
        mock_run.return_value = (
            _mock_stage1_results(),
            _mock_stage2_results(),
            _mock_stage3_result(),
            {"debug": {"duration_ms": 1, "successful_council_models": 1}, "run_status": {}},
        )
        # Note: ctx defaults to None, no progress_callback should be passed in.
        result = await server._execute_council_deliberation("test")

    assert "final" in result
    # Confirm progress_callback was None in the call.
    _, kwargs = mock_run.call_args
    assert kwargs.get("progress_callback") is None


@pytest.mark.asyncio
async def test_mcp_server_initial_progress_cancellation_propagates():
    """Cancellation while reporting initial progress must cancel the tool call."""
    from mcp_server import server

    ctx = MagicMock()
    ctx.report_progress = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await server._execute_council_deliberation("test", ctx=ctx)


@pytest.mark.asyncio
async def test_mcp_context_parameter_is_not_exposed_as_tool_argument():
    """FastMCP should inject ctx internally, not expose it to MCP callers."""
    from mcp_server import server

    # Some older tests reload mcp_server.server with mocked MCP modules. Reload
    # here so this schema check always inspects the real FastMCP decorators.
    server = importlib.reload(server)
    tools = await server.mcp.list_tools()
    ask_tool = next(tool for tool in tools if tool.name == "ask_council")

    assert "ctx" not in ask_tool.inputSchema["properties"]


@pytest.mark.asyncio
async def test_provider_fanout_cancels_in_flight_model_tasks():
    """Cancelling a fan-out must cancel every in-flight provider request."""
    from backend import openrouter

    started_models = set()
    cancelled_models = set()
    all_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_query_model(model, messages):
        started_models.add(model)
        if started_models == {"m1", "m2"}:
            all_started.set()
        try:
            await never_finish.wait()
        except asyncio.CancelledError:
            cancelled_models.add(model)
            raise

    with patch.object(openrouter, "query_model", new=slow_query_model):
        task = asyncio.create_task(
            openrouter.query_models_parallel(
                ["m1", "m2"],
                [{"role": "user", "content": "slow question"}],
            )
        )
        await asyncio.wait_for(all_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled_models == {"m1", "m2"}


@pytest.mark.asyncio
async def test_stage2b_cancels_in_flight_revision_tasks():
    """Thorough-mode revision fan-out must not keep running after cancellation."""
    from backend import council

    started_models = set()
    cancelled_models = set()
    all_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_query_model(model, messages):
        started_models.add(model)
        if started_models == {"m1", "m2"}:
            all_started.set()
        try:
            await never_finish.wait()
        except asyncio.CancelledError:
            cancelled_models.add(model)
            raise

    stage1_results = [
        {"model": "m1", "response": "answer 1"},
        {"model": "m2", "response": "answer 2"},
    ]
    stage2a_results = [
        {"model": "critic", "critiques": "## Critique of Response A\nNeeds detail.\n\n## Critique of Response B\nGood."},
    ]

    with patch.object(council, "query_model", new=slow_query_model):
        task = asyncio.create_task(
            council.stage2b_collect_revisions(
                "slow question",
                stage1_results,
                stage2a_results,
                ["A", "B"],
                {"Response A": "m1", "Response B": "m2"},
            )
        )
        await asyncio.wait_for(all_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled_models == {"m1", "m2"}


@pytest.mark.asyncio
async def test_run_full_council_cancels_current_stage_and_resets_context():
    """Cancelling the orchestrator must propagate into the active stage."""
    import backend.council as council

    stage_started = asyncio.Event()
    stage_cancelled = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_stage1(query):
        stage_started.set()
        try:
            await never_finish.wait()
        except asyncio.CancelledError:
            stage_cancelled.set()
            raise

    with patch.object(council, "stage1_collect_responses", new=slow_stage1), \
         patch.object(council, "reset_request_id", wraps=council.reset_request_id) as reset_spy:
        task = asyncio.create_task(council.run_full_council("cancel this"))
        await asyncio.wait_for(stage_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await asyncio.wait_for(stage_cancelled.wait(), timeout=1)
    reset_spy.assert_called_once()


@pytest.mark.asyncio
async def test_mcp_cancellation_does_not_store_partial_assistant_message():
    """A cancelled MCP run stores the user turn but no partial assistant result."""
    from mcp_server import server

    conversation_id = f"cancel-test-{uuid.uuid4()}"
    storage.create_conversation(conversation_id)

    run_started = asyncio.Event()
    run_cancelled = asyncio.Event()
    never_finish = asyncio.Event()

    async def slow_run_full_council(*args, **kwargs):
        run_started.set()
        try:
            await never_finish.wait()
        except asyncio.CancelledError:
            run_cancelled.set()
            raise

    with patch.object(server, "run_full_council", new=slow_run_full_council), \
         patch.object(server, "reset_request_id", wraps=server.reset_request_id) as reset_spy:
        task = asyncio.create_task(
            server._execute_council_deliberation(
                "cancel this council run",
                conversation_id=conversation_id,
            )
        )
        await asyncio.wait_for(run_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await asyncio.wait_for(run_cancelled.wait(), timeout=1)
    reset_spy.assert_called_once()

    conversation = storage.get_conversation(conversation_id)
    assert conversation is not None
    assert [message["role"] for message in conversation["messages"]] == ["user"]
