#!/usr/bin/env python3
"""Offline adaptive-routing benchmark over deterministic council scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.agent_router import build_agent_route, mark_route_expanded
from backend.config import COUNCIL_MODELS
from backend.eval.judge import evaluate_deliberation_result


def _judge_payload() -> str:
    return json.dumps({
        "schema_version": "judge.v1",
        "scores": {
            "candidate": {
                "factuality": 0.9,
                "completeness": 0.88,
                "reasoning": 0.9,
                "clarity": 0.9,
                "overall": 0.895,
            },
            "baseline": {
                "factuality": 0.9,
                "completeness": 0.88,
                "reasoning": 0.9,
                "clarity": 0.9,
                "overall": 0.895,
            },
        },
        "winner": "tie",
        "confidence": 0.8,
        "criterion_explanations": {
            "factuality": "Both answers preserve the mocked fact pattern.",
            "completeness": "Both answers cover the mocked requirements.",
            "reasoning": "Both answers use equivalent reasoning in this smoke.",
            "clarity": "Both answers are similarly clear in this smoke.",
        },
        "overall_explanation": "Mocked judge confirms no quality delta in deterministic smoke.",
    })


async def _mock_judge(_: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
    return {"content": _judge_payload(), "_debug": {"ok": True}}


async def run_benchmark(full_pool: list[str] | None = None) -> dict[str, Any]:
    models = list(full_pool or COUNCIL_MODELS)
    scenarios = [
        {
            "name": "routine_sparse",
            "question": "Explain why answer caching reduces latency.",
            "mode_selection": {"requested_mode": "auto", "selected_mode": "standard"},
            "expand": False,
        },
        {
            "name": "high_risk_full",
            "question": "Review this production security migration plan.",
            "mode_selection": {"requested_mode": "auto", "selected_mode": "standard"},
            "expand": False,
        },
        {
            "name": "low_confidence_expands",
            "question": "Explain why answer caching reduces latency.",
            "mode_selection": {"requested_mode": "auto", "selected_mode": "standard"},
            "expand": True,
        },
    ]

    results = []
    for scenario in scenarios:
        route = build_agent_route(
            scenario["question"],
            scenario["mode_selection"],
            full_pool=models,
        )
        if scenario["expand"] and route.get("applied"):
            route = mark_route_expanded(route, "mock_low_confidence")

        full_model_calls = len(models) * 2
        if route.get("expanded"):
            initial_count = int(route.get("initial_model_count", len(models)) or 0)
            skipped_count = max(len(models) - initial_count, 0)
            routed_model_calls = (initial_count * 2) + skipped_count + len(models)
        else:
            routed_model_calls = route["final_model_count"] * 2
        saved_model_calls = full_model_calls - routed_model_calls
        judge_result = await evaluate_deliberation_result(
            question=scenario["question"],
            stage1_results=[{"model": models[0], "response": "Baseline answer."}],
            stage3_result={"model": "chairman", "response": "Baseline answer."},
            aggregate_rankings=[{"model": models[0], "average_rank": 1.0}],
            query_fn=_mock_judge,
        )
        quality_delta = 0.0
        if judge_result.get("available"):
            quality_delta = round(
                judge_result["scores"]["candidate"]["overall"]
                - judge_result["scores"]["baseline"]["overall"],
                4,
            )

        results.append({
            "name": scenario["name"],
            "route": route,
            "full_model_calls": full_model_calls,
            "routed_model_calls": routed_model_calls,
            "saved_model_calls": saved_model_calls,
            "quality_delta": quality_delta,
            "quality_within_5_percent": abs(quality_delta) <= 0.05,
            "judge": {
                "available": judge_result.get("available"),
                "winner": judge_result.get("winner"),
                "status": judge_result.get("status"),
            },
        })

    routine = next(item for item in results if item["name"] == "routine_sparse")
    return {
        "model_pool_size": len(models),
        "scenarios": results,
        "summary": {
            "routine_saved_model_calls": routine["saved_model_calls"],
            "routine_saved_fraction": round(
                routine["saved_model_calls"] / routine["full_model_calls"],
                4,
            ) if routine["full_model_calls"] else 0.0,
            "all_quality_within_5_percent": all(
                item["quality_within_5_percent"] for item in results
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()

    report = asyncio.run(run_benchmark())
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
