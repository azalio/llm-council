"""Tests for durable ETA / expected-wait-time estimates."""

import json
import threading
from unittest.mock import AsyncMock, patch

import pytest

from backend import eta as eta_module
from backend import storage as storage_module
from backend.eta import estimate_council_wait
from backend.metrics import record_run_timing

# DB isolation (autouse `isolated_db`) and the `api_client` TestClient fixture
# live in tests/conftest.py so every test module shares them.


def _standard_debug(duration_ms=40000, request_id="req-1"):
    """A minimal debug payload shaped like build_council_run_debug output."""
    return {
        "request_id": request_id,
        "thorough": False,
        "duration_ms": duration_ms,
        "deliberation_mode": "standard",
        "stages": {
            "stage1": {"duration_ms": 10000.0},
            "stage2": {"duration_ms": 8000.0},
            "stage3": {"duration_ms": 20000.0},
        },
    }


def _quick_debug(duration_ms=5000, request_id="req-q"):
    return {
        "request_id": request_id,
        "thorough": False,
        "duration_ms": duration_ms,
        "deliberation_mode": "quick",
        "stages": {
            "quick_answer": {"duration_ms": duration_ms},
            "stage3": {"duration_ms": 4000.0},
        },
    }


def _deep_debug(duration_ms=120000, request_id="req-d"):
    return {
        "request_id": request_id,
        "thorough": True,
        "duration_ms": duration_ms,
        "deliberation_mode": "deep",
        "stages": {
            "stage1": {"duration_ms": 20000.0},
            "stage2": {"duration_ms": 15000.0},
            "stage3": {"duration_ms": 25000.0},
            "stage2a": {"duration_ms": 30000.0},
            "stage2b": {"duration_ms": 30000.0},
        },
    }


def _count_runs():
    conn = storage_module._get_conn()
    return conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]


# 1. schema migration idempotency
def test_runs_table_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "council.db"
    monkeypatch.setenv("LLM_COUNCIL_ROOT", str(tmp_path))
    monkeypatch.setattr(storage_module, "DB_PATH", str(db_path))
    existing = getattr(storage_module._local, "conn", None)
    if existing is not None:
        existing.close()
    storage_module._local = threading.local()

    storage_module._ensure_schema()
    storage_module._ensure_schema()  # second call must be a no-op

    conn = storage_module._get_conn()
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    assert {"request_id", "deliberation_mode", "duration_ms", "started_at_epoch",
            "stage1_ms", "stage2b_ms", "completed"}.issubset(cols)
    idx = {row["name"] for row in conn.execute("PRAGMA index_list(runs)").fetchall()}
    assert "idx_runs_mode_epoch" in idx
    assert "idx_runs_request_id" in idx

    conn.close()
    storage_module._local = threading.local()


# 2. exactly-once per run + idempotent guard on request_id
def test_record_run_timing_writes_exactly_once():
    debug = _standard_debug(request_id="req-once")
    record_run_timing(debug, conversation_id=None, completed=True, started_at_epoch=1000.0)
    assert _count_runs() == 1
    # second call with the same request_id is a no-op (first measurement wins)
    record_run_timing(debug, conversation_id=None, completed=False, started_at_epoch=2000.0)
    assert _count_runs() == 1
    conn = storage_module._get_conn()
    row = conn.execute("SELECT completed FROM runs WHERE request_id = ?", ("req-once",)).fetchone()
    assert row["completed"] == 1  # first measurement wins, not the False


# 3. quick runs have null stage1/stage2
def test_record_run_timing_quick_run_has_null_stage1_stage2():
    record_run_timing(_quick_debug(request_id="req-quick"), completed=True, started_at_epoch=1.0)
    conn = storage_module._get_conn()
    row = conn.execute("SELECT * FROM runs WHERE request_id = ?", ("req-quick",)).fetchone()
    assert row["stage1_ms"] is None
    assert row["stage2_ms"] is None
    assert row["stage2a_ms"] is None
    assert row["stage2b_ms"] is None
    assert row["stage3_ms"] == 4000


# 4. p50 math
def test_estimate_council_wait_p50_math():
    for i, ms in enumerate([30000, 40000, 50000, 60000, 70000]):
        record_run_timing(
            _standard_debug(duration_ms=ms, request_id=f"req-p50-{i}"),
            completed=True,
            started_at_epoch=1000.0 + i,
        )
    result = estimate_council_wait({"selected_mode": "standard"})
    assert result["basis"] == "measured_p50"
    assert result["sample_count"] == 5
    assert result["expected_wait_seconds"] == 50.0  # median of [30,40,50,60,70]


# 5. insufficient samples -> null + fallback
def test_estimate_council_wait_insufficient_samples_returns_null():
    record_run_timing(_standard_debug(duration_ms=30000, request_id="req-ins-1"),
                      completed=True, started_at_epoch=1.0)
    record_run_timing(_standard_debug(duration_ms=40000, request_id="req-ins-2"),
                      completed=True, started_at_epoch=2.0)
    result = estimate_council_wait({"selected_mode": "standard"})
    assert result["expected_wait_seconds"] is None
    assert result["basis"] == "insufficient_data"
    assert result["fallback_seconds"] == eta_module._config.COUNCIL_ETA_STANDARD_FALLBACK_SECONDS
    assert result["note"] is not None


# 6. partitions by mode
def test_estimate_council_wait_partitions_by_mode():
    for i in range(5):
        record_run_timing(_quick_debug(duration_ms=5000, request_id=f"req-q-{i}"),
                          completed=True, started_at_epoch=float(i))
    for i in range(2):
        record_run_timing(_standard_debug(duration_ms=40000, request_id=f"req-s-{i}"),
                          completed=True, started_at_epoch=float(i))
    quick = estimate_council_wait({"selected_mode": "quick"})
    standard = estimate_council_wait({"selected_mode": "standard"})
    assert quick["basis"] == "measured_p50"
    assert quick["expected_wait_seconds"] == 5.0
    assert standard["basis"] == "insufficient_data"
    assert standard["expected_wait_seconds"] is None


# 7. per-stage estimates for deep
def test_estimate_council_wait_per_stage_estimates():
    for i in range(5):
        record_run_timing(_deep_debug(duration_ms=120000, request_id=f"req-deep-{i}"),
                          completed=True, started_at_epoch=float(i))
    result = estimate_council_wait({"selected_mode": "deep"})
    assert result["basis"] == "measured_p50"
    assert result["per_stage_estimates"] is not None
    assert result["per_stage_estimates"]["stage2a"] is not None
    assert result["per_stage_estimates"]["stage2b"] is not None
    assert result["per_stage_estimates"]["stage1"] is not None


# 8. ETA tool returns valid JSON
@pytest.mark.asyncio
async def test_get_council_eta_tool_returns_valid_json():
    from mcp_server.server import get_council_eta as get_eta_tool

    mode_selection = {"selected_mode": "quick", "confidence": 1.0, "reason": "test", "source": "explicit"}
    with patch(
        "mcp_server.server.resolve_deliberation_mode",
        new_callable=AsyncMock,
        return_value=mode_selection,
    ):
        raw = await get_eta_tool(question="hi", mode="quick")
    payload = json.loads(raw)
    assert payload["deliberation_mode"] == "quick"
    assert "expected_wait_seconds" in payload
    assert "basis" in payload
    assert "fallback_seconds" in payload


# 9. poll_council_task includes eta_seconds
@pytest.mark.asyncio
async def test_poll_council_task_includes_eta_seconds(monkeypatch):
    from mcp_server import server as mcp_server

    mode_selection = {"selected_mode": "quick", "confidence": 1.0, "reason": "test", "source": "explicit"}
    monkeypatch.setattr(
        mcp_server, "resolve_deliberation_mode",
        AsyncMock(return_value=mode_selection),
    )

    async def _fake_run(*_args, **_kwargs):
        # The background coroutine must not run a real council.
        return

    # Patch the background coroutine so it does not run a real council.
    monkeypatch.setattr(mcp_server, "_run_task_background", _fake_run)

    raw = await mcp_server.start_council_async(question="hi", mode="quick")
    task_id = json.loads(raw)["task_id"]
    poll_raw = await mcp_server.poll_council_task(task_id=task_id)
    poll = json.loads(poll_raw)
    assert "eta_seconds" in poll
    assert "eta" in poll


# 10. SSE mode_selection_complete emits eta
def test_sse_mode_selection_complete_emits_eta(api_client):
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
        backend_main, "resolve_deliberation_mode",
        new_callable=AsyncMock, return_value=mode_metadata,
    ), patch.object(
        backend_main, "stage_quick_answer",
        new_callable=AsyncMock,
        return_value=({"model": "chairman", "response": "Four.", "mode": "quick"}, quick_debug),
    ), patch.object(
        backend_main, "generate_conversation_title",
        new_callable=AsyncMock, return_value="Quick",
    ), patch.object(
        backend_main, "generate_conversation_summary",
        new_callable=AsyncMock, return_value=None,
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
    ms_event = next(e for e in events if e["type"] == "mode_selection_complete")
    assert "eta" in ms_event["metadata"]
    assert ms_event["metadata"]["eta"]["deliberation_mode"] == "quick"


# 11. pruning at cap
def test_record_run_timing_pruning_at_cap(monkeypatch):
    monkeypatch.setattr(eta_module._config, "COUNCIL_ETA_MAX_RUN_ROWS", 10)
    for i in range(12):
        record_run_timing(
            _standard_debug(duration_ms=40000, request_id=f"req-prune-{i}"),
            completed=True,
            started_at_epoch=float(i),
        )
    assert _count_runs() == 10
    conn = storage_module._get_conn()
    # oldest (epoch 0,1) pruned; newest (epoch 11) kept
    kept_epochs = [r["started_at_epoch"] for r in conn.execute(
        "SELECT started_at_epoch FROM runs ORDER BY started_at_epoch DESC").fetchall()]
    assert 11.0 in kept_epochs
    assert 0.0 not in kept_epochs


# 12. ETA disabled -> stub
def test_eta_disabled_returns_stub(monkeypatch):
    monkeypatch.setattr(eta_module._config, "COUNCIL_ETA_ENABLED", False)
    result = estimate_council_wait({"selected_mode": "standard"})
    assert result["basis"] == "disabled"
    assert result["expected_wait_seconds"] is None
