"""Versioned atomic yes/no checklist for the binary factuality judge.

BINEVAL ("Ask, Don't Judge") decomposes a holistic criterion into atomic
yes/no questions answered independently per answer, then aggregates the verdicts
into a calibrated score. This module is the human-owned, code-versioned question
bank for the `factuality` criterion only (the pilot scope; see
docs/bineval-ab-plan.md).

Design rules baked into the questions:
- Each question is phrased about "the answer" / "the question". It never mentions
  "candidate"/"baseline", a winner, or which side is better — that would leak a
  verdict (enforced by backend.eval.leakage_audit.audit_binary_checklist).
- `polarity` records what a "yes" verdict means:
    "positive" -> yes is good  (the answer has the desirable property);
    "negative" -> yes is bad   (the answer exhibits the defect).
- `critical` marks defects severe enough that a single failure should cap the
  factuality score, regardless of how many trivial checks pass.
- The verdict vocabulary is yes / no / not_applicable; an item that does not
  apply to a given answer is excluded from the score denominator.

Treat this file like source: bump CHECKLIST_VERSION and record the rationale in
the same change whenever the questions change.
"""

from __future__ import annotations

from dataclasses import dataclass

CHECKLIST_VERSION = "0.1"

VERDICT_YES = "yes"
VERDICT_NO = "no"
VERDICT_NA = "not_applicable"
VERDICT_VALUES: frozenset[str] = frozenset({VERDICT_YES, VERDICT_NO, VERDICT_NA})

POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"
POLARITY_VALUES: frozenset[str] = frozenset({POLARITY_POSITIVE, POLARITY_NEGATIVE})


@dataclass(frozen=True)
class ChecklistQuestion:
    """One atomic yes/no factuality question.

    polarity: "positive" (yes = good) or "negative" (yes = a defect).
    critical: a failing answer caps the factuality sub-score.
    """

    id: str
    text: str
    polarity: str
    critical: bool = False

    def __post_init__(self) -> None:
        if self.polarity not in POLARITY_VALUES:
            raise ValueError(
                f"ChecklistQuestion {self.id!r} polarity must be one of {sorted(POLARITY_VALUES)}"
            )

    @property
    def good_verdict(self) -> str:
        """The verdict that indicates the answer is factually sound on this item."""
        return VERDICT_NO if self.polarity == POLARITY_NEGATIVE else VERDICT_YES


FACTUALITY_CHECKLIST: list[ChecklistQuestion] = [
    ChecklistQuestion(
        id="contradicts_question",
        text=(
            "Does the answer assert something that directly contradicts a fact, "
            "definition, or constraint stated in the question?"
        ),
        polarity=POLARITY_NEGATIVE,
        critical=True,
    ),
    ChecklistQuestion(
        id="internal_contradiction",
        text="Does the answer contain two statements that contradict each other?",
        polarity=POLARITY_NEGATIVE,
        critical=True,
    ),
    ChecklistQuestion(
        id="fabricated_reference",
        text=(
            "Does the answer cite a specific source, paper, author, quotation, or "
            "URL that appears fabricated or cannot be a real reference?"
        ),
        polarity=POLARITY_NEGATIVE,
        critical=True,
    ),
    ChecklistQuestion(
        id="accepts_false_premise",
        text=(
            "If the question rests on a false or unverifiable premise, does the "
            "answer accept that premise as true instead of flagging it?"
        ),
        polarity=POLARITY_NEGATIVE,
        critical=True,
    ),
    ChecklistQuestion(
        id="unsupported_specific_claim",
        text=(
            "Does the answer present a specific factual detail (a number, date, "
            "name, or statistic) as certain without any supporting basis?"
        ),
        polarity=POLARITY_NEGATIVE,
    ),
    ChecklistQuestion(
        id="arithmetic_consistent",
        text=(
            "Are all calculations, unit conversions, and quantitative steps in the "
            "answer internally consistent and correctly computed?"
        ),
        polarity=POLARITY_POSITIVE,
    ),
    ChecklistQuestion(
        id="claims_grounded",
        text=(
            "Are the main factual claims in the answer either common knowledge or "
            "supported by reasoning or evidence the answer itself provides?"
        ),
        polarity=POLARITY_POSITIVE,
    ),
    ChecklistQuestion(
        id="calibrated_uncertainty",
        text=(
            "Where the answer cannot be certain, does it express appropriate "
            "uncertainty rather than overstating confidence?"
        ),
        polarity=POLARITY_POSITIVE,
    ),
    ChecklistQuestion(
        id="factually_responsive",
        text=(
            "Does the answer engage with the factual substance of the question "
            "rather than evading it or answering a different question?"
        ),
        polarity=POLARITY_POSITIVE,
    ),
]


def checklist_ids(checklist: list[ChecklistQuestion]) -> list[str]:
    """Return the ordered question ids of a checklist."""
    return [question.id for question in checklist]
