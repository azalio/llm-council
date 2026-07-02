"""Tests for conservative first-turn answer cache behavior."""

import importlib
import json
import sys
import threading
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from backend.answer_cache import (
    find_answer_cache_hit,
    question_similarity,
    semantic_question_similarity,
)
from backend.metrics import council_metrics


@pytest.fixture(autouse=True)
def reset_answer_cache_metrics():
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
        yield client, backend_main, storage

    conn = getattr(storage._local, "conn", None)
    if conn is not None:
        conn.close()
    storage._local = threading.local()


def test_question_similarity_is_conservative_for_short_queries():
    assert question_similarity("What is 2 + 2?", "what is 2+2") == 1.0
    assert question_similarity("What is 2 + 2?", "What is 2 + 3?") == 0.0


def test_semantic_similarity_matches_paraphrases_without_numeric_collisions():
    assert semantic_question_similarity(
        "Explain duplicate question answer reuse in llm council",
        "How can llm-council cache answers for repeated prompts?",
    ) >= 0.86
    assert semantic_question_similarity("What is 2 + 2?", "What is 2 + 3?") == 0.0
    assert semantic_question_similarity(
        "What is 2 + 2 in Python?",
        "What is 2 + 3 in Python?",
    ) == 0.0


def test_cache_lookup_skips_too_short_questions(api_client):
    _, _, storage = api_client
    storage.create_conversation("short-seed")
    storage.add_user_message("short-seed", "test")
    storage.add_assistant_message(
        "short-seed",
        [],
        [],
        {"model": "chairman", "response": "Should not be cached for short prompts."},
    )

    assert find_answer_cache_hit("test") is None


def test_cache_lookup_skips_cached_assistant_messages(api_client):
    _, _, storage = api_client
    storage.create_conversation("cached-seed")
    storage.add_user_message("cached-seed", "Explain council answer cache behavior")
    storage.add_assistant_message(
        "cached-seed",
        [],
        [],
        {
            "model": "chairman",
            "response": "_Served from answer cache._\n\nOriginal answer.",
            "cached": True,
        },
        metadata={"answer_cache": {"hit": True}},
    )

    assert find_answer_cache_hit("Explain council answer cache behavior") is None


@pytest.mark.asyncio
async def test_cache_lookup_skips_context_dependent_followup_sources(api_client):
    _, _, storage = api_client
    storage.create_conversation("followup-seed")
    storage.add_user_message("followup-seed", "Start a conversation about cache design")
    storage.add_assistant_message(
        "followup-seed",
        [],
        [],
        {"model": "chairman", "response": "Opening answer."},
    )
    storage.add_user_message("followup-seed", "Explain answer cache behavior for repeated prompts")
    storage.add_assistant_message(
        "followup-seed",
        [],
        [],
        {"model": "chairman", "response": "Follow-up answer depends on prior context."},
    )

    import backend.answer_cache as answer_cache

    assert await answer_cache.find_answer_cache_hit_with_validation(
        "Explain cache behavior for similar requests"
    ) is None


def test_storage_returns_completed_answer_candidates(api_client):
    _, _, storage = api_client
    conversation = storage.create_conversation("seed")
    assert conversation["messages"] == []
    storage.add_user_message("seed", "Explain council confidence")
    storage.add_assistant_message(
        "seed",
        [{"model": "alpha", "response": "A"}],
        [{"model": "alpha", "parsed_ranking": ["Response A"]}],
        {"model": "chairman", "response": "Final"},
        metadata={"label_to_model": {"Response A": "alpha"}},
    )

    candidates = storage.find_completed_answer_candidates()

    assert candidates[0]["conversation_id"] == "seed"
    assert candidates[0]["question"] == "Explain council confidence"
    assert candidates[0]["stage3"]["response"] == "Final"
    assert candidates[0]["metadata"]["label_to_model"] == {"Response A": "alpha"}


def test_direct_message_reuses_cached_first_turn_answer(api_client):
    client, backend_main, _ = api_client
    seed = client.post("/api/conversations", json={}).json()["id"]
    cached_metadata = {
        "label_to_model": {"Response A": "alpha"},
        "deliberation_mode": {"selected_mode": "standard"},
        "run_status": {"summary": "All 1 council members responded."},
    }

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "alpha", "response": "A"}],
            [{"model": "alpha", "parsed_ranking": ["Response A"]}],
            {"model": "chairman", "response": "Cached final answer."},
            cached_metadata,
        ),
    ), patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Cache Seed",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        seed_response = client.post(
            f"/api/conversations/{seed}/message",
            json={"content": "Explain council confidence in llm-council"},
        )
    assert seed_response.status_code == 200

    target = client.post("/api/conversations", json={}).json()["id"]
    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
    ) as run_mock, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Cache Hit",
    ) as title_mock:
        response = client.post(
            f"/api/conversations/{target}/message",
            json={"content": "Explain council confidence in llm-council"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stage3"]["cached"] is True
    assert "Served from answer cache" in payload["stage3"]["response"]
    assert payload["metadata"]["answer_cache"]["hit"] is True
    run_mock.assert_not_called()
    title_mock.assert_not_called()

    metrics = client.get("/api/metrics/council").json()["answer_cache"]
    assert metrics["totals"]["lookups"] == 2
    assert metrics["totals"]["hits"] == 1
    assert metrics["totals"]["misses"] == 1
    assert metrics["match_types"] == {"token": 1}
    assert metrics["latency_ms"]["hit"]["count"] == 1

    stored = client.get(f"/api/conversations/{target}").json()
    assert stored["title"] == "Explain council confidence in llm-council"
    assistant_metadata = stored["messages"][1]["metadata"]
    assert assistant_metadata["answer_cache"]["source_conversation_id"] == seed
    assert assistant_metadata["run_status"]["cached"] is True


def test_direct_message_reuses_high_confidence_semantic_cache_hit(api_client):
    client, backend_main, _ = api_client
    seed = client.post("/api/conversations", json={}).json()["id"]

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=(
            [{"model": "alpha", "response": "A"}],
            [{"model": "alpha", "parsed_ranking": ["Response A"]}],
            {"model": "chairman", "response": "Semantic cached final answer."},
            {"label_to_model": {"Response A": "alpha"}},
        ),
    ), patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Cache Seed",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        seed_response = client.post(
            f"/api/conversations/{seed}/message",
            json={"content": "Explain duplicate question answer reuse in llm council"},
        )
    assert seed_response.status_code == 200

    target = client.post("/api/conversations", json={}).json()["id"]
    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
    ) as run_mock, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Should Not Be Used",
    ) as title_mock:
        response = client.post(
            f"/api/conversations/{target}/message",
            json={"content": "How can llm-council cache answers for repeated prompts?"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stage3"]["cached"] is True
    assert payload["metadata"]["answer_cache"]["match_type"] == "semantic"
    assert payload["metadata"]["answer_cache"]["semantic_similarity"] >= 0.86
    run_mock.assert_not_called()
    title_mock.assert_not_called()


@pytest.mark.asyncio
async def test_borderline_semantic_cache_requires_chairman_validation(api_client, monkeypatch):
    _, _, storage = api_client
    import backend.answer_cache as answer_cache

    storage.create_conversation("validation-seed")
    storage.add_user_message("validation-seed", "Explain answer cache behavior for repeated prompts")
    storage.add_assistant_message(
        "validation-seed",
        [{"model": "alpha", "response": "A"}],
        [{"model": "alpha", "parsed_ranking": ["Response A"]}],
        {"model": "chairman", "response": "Validated cache answer."},
    )
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD", 0.95)
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_VALIDATION_THRESHOLD", 0.68)
    query_mock = AsyncMock(
        return_value={
            "content": "CACHE_MATCH: yes\nREASON: Same cache behavior question.",
            "_debug": {"ok": True},
        }
    )
    monkeypatch.setattr(answer_cache, "query_model", query_mock)

    hit = await answer_cache.find_answer_cache_hit_with_validation(
        "Explain cache behavior for similar requests"
    )

    assert hit is not None
    assert hit["metadata"]["answer_cache"]["match_type"] == "validated_semantic"
    assert hit["metadata"]["answer_cache"]["validation"]["approved"] is True
    query_mock.assert_awaited_once()
    metrics = council_metrics.snapshot()["answer_cache"]
    assert metrics["totals"]["validation_attempts"] == 1
    assert metrics["totals"]["validation_approved"] == 1
    assert metrics["match_types"] == {"validated_semantic": 1}


@pytest.mark.asyncio
async def test_borderline_semantic_cache_skips_when_validation_fails(api_client, monkeypatch):
    _, _, storage = api_client
    import backend.answer_cache as answer_cache

    storage.create_conversation("validation-fail-seed")
    storage.add_user_message("validation-fail-seed", "Explain answer cache behavior for repeated prompts")
    storage.add_assistant_message(
        "validation-fail-seed",
        [{"model": "alpha", "response": "A"}],
        [{"model": "alpha", "parsed_ranking": ["Response A"]}],
        {"model": "chairman", "response": "Do not reuse this."},
    )
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD", 0.95)
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_VALIDATION_THRESHOLD", 0.68)
    monkeypatch.setattr(
        answer_cache,
        "query_model",
        AsyncMock(
            return_value={
                "content": "CACHE_MATCH: no\nREASON: Different enough to require fresh council.",
                "_debug": {"ok": True},
            }
        ),
    )

    assert await answer_cache.find_answer_cache_hit_with_validation(
        "Explain cache behavior for similar requests"
    ) is None
    metrics = council_metrics.snapshot()["answer_cache"]
    assert metrics["totals"]["validation_attempts"] == 1
    assert metrics["totals"]["validation_rejected"] == 1


@pytest.mark.asyncio
async def test_borderline_semantic_cache_requires_structured_validation(api_client, monkeypatch):
    _, _, storage = api_client
    import backend.answer_cache as answer_cache

    storage.create_conversation("validation-unstructured-seed")
    storage.add_user_message(
        "validation-unstructured-seed",
        "Explain answer cache behavior for repeated prompts",
    )
    storage.add_assistant_message(
        "validation-unstructured-seed",
        [{"model": "alpha", "response": "A"}],
        [{"model": "alpha", "parsed_ranking": ["Response A"]}],
        {"model": "chairman", "response": "Do not reuse without structured validation."},
    )
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD", 0.95)
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_VALIDATION_THRESHOLD", 0.68)
    monkeypatch.setattr(
        answer_cache,
        "query_model",
        AsyncMock(
            return_value={
                "content": "Yes, this looks similar, but I ignored the requested schema.",
                "_debug": {"ok": True},
            }
        ),
    )

    assert await answer_cache.find_answer_cache_hit_with_validation(
        "Explain cache behavior for similar requests"
    ) is None


def test_direct_message_bypass_cache_forces_fresh_run(api_client):
    client, backend_main, _ = api_client
    seed = client.post("/api/conversations", json={}).json()["id"]

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=([], [], {"model": "chairman", "response": "Cached answer."}, {}),
    ), patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Seed",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        client.post(
            f"/api/conversations/{seed}/message",
            json={"content": "Explain answer caching"},
        )

    target = client.post("/api/conversations", json={}).json()["id"]
    fresh_result = ([], [], {"model": "chairman", "response": "Fresh answer."}, {})
    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=fresh_result,
    ) as run_mock, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Fresh",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(
            f"/api/conversations/{target}/message",
            json={"content": "Explain answer caching", "bypass_cache": True},
        )

    assert response.json()["stage3"]["response"] == "Fresh answer."
    assert run_mock.await_count == 1
    metrics = client.get("/api/metrics/council").json()["answer_cache"]
    assert metrics["totals"]["bypasses"] == 1


def test_stream_message_reuses_cached_answer_without_model_calls(api_client):
    client, backend_main, _ = api_client
    seed = client.post("/api/conversations", json={}).json()["id"]

    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=([], [], {"model": "chairman", "response": "Stream cached answer."}, {}),
    ), patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Seed",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        client.post(
            f"/api/conversations/{seed}/message",
            json={"content": "How does repeat answer caching work?"},
        )

    target = client.post("/api/conversations", json={}).json()["id"]
    with patch.object(
        backend_main,
        "run_full_council",
        new_callable=AsyncMock,
    ) as run_mock, patch.object(
        backend_main,
        "stage1_collect_responses",
        new_callable=AsyncMock,
    ) as stage1_mock, patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
    ) as title_mock:
        response = client.post(
            f"/api/conversations/{target}/message/stream",
            json={"content": "How does repeat answer caching work?"},
        )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == [
        "answer_cache_hit",
        "stage3_complete",
        "title_complete",
        "complete",
    ]
    assert events[1]["data"]["cached"] is True
    run_mock.assert_not_called()
    stage1_mock.assert_not_called()
    title_mock.assert_not_called()
    metrics = client.get("/api/metrics/council").json()["answer_cache"]
    assert metrics["totals"]["hits"] == 1
    assert metrics["latency_ms"]["hit"]["count"] == 1


def test_stream_message_bypass_cache_records_metric(api_client):
    client, backend_main, _ = api_client
    conversation = client.post("/api/conversations", json={}).json()
    conversation_id = conversation["id"]
    mode_selection = {
        "requested_mode": "auto",
        "selected_mode": "quick",
        "confidence": 1.0,
        "reason": "test quick path",
        "source": "heuristic",
    }

    with patch.object(
        backend_main,
        "resolve_deliberation_mode",
        new_callable=AsyncMock,
        return_value=mode_selection,
    ), patch.object(
        backend_main,
        "stage_quick_answer",
        new_callable=AsyncMock,
        return_value=(
            {"model": "chairman", "response": "Fresh quick answer."},
            {"stage": "quick_answer", "successful_models": 1, "failed_models_count": 0},
        ),
    ), patch.object(
        backend_main,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="Bypass stream",
    ), patch.object(
        backend_main,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.post(
            f"/api/conversations/{conversation_id}/message/stream",
            json={"content": "Explain answer caching", "bypass_cache": True},
        )

    assert response.status_code == 200
    metrics = client.get("/api/metrics/council").json()["answer_cache"]
    assert metrics["totals"]["bypasses"] == 1


def test_answer_cache_replay_reports_chronological_hit_samples(api_client):
    _, _, storage = api_client
    from scripts.answer_cache_replay import replay_answer_cache_candidates

    storage.create_conversation("replay-seed")
    storage.add_user_message(
        "replay-seed",
        "Explain duplicate question answer reuse in llm council",
    )
    storage.add_assistant_message(
        "replay-seed",
        [],
        [],
        {"model": "chairman", "response": "Seed answer."},
    )
    storage.create_conversation("replay-hit")
    storage.add_user_message(
        "replay-hit",
        "How can llm-council cache answers for repeated prompts?",
    )
    storage.add_assistant_message(
        "replay-hit",
        [],
        [],
        {"model": "chairman", "response": "Cached-topic answer."},
    )

    report = replay_answer_cache_candidates(limit=20, sample_size=5)

    assert report["totals"]["replayed_questions"] == 1
    assert report["totals"]["hits"] == 1
    assert report["rates"]["hit_rate"] == 1.0
    assert report["served_match_types"] == {"semantic": 1}
    assert report["manual_review_samples"][0]["source_conversation_id"] == "replay-seed"


def test_answer_cache_replay_rejects_negative_limits(api_client):
    from scripts.answer_cache_replay import replay_answer_cache_candidates

    with pytest.raises(ValueError, match="limit must be non-negative"):
        replay_answer_cache_candidates(limit=-1)

    with pytest.raises(ValueError, match="sample_size must be non-negative"):
        replay_answer_cache_candidates(sample_size=-1)


def test_answer_cache_replay_separates_validation_candidates(api_client, monkeypatch):
    _, _, storage = api_client
    import backend.answer_cache as answer_cache
    import scripts.answer_cache_replay as replay

    storage.create_conversation("validation-replay-seed")
    storage.add_user_message(
        "validation-replay-seed",
        "Explain answer cache behavior for repeated prompts",
    )
    storage.add_assistant_message(
        "validation-replay-seed",
        [],
        [],
        {"model": "chairman", "response": "Seed answer."},
    )
    storage.create_conversation("validation-replay-candidate")
    storage.add_user_message(
        "validation-replay-candidate",
        "Explain cache behavior for similar requests",
    )
    storage.add_assistant_message(
        "validation-replay-candidate",
        [],
        [],
        {"model": "chairman", "response": "Candidate answer."},
    )
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD", 0.95)
    monkeypatch.setattr(answer_cache, "ANSWER_CACHE_VALIDATION_THRESHOLD", 0.68)

    report = replay.replay_answer_cache_candidates(limit=20, sample_size=5)

    assert report["totals"]["hits"] == 0
    assert report["totals"]["validation_candidates"] == 1
    assert report["rates"]["hit_rate"] == 0.0
    assert report["validation_match_types"] == {"validated_semantic": 1}


@pytest.mark.asyncio
async def test_mcp_bypass_cache_records_metric(api_client):
    from mcp_server import server as mcp_server

    server = importlib.reload(mcp_server)

    with patch.object(
        server,
        "run_full_council",
        new_callable=AsyncMock,
        return_value=([], [], {"model": "chairman", "response": "Fresh MCP answer."}, {}),
    ), patch.object(
        server,
        "generate_conversation_title",
        new_callable=AsyncMock,
        return_value="MCP bypass",
    ), patch.object(
        server,
        "generate_conversation_summary",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = await server._execute_council_deliberation(
            "Explain answer caching",
            bypass_cache=True,
        )

    assert "Fresh MCP answer." in response
    metrics = json.loads(await server.get_council_metrics())["answer_cache"]
    assert metrics["totals"]["bypasses"] == 1


@pytest.mark.asyncio
async def test_mcp_schema_exposes_bypass_cache_and_hides_context():
    from mcp_server import server

    server = importlib.reload(server)
    tools = await server.mcp.list_tools()
    ask_tool = next(tool for tool in tools if tool.name == "ask_council")

    assert "bypass_cache" in ask_tool.inputSchema["properties"]
    assert "ctx" not in ask_tool.inputSchema["properties"]
