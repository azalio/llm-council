"""Tests for bounded Stage 1 council fan-out and provider backoff."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

import backend.config as config
import backend.openrouter as openrouter
from backend.council import stage1_collect_responses


@pytest.mark.asyncio
async def test_stage1_collect_responses_passes_limiter_settings():
    responses = {
        "alpha": {"content": "Alpha", "_debug": {"ok": True, "provider": "openrouter"}},
        "beta": {"content": "Beta", "_debug": {"ok": True, "provider": "openrouter"}},
    }

    with patch("backend.council.COUNCIL_MODELS", ["alpha", "beta"]), \
         patch("backend.council.STAGE1_MAX_CONCURRENCY", 2), \
         patch("backend.council.STAGE1_PROVIDER_BACKOFF_SECONDS", 0.25), \
         patch(
             "backend.council.query_models_parallel",
             new_callable=AsyncMock,
             return_value=responses,
         ) as query_models:
        stage1_results, stage1_debug = await stage1_collect_responses("How should we test?")

    query_models.assert_awaited_once_with(
        ["alpha", "beta"],
        [{"role": "user", "content": "How should we test?"}],
        max_concurrency=2,
        failure_backoff_seconds=0.25,
    )
    assert len(stage1_results) == 2
    assert stage1_debug["successful_models"] == 2


@pytest.mark.asyncio
async def test_query_models_parallel_limits_in_flight_calls(monkeypatch):
    in_flight = 0
    max_seen = 0

    async def fake_query_model(model, messages):
        nonlocal in_flight, max_seen
        in_flight += 1
        max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"content": f"{model} response", "_debug": {"ok": True, "provider": "openrouter"}}

    monkeypatch.setattr(openrouter, "query_model", fake_query_model)

    responses = await openrouter.query_models_parallel(
        ["alpha", "beta", "gamma", "delta"],
        [{"role": "user", "content": "Hello"}],
        max_concurrency=2,
    )

    assert list(responses) == ["alpha", "beta", "gamma", "delta"]
    assert max_seen <= 2


@pytest.mark.asyncio
async def test_query_models_parallel_backs_off_after_provider_failure(monkeypatch):
    starts = []

    async def fake_query_model(model, messages):
        starts.append((model, time.perf_counter()))
        if model == "alpha":
            return {
                "content": None,
                "_debug": {
                    "ok": False,
                    "provider": "openrouter",
                    "failure_type": "timeout",
                },
            }
        return {"content": f"{model} response", "_debug": {"ok": True, "provider": "openrouter"}}

    monkeypatch.setattr(openrouter, "query_model", fake_query_model)

    responses = await openrouter.query_models_parallel(
        ["alpha", "beta"],
        [{"role": "user", "content": "Hello"}],
        max_concurrency=1,
        failure_backoff_seconds=0.02,
    )

    assert responses["alpha"]["content"] is None
    assert responses["beta"]["content"] == "beta response"
    assert starts[1][1] - starts[0][1] >= 0.015


@pytest.mark.asyncio
async def test_query_models_parallel_serializes_same_provider_backoff_waiters(monkeypatch):
    starts = []

    async def fake_query_model(model, messages):
        starts.append((model, time.perf_counter()))
        if model == "alpha":
            return {
                "content": None,
                "_debug": {
                    "ok": False,
                    "provider": "openrouter",
                    "failure_type": "timeout",
                },
            }
        if model == "beta":
            await asyncio.sleep(0.005)
        return {"content": f"{model} response", "_debug": {"ok": True, "provider": "openrouter"}}

    monkeypatch.setattr(openrouter, "query_model", fake_query_model)

    await openrouter.query_models_parallel(
        ["alpha", "beta", "gamma", "delta"],
        [{"role": "user", "content": "Hello"}],
        max_concurrency=2,
        failure_backoff_seconds=0.03,
    )

    start_by_model = {model: started_at for model, started_at in starts}
    assert start_by_model["delta"] - start_by_model["gamma"] >= 0.015


@pytest.mark.asyncio
async def test_provider_backoff_does_not_block_unrelated_provider_paths(monkeypatch):
    start_order = []

    async def fake_query_model(model, messages):
        start_order.append(model)
        if model == "alpha":
            return {
                "content": None,
                "_debug": {
                    "ok": False,
                    "provider": "other-provider",
                    "vendor": "anthropic",
                    "failure_type": "timeout",
                },
            }
        return {"content": f"{model} response", "_debug": {"ok": True, "provider": "other-provider"}}

    def fake_provider_path(model):
        if model in {"alpha", "gamma"}:
            return "other-provider:anthropic"
        return "other-provider:google"

    monkeypatch.setattr(openrouter, "query_model", fake_query_model)
    monkeypatch.setattr(openrouter, "_model_provider_path", fake_provider_path)

    await openrouter.query_models_parallel(
        ["alpha", "gamma", "delta"],
        [{"role": "user", "content": "Hello"}],
        max_concurrency=1,
        failure_backoff_seconds=0.02,
    )

    assert start_order.index("delta") < start_order.index("gamma")


@pytest.mark.asyncio
async def test_provider_backoff_revalidates_after_later_in_flight_failure(monkeypatch):
    starts = {}
    failures = {}

    async def fake_query_model(model, messages):
        starts[model] = time.perf_counter()
        if model == "alpha":
            await asyncio.sleep(0.001)
            failures[model] = time.perf_counter()
            return {
                "content": None,
                "_debug": {
                    "ok": False,
                    "provider": "openrouter",
                    "failure_type": "timeout",
                },
            }
        if model == "beta":
            await asyncio.sleep(0.02)
            failures[model] = time.perf_counter()
            return {
                "content": None,
                "_debug": {
                    "ok": False,
                    "provider": "openrouter",
                    "failure_type": "timeout",
                },
            }
        return {"content": f"{model} response", "_debug": {"ok": True, "provider": "openrouter"}}

    monkeypatch.setattr(openrouter, "query_model", fake_query_model)

    await openrouter.query_models_parallel(
        ["alpha", "beta", "gamma"],
        [{"role": "user", "content": "Hello"}],
        max_concurrency=2,
        failure_backoff_seconds=0.05,
    )

    assert starts["gamma"] - failures["beta"] >= 0.045


def test_stage1_max_concurrency_env_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("COUNCIL_STAGE1_MAX_CONCURRENCY", "0")

    with pytest.raises(ValueError, match="COUNCIL_STAGE1_MAX_CONCURRENCY"):
        config._read_positive_int_env("COUNCIL_STAGE1_MAX_CONCURRENCY", 3)

    monkeypatch.setenv("COUNCIL_STAGE1_MAX_CONCURRENCY", "")

    with pytest.raises(ValueError, match="COUNCIL_STAGE1_MAX_CONCURRENCY"):
        config._read_positive_int_env("COUNCIL_STAGE1_MAX_CONCURRENCY", 3)


def test_stage1_provider_backoff_env_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS", "-0.1")

    with pytest.raises(ValueError, match="COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS"):
        config._read_non_negative_float_env(
            "COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS",
            0.25,
        )

    monkeypatch.setenv("COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS", "0")
    assert config._read_non_negative_float_env(
        "COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS",
        0.25,
    ) == 0.0

    for raw_value in ("nan", "inf", "-inf"):
        monkeypatch.setenv("COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS", raw_value)
        with pytest.raises(ValueError, match="COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS"):
            config._read_non_negative_float_env(
                "COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS",
                0.25,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_backoff", [float("nan"), float("inf"), float("-inf")])
async def test_query_models_parallel_rejects_non_finite_backoff(monkeypatch, bad_backoff):
    query_model = AsyncMock()
    monkeypatch.setattr(openrouter, "query_model", query_model)

    with pytest.raises(ValueError, match="failure_backoff_seconds"):
        await openrouter.query_models_parallel(
            ["alpha"],
            [{"role": "user", "content": "Hello"}],
            failure_backoff_seconds=bad_backoff,
        )

    query_model.assert_not_awaited()
