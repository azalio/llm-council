"""Answer-leakage audit for the council evaluation surface.

Mirrors the two leakage classes from Canedo & Chethan, "Self-Reflective APIs:
Structure Beats Verbosity for AI Agent Recovery" (arXiv:2606.05037), where an
undetected answer leak inverted the paper's headline result until it was audited
away:

* **Response-channel leak** — the grader's own prompt reveals which answer it
  should prefer (their validator-message leak). For us that is the judge prompt:
  it must score two answers blind, never naming a home team or a verdict.
* **Task-channel leak** — an eval fixture's inputs already encode the expected
  verdict (their task-prompt leak). For us that is any judge-visible input field
  carrying a schema-only token that belongs in the judge's *output*, not its
  input.

Both classes silently bias eval numbers that we use to set thresholds, so this
ships as CI infrastructure: enumerate known leak patterns, scan every
model-visible field, and exit non-zero on any finding (see
``scripts/audit_eval_leakage.py``).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


# Response-channel: phrases in the judge prompt that prime a verdict or name the
# council as the answer's author. The neutral schema labels "candidate answer"
# and "baseline answer" are intentionally NOT listed here — they carry no
# preference on their own.
JUDGE_VERDICT_LEAK_PATTERNS: List[tuple[str, str]] = [
    (r"\bllm council\b", "names one side as the council's own answer"),
    (r"\bour (?:answer|synthesis|candidate|response)\b", "marks one side as the home team"),
    (r"\b(?:the )?correct answer is\b", "states the correct answer"),
    (r"\b(?:candidate|baseline) is (?:better|correct|right|preferred|the winner)\b",
     "states the winner outright"),
    (r"\bprefer the (?:candidate|baseline)\b", "instructs a preferred side"),
    (r"\b(?:candidate|baseline) should (?:win|score higher|be preferred)\b",
     "instructs which side should win"),
]

# Task-channel: tokens that belong only to the judge's OUTPUT schema. If any of
# them appears in a judge-visible INPUT field, someone pasted grader output into
# the fixture and the verdict has leaked into the prompt.
FIXTURE_LEAK_TOKENS: tuple[str, ...] = (
    "schema_version",
    "criterion_explanations",
    "overall_explanation",
    '"winner"',
)

# The judge-visible input fields of an eval fixture.
FIXTURE_INPUT_FIELDS: tuple[str, ...] = (
    "question",
    "candidate_answer",
    "baseline_answer",
)

# Response-channel patterns specific to the binary factuality judge. A binary
# checklist question must ask about a property of "the answer"; it must never
# compare the two answers, name a winner, or restate the prompt as a deliverable
# (a completeness-priming shortcut the council flagged).
BINARY_CHECKLIST_LEAK_PATTERNS: List[tuple[str, str]] = [
    (r"\bthe other (?:answer|response|candidate|baseline)\b",
     "compares against the other answer"),
    (r"\bcompared to the (?:other|candidate|baseline)\b",
     "compares against the other answer"),
    (r"\b(?:better|worse|superior|inferior|preferred|winner)\b",
     "uses comparative/verdict language"),
    (r"\bdoes (?:it|the answer) deliver\b",
     "restates the prompt as a deliverable (priming)"),
    (r"\bgiven (?:that )?the (?:prompt|question) asks\b",
     "restates the prompt as a deliverable (priming)"),
]

# Neutral placeholders for auditing a prompt template without real content.
NEUTRAL_PLACEHOLDERS: Dict[str, str] = {
    "question": "<<QUESTION>>",
    "candidate_answer": "<<CANDIDATE>>",
    "baseline_answer": "<<BASELINE>>",
}


@dataclass(frozen=True)
class LeakageFinding:
    """A single detected leak."""

    channel: str  # "response" (grader prompt) | "task" (fixture input)
    field: str
    rule: str
    excerpt: str

    def as_dict(self) -> Dict[str, str]:
        return asdict(self)


def _excerpt(text: str, start: int, end: int, pad: int = 24) -> str:
    lead = max(0, start - pad)
    tail = min(len(text), end + pad)
    snippet = " ".join(text[lead:tail].split())
    prefix = "…" if lead > 0 else ""
    suffix = "…" if tail < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def audit_judge_prompt(prompt: str) -> List[LeakageFinding]:
    """Scan an assembled judge prompt for verdict-priming language."""
    findings: List[LeakageFinding] = []
    lowered = prompt.lower()
    for pattern, rule in JUDGE_VERDICT_LEAK_PATTERNS:
        for match in re.finditer(pattern, lowered):
            findings.append(
                LeakageFinding(
                    channel="response",
                    field="judge_prompt",
                    rule=rule,
                    excerpt=_excerpt(prompt, match.start(), match.end()),
                )
            )
    return findings


def audit_eval_fixture(
    fixture: Dict[str, Any],
    *,
    name: str = "fixture",
) -> List[LeakageFinding]:
    """Scan a single eval fixture's judge-visible inputs for schema-token leaks."""
    findings: List[LeakageFinding] = []
    for field in FIXTURE_INPUT_FIELDS:
        value = fixture.get(field)
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        for token in FIXTURE_LEAK_TOKENS:
            index = lowered.find(token.lower())
            if index != -1:
                findings.append(
                    LeakageFinding(
                        channel="task",
                        field=f"{name}.{field}",
                        rule=f"input carries schema-only token {token!r}",
                        excerpt=_excerpt(value, index, index + len(token)),
                    )
                )
    return findings


def audit_eval_fixtures(fixtures: List[Dict[str, Any]]) -> List[LeakageFinding]:
    """Audit a list of eval fixtures, labelling each by index."""
    findings: List[LeakageFinding] = []
    for position, fixture in enumerate(fixtures):
        label = str(fixture.get("name") or f"fixture[{position}]")
        findings.extend(audit_eval_fixture(fixture, name=label))
    return findings


def audit_live_judge_prompt(
    rubric: Optional[List[Dict[str, Any]]] = None,
) -> List[LeakageFinding]:
    """Build the production judge prompt with neutral content and audit it."""
    # Imported lazily so the audit library has no import-time dependency on the
    # judge configuration (keeps it cheap to import in tests and CI).
    from .judge import build_judge_prompt

    prompt = build_judge_prompt(
        question=NEUTRAL_PLACEHOLDERS["question"],
        candidate_answer=NEUTRAL_PLACEHOLDERS["candidate_answer"],
        baseline_answer=NEUTRAL_PLACEHOLDERS["baseline_answer"],
        rubric=rubric,
    )
    return audit_judge_prompt(prompt)


def _scan_patterns(
    text: str,
    patterns: List[tuple[str, str]],
    *,
    channel: str,
    field: str,
) -> List[LeakageFinding]:
    findings: List[LeakageFinding] = []
    lowered = text.lower()
    for pattern, rule in patterns:
        for match in re.finditer(pattern, lowered):
            findings.append(
                LeakageFinding(
                    channel=channel,
                    field=field,
                    rule=rule,
                    excerpt=_excerpt(text, match.start(), match.end()),
                )
            )
    return findings


def audit_binary_judge_prompt(prompt: str) -> List[LeakageFinding]:
    """Scan an assembled binary factuality judge prompt for verdict priming."""
    patterns = JUDGE_VERDICT_LEAK_PATTERNS + BINARY_CHECKLIST_LEAK_PATTERNS
    return _scan_patterns(
        prompt, patterns, channel="response", field="binary_judge_prompt"
    )


def audit_binary_checklist(checklist: Optional[List[Any]] = None) -> List[LeakageFinding]:
    """Scan each binary checklist question for verdict-priming language."""
    if checklist is None:
        from .factuality_checklist import FACTUALITY_CHECKLIST

        checklist = FACTUALITY_CHECKLIST

    patterns = JUDGE_VERDICT_LEAK_PATTERNS + BINARY_CHECKLIST_LEAK_PATTERNS
    findings: List[LeakageFinding] = []
    for question in checklist:
        findings.extend(
            _scan_patterns(
                question.text,
                patterns,
                channel="response",
                field=f"binary_checklist.{question.id}",
            )
        )
    return findings


def audit_live_binary_judge_prompt(
    checklist: Optional[List[Any]] = None,
) -> List[LeakageFinding]:
    """Build the binary judge prompt with neutral content and audit it."""
    from .factuality_checklist import FACTUALITY_CHECKLIST
    from .judge import build_binary_factuality_prompt

    if checklist is None:
        checklist = FACTUALITY_CHECKLIST

    prompt = build_binary_factuality_prompt(
        question=NEUTRAL_PLACEHOLDERS["question"],
        answer=NEUTRAL_PLACEHOLDERS["candidate_answer"],
        checklist=checklist,
    )
    return audit_binary_judge_prompt(prompt)


# Neutral placeholders for the source-grounded BINEVAL replication prompts.
BINEVAL_PLACEHOLDERS: Dict[str, str] = {
    "source": "<<SOURCE>>",
    "summary": "<<SUMMARY>>",
}

# The same verdict/comparison priming patterns apply to the BINEVAL replication
# prompts: a pointwise evaluator must judge one output against the source, never
# compare two outputs or be told which verdict to reach.
_BINEVAL_PATTERNS = JUDGE_VERDICT_LEAK_PATTERNS + BINARY_CHECKLIST_LEAK_PATTERNS


def audit_bineval_questions(questions: Optional[List[Any]] = None) -> List[LeakageFinding]:
    """Scan each BINEVAL question for verdict/comparison priming language."""
    if questions is None:
        from .bineval import PAPER_CONSISTENCY_QUESTIONS

        questions = list(PAPER_CONSISTENCY_QUESTIONS)

    findings: List[LeakageFinding] = []
    for question in questions:
        findings.extend(
            _scan_patterns(
                question.text,
                _BINEVAL_PATTERNS,
                channel="response",
                field=f"bineval_question.{question.id}",
            )
        )
    return findings


def audit_live_bineval_prompts() -> List[LeakageFinding]:
    """Build the BINEVAL replication prompts with neutral content and audit them.

    Covers the meta-prompt (question generation), the source-grounded pointwise
    evaluation prompt, and the holistic G-Eval-style baseline prompt, plus the
    published question bank.
    """
    from .bineval import (
        BinevalQuestion,
        CONSISTENCY,
        build_holistic_consistency_prompt,
        build_question_generation_prompt,
        build_single_question_prompt,
    )

    findings: List[LeakageFinding] = []

    meta_prompt = build_question_generation_prompt(CONSISTENCY)
    findings.extend(
        _scan_patterns(
            meta_prompt, _BINEVAL_PATTERNS, channel="response", field="bineval_meta_prompt"
        )
    )

    pointwise_prompt = build_single_question_prompt(
        source=BINEVAL_PLACEHOLDERS["source"],
        summary=BINEVAL_PLACEHOLDERS["summary"],
        question=BinevalQuestion("Q0", "<<QUESTION>>"),
    )
    findings.extend(
        _scan_patterns(
            pointwise_prompt,
            _BINEVAL_PATTERNS,
            channel="response",
            field="bineval_pointwise_prompt",
        )
    )

    holistic_prompt = build_holistic_consistency_prompt(
        source=BINEVAL_PLACEHOLDERS["source"],
        summary=BINEVAL_PLACEHOLDERS["summary"],
    )
    findings.extend(
        _scan_patterns(
            holistic_prompt,
            _BINEVAL_PATTERNS,
            channel="response",
            field="bineval_holistic_prompt",
        )
    )

    findings.extend(audit_bineval_questions())
    return findings
