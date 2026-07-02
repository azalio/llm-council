#!/usr/bin/env python3
"""Probe Stage 2 peer-ranking order sensitivity (counterbalance off vs on).

Position bias shows up when answers are near-ties: the aggregate winner then
tracks the presentation slot instead of the content. This probe feeds a set of
deliberately close answers, runs Stage 2 under several input orderings, and
measures how often the aggregate winner changes with the ordering — with
`COUNCIL_STAGE2_COUNTERBALANCE` off and on.

Lower order-sensitivity (fewer distinct winners across orderings) = less
position bias. Live: uses the configured council models as rankers.

    python scripts/stage2_position_bias.py --perms 4 --output output/stage2-bias.json
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import backend.council as council  # noqa: E402
from backend.council import calculate_aggregate_rankings, stage2_collect_rankings  # noqa: E402

# Near-tie cases: four genuinely comparable answers, so any winner is driven by
# position rather than quality.
CASES = [
    {
        "question": "Give one concrete benefit of writing unit tests.",
        "answers": [
            "Unit tests catch regressions early, before they reach production.",
            "They document the intended behavior of each function for future readers.",
            "They let you refactor with confidence that behavior is preserved.",
            "They shorten the debugging loop by pinpointing the failing component.",
        ],
    },
    {
        "question": "Name one reason to use an index in a relational database.",
        "answers": [
            "An index speeds up lookups on the indexed column for large tables.",
            "It avoids full table scans for selective WHERE clauses.",
            "It can enforce uniqueness while also accelerating equality search.",
            "It makes ORDER BY on the indexed column cheaper to evaluate.",
        ],
    },
]


def _permutations(n: int, k: int) -> list[list[int]]:
    # Deterministic: k rotations of the identity order.
    return [[(i + r) % n for i in range(n)] for r in range(min(k, n))]


async def _winner_for_order(question: str, ordered: list[dict]) -> str | None:
    # Rankers default to the configured COUNCIL_MODELS; the winner is one of the
    # answer ids (ans0..ansN), independent of the ranker identities.
    stage2_results, label_to_model, _ = await stage2_collect_rankings(question, ordered)
    agg = calculate_aggregate_rankings(stage2_results, label_to_model)
    return agg[0]["model"] if agg else None


async def _probe_case(case: dict, perms: list[list[int]]) -> dict:
    # Stable model ids tied to content (so winners are comparable across orders).
    answers = [
        {"model": f"ans{i}", "response": text}
        for i, text in enumerate(case["answers"])
    ]
    out = {}
    for mode in (False, True):
        council.STAGE2_COUNTERBALANCE_ENABLED = mode
        winners = []
        for perm in perms:
            ordered = [answers[i] for i in perm]
            winners.append(await _winner_for_order(case["question"], ordered))
        winners = [w for w in winners if w]
        distinct = len(set(winners))
        modal = Counter(winners).most_common(1)[0][1] if winners else 0
        out["counterbalanced" if mode else "fixed_order"] = {
            "winners": winners,
            "distinct_winners": distinct,
            "order_sensitivity": round(1 - modal / len(winners), 4) if winners else None,
        }
    return out


async def _run(args: argparse.Namespace) -> dict:
    n = len(CASES[0]["answers"])
    perms = _permutations(n, args.perms)
    original = council.STAGE2_COUNTERBALANCE_ENABLED
    try:
        results = []
        for case in CASES:
            probe = await _probe_case(case, perms)
            probe["question"] = case["question"]
            results.append(probe)
    finally:
        council.STAGE2_COUNTERBALANCE_ENABLED = original
    return {
        "orderings": len(perms),
        "note": "order_sensitivity = fraction of orderings whose winner != modal; lower is better",
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--perms", type=int, default=4, help="Input orderings to test.")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    report = asyncio.run(_run(args))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
