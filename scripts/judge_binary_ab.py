#!/usr/bin/env python3
"""A/B harness: holistic vs binary factuality judge on a labeled corpus.

Each corpus record is a labeled pair (a factually sound ``good_answer`` and a
``bad_answer`` carrying one injected defect). The harness runs BOTH judge
variants over every pair, in both candidate/baseline orientations, ``--repeats``
times, and emits a pre-registered scorecard (see docs/bineval-ab-plan.md):

* factuality discrimination — does the variant's factuality score favor the good
  side? (the core no-gold construct-validity signal);
* winner-for-good rate — does the overall winner land on the good side;
* self-consistency — winner-flip rate across repeats of the same orientation;
* position-bias — winner-flip rate when the order is swapped (≈0 expected for the
  binary path because it scores each answer in an isolated call);
* cost — total judge calls; parse-rate — share of available verdicts.

Checkable records (ids present in backend.eval.answer_check.CHECKS) are also
cross-validated against deterministic ground truth so the labels themselves are
audited.

Modes (one is required):
    --live        run against real provider models (real measurement);
    --self-test   run a deterministic offline pipeline check (CI; synthetic).

    python scripts/judge_binary_ab.py --self-test
    python scripts/judge_binary_ab.py --live --repeats 3 --output output/binary-ab.json
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import backend.eval.judge as judge  # noqa: E402
from backend.config import JUDGE_MODEL  # noqa: E402
from backend.eval.answer_check import check_answer  # noqa: E402
from backend.eval.factuality_checklist import FACTUALITY_CHECKLIST  # noqa: E402
from backend.eval.judge import JUDGE_SCHEMA_VERSION, compare_answers_with_judge  # noqa: E402
from backend.openrouter import query_model  # noqa: E402

DEFAULT_CORPUS = PROJECT_ROOT / "tests" / "fixtures" / "binary_metamorphic.json"
VARIANTS = ("holistic", "holistic_swap", "binary")

GOOD_SENTINEL = "[[SELFTEST_GOOD]]"
BAD_SENTINEL = "[[SELFTEST_BAD]]"


# ---------------------------------------------------------------------------
# Offline self-test stubs (deterministic; synthetic, not a model measurement)
# ---------------------------------------------------------------------------
def _extract_between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i:j] if j != -1 else text[i:]


def _binary_stub_verdicts(good: bool) -> str:
    verdicts = {q.id: q.good_verdict for q in FACTUALITY_CHECKLIST}
    if not good:
        verdicts["fabricated_reference"] = "yes"
    return json.dumps(verdicts)


def _holistic_stub_payload(candidate_good: bool) -> str:
    cand_fact, base_fact = (0.9, 0.4) if candidate_good else (0.4, 0.9)
    cand_overall, base_overall = (0.8, 0.5) if candidate_good else (0.5, 0.8)
    return json.dumps({
        "schema_version": JUDGE_SCHEMA_VERSION,
        "scores": {
            "candidate": {
                "factuality": cand_fact, "completeness": 0.7,
                "reasoning": 0.7, "clarity": 0.7, "overall": cand_overall,
            },
            "baseline": {
                "factuality": base_fact, "completeness": 0.7,
                "reasoning": 0.7, "clarity": 0.7, "overall": base_overall,
            },
        },
        "winner": "candidate" if candidate_good else "baseline",
        "confidence": 0.8,
        "criterion_explanations": {
            "factuality": "stub", "completeness": "stub",
            "reasoning": "stub", "clarity": "stub",
        },
        "overall_explanation": "stub self-test verdict",
    })


def _make_stub_query() -> Callable[..., Awaitable[dict[str, Any]]]:
    async def stub(model, messages, **kwargs):
        content = messages[0]["content"]
        if "impartial fact-checker" in content:
            return {"content": _binary_stub_verdicts(GOOD_SENTINEL in content),
                    "_debug": {"ok": True}}
        candidate = _extract_between(content, "<<<CANDIDATE", "CANDIDATE>>>")
        return {"content": _holistic_stub_payload(GOOD_SENTINEL in candidate),
                "_debug": {"ok": True}}
    return stub


def _self_test_corpus() -> list[dict[str, Any]]:
    return [
        {"id": "selftest_1", "checkable": False, "expected": "good",
         "question": "Self-test question one.",
         "good_answer": f"A correct, well-grounded answer. {GOOD_SENTINEL}",
         "bad_answer": f"A flawed answer with a fabricated source. {BAD_SENTINEL}"},
        {"id": "selftest_2", "checkable": False, "expected": "good",
         "question": "Self-test question two.",
         "good_answer": f"Another correct answer. {GOOD_SENTINEL}",
         "bad_answer": f"Another flawed answer. {BAD_SENTINEL}"},
    ]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _counting_query(
    query_fn: Callable[..., Awaitable[dict[str, Any]]],
    counter: list[int],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def wrapped(model, messages, **kwargs):
        counter[0] += 1
        return await query_fn(model, messages, **kwargs)
    return wrapped


async def _run_one(
    record: dict[str, Any],
    orientation: int,
    judge_model: str,
    query_fn: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    if orientation == 0:
        cand, base, cand_side, base_side = (
            record["good_answer"], record["bad_answer"], "good", "bad")
    else:
        cand, base, cand_side, base_side = (
            record["bad_answer"], record["good_answer"], "bad", "good")

    result = await compare_answers_with_judge(
        question=record["question"],
        candidate_answer=cand,
        baseline_answer=base,
        judge_model=judge_model,
        query_fn=query_fn,
    )
    available = bool(result.get("available"))
    winner = str(result.get("winner") or "")
    winner_side = {"candidate": cand_side, "baseline": base_side}.get(winner, "tie")

    fact = {"good": None, "bad": None}
    if available:
        if result.get("judge_variant") == judge.JUDGE_BINARY_VARIANT:
            bf = result["experimental"]["binary_factuality"]
            cand_fact = bf["candidate"]["score"]
            base_fact = bf["baseline"]["score"]
        else:
            cand_fact = result["scores"]["candidate"]["factuality"]
            base_fact = result["scores"]["baseline"]["factuality"]
        fact[cand_side] = cand_fact
        fact[base_side] = base_fact

    return {
        "id": record["id"],
        "orientation": orientation,
        "available": available,
        "winner_side": winner_side,
        "fact_good": fact["good"],
        "fact_bad": fact["bad"],
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _scorecard(evals: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evals)
    available = [e for e in evals if e["available"]]

    disc = [e for e in available if e["fact_good"] is not None and e["fact_bad"] is not None]
    disc_acc = _mean([1.0 if e["fact_good"] > e["fact_bad"] else 0.0 for e in disc])
    winner_good = _mean([1.0 if e["winner_side"] == "good" else 0.0 for e in available])

    by_orientation: dict[tuple[str, int], list[str]] = defaultdict(list)
    for e in available:
        by_orientation[(e["id"], e["orientation"])].append(e["winner_side"])
    self_flip = _mean([
        1 - max(Counter(w).values()) / len(w) for w in by_orientation.values()
    ])

    by_id: dict[str, dict[int, list[str]]] = defaultdict(lambda: {0: [], 1: []})
    for e in available:
        by_id[e["id"]][e["orientation"]].append(e["winner_side"])
    pos_flips: list[float] = []
    for sides in by_id.values():
        if sides[0] and sides[1]:
            m0 = Counter(sides[0]).most_common(1)[0][0]
            m1 = Counter(sides[1]).most_common(1)[0][0]
            if m0 != "tie" and m1 != "tie":
                pos_flips.append(1.0 if m0 != m1 else 0.0)

    return {
        "evaluations": total,
        "parse_rate": _mean([1.0] * len(available) + [0.0] * (total - len(available))),
        "factuality_discrimination_acc": disc_acc,
        "winner_for_good_rate": winner_good,
        "self_consistency_flip_rate": self_flip,
        "position_bias_flip_rate": _mean(pos_flips),
    }


def _label_check(corpus: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-validate checkable fixture labels against deterministic ground truth."""
    mismatches = []
    checked = 0
    for record in corpus:
        if not record.get("checkable"):
            continue
        good = check_answer(record["id"], record["good_answer"])
        bad = check_answer(record["id"], record["bad_answer"])
        if not good.checkable:
            continue
        checked += 1
        if good.correct is not True or bad.correct is True:
            mismatches.append({
                "id": record["id"],
                "good_correct": good.correct,
                "bad_correct": bad.correct,
                "good_detail": good.detail,
                "bad_detail": bad.detail,
            })
    return {"checked": checked, "mismatches": mismatches}


async def _run_variant(
    variant: str,
    corpus: list[dict[str, Any]],
    *,
    repeats: int,
    swap_repeats: int,
    judge_model: str,
    base_query: Callable[..., Awaitable[dict[str, Any]]],
    concurrency: int,
) -> dict[str, Any]:
    counter = [0]
    query_fn = _counting_query(base_query, counter)
    semaphore = asyncio.Semaphore(concurrency)

    async def _guarded(record: dict[str, Any], orientation: int) -> dict[str, Any]:
        async with semaphore:
            return await _run_one(record, orientation, judge_model, query_fn)

    original_binary = judge.JUDGE_BINARY_ENABLED
    original_swap = judge.JUDGE_ORDER_SWAP_ENABLED
    judge.JUDGE_BINARY_ENABLED = variant == "binary"
    judge.JUDGE_ORDER_SWAP_ENABLED = variant == "holistic_swap"
    try:
        tasks = []
        for record in corpus:
            tasks.extend(_guarded(record, 0) for _ in range(repeats))
            tasks.extend(_guarded(record, 1) for _ in range(swap_repeats))
        evals = await asyncio.gather(*tasks)
    finally:
        judge.JUDGE_BINARY_ENABLED = original_binary
        judge.JUDGE_ORDER_SWAP_ENABLED = original_swap
    return {"scorecard": _scorecard(evals), "calls": counter[0]}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.self_test:
        corpus = _self_test_corpus()
        base_query = _make_stub_query()
        judge_model = "stub-judge"
    else:
        corpus = json.loads(Path(args.corpus).read_text())
        if args.limit:
            corpus = corpus[: args.limit]
        base_query = query_model
        judge_model = JUDGE_MODEL

    swap_repeats = max(1, round(args.repeats * args.swap_frac))
    variants = {
        variant: await _run_variant(
            variant, corpus,
            repeats=args.repeats, swap_repeats=swap_repeats,
            judge_model=judge_model, base_query=base_query,
            concurrency=args.concurrency,
        )
        for variant in VARIANTS
    }

    return {
        "mode": "self-test" if args.self_test else "live",
        "judge_model": judge_model,
        "corpus_size": len(corpus),
        "repeats": args.repeats,
        "swap_repeats": swap_repeats,
        "checkable_label_check": _label_check(corpus),
        "variants": variants,
        "gates_reference": "docs/bineval-ab-plan.md",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS),
                        help="Path to a labeled JSON corpus of good/bad answer pairs.")
    parser.add_argument("--repeats", type=int, default=3,
                        help="Runs per pair in the primary orientation.")
    parser.add_argument("--swap-frac", type=float, default=0.25,
                        help="Swapped-orientation runs as a fraction of --repeats.")
    parser.add_argument("--limit", type=int, help="Cap the number of corpus records.")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Max in-flight judge evaluations (caps provider fan-out).")
    parser.add_argument("--live", action="store_true",
                        help="Run against real provider models (real measurement).")
    parser.add_argument("--self-test", action="store_true",
                        help="Deterministic offline pipeline check (synthetic).")
    parser.add_argument("--output", help="Optional JSON output path.")
    args = parser.parse_args()

    if not args.live and not args.self_test:
        parser.error("pass --live for a real run or --self-test for an offline check")

    report = asyncio.run(_run(args))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
