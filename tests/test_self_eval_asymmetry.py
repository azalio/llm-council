"""Tests for the self-evaluation asymmetry benchmark (arXiv:2606.28050, issue #31).

Covers the deterministic oracle checks, the generation/self-eval/C-MASK/C-SWAP
orchestration, and — most importantly — that `compute_asymmetry_metrics()`
correctly identifies a rubber-stamp evaluator (always says "yes") as unreliable:
high recall but precision no better than the base generation accuracy, per the
issue's acceptance criteria. Fully offline; no provider access.
"""

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.eval.judge import JudgeResponseError
from backend.eval.self_eval_asymmetry import (
    DEFAULT_CORPUS_PATH,
    REDACTED_ANSWER,
    SelfEvalCase,
    check_answer_against_gold,
    compute_asymmetry_metrics,
    load_corpus,
    parse_self_eval_verdict,
    run_case,
)
from scripts.self_eval_asymmetry import run as run_self_eval_asymmetry


# --- deterministic ground-truth checks ---------------------------------------


def test_check_contains_ci():
    assert check_answer_against_gold({"type": "contains_ci", "value": "Tokyo"}, "The capital is Tokyo.")
    assert not check_answer_against_gold({"type": "contains_ci", "value": "Tokyo"}, "The capital is Osaka.")


def test_check_contains_any_ci():
    check = {"type": "contains_any_ci", "values": ["18th", "eighteenth"]}
    assert check_answer_against_gold(check, "Born in the eighteenth century.")
    assert check_answer_against_gold(check, "The 18th century.")
    assert not check_answer_against_gold(check, "The 19th century.")


def test_check_numeric_exact():
    check = {"type": "numeric_exact", "value": 391}
    assert check_answer_against_gold(check, "The answer is 391.")
    assert not check_answer_against_gold(check, "The answer is 380.")


def test_check_numeric_exact_with_tolerance():
    check = {"type": "numeric_exact", "value": 100, "tolerance": 2}
    assert check_answer_against_gold(check, "About 101.")
    assert not check_answer_against_gold(check, "About 105.")


def test_check_false_premise_flag():
    check = {"type": "false_premise_flag"}
    assert check_answer_against_gold(check, "That premise is false; he never won it for that.")
    assert not check_answer_against_gold(check, "He won it in 1921.")


def test_check_unknown_type_raises():
    with pytest.raises(ValueError):
        check_answer_against_gold({"type": "nonsense"}, "anything")


# --- corpus loading -----------------------------------------------------------


def test_load_corpus_has_all_four_categories():
    corpus = load_corpus(DEFAULT_CORPUS_PATH)
    categories = {case.category for case in corpus}
    assert categories == {"short_answer", "numeric", "multi_hop", "false_premise"}
    assert len(corpus) == 8
    assert all(case.wrong_plausible for case in corpus)


# --- verdict parsing ------------------------------------------------------------


def test_parse_self_eval_verdict_valid():
    parsed = parse_self_eval_verdict('{"verdict": "YES", "explanation": "looks right"}')
    assert parsed == {"verdict": "yes", "explanation": "looks right"}


def test_parse_self_eval_verdict_rejects_out_of_vocabulary():
    with pytest.raises(JudgeResponseError):
        parse_self_eval_verdict('{"verdict": "maybe"}')


def test_parse_self_eval_verdict_rejects_unparseable_text():
    with pytest.raises(JudgeResponseError):
        parse_self_eval_verdict("not json at all")


# --- run_case orchestration -----------------------------------------------------


def _verdict_response(verdict: str):
    return {"content": f'{{"verdict": "{verdict}", "explanation": "x"}}', "_debug": {"ok": True}}


@pytest.mark.asyncio
async def test_run_case_full_success_path_with_cswap():
    case = SelfEvalCase(
        id="c1", category="short_answer", question="Capital of Japan?",
        check={"type": "contains_ci", "value": "Tokyo"}, wrong_plausible="Osaka.",
    )
    calls = []

    async def fake_query(model, messages, **kwargs):
        content = messages[0]["content"]
        calls.append(content)
        if "Judge whether YOUR OWN answer is correct" not in content:
            return {"content": "Tokyo.", "_debug": {"ok": True}}
        if REDACTED_ANSWER in content:
            return _verdict_response("no")
        if "Osaka." in content:
            return _verdict_response("no")
        return _verdict_response("yes")

    result = await run_case(case, model="test-model", query_fn=fake_query)

    assert result["status"] == "ok"
    assert result["generation_correct"] is True
    assert result["self_eval"]["verdict"] == "yes"
    assert result["cmask_eval"]["verdict"] == "no"
    assert result["cswap_eval"]["verdict"] == "no"
    assert result["calls"] == 4
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_run_case_skips_cswap_without_wrong_plausible():
    case = SelfEvalCase(
        id="c1", category="short_answer", question="Capital of Japan?",
        check={"type": "contains_ci", "value": "Tokyo"}, wrong_plausible=None,
    )

    async def fake_query(model, messages, **kwargs):
        content = messages[0]["content"]
        if "Judge whether YOUR OWN answer is correct" not in content:
            return {"content": "Tokyo.", "_debug": {"ok": True}}
        return _verdict_response("yes")

    result = await run_case(case, model="test-model", query_fn=fake_query)

    assert result["cswap_eval"] is None
    assert result["calls"] == 3


@pytest.mark.asyncio
async def test_run_case_short_circuits_on_generation_failure():
    case = SelfEvalCase(
        id="c1", category="short_answer", question="Capital of Japan?",
        check={"type": "contains_ci", "value": "Tokyo"},
    )

    async def failing_query(model, messages, **kwargs):
        return {"content": None, "_debug": {"ok": False, "failure_type": "timeout"}}

    result = await run_case(case, model="test-model", query_fn=failing_query)

    assert result["status"] == "generation_failed"
    assert result["calls"] == 1


# --- compute_asymmetry_metrics: the core reliability claim ---------------------


def _row(*, generation_correct: bool, self_eval_verdict: str | None, self_eval_status: str = "ok"):
    return {
        "status": "ok",
        "generation_correct": generation_correct,
        "self_eval": {"status": self_eval_status, "verdict": self_eval_verdict},
        "cmask_eval": None,
        "cswap_eval": None,
        "calls": 2,
    }


def test_compute_asymmetry_metrics_perfect_evaluator():
    rows = [
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=False, self_eval_verdict="no"),
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=False, self_eval_verdict="no"),
    ]
    metrics = compute_asymmetry_metrics(rows)
    assert metrics["ga"] == 0.5
    assert metrics["ea"] == 1.0
    assert metrics["delta"] == 0.5
    assert metrics["evaluation_precision"] == 1.0
    assert metrics["evaluation_recall"] == 1.0


def test_compute_asymmetry_metrics_rubber_stamp_evaluator_is_not_reliable():
    """A rubber-stamp evaluator that always says "yes" must show high recall but
    precision no better than the base generation accuracy — proving it adds no
    discriminative signal, per the issue's acceptance criterion."""
    rows = [
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=False, self_eval_verdict="yes"),
    ]
    metrics = compute_asymmetry_metrics(rows)
    assert metrics["ga"] == 0.75
    assert metrics["evaluation_recall"] == 1.0
    assert metrics["evaluation_precision"] == metrics["ga"]
    assert metrics["evaluation_precision"] < 1.0
    assert metrics["confusion"] == {"tp": 3, "fp": 1, "tn": 0, "fn": 0}


def test_compute_asymmetry_metrics_negative_delta_case():
    """Evaluation worse than generation: GA high, EA low -> negative Delta,
    matching the paper's central finding on 3 of 4 benchmarks."""
    rows = [
        _row(generation_correct=True, self_eval_verdict="no"),   # correct, wrongly rejected
        _row(generation_correct=True, self_eval_verdict="no"),   # correct, wrongly rejected
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=False, self_eval_verdict="no"),
    ]
    metrics = compute_asymmetry_metrics(rows)
    assert metrics["ga"] == 0.75
    assert metrics["ea"] == 0.5
    assert metrics["delta"] == -0.25


def test_compute_asymmetry_metrics_excludes_unparseable_from_ea_but_counts_them():
    rows = [
        _row(generation_correct=True, self_eval_verdict="yes"),
        _row(generation_correct=True, self_eval_verdict=None, self_eval_status="unparseable"),
    ]
    metrics = compute_asymmetry_metrics(rows)
    assert metrics["n_self_eval_unparseable"] == 1
    assert metrics["ea"] == 1.0  # computed only over the one parseable row


def test_compute_asymmetry_metrics_empty_rows_are_all_none():
    metrics = compute_asymmetry_metrics([])
    assert metrics["ga"] is None
    assert metrics["ea"] is None
    assert metrics["delta"] is None
    assert metrics["evaluation_precision"] is None
    assert metrics["evaluation_recall"] is None


def test_compute_asymmetry_metrics_cmask_cswap_unavailable_reason_when_absent():
    rows = [_row(generation_correct=True, self_eval_verdict="yes")]
    metrics = compute_asymmetry_metrics(rows)
    assert metrics["cmask"]["flip_rate"] is None
    assert "unavailable_reason" in metrics["cmask"]
    assert metrics["cswap"]["rejection_rate"] is None
    assert "unavailable_reason" in metrics["cswap"]


# --- CLI self-test mode: deterministic, offline, end-to-end --------------------


@pytest.mark.asyncio
async def test_cli_self_test_mode_runs_offline_and_populates_every_metric():
    args = argparse.Namespace(
        corpus=str(DEFAULT_CORPUS_PATH),
        model="stub-model",
        limit=None,
        concurrency=4,
        include_rows=False,
        live=False,
        self_test=True,
    )
    report = await run_self_eval_asymmetry(args)

    assert report["mode"] == "self-test"
    assert report["n_cases"] == 8
    overall = report["overall"]
    assert overall["n_generation_failed"] == 0
    assert overall["n_self_eval_unparseable"] == 0
    assert overall["ga"] == 1.0
    assert overall["ea"] == 1.0
    assert overall["cmask"]["n"] == 8
    assert overall["cswap"]["n"] == 8
    assert set(report["by_category"]) == {"short_answer", "numeric", "multi_hop", "false_premise"}
