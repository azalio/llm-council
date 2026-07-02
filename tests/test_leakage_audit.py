"""Tests for the eval answer-leakage audit (arXiv:2606.05037 analogue)."""

from backend.config import DEFAULT_JUDGE_RUBRIC
from backend.eval.factuality_checklist import ChecklistQuestion
from backend.eval.judge import build_judge_prompt
from backend.eval.leakage_audit import (
    audit_binary_checklist,
    audit_binary_judge_prompt,
    audit_eval_fixture,
    audit_eval_fixtures,
    audit_judge_prompt,
    audit_live_binary_judge_prompt,
    audit_live_judge_prompt,
)


def test_live_judge_prompt_is_leak_free():
    findings = audit_live_judge_prompt(DEFAULT_JUDGE_RUBRIC)
    assert findings == []


def test_neutral_schema_labels_are_not_flagged():
    # The legitimate prompt names "candidate answer" / "baseline answer"; those
    # neutral labels must not trip the verdict detector.
    prompt = build_judge_prompt(
        question="Q",
        candidate_answer="A",
        baseline_answer="B",
    )
    assert audit_judge_prompt(prompt) == []


def test_audit_flags_home_team_framing():
    findings = audit_judge_prompt(
        "You are evaluating an LLM Council answer against a baseline answer."
    )
    assert len(findings) == 1
    assert findings[0].channel == "response"
    assert findings[0].field == "judge_prompt"
    assert "council" in findings[0].rule


def test_audit_flags_verdict_phrase():
    findings = audit_judge_prompt("Note that the candidate is better than the baseline.")
    assert any("winner" in f.rule for f in findings)


def test_audit_flags_explicit_correct_answer_leak():
    findings = audit_judge_prompt("The correct answer is to migrate after a smoke test.")
    assert any("correct answer" in f.rule for f in findings)


def test_fixture_schema_token_leak_is_flagged():
    fixture = {
        "question": "Should we migrate today?",
        "candidate_answer": 'Yes. ("winner": "candidate")',
        "baseline_answer": "Migrate after a smoke test.",
    }
    findings = audit_eval_fixture(fixture, name="leaky")
    assert len(findings) == 1
    assert findings[0].channel == "task"
    assert findings[0].field == "leaky.candidate_answer"


def test_clean_fixture_has_no_findings():
    fixture = {
        "question": "Should we migrate today?",
        "candidate_answer": "Migrate after a smoke test passes and keep rollback ready.",
        "baseline_answer": "Migrate after a smoke test passes.",
    }
    assert audit_eval_fixture(fixture) == []


def test_audit_eval_fixtures_labels_by_name_or_index():
    fixtures = [
        {"name": "first", "candidate_answer": "schema_version leak"},
        {"baseline_answer": "clean"},
    ]
    findings = audit_eval_fixtures(fixtures)
    assert len(findings) == 1
    assert findings[0].field == "first.candidate_answer"


def test_finding_is_serializable():
    findings = audit_judge_prompt("our synthesis is the right answer")
    assert findings, "expected at least one finding"
    payload = findings[0].as_dict()
    assert set(payload) == {"channel", "field", "rule", "excerpt"}
    assert all(isinstance(v, str) for v in payload.values())


def test_live_binary_judge_prompt_is_leak_free():
    assert audit_live_binary_judge_prompt() == []


def test_shipped_binary_checklist_is_leak_free():
    assert audit_binary_checklist() == []


def test_audit_binary_judge_prompt_flags_comparison():
    findings = audit_binary_judge_prompt(
        "Decide whether this answer is better than the other answer."
    )
    assert findings
    assert all(f.channel == "response" for f in findings)
    assert all(f.field == "binary_judge_prompt" for f in findings)


def test_audit_binary_checklist_flags_priming_question():
    leaky = [
        ChecklistQuestion(
            id="primed",
            text="Given the prompt asks for a rollback, does the answer deliver it?",
            polarity="positive",
        ),
    ]
    findings = audit_binary_checklist(leaky)
    assert findings
    assert findings[0].field == "binary_checklist.primed"
