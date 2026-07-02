"""Tests for rolling council KPI collection and exposure."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.council import run_full_council
from backend.metrics import council_metrics, record_council_metrics
from mcp_server.server import get_council_metrics as get_council_metrics_tool


@pytest.fixture(autouse=True)
def reset_council_metrics():
    council_metrics.reset()
    yield
    council_metrics.reset()


# DB isolation (autouse `isolated_db`) and the `api_client` TestClient fixture
# live in tests/conftest.py so every test module shares them.


@pytest.mark.asyncio
async def test_run_full_council_updates_clean_and_degraded_metrics():
    clean_stage1 = {
        "alpha": {"content": "Alpha answer", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": "Beta answer", "_debug": {"ok": True, "provider": "openrouter"}},
        "gamma": {"content": "Gamma answer", "_debug": {"ok": True, "provider": "openrouter"}},
    }
    clean_stage2 = {
        "alpha": {"content": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": "FINAL RANKING:\n1. Response B\n2. Response C\n3. Response A", "_debug": {"ok": True, "provider": "openrouter"}},
        "gamma": {"content": "FINAL RANKING:\n1. Response C\n2. Response A\n3. Response B", "_debug": {"ok": True, "provider": "openrouter"}},
    }
    degraded_stage1 = {
        "alpha": {"content": "Alpha answer", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": None, "_debug": {"ok": False, "provider": "openrouter", "failure_type": "timeout"}},
        "gamma": {"content": "Gamma answer", "_debug": {"ok": True, "provider": "openrouter"}},
    }
    degraded_stage2 = {
        "alpha": {"content": "FINAL RANKING:\n1. Response A\n2. Response B", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": None, "_debug": {"ok": False, "provider": "openrouter", "failure_type": "http_status"}},
        "gamma": {"content": "FINAL RANKING:\n1. Response B\n2. Response A", "_debug": {"ok": True, "provider": "openrouter"}},
    }

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta", "gamma"]), \
         patch(
             "backend.council.query_models_parallel",
             new_callable=AsyncMock,
             side_effect=[clean_stage1, clean_stage2, degraded_stage1, degraded_stage2],
         ), \
         patch(
             "backend.council.query_model",
             new_callable=AsyncMock,
             side_effect=[
                 {"content": "Clean synthesis", "_debug": {"ok": True, "provider": "openrouter"}},
                 {"content": "Degraded synthesis", "_debug": {"ok": True, "provider": "openrouter"}},
             ],
         ):
        await run_full_council("How should we test observability?")
        await run_full_council("How should we test degradation?")

    snapshot = council_metrics.snapshot()

    assert snapshot["totals"]["total_runs"] == 2
    assert snapshot["totals"]["successful_runs"] == 2
    assert snapshot["totals"]["clean_successful_runs"] == 1
    assert snapshot["totals"]["degraded_runs"] == 1
    assert snapshot["totals"]["failed_runs"] == 0
    assert snapshot["rates"]["council_success_rate"] == 1.0
    assert snapshot["rates"]["degraded_run_rate"] == 0.5
    assert snapshot["rates"]["stage1_degradation_rate"] == 0.5
    assert snapshot["stages"]["stage1"]["latency_ms"]["count"] == 2
    assert snapshot["stages"]["stage2"]["failed_models_in_window"] == 1


def test_stream_endpoint_records_metrics_and_exposes_json(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]

    stage1_results = [
        {"model": "alpha", "response": "Alpha answer"},
        {"model": "gamma", "response": "Gamma answer"},
    ]
    stage1_debug = {
        "stage": "stage1",
        "request_id": "req-stream",
        "duration_ms": 11.0,
        "requested_models": 3,
        "successful_models": 2,
        "failed_models_count": 1,
        "failed_models": [{"model": "beta", "failure_type": "timeout"}],
    }
    stage2_results = [
        {
            "model": "alpha",
            "ranking": "FINAL RANKING:\n1. Response A\n2. Response B",
            "parsed_ranking": ["Response A", "Response B"],
        },
        {
            "model": "gamma",
            "ranking": "FINAL RANKING:\n1. Response B\n2. Response A",
            "parsed_ranking": ["Response B", "Response A"],
        },
    ]
    stage2_debug = {
        "stage": "stage2",
        "request_id": "req-stream",
        "duration_ms": 13.5,
        "requested_models": 3,
        "successful_models": 2,
        "failed_models_count": 1,
        "failed_models": [{"model": "beta", "failure_type": "http_status"}],
    }
    stage3_result = {"model": "chairman", "response": "Final answer"}
    stage3_debug = {
        "stage": "stage3",
        "request_id": "req-stream",
        "duration_ms": 7.25,
        "requested_models": 1,
        "successful_models": 1,
        "failed_models_count": 0,
        "failed_models": [],
    }

    with patch.object(
        backend_main,
        "stage1_collect_responses",
        new_callable=AsyncMock,
        return_value=(stage1_results, stage1_debug),
    ), \
         patch.object(
             backend_main,
             "stage2_collect_rankings",
             new_callable=AsyncMock,
             return_value=(stage2_results, {"Response A": "alpha", "Response B": "gamma"}, stage2_debug),
         ), \
         patch.object(
             backend_main,
             "stage3_synthesize_final",
             new_callable=AsyncMock,
             return_value=(stage3_result, stage3_debug),
         ), \
         patch.object(
             backend_main,
             "generate_conversation_title",
             new_callable=AsyncMock,
             return_value="Metrics stream",
         ), \
         patch.object(
             backend_main,
             "generate_conversation_summary",
             new_callable=AsyncMock,
             return_value=None,
         ):
        response = client.post(
            f"/api/conversations/{conversation_id}/message/stream",
            json={"content": "Why do metrics matter?", "mode": "standard"},
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    stage2_event = next(event for event in events if event["type"] == "stage2_complete")

    assert any(event["type"] == "stage1_complete" for event in events)
    assert stage2_event["metadata"]["council_confidence"]["available"] is True
    assert stage2_event["metadata"]["council_confidence"]["top1_stability"] == 0.5
    assert stage2_event["metadata"]["council_confidence"]["low_confidence"] is True
    assert stage2_event["metadata"]["council_confidence"]["status"] == "low"
    assert stage3_event["metadata"]["debug"]["stages"]["stage3"]["duration_ms"] == 7.25
    assert stage3_event["metadata"]["debug"]["stages"]["stage1"]["failed_models_count"] == 1
    assert stage3_event["metadata"]["run_status"]["degraded"] is True
    assert stage3_event["metadata"]["run_status"]["summary"] == "2 of 3 council members responded."
    assert stage3_event["metadata"]["council_confidence"] == stage2_event["metadata"]["council_confidence"]

    stored_conversation = client.get(f"/api/conversations/{conversation_id}").json()
    assistant_message = stored_conversation["messages"][1]
    assert assistant_message["metadata"]["label_to_model"] == {
        "Response A": "alpha",
        "Response B": "gamma",
    }
    assert assistant_message["metadata"]["aggregate_rankings"][0]["model"] == "alpha"
    assert assistant_message["metadata"]["council_confidence"] == stage3_event["metadata"]["council_confidence"]
    assert assistant_message["metadata"]["run_status"]["stages"]["stage1"]["failed_models"] == [
        {"model": "beta", "failure_type": "timeout"}
    ]
    assert "debug" not in assistant_message["metadata"]

    metrics_response = client.get("/api/metrics/council")
    assert metrics_response.status_code == 200

    snapshot = metrics_response.json()
    assert snapshot["totals"]["total_runs"] == 1
    assert snapshot["totals"]["successful_runs"] == 1
    assert snapshot["totals"]["degraded_runs"] == 1
    assert snapshot["rates"]["stage1_degradation_rate"] == 1.0
    assert snapshot["stages"]["stage3"]["latency_ms"]["p50"] == 7.25


def test_stream_endpoint_escalates_auto_low_confidence_to_revision_stages(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]

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

    with patch.object(backend_main, "resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch.object(backend_main, "stage1_collect_responses", new_callable=AsyncMock, return_value=(stage1_results, {"stage": "stage1", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "stage2_collect_rankings", new_callable=AsyncMock, return_value=(stage2_results, {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"}, {"stage": "stage2", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "stage2a_collect_critiques", new_callable=AsyncMock, return_value=(stage2a_results, {"stage": "stage2a", "requested_models": 3, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "stage2b_collect_revisions", new_callable=AsyncMock, return_value=(stage2b_results, {"stage": "stage2b", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Final from revisions"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "generate_conversation_title", new_callable=AsyncMock, return_value="Escalation stream"), \
         patch.object(backend_main, "generate_conversation_summary", new_callable=AsyncMock, return_value=None):
        response = client.post(
            f"/api/conversations/{conversation_id}/message/stream",
            json={"content": "Which migration path is safest?", "mode": "auto"},
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert any(event["type"] == "stage2a_complete" for event in events)
    assert any(event["type"] == "stage2b_complete" for event in events)
    stage2_event = next(event for event in events if event["type"] == "stage2_complete")
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    assert stage2_event["metadata"]["confidence_escalation"]["triggered"] is True
    assert stage3_event["metadata"]["confidence_escalation"]["triggered"] is True
    assert stage3_event["metadata"]["debug"]["thorough"] is True

    stored_conversation = client.get(f"/api/conversations/{conversation_id}").json()
    assistant_message = stored_conversation["messages"][1]
    assert assistant_message["metadata"]["confidence_escalation"]["triggered"] is True
    assert assistant_message["stage2a"] == stage2a_results
    assert assistant_message["stage2b"] == stage2b_results


def test_send_message_returns_debug_and_persists_sanitized_metadata(api_client):
    client, backend_main = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]

    metadata = {
        "label_to_model": {"Response A": "alpha", "Response B": "gamma"},
        "aggregate_rankings": [
            {"model": "alpha", "average_rank": 1.0, "rankings_count": 1},
            {"model": "gamma", "average_rank": 2.0, "rankings_count": 1},
        ],
        "debug": {
            "request_id": "req-direct",
            "duration_ms": 42.0,
            "successful_council_models": 2,
            "failed_council_models": 1,
            "stages": {
                "stage1": {
                    "requested_models": 3,
                    "successful_models": 2,
                    "failed_models_count": 1,
                    "failed_models": [
                        {
                            "model": "beta",
                            "provider": "openrouter",
                            "failure_type": "timeout",
                            "status_code": 504,
                        }
                    ],
                },
                "stage3": {
                    "requested_models": 1,
                    "successful_models": 1,
                    "failed_models_count": 0,
                    "failed_models": [],
                },
            },
        },
        "run_status": {
            "degraded": True,
            "summary": "2 of 3 council members responded.",
            "successful_council_models": 2,
            "failed_council_models": 1,
            "stages": {
                "stage1": {
                    "requested_models": 3,
                    "successful_models": 2,
                    "failed_models_count": 1,
                    "failed_models": [{"model": "beta", "failure_type": "timeout"}],
                },
                "stage3": {
                    "requested_models": 1,
                    "successful_models": 1,
                    "failed_models_count": 0,
                    "failed_models": [],
                },
            },
        },
    }

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "alpha", "response": "Alpha answer"}],
            [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}],
            {"model": "chairman", "response": "Final answer"},
            metadata,
        ),
    ), \
         patch.object(
             backend_main,
             "generate_conversation_title",
             new_callable=AsyncMock,
             return_value="Direct metadata",
         ), \
         patch.object(
             backend_main,
             "generate_conversation_summary",
             new_callable=AsyncMock,
             return_value=None,
         ):
        response = client.post(
            f"/api/conversations/{conversation_id}/message",
            json={"content": "Why is degraded status important?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["debug"]["stages"]["stage1"]["failed_models"][0]["provider"] == "openrouter"
    assert payload["metadata"]["run_status"]["degraded"] is True

    stored_conversation = client.get(f"/api/conversations/{conversation_id}").json()
    assistant_message = stored_conversation["messages"][1]
    assert assistant_message["metadata"]["label_to_model"] == metadata["label_to_model"]
    assert assistant_message["metadata"]["aggregate_rankings"] == metadata["aggregate_rankings"]
    assert assistant_message["metadata"]["run_status"] == metadata["run_status"]
    assert "debug" not in assistant_message["metadata"]


@pytest.mark.asyncio
async def test_get_council_metrics_tool_returns_json():
    record_council_metrics(
        {
            "request_id": "req-tool",
            "thorough": False,
            "duration_ms": 25.0,
            "successful_council_models": 2,
            "failed_council_models": 1,
            "stages": {
                "stage1": {
                    "duration_ms": 10.0,
                    "requested_models": 3,
                    "successful_models": 2,
                    "failed_models_count": 1,
                },
                "stage3": {
                    "duration_ms": 5.0,
                    "requested_models": 1,
                    "successful_models": 1,
                    "failed_models_count": 0,
                },
            },
        }
    )

    payload = json.loads(await get_council_metrics_tool())

    assert payload["totals"]["total_runs"] == 1
    assert payload["totals"]["degraded_runs"] == 1
    assert payload["rates"]["council_success_rate"] == 1.0


@pytest.mark.asyncio
async def test_record_council_metrics_unchanged_by_eta_feature():
    """Regression: the in-memory collector must snapshot identically even when
    the durable record_run_timing helper is called alongside it (§0.4 of the ETA
    plan — ETA reads from the runs table, never mutates the collector)."""
    debug = {
        "request_id": "req-regression",
        "thorough": False,
        "duration_ms": 42.0,
        "successful_council_models": 2,
        "failed_council_models": 0,
        "deliberation_mode": "standard",
        "stages": {
            "stage1": {"duration_ms": 10.0, "requested_models": 2,
                       "successful_models": 2, "failed_models_count": 0},
            "stage3": {"duration_ms": 5.0, "requested_models": 1,
                       "successful_models": 1, "failed_models_count": 0},
        },
    }
    record_council_metrics(debug)
    snapshot_before = council_metrics.snapshot()

    # Calling the durable helper must not perturb the in-memory collector.
    from backend.metrics import record_run_timing
    record_run_timing(debug, conversation_id=None, completed=True, started_at_epoch=1.0)
    snapshot_after = council_metrics.snapshot()

    assert snapshot_after == snapshot_before
