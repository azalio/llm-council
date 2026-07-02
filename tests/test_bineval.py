"""Offline tests for the faithful BINEVAL replication primitives.

All model calls are mocked, so these run without network or credentials. They
guard the paper-fidelity invariants: task-level question generation, isolated
source-grounded per-question evaluation, pointwise aggregation, holistic
parsing, and leak-free prompts.
"""

import json

import pytest

from backend.eval.bineval import (
    CONSISTENCY,
    PAPER_CONSISTENCY_QUESTIONS,
    build_holistic_consistency_prompt,
    build_question_generation_prompt,
    build_single_question_prompt,
    generate_binary_questions,
    parse_generated_questions,
    parse_holistic_score,
    parse_single_question_response,
    score_bineval_verdicts,
    score_summary_decomposed,
    score_summary_holistic,
)
from backend.eval.judge import JudgeResponseError
from backend.eval.leakage_audit import (
    audit_bineval_questions,
    audit_live_bineval_prompts,
)


def _ok(content: str) -> dict:
    return {"content": content, "_debug": {"ok": True}}


def _make_query(handler):
    async def query_fn(_model, messages, **_kwargs):
        return _ok(handler(messages[0]["content"]))

    return query_fn


# --------------------------------------------------------------------------
# Question generation (meta-prompt F_LLM(T; M))
# --------------------------------------------------------------------------
def test_parse_generated_questions_validates_and_assigns_ids():
    raw = json.dumps({
        "requirements": ["all facts supported"],
        "questions": [
            {"id": "Q1", "text": "Are all facts supported?", "violation_example": "adds a fact"},
            {"text": "Are names accurate?"},  # missing id -> auto-assigned
        ],
    })
    questions = parse_generated_questions(raw)
    assert [q.id for q in questions] == ["Q1", "Q2"]
    assert questions[0].violation_example == "adds a fact"
    assert questions[1].text == "Are names accurate?"


def test_parse_generated_questions_rejects_empty_list():
    with pytest.raises(JudgeResponseError):
        parse_generated_questions(json.dumps({"questions": []}))


@pytest.mark.asyncio
async def test_generate_binary_questions_happy_path():
    payload = json.dumps({
        "requirements": ["supported"],
        "questions": [{"id": "Q1", "text": "Are all facts supported by the source?"}],
    })
    result = await generate_binary_questions(
        CONSISTENCY, judge_model="m", query_fn=_make_query(lambda _: payload)
    )
    assert result["status"] == "ok"
    assert result["questions"][0].id == "Q1"


@pytest.mark.asyncio
async def test_generate_binary_questions_reports_unparseable():
    result = await generate_binary_questions(
        CONSISTENCY, judge_model="m", query_fn=_make_query(lambda _: "not json")
    )
    assert result["status"] == "generation_unparseable"


# --------------------------------------------------------------------------
# Pointwise per-question evaluation + aggregation
# --------------------------------------------------------------------------
def test_parse_single_question_response_normalizes_verdict():
    parsed = parse_single_question_response('{"verdict": "YES", "explanation": "ok"}')
    assert parsed["verdict"] == "yes"


def test_parse_single_question_response_rejects_out_of_vocab():
    with pytest.raises(JudgeResponseError):
        parse_single_question_response('{"verdict": "maybe"}')


def test_score_bineval_verdicts_is_fraction_of_yes_over_answered():
    verdicts = [
        {"status": "ok", "verdict": "yes"},
        {"status": "ok", "verdict": "no"},
        {"status": "ok", "verdict": "yes"},
        {"status": "failed"},  # excluded from denominator
    ]
    scored = score_bineval_verdicts(verdicts)
    assert scored["answered"] == 3
    assert scored["yes"] == 2
    assert scored["score"] == pytest.approx(2 / 3, abs=1e-6)


def test_score_bineval_verdicts_all_failed_is_unavailable():
    scored = score_bineval_verdicts([{"status": "failed"}, {"status": "unparseable"}])
    assert scored["score"] is None
    assert scored["answered"] == 0


@pytest.mark.asyncio
async def test_score_summary_decomposed_uses_one_call_per_question():
    seen_questions = []

    async def query_fn(_model, messages, **_kwargs):
        content = messages[0]["content"]
        # Each prompt must contain exactly one of the bank's questions.
        present = [q.text for q in PAPER_CONSISTENCY_QUESTIONS if q.text in content]
        seen_questions.append(present)
        return _ok('{"verdict": "yes", "explanation": "x"}')

    questions = list(PAPER_CONSISTENCY_QUESTIONS)
    result = await score_summary_decomposed(
        source="article", summary="summary", questions=questions,
        judge_model="m", query_fn=query_fn,
    )
    assert result["score"] == 1.0
    assert result["answered"] == len(questions)
    # Exactly one question per call -> structural independence.
    assert all(len(present) == 1 for present in seen_questions)
    assert len(seen_questions) == len(questions)


@pytest.mark.asyncio
async def test_single_question_prompt_omits_other_questions():
    q = PAPER_CONSISTENCY_QUESTIONS[0]
    other = PAPER_CONSISTENCY_QUESTIONS[1]
    prompt = build_single_question_prompt(source="s", summary="y", question=q)
    assert q.text in prompt
    assert other.text not in prompt


# --------------------------------------------------------------------------
# Holistic baseline parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("reasoning...\nSCORE: 5", 1.0),
        ("SCORE: 1", 0.0),
        ("SCORE: 3", 0.5),
        ("I think this is a 4 overall.\nSCORE: 4", 0.75),
    ],
)
def test_parse_holistic_score_maps_1_5_to_unit_interval(raw, expected):
    assert parse_holistic_score(raw)["score"] == pytest.approx(expected, abs=1e-6)


def test_parse_holistic_score_falls_back_to_last_integer():
    assert parse_holistic_score("the rating is 2")["raw_score"] == 2


def test_parse_holistic_score_rejects_unparseable():
    with pytest.raises(JudgeResponseError):
        parse_holistic_score("no number here at all")


@pytest.mark.asyncio
async def test_score_summary_holistic_happy_path():
    result = await score_summary_holistic(
        source="s", summary="y", judge_model="m",
        query_fn=_make_query(lambda _: "analysis...\nSCORE: 4"),
    )
    assert result["status"] == "ok"
    assert result["score"] == pytest.approx(0.75, abs=1e-6)


# --------------------------------------------------------------------------
# Leakage: BINEVAL prompts and question bank must be verdict-priming free
# --------------------------------------------------------------------------
def test_live_bineval_prompts_are_leak_free():
    assert audit_live_bineval_prompts() == []


def test_paper_question_bank_is_leak_free():
    assert audit_bineval_questions() == []


def test_meta_prompt_is_task_level_and_omits_instances():
    prompt = build_question_generation_prompt(CONSISTENCY)
    # The meta-prompt sees the task, never a specific summary/source.
    assert CONSISTENCY.task in prompt
    assert "<<SUMMARY>>" not in prompt


def test_holistic_prompt_contains_source_and_summary_slots():
    prompt = build_holistic_consistency_prompt(source="ARTICLE", summary="SUMM")
    assert "ARTICLE" in prompt
    assert "SUMM" in prompt
