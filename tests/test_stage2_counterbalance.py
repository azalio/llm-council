"""Tests for Stage 2 per-ranker order counterbalancing."""

import pytest

import backend.council as council
from backend.council import (
    _relabel_responses_in_text,
    calculate_aggregate_rankings,
    stage2_collect_rankings,
)

STAGE1 = [
    {"model": "m0", "response": "answer zero"},
    {"model": "m1", "response": "answer one"},
    {"model": "m2", "response": "answer two"},
]
RANKERS = ["r0", "r1", "r2"]

# A ranker with pure position bias: always ranks the presentation slots A, B, C
# in order, regardless of content.
BIASED_RANKING = "Some eval.\n\nFINAL RANKING:\n1. Response A\n2. Response B\n3. Response C"


def test_relabel_is_atomic():
    out = _relabel_responses_in_text(
        "Response A beats Response B.", {"A": "C", "B": "A"}
    )
    assert out == "Response C beats Response A."


def _fake_query_model_factory(seen):
    async def fake_query_model(model, messages, **kwargs):
        seen.append(messages[0]["content"])
        return {"content": BIASED_RANKING, "_debug": {"ok": True}}
    return fake_query_model


async def _fake_qmp(models, messages, **kwargs):
    return {m: {"content": BIASED_RANKING, "_debug": {"ok": True}} for m in models}


@pytest.mark.asyncio
async def test_counterbalance_neutralizes_position_bias(monkeypatch):
    monkeypatch.setattr(council, "STAGE2_COUNTERBALANCE_ENABLED", True)
    seen = []
    monkeypatch.setattr(council, "query_model", _fake_query_model_factory(seen))

    stage2_results, label_to_model, _ = await stage2_collect_rankings(
        "Q", STAGE1, models=RANKERS
    )
    # Each ranker saw a different rotation (distinct prompts).
    assert len(seen) == 3
    assert len(set(seen)) == 3

    agg = calculate_aggregate_rankings(stage2_results, label_to_model)
    ranks = {row["model"]: row["average_rank"] for row in agg}
    # The position bias (always slot A first) is spread across all models by the
    # rotation, so no model wins purely from its slot — all average to the middle.
    assert set(ranks) == {"m0", "m1", "m2"}
    assert ranks["m0"] == ranks["m1"] == ranks["m2"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_without_counterbalance_position_bias_picks_slot_a(monkeypatch):
    monkeypatch.setattr(council, "STAGE2_COUNTERBALANCE_ENABLED", False)
    monkeypatch.setattr(council, "query_models_parallel", _fake_qmp)

    stage2_results, label_to_model, _ = await stage2_collect_rankings(
        "Q", STAGE1, models=RANKERS
    )
    agg = calculate_aggregate_rankings(stage2_results, label_to_model)
    # Shared fixed order: every ranker puts slot A (= m0) first → m0 wins on position.
    assert agg[0]["model"] == "m0"
    assert agg[0]["average_rank"] == pytest.approx(1.0)
