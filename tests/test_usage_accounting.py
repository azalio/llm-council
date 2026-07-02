"""Tests for token/cost usage accounting (issue #26).

Covers the full pipeline: provider parsing -> stage aggregation -> run-level
debug -> rolling metrics -> MCP debug rendering. Usage must be additive and
opt-in: when no provider in a run reports usage, none of these layers should
fabricate a zeroed usage block.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.metrics import CouncilMetricsCollector, build_council_run_debug, council_metrics
from backend.usage import (
    normalize_anthropic_usage,
    normalize_google_usage,
    normalize_openai_usage,
    sum_usage,
)
from backend.council import (
    _build_stage_debug,
    _combine_stage_debug,
    _with_usage,
    build_run_status,
    run_full_council,
    stage1_collect_responses,
)
from mcp_server.server import format_debug_output


@pytest.fixture(autouse=True)
def reset_council_metrics():
    council_metrics.reset()
    yield
    council_metrics.reset()


# --- backend/usage.py normalization -----------------------------------------


def test_normalize_openai_usage_extracts_and_defaults_total():
    usage = normalize_openai_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_normalize_openai_usage_prefers_explicit_total():
    usage = normalize_openai_usage(
        {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 999}}
    )
    assert usage["total_tokens"] == 999


def test_normalize_openai_usage_returns_none_when_absent():
    assert normalize_openai_usage({}) is None
    assert normalize_openai_usage({"usage": {}}) is None
    assert normalize_openai_usage({"usage": "not-a-dict"}) is None


def test_normalize_openai_usage_degrades_gracefully_on_malformed_field():
    """A malformed usage field must not raise: it must never fail an otherwise
    successful model call just because a metric field was garbage."""
    usage = normalize_openai_usage(
        {"usage": {"prompt_tokens": "not-a-number", "completion_tokens": 5}}
    )
    assert usage == {"prompt_tokens": 0, "completion_tokens": 5, "total_tokens": 5}


def test_normalize_anthropic_usage_degrades_gracefully_on_malformed_field():
    usage = normalize_anthropic_usage({"usage": {"input_tokens": [1, 2], "output_tokens": 4}})
    assert usage == {"prompt_tokens": 0, "completion_tokens": 4, "total_tokens": 4}


def test_sum_usage_degrades_gracefully_on_malformed_entry():
    total = sum_usage([{"prompt_tokens": "bad", "completion_tokens": 3, "total_tokens": 3}])
    assert total == {"prompt_tokens": 0, "completion_tokens": 3, "total_tokens": 3}


def test_normalize_anthropic_usage_maps_input_output_tokens():
    usage = normalize_anthropic_usage({"usage": {"input_tokens": 20, "output_tokens": 8}})
    assert usage == {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}


def test_normalize_anthropic_usage_returns_none_when_absent():
    assert normalize_anthropic_usage({}) is None


def test_normalize_google_usage_maps_prompt_and_candidates_token_count():
    usage = normalize_google_usage(
        {"usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 12, "totalTokenCount": 42}}
    )
    assert usage == {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42}


def test_normalize_google_usage_returns_none_when_absent():
    assert normalize_google_usage({}) is None


def test_sum_usage_adds_across_calls_and_skips_none():
    total = sum_usage(
        [
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            None,
            {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        ]
    )
    assert total == {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}


def test_sum_usage_returns_none_when_nothing_seen():
    assert sum_usage([None, None]) is None
    assert sum_usage([]) is None


# --- backend/council.py per-model "omit, don't fabricate null" contract -----


def test_with_usage_omits_key_when_none():
    entry = _with_usage({"model": "alpha"}, None)
    assert "usage" not in entry


def test_with_usage_attaches_key_when_present():
    entry = _with_usage({"model": "alpha"}, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    assert entry["usage"] == {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


# --- backend/council.py stage-level aggregation ------------------------------


@pytest.mark.asyncio
async def test_stage1_collect_responses_aggregates_usage_and_persists_per_model():
    responses = {
        "alpha": {
            "content": "Alpha answer",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
        "beta": {
            "content": "Beta answer",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
    }

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta"]), \
         patch("backend.council.query_models_parallel", new_callable=AsyncMock, return_value=responses):
        stage1_results, stage_debug = await stage1_collect_responses("question")

    assert {"model": "alpha", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}.items() <= stage1_results[0].items()
    assert stage_debug["usage"] == {"prompt_tokens": 30, "completion_tokens": 13, "total_tokens": 43}


@pytest.mark.asyncio
async def test_stage1_collect_responses_omits_usage_when_provider_has_no_data():
    responses = {
        "alpha": {"content": "Alpha answer", "_debug": {"ok": True, "provider": "internal"}},
    }
    with patch("backend.council.COUNCIL_MODELS", ["alpha"]), \
         patch("backend.council.query_models_parallel", new_callable=AsyncMock, return_value=responses):
        stage1_results, stage_debug = await stage1_collect_responses("question")

    assert "usage" not in stage1_results[0]
    assert "usage" not in stage_debug


def test_combine_stage_debug_sums_usage_across_entries():
    first = _build_stage_debug(
        "stage1", __import__("time").perf_counter(), requested_models=1, successful_models=1,
        failed_models=[], usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    )
    second = _build_stage_debug(
        "stage1", __import__("time").perf_counter(), requested_models=1, successful_models=1,
        failed_models=[], usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    )
    combined = _combine_stage_debug("stage1", [first, second], requested_models=2)
    assert combined["usage"] == {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}


# --- run-level rollup via build_council_run_debug / build_run_status --------


def test_build_council_run_debug_sums_usage_across_stages():
    stage1_debug = {"successful_models": 1, "failed_models_count": 0, "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
    stage2_debug = {"successful_models": 1, "failed_models_count": 0, "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}}
    debug = build_council_run_debug(
        request_id="req-usage",
        thorough=False,
        started_at=__import__("time").perf_counter(),
        stage1_debug=stage1_debug,
        stage2_debug=stage2_debug,
    )
    assert debug["usage"] == {"prompt_tokens": 16, "completion_tokens": 6, "total_tokens": 22}


def test_build_council_run_debug_omits_usage_when_no_stage_has_it():
    stage1_debug = {"successful_models": 1, "failed_models_count": 0}
    debug = build_council_run_debug(
        request_id="req-no-usage",
        thorough=False,
        started_at=__import__("time").perf_counter(),
        stage1_debug=stage1_debug,
    )
    assert "usage" not in debug


def test_build_run_status_surfaces_run_and_stage_usage():
    debug = {
        "successful_council_models": 1,
        "failed_council_models": 0,
        "usage": {"prompt_tokens": 16, "completion_tokens": 6, "total_tokens": 22},
        "stages": {
            "stage1": {
                "requested_models": 1,
                "successful_models": 1,
                "failed_models_count": 0,
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        },
    }
    status = build_run_status(debug)
    assert status["usage"] == {"prompt_tokens": 16, "completion_tokens": 6, "total_tokens": 22}
    assert status["stages"]["stage1"]["usage"] == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}


# --- end-to-end run_full_council + persisted metadata ------------------------


@pytest.mark.asyncio
async def test_run_full_council_exposes_aggregated_usage_end_to_end():
    stage1_responses = {
        "alpha": {
            "content": "Alpha answer",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
        "beta": {
            "content": "Beta answer",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
    }
    stage2_responses = {
        "alpha": {
            "content": "FINAL RANKING:\n1. Response A\n2. Response B",
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
        "beta": {
            "content": "FINAL RANKING:\n1. Response B\n2. Response A",
            "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
    }
    stage3_response = {
        "content": "Final synthesis",
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        "_debug": {"ok": True, "provider": "openrouter"},
    }

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta"]), \
         patch(
             "backend.council.query_models_parallel",
             new_callable=AsyncMock,
             side_effect=[stage1_responses, stage2_responses],
         ), \
         patch("backend.council.query_model", new_callable=AsyncMock, return_value=stage3_response):
        stage1, stage2, stage3, metadata = await run_full_council("Why does testing matter?")

    debug = metadata["debug"]
    assert debug["stages"]["stage1"]["usage"] == {"prompt_tokens": 30, "completion_tokens": 13, "total_tokens": 43}
    assert debug["stages"]["stage2"]["usage"] == {"prompt_tokens": 23, "completion_tokens": 7, "total_tokens": 30}
    assert debug["stages"]["stage3"]["usage"] == {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
    assert debug["usage"] == {"prompt_tokens": 103, "completion_tokens": 40, "total_tokens": 143}

    run_status = metadata["run_status"]
    assert run_status["usage"] == debug["usage"]

    # Per-model usage rides along in stage1/stage2 results, ready for storage.
    assert stage1[0]["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert stage2[0]["usage"]["total_tokens"] in (15,)
    assert stage3["usage"] == {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}


@pytest.mark.asyncio
async def test_run_full_council_omits_usage_when_providers_report_none():
    stage1_responses = {
        "alpha": {"content": "Alpha answer", "_debug": {"ok": True, "provider": "internal"}},
    }
    stage2_responses = {
        "alpha": {"content": "FINAL RANKING:\n1. Response A", "_debug": {"ok": True, "provider": "internal"}},
    }
    stage3_response = {"content": "Final synthesis", "_debug": {"ok": True, "provider": "internal"}}

    with patch("backend.council.COUNCIL_MODELS", ["alpha"]), \
         patch(
             "backend.council.query_models_parallel",
             new_callable=AsyncMock,
             side_effect=[stage1_responses, stage2_responses],
         ), \
         patch("backend.council.query_model", new_callable=AsyncMock, return_value=stage3_response):
        _, _, _, metadata = await run_full_council("Why does testing matter?")

    assert "usage" not in metadata["debug"]
    assert "usage" not in metadata["run_status"]


@pytest.mark.asyncio
async def test_run_full_council_quick_mode_surfaces_usage():
    """Quick mode's single chairman call is a `stages["quick_answer"]` entry, not
    `stages["stage1"]` — confirm the run-level rollup still picks it up."""
    with patch(
        "backend.council.query_model",
        new_callable=AsyncMock,
        return_value={
            "content": "Four.",
            "usage": {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8},
            "_debug": {"ok": True, "provider": "openrouter"},
        },
    ):
        stage1_results, _, stage3_result, metadata = await run_full_council(
            "What is 2 + 2?",
            mode="quick",
        )

    assert stage1_results[0]["usage"] == {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}
    assert stage3_result["usage"] == {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}
    assert metadata["debug"]["usage"] == {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}
    assert metadata["run_status"]["usage"] == {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}


def test_stream_message_quick_mode_persists_usage(api_client):
    """The streaming FastAPI endpoint reconstructs its own quick-mode stage1_results
    (mirrors backend.council.run_full_council manually) — confirm that path also
    carries usage into the persisted stage1 blob, not just the direct-call path."""
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
    quick_usage = {"prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8}
    quick_debug = {
        "stage": "quick_answer",
        "duration_ms": 5.0,
        "requested_models": 1,
        "successful_models": 1,
        "failed_models_count": 0,
        "failed_models": [],
        "usage": quick_usage,
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
        return_value=(
            {"model": "chairman", "response": "Four.", "mode": "quick", "usage": quick_usage},
            quick_debug,
        ),
    ), patch.object(
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
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    assert stage3_event["metadata"]["run_status"]["usage"] == quick_usage

    stored = client.get(f"/api/conversations/{conversation_id}").json()
    assert stored["messages"][1]["stage1"][0]["usage"] == quick_usage


# --- backend/metrics.py rolling token aggregates -----------------------------


def test_metrics_collector_accumulates_tokens_across_runs():
    collector = CouncilMetricsCollector()
    debug_with_usage = {
        "stages": {"stage1": {"duration_ms": 10, "failed_models_count": 0}},
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
    }
    debug_without_usage = {
        "stages": {"stage1": {"duration_ms": 10, "failed_models_count": 0}},
    }

    collector.record_run(debug_with_usage)
    collector.record_run(debug_with_usage)
    snapshot = collector.record_run(debug_without_usage)

    tokens = snapshot["tokens"]
    assert tokens["totals"] == {
        "runs_with_usage": 2,
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
    }
    assert tokens["average_total_tokens_per_run"] == 14.0


def test_metrics_collector_snapshot_has_zeroed_tokens_block_when_no_usage_seen():
    collector = CouncilMetricsCollector()
    snapshot = collector.record_run({"stages": {}})
    assert snapshot["tokens"]["totals"] == {
        "runs_with_usage": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    assert snapshot["tokens"]["average_total_tokens_per_run"] == 0.0


# --- MCP debug rendering ------------------------------------------------------


def test_format_debug_output_renders_run_and_stage_token_lines():
    debug = {
        "request_id": "req-1",
        "duration_ms": 100,
        "successful_council_models": 1,
        "failed_council_models": 0,
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        "stages": {
            "stage1": {
                "successful_models": 1,
                "requested_models": 1,
                "duration_ms": 50,
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                "failed_models": [],
            },
        },
    }
    rendered = format_debug_output(debug)
    assert "- Tokens: `10` prompt + `4` completion = `14` total" in rendered
    assert "Tokens: `14` total" in rendered


def test_format_debug_output_omits_token_lines_when_usage_absent():
    debug = {
        "request_id": "req-2",
        "duration_ms": 100,
        "successful_council_models": 1,
        "failed_council_models": 0,
        "stages": {
            "stage1": {
                "successful_models": 1,
                "requested_models": 1,
                "duration_ms": 50,
                "failed_models": [],
            },
        },
    }
    rendered = format_debug_output(debug)
    assert "Tokens" not in rendered
