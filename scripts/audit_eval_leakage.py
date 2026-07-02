#!/usr/bin/env python3
"""Answer-leakage audit for the council evaluation surface (CI gate).

Implements the leakage audit recommended by Canedo & Chethan, "Self-Reflective
APIs" (arXiv:2606.05037): scan every grader-visible field for content that
leaks the verdict, and fail non-zero if anything is found. Two channels:

* response channel — the live judge prompt template (built with neutral
  placeholders) must not prime a preferred answer;
* task channel — eval fixtures' judge-visible inputs must not carry schema-only
  tokens from the judge's output.

Run before trusting judge / cache / routing benchmark numbers used to set
thresholds, and in CI:

    python scripts/audit_eval_leakage.py
    python scripts/audit_eval_leakage.py --fixtures my_fixtures.json --output out.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import DEFAULT_JUDGE_RUBRIC  # noqa: E402
from backend.eval.leakage_audit import (  # noqa: E402
    LeakageFinding,
    audit_binary_checklist,
    audit_bineval_questions,
    audit_eval_fixtures,
    audit_live_binary_judge_prompt,
    audit_live_bineval_prompts,
    audit_live_judge_prompt,
)


# Default task-channel fixtures: the judge-visible inputs exercised by the judge
# smoke (scripts/judge_eval_smoke.py). Kept in sync so the smoke inputs are
# always leak-audited.
DEFAULT_FIXTURES: List[Dict[str, Any]] = [
    {
        "name": "judge_smoke",
        "question": "Should we migrate the API route today?",
        "candidate_answer": "Migrate after a smoke test passes and keep rollback ready.",
        "baseline_answer": "Migrate after a smoke test passes.",
    },
]


def _load_fixtures(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise SystemExit(f"Fixtures file {path!r} must contain a JSON list of objects.")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        help="Path to a JSON list of eval fixtures to audit (task channel).",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the full findings artifact as JSON.",
    )
    args = parser.parse_args()

    fixtures = _load_fixtures(args.fixtures) if args.fixtures else DEFAULT_FIXTURES

    findings: List[LeakageFinding] = []
    findings.extend(audit_live_judge_prompt(DEFAULT_JUDGE_RUBRIC))
    findings.extend(audit_live_binary_judge_prompt())
    findings.extend(audit_binary_checklist())
    findings.extend(audit_live_bineval_prompts())
    findings.extend(audit_bineval_questions())
    findings.extend(audit_eval_fixtures(fixtures))

    report = {
        "clean": not findings,
        "finding_count": len(findings),
        "response_channel": sum(1 for f in findings if f.channel == "response"),
        "task_channel": sum(1 for f in findings if f.channel == "task"),
        "findings": [f.as_dict() for f in findings],
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
