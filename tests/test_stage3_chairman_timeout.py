"""Regression tests for the Stage 3 chairman timeout (issue #34).

Deep mode's chairman prompt embeds Stage 1 + Stage 2 + Stage 2b + hedge/
attribution instructions across every council model, and is provably larger
than standard/quick mode's prompt. `stage3_synthesize_final()` previously
called `query_model()` with no timeout override, so it always used the
hardcoded 600s default (`backend/openrouter.py`) regardless of mode — a real
deep-mode run timed out there. These tests assert the timeout is now
config-driven and selects the larger deep-mode budget whenever Stage 2b
results are present.
"""

from unittest.mock import patch

import pytest

from backend.config import (
    COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS,
    COUNCIL_STAGE3_TIMEOUT_SECONDS,
)
from backend.council import stage3_synthesize_final

STAGE1_RESULTS = [{"model": "alpha", "response": "Original answer."}]
STAGE2_RESULTS = [{"model": "alpha", "ranking": "FINAL RANKING:\n1. Response A"}]
STAGE2B_RESULTS = [
    {
        "model": "alpha",
        "original_label": "Response A",
        "revision": "Revised answer.",
        "revision_policy": "evidence_gated",
    }
]


def test_deep_timeout_budget_exceeds_standard_budget():
    # Guards the config defaults themselves: if these were ever flipped or
    # made equal, the mode-aware selection below would stop meaning anything.
    assert COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS > COUNCIL_STAGE3_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_standard_mode_uses_standard_timeout_budget():
    captured_kwargs = {}

    async def fake_query_model(model, messages, **kwargs):
        captured_kwargs.update(kwargs)
        return {"content": "Final answer [A].", "_debug": {"ok": True}}

    with patch("backend.council.query_model", side_effect=fake_query_model):
        await stage3_synthesize_final(
            "Question?", STAGE1_RESULTS, STAGE2_RESULTS, {"Response A": "alpha"}
        )

    assert captured_kwargs["timeout"] == COUNCIL_STAGE3_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_deep_mode_with_stage2b_results_uses_deep_timeout_budget():
    captured_kwargs = {}

    async def fake_query_model(model, messages, **kwargs):
        captured_kwargs.update(kwargs)
        return {"content": "Final answer [A].", "_debug": {"ok": True}}

    with patch("backend.council.query_model", side_effect=fake_query_model):
        await stage3_synthesize_final(
            "Question?",
            STAGE1_RESULTS,
            STAGE2_RESULTS,
            {"Response A": "alpha"},
            stage2b_results=STAGE2B_RESULTS,
        )

    assert captured_kwargs["timeout"] == COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_deep_mode_chairman_call_no_longer_capped_at_standard_budget():
    """Reproduces the original bug: a chairman call slow enough to exceed the
    old fixed 600s budget must succeed now that deep mode gets a larger one.
    """

    async def fake_query_model(model, messages, timeout=None, **kwargs):
        # Simulates query_model()'s real timeout enforcement (httpx.TimeoutException
        # -> failure_type="timeout") for a call slower than the pre-fix budget.
        simulated_call_duration = COUNCIL_STAGE3_TIMEOUT_SECONDS + 60
        if timeout is None or timeout < simulated_call_duration:
            return {
                "content": None,
                "_debug": {"ok": False, "failure_type": "timeout"},
            }
        return {"content": "Final answer [A].", "_debug": {"ok": True}}

    with patch("backend.council.query_model", side_effect=fake_query_model):
        result, stage_debug = await stage3_synthesize_final(
            "Question?",
            STAGE1_RESULTS,
            STAGE2_RESULTS,
            {"Response A": "alpha"},
            stage2b_results=STAGE2B_RESULTS,
        )

    assert result["response"] == "Final answer [A]."
    assert stage_debug["failed_models"] == []


@pytest.mark.asyncio
async def test_chairman_timeout_failure_is_distinguishable_from_other_failures():
    async def fake_query_model(model, messages, **kwargs):
        return {
            "content": None,
            "_debug": {"ok": False, "failure_type": "timeout"},
        }

    with patch("backend.council.query_model", side_effect=fake_query_model):
        result, stage_debug = await stage3_synthesize_final(
            "Question?", STAGE1_RESULTS, STAGE2_RESULTS, {"Response A": "alpha"}
        )

    assert result["response"] == "Error: Unable to generate final synthesis."
    assert stage_debug["failed_models"][0]["failure_type"] == "timeout"
    assert stage_debug["failed_models"][0]["failure_type"] != "chairman_failed"
