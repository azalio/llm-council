"""Tests for adaptive sparse council routing."""

import importlib
import json
import sys
import threading
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.agent_router import build_agent_route
from backend.council import run_full_council
from backend.metrics import council_metrics


@pytest.fixture(autouse=True)
def reset_metrics():
    council_metrics.reset()
    yield
    council_metrics.reset()


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


def test_build_agent_route_selects_sparse_subset_for_routine_auto_standard():
    route = build_agent_route(
        "Explain why caching helps latency.",
        {"requested_mode": "auto", "selected_mode": "standard"},
        full_pool=["alpha", "beta", "gamma", "delta"],
    )

    assert route["applied"] is True
    assert route["selected_models"] == ["alpha", "beta", "gamma"]
    assert route["skipped_models"] == ["delta"]
    assert route["saved_initial_model_calls"] == 2


def test_build_agent_route_uses_full_pool_for_high_risk_questions():
    route = build_agent_route(
        "Review this production security migration plan.",
        {"requested_mode": "auto", "selected_mode": "standard"},
        full_pool=["alpha", "beta", "gamma", "delta"],
    )

    assert route["applied"] is False
    assert route["selected_models"] == ["alpha", "beta", "gamma", "delta"]
    assert "high-risk" in route["reason"]


@pytest.mark.asyncio
async def test_run_full_council_completes_sparse_route_without_expansion():
    mode_selection = {
        "requested_mode": "auto",
        "selected_mode": "standard",
        "confidence": 0.55,
        "reason": "test",
        "source": "model",
    }
    stage1_results = [
        {"model": "alpha", "response": "Alpha answer"},
        {"model": "beta", "response": "Beta answer"},
    ]
    stage2_results = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
        {"model": "beta", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
    ]

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta", "gamma"]), \
         patch("backend.council.resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, return_value=(stage1_results, {"stage": "stage1", "requested_models": 2, "successful_models": 2, "failed_models_count": 0, "duration_ms": 1})) as stage1, \
         patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, return_value=(stage2_results, {"Response A": "alpha", "Response B": "beta"}, {"stage": "stage2", "requested_models": 2, "successful_models": 2, "failed_models_count": 0, "duration_ms": 1})) as stage2, \
         patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Final"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})):
        _, _, _, metadata = await run_full_council("Explain why caching helps latency.", mode="auto")

    stage1.assert_awaited_once()
    stage2.assert_awaited_once()
    assert stage1.await_args.kwargs["models"] == ["alpha", "beta"]
    assert stage2.await_args.kwargs["models"] == ["alpha", "beta"]
    assert metadata["agent_routing"]["applied"] is True
    assert metadata["agent_routing"]["expanded"] is False
    assert metadata["run_status"]["agent_routing"]["applied"] is True

    routing_metrics = council_metrics.snapshot()["agent_routing"]
    assert routing_metrics["totals"]["applied_runs"] == 1
    assert routing_metrics["totals"]["sparse_completed_runs"] == 1


@pytest.mark.asyncio
async def test_run_full_council_expands_sparse_route_on_low_confidence():
    mode_selection = {
        "requested_mode": "auto",
        "selected_mode": "standard",
        "confidence": 0.55,
        "reason": "test",
        "source": "model",
    }
    routed_stage1 = [
        {"model": "alpha", "response": "Alpha answer"},
        {"model": "beta", "response": "Beta answer"},
    ]
    expanded_stage1 = [{"model": "gamma", "response": "Gamma answer"}]
    split_stage2 = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
        {"model": "beta", "ranking": "FINAL RANKING:\n1. Response B\n2. Response A", "parsed_ranking": ["Response B", "Response A"]},
    ]
    full_stage2 = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "parsed_ranking": ["Response A", "Response B", "Response C"]},
        {"model": "beta", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "parsed_ranking": ["Response A", "Response B", "Response C"]},
        {"model": "gamma", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B\n3. Response C", "parsed_ranking": ["Response A", "Response B", "Response C"]},
    ]

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta", "gamma"]), \
         patch("backend.council.resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, side_effect=[
             (routed_stage1, {"stage": "stage1", "requested_models": 2, "successful_models": 2, "failed_models_count": 0, "failed_models": [], "duration_ms": 1}),
             (expanded_stage1, {"stage": "stage1", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "failed_models": [], "duration_ms": 1}),
         ]) as stage1, \
         patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, side_effect=[
             (split_stage2, {"Response A": "alpha", "Response B": "beta"}, {"stage": "stage2", "requested_models": 2, "successful_models": 2, "failed_models_count": 0, "duration_ms": 1}),
             (full_stage2, {"Response A": "alpha", "Response B": "beta", "Response C": "gamma"}, {"stage": "stage2", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1}),
         ]) as stage2, \
         patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Final"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})):
        stage1_results, _, _, metadata = await run_full_council("Explain why caching helps latency.", mode="auto")

    assert [call.kwargs["models"] for call in stage1.await_args_list] == [["alpha", "beta"], ["gamma"]]
    assert [call.kwargs["models"] for call in stage2.await_args_list] == [["alpha", "beta"], ["alpha", "beta", "gamma"]]
    assert [result["model"] for result in stage1_results] == ["alpha", "beta", "gamma"]
    assert metadata["agent_routing"]["expanded"] is True
    assert metadata["agent_routing"]["expansion_reason"] == "routed_confidence_low"

    routing_metrics = council_metrics.snapshot()["agent_routing"]
    assert routing_metrics["totals"]["expanded_runs"] == 1
    assert routing_metrics["expansion_reasons"] == {"routed_confidence_low": 1}


@pytest.mark.asyncio
async def test_run_full_council_expands_sparse_route_on_routed_stage1_failure():
    mode_selection = {
        "requested_mode": "auto",
        "selected_mode": "standard",
        "confidence": 0.55,
        "reason": "test",
        "source": "model",
    }
    routed_stage1 = [{"model": "alpha", "response": "Alpha answer"}]
    expanded_stage1 = [{"model": "gamma", "response": "Gamma answer"}]
    full_stage2 = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
        {"model": "gamma", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
    ]

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta", "gamma"]), \
         patch("backend.council.resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch("backend.council.stage1_collect_responses", new_callable=AsyncMock, side_effect=[
             (routed_stage1, {"stage": "stage1", "requested_models": 2, "successful_models": 1, "failed_models_count": 1, "failed_models": [{"model": "beta", "failure_type": "timeout"}], "duration_ms": 1}),
             (expanded_stage1, {"stage": "stage1", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "failed_models": [], "duration_ms": 1}),
         ]) as stage1, \
         patch("backend.council.stage2_collect_rankings", new_callable=AsyncMock, return_value=(full_stage2, {"Response A": "alpha", "Response B": "gamma"}, {"stage": "stage2", "requested_models": 3, "successful_models": 3, "failed_models_count": 0, "duration_ms": 1})) as stage2, \
         patch("backend.council.stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Final"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})):
        stage1_results, _, _, metadata = await run_full_council("Explain why caching helps latency.", mode="auto")

    assert [call.kwargs["models"] for call in stage1.await_args_list] == [["alpha", "beta"], ["gamma"]]
    assert stage2.await_args.kwargs["models"] == ["alpha", "beta", "gamma"]
    assert [result["model"] for result in stage1_results] == ["alpha", "gamma"]
    assert metadata["agent_routing"]["expanded"] is True
    assert metadata["agent_routing"]["expansion_reason"] == "routed_stage1_model_failed"


def test_stream_endpoint_persists_sparse_route_metadata(api_client, monkeypatch):
    client, backend_main = api_client
    monkeypatch.setattr(backend_main, "COUNCIL_MODELS", ["alpha", "beta", "gamma"])
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]

    mode_selection = {
        "requested_mode": "auto",
        "selected_mode": "standard",
        "confidence": 0.55,
        "reason": "test",
        "source": "model",
    }
    stage1_results = [
        {"model": "alpha", "response": "Alpha answer"},
        {"model": "beta", "response": "Beta answer"},
    ]
    stage2_results = [
        {"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
        {"model": "beta", "ranking": "FINAL RANKING:\n1. Response A\n2. Response B", "parsed_ranking": ["Response A", "Response B"]},
    ]

    with patch.object(backend_main, "resolve_deliberation_mode", new_callable=AsyncMock, return_value=mode_selection), \
         patch.object(backend_main, "stage1_collect_responses", new_callable=AsyncMock, return_value=(stage1_results, {"stage": "stage1", "requested_models": 2, "successful_models": 2, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "stage2_collect_rankings", new_callable=AsyncMock, return_value=(stage2_results, {"Response A": "alpha", "Response B": "beta"}, {"stage": "stage2", "requested_models": 2, "successful_models": 2, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "stage3_synthesize_final", new_callable=AsyncMock, return_value=({"model": "chairman", "response": "Final"}, {"stage": "stage3", "requested_models": 1, "successful_models": 1, "failed_models_count": 0, "duration_ms": 1})), \
         patch.object(backend_main, "generate_conversation_title", new_callable=AsyncMock, return_value="Routing"), \
         patch.object(backend_main, "generate_conversation_summary", new_callable=AsyncMock, return_value=None):
        response = client.post(
            f"/api/conversations/{conversation_id}/message/stream",
            json={"content": "Explain why caching helps latency.", "mode": "auto"},
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    stage2_event = next(event for event in events if event["type"] == "stage2_complete")
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    assert stage2_event["metadata"]["agent_routing"]["applied"] is True
    assert stage3_event["metadata"]["agent_routing"]["applied"] is True

    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert stored["messages"][1]["metadata"]["agent_routing"]["applied"] is True
