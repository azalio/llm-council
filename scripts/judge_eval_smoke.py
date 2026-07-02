#!/usr/bin/env python3
"""Offline smoke for the LLM-as-a-judge evaluation path."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import backend.eval.judge as judge  # noqa: E402
from backend.eval.judge import JUDGE_SCHEMA_VERSION, evaluate_deliberation_result  # noqa: E402
from backend.eval.factuality_checklist import FACTUALITY_CHECKLIST  # noqa: E402


def _mock_judge_payload() -> str:
    return json.dumps({
        "schema_version": JUDGE_SCHEMA_VERSION,
        "scores": {
            "candidate": {
                "factuality": 0.9,
                "completeness": 0.85,
                "reasoning": 0.88,
                "clarity": 0.9,
                "overall": 0.88,
            },
            "baseline": {
                "factuality": 0.72,
                "completeness": 0.6,
                "reasoning": 0.62,
                "clarity": 0.7,
                "overall": 0.66,
            },
        },
        "winner": "candidate",
        "confidence": 0.8,
        "criterion_explanations": {
            "factuality": "The candidate avoids the unsupported timing claim.",
            "completeness": "The candidate includes the rollout guard.",
            "reasoning": "The candidate connects risk to validation.",
            "clarity": "The candidate is direct.",
        },
        "overall_explanation": "The candidate is safer because it includes a validation boundary.",
    })


def _mock_binary_payload(good: bool) -> str:
    """Verdicts for one answer: all good, or a critical fabricated-reference fail."""
    verdicts = {question.id: question.good_verdict for question in FACTUALITY_CHECKLIST}
    if not good:
        verdicts["fabricated_reference"] = "yes"  # negative-polarity yes = a defect
    return json.dumps(verdicts)


async def _evaluate(query_fn) -> dict:
    return await evaluate_deliberation_result(
        question="Should we migrate the API route today?",
        stage1_results=[
            {"model": "alpha", "response": "Migrate today with no extra check."},
            {"model": "beta", "response": "Migrate after a smoke test passes."},
        ],
        stage3_result={
            "model": "chairman",
            "response": "Migrate after a smoke test passes and keep rollback ready.",
        },
        aggregate_rankings=[{"model": "beta", "average_rank": 1.0}],
        query_fn=query_fn,
    )


async def _run_mock() -> dict:
    async def fake_query(model, messages, **kwargs):
        return {"content": _mock_judge_payload(), "_debug": {"ok": True}}

    return await _evaluate(fake_query)


async def _run_binary_mock() -> dict:
    """Run the hybrid binary judge with a stubbed query_fn (3 calls per pair)."""
    async def fake_query(model, messages, **kwargs):
        content = messages[0]["content"]
        if "impartial fact-checker" in content:
            # Detect which answer is being scored; the chairman candidate keeps a
            # "rollback" guard the Stage 1 baseline lacks.
            return {
                "content": _mock_binary_payload("rollback" in content),
                "_debug": {"ok": True},
            }
        return {"content": _mock_judge_payload(), "_debug": {"ok": True}}

    original = judge.JUDGE_BINARY_ENABLED
    judge.JUDGE_BINARY_ENABLED = True
    try:
        return await _evaluate(fake_query)
    finally:
        judge.JUDGE_BINARY_ENABLED = original


def _summary(variant: str, result: dict, output_path: Path) -> dict:
    summary = {
        "variant": variant,
        "status": result.get("status"),
        "available": result.get("available"),
        "winner": result.get("winner"),
        "judge_variant": result.get("judge_variant"),
        "output": str(output_path),
    }
    binary = result.get("experimental", {}).get("binary_factuality")
    if binary is not None:
        summary["binary_factuality"] = {
            "checklist_version": binary.get("checklist_version"),
            "candidate_score": binary.get("candidate", {}).get("score"),
            "baseline_score": binary.get("baseline", {}).get("score"),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="output/judge-eval-smoke.json",
        help="Path for the parsed evaluation artifact.",
    )
    parser.add_argument(
        "--judge",
        choices=["holistic", "binary", "both"],
        default="holistic",
        help="Which judge variant(s) to run.",
    )
    args = parser.parse_args()

    runners = {"holistic": _run_mock, "binary": _run_binary_mock}
    variants = ["holistic", "binary"] if args.judge == "both" else [args.judge]
    results = {variant: asyncio.run(runners[variant]()) for variant in variants}

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")

    print(json.dumps(
        [_summary(variant, results[variant], output_path) for variant in variants],
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
