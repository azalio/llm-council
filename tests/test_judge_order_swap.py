"""Tests for the order-swap (position-bias symmetrization) holistic judge."""

import json

import pytest

import backend.eval.judge as judge
from backend.eval.judge import JUDGE_SCHEMA_VERSION, compare_answers_with_judge

GOOD = "GOODANS_TOKEN"
BAD = "BADANS_TOKEN"


def _extract_between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i:j] if j != -1 else text[i:]


def _payload(winner: str) -> str:
    cand, base = (0.9, 0.6) if winner == "candidate" else (0.6, 0.9)
    return json.dumps({
        "schema_version": JUDGE_SCHEMA_VERSION,
        "scores": {
            "candidate": {"factuality": cand, "completeness": cand,
                          "reasoning": cand, "clarity": cand, "overall": cand},
            "baseline": {"factuality": base, "completeness": base,
                         "reasoning": base, "clarity": base, "overall": base},
        },
        "winner": winner,
        "confidence": 0.8,
        "criterion_explanations": {"factuality": "x", "completeness": "x",
                                   "reasoning": "x", "clarity": "x"},
        "overall_explanation": "x",
    })


def _content_based_query(calls):
    """An unbiased judge: picks whichever slot holds the GOOD answer."""
    async def fake_query(model, messages, **kwargs):
        calls.append(messages[0]["content"])
        cand = _extract_between(messages[0]["content"], "<<<CANDIDATE", "CANDIDATE>>>")
        return {"content": _payload("candidate" if GOOD in cand else "baseline"),
                "_debug": {"ok": True}}
    return fake_query


def _first_slot_biased_query(calls):
    """A position-biased judge: always prefers whatever is in the candidate slot."""
    async def fake_query(model, messages, **kwargs):
        calls.append(messages[0]["content"])
        return {"content": _payload("candidate"), "_debug": {"ok": True}}
    return fake_query


@pytest.mark.asyncio
async def test_order_swap_agreement_keeps_winner(monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_ORDER_SWAP_ENABLED", True)
    calls = []
    result = await compare_answers_with_judge(
        question="Q",
        candidate_answer=f"answer with {GOOD}",
        baseline_answer=f"answer with {BAD}",
        judge_model="judge-model",
        query_fn=_content_based_query(calls),
    )
    assert len(calls) == 2  # both orderings
    assert result["available"] is True
    assert result["judge_variant"] == "holistic_order_swap"
    assert result["experimental"]["order_swap"]["agree"] is True
    assert result["winner"] == "candidate"
    # scores averaged in the canonical frame (both orders gave candidate 0.9).
    assert result["scores"]["candidate"]["overall"] == pytest.approx(0.9, abs=1e-4)


@pytest.mark.asyncio
async def test_order_swap_flip_resolves_to_tie(monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_ORDER_SWAP_ENABLED", True)
    calls = []
    result = await compare_answers_with_judge(
        question="Q",
        candidate_answer=f"answer with {GOOD}",
        baseline_answer=f"answer with {BAD}",
        judge_model="judge-model",
        query_fn=_first_slot_biased_query(calls),
    )
    assert len(calls) == 2
    assert result["experimental"]["order_swap"]["agree"] is False
    assert result["winner"] == "tie"  # position-sensitive verdict is suppressed


@pytest.mark.asyncio
async def test_order_swap_flag_off_is_single_call(monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_ORDER_SWAP_ENABLED", False)
    calls = []
    result = await compare_answers_with_judge(
        question="Q",
        candidate_answer=f"answer with {GOOD}",
        baseline_answer=f"answer with {BAD}",
        judge_model="judge-model",
        query_fn=_content_based_query(calls),
    )
    assert len(calls) == 1
    assert "judge_variant" not in result
    assert result["winner"] == "candidate"


@pytest.mark.asyncio
async def test_order_swap_degrades_when_one_order_fails(monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_ORDER_SWAP_ENABLED", True)

    state = {"n": 0}

    async def flaky_query(model, messages, **kwargs):
        state["n"] += 1
        if state["n"] == 2:  # second ordering fails
            return {"content": None, "_debug": {"ok": False, "failure_type": "timeout"}}
        return {"content": _payload("candidate"), "_debug": {"ok": True}}

    result = await compare_answers_with_judge(
        question="Q",
        candidate_answer=f"answer with {GOOD}",
        baseline_answer=f"answer with {BAD}",
        judge_model="judge-model",
        query_fn=flaky_query,
    )
    assert result["available"] is True
    assert result["judge_variant"] == "holistic_order_swap"
    assert result["experimental"]["order_swap"]["partial"]
