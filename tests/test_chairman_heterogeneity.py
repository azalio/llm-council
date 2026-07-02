"""Tests for chairman/council model heterogeneity."""

import pytest

from backend.config import (
    CHAIRMAN_MODEL_FAMILY,
    CHAIRMAN_MODEL_OPENROUTER,
    COUNCIL_MODELS_OPENROUTER,
    _get_float_env,
    infer_model_family,
    validate_chairman_heterogeneity,
)
from mcp_server.server import get_available_models


@pytest.mark.parametrize(
    ("model", "expected_family"),
    [
        ("openai/gpt-5.1", "openai"),
        ("x-ai/grok-4", "xai"),
        ("gpt-5.4", "openai"),
        ("claude-opus-4-6", "anthropic"),
        ("google/gemini-3.1-pro-preview", "google"),
        ("gemini-3-flash-preview", "google"),
        ("deepseek/deepseek-v3.2-exp", "deepseek"),
        ("qwen/qwen3-235b-a22b-2507", "qwen"),
    ],
)
def test_infer_model_family_normalizes_provider_prefixes(model, expected_family):
    assert infer_model_family(model) == expected_family


@pytest.mark.parametrize(
    ("provider", "council_models", "chairman_model"),
    [
        ("openrouter", COUNCIL_MODELS_OPENROUTER, CHAIRMAN_MODEL_OPENROUTER),
    ],
)
def test_configured_chairmen_are_outside_council_families(
    provider,
    council_models,
    chairman_model,
):
    summary = validate_chairman_heterogeneity(
        council_models,
        chairman_model,
        provider=provider,
    )

    assert chairman_model not in council_models
    assert summary["chairman_family"] not in summary["council_families"].values()


def test_exact_chairman_overlap_is_rejected():
    with pytest.raises(ValueError, match="exactly matches"):
        validate_chairman_heterogeneity(
            ["openai/gpt-5.1", "anthropic/claude-sonnet-4.5"],
            "openai/gpt-5.1",
            provider="openrouter",
        )


def test_chairman_family_overlap_is_rejected():
    with pytest.raises(ValueError, match="same model family"):
        validate_chairman_heterogeneity(
            ["openai/gpt-5.1", "anthropic/claude-sonnet-4.5"],
            "openai/o3",
            provider="openrouter",
        )


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf", "not-a-number"])
def test_float_env_rejects_invalid_probability_threshold(monkeypatch, value):
    monkeypatch.setenv("TEST_FLOAT_ENV", value)

    with pytest.raises(ValueError, match="finite number between 0 and 1"):
        _get_float_env("TEST_FLOAT_ENV", 0.5)


def test_float_env_accepts_probability_threshold(monkeypatch):
    monkeypatch.setenv("TEST_FLOAT_ENV", "0.75")

    assert _get_float_env("TEST_FLOAT_ENV", 0.5) == 0.75


@pytest.mark.asyncio
async def test_get_available_models_surfaces_chairman_family():
    output = await get_available_models()

    assert "Chairman family:" in output
    assert CHAIRMAN_MODEL_FAMILY in output
