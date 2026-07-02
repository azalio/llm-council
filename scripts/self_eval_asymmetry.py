#!/usr/bin/env python3
"""Self-evaluation asymmetry benchmark on quick/deep verifier surfaces (arXiv:2606.28050).

Tests whether this repo's own verifier-like surfaces (quick mode's prompt-only
self-check, Stage 2b's same-model revision) can trust same-model self-evaluation:
for each question in a small fixed local corpus, a model generates an answer,
then separately judges whether ITS OWN answer is correct. Reports generation
accuracy (GA), evaluation accuracy (EA), Delta = EA - GA, evaluation
precision/recall/F1, and C-MASK/C-SWAP candidate-answer ablations.

This is operator-facing and offline; it does not touch `ask_council` or the
production judge (see CLAUDE.md gotcha #14's judge boundary).

Modes (one is required):
    --self-test   deterministic offline pipeline check (CI; no provider access);
    --live        run against the configured provider (real measurement).

    python scripts/self_eval_asymmetry.py --self-test
    python scripts/self_eval_asymmetry.py --live --model claude-sonnet-4-6 \\
        --output output/self-eval-asymmetry.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import API_PROVIDER, JUDGE_MODEL  # noqa: E402
from backend.eval.self_eval_asymmetry import (  # noqa: E402
    DEFAULT_CORPUS_PATH,
    SELF_EVAL_ASYMMETRY_VERSION,
    compute_asymmetry_metrics,
    load_corpus,
    run_case,
)
from backend.openrouter import query_model  # noqa: E402

SELF_EVAL_MARKER = "Judge whether YOUR OWN answer is correct"


# ---------------------------------------------------------------------------
# Offline self-test stub (deterministic; synthetic, not a model measurement)
# ---------------------------------------------------------------------------
def _stub_correct_answer(check: dict[str, Any]) -> str:
    """Synthesize an answer that check_answer_against_gold() scores as correct."""
    check_type = check.get("type")
    if check_type == "contains_ci":
        return check["value"]
    if check_type == "contains_any_ci":
        return check["values"][0]
    if check_type == "numeric_exact":
        return str(check["value"])
    if check_type == "false_premise_flag":
        return "That premise is false; the question rests on a common misconception."
    raise ValueError(f"unknown check type: {check_type!r}")


def _make_stub_query(corpus):
    """A deterministic stub: generation always answers correctly, real self-eval
    always says "yes" to its own correct answer, C-MASK (redacted candidate)
    always flips to "no" (can't verify nothing), C-SWAP (wrong_plausible
    candidate) always correctly rejects with "no" — a well-behaved synthetic
    baseline that exercises every metric field end-to-end offline."""
    correct_by_question = {case.question: _stub_correct_answer(case.check) for case in corpus}

    async def stub(model, messages, **kwargs):
        content = messages[0]["content"]
        if SELF_EVAL_MARKER not in content:
            # Generation call.
            for question, answer in correct_by_question.items():
                if question in content:
                    return {"content": answer, "_debug": {"ok": True}}
            return {"content": "", "_debug": {"ok": True}}

        # Self-evaluation call (real / C-MASK / C-SWAP share this prompt shape).
        if "[REDACTED" in content:
            verdict = "no"
        else:
            answer_block = content.split("<<<ANSWER", 1)[1].split("ANSWER>>>", 1)[0].strip()
            is_known_correct_answer = answer_block in correct_by_question.values()
            verdict = "yes" if is_known_correct_answer else "no"
        return {
            "content": json.dumps({"verdict": verdict, "explanation": "stub self-test verdict"}),
            "_debug": {"ok": True},
        }

    return stub


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> dict[str, Any]:
    corpus = load_corpus(Path(args.corpus))
    if args.limit:
        corpus = corpus[: args.limit]

    model = args.model or JUDGE_MODEL
    if args.self_test:
        query_fn = _make_stub_query(corpus)
        mode = "self-test"
    else:
        query_fn = query_model
        mode = "live"

    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def bounded_run_case(case):
        async with semaphore:
            return await run_case(case, model=model, query_fn=query_fn)

    rows = await asyncio.gather(*[bounded_run_case(case) for case in corpus])

    by_category: dict[str, Any] = {}
    categories = sorted({case.category for case in corpus})
    for category in categories:
        category_rows = [r for r, case in zip(rows, corpus) if case.category == category]
        by_category[category] = compute_asymmetry_metrics(category_rows)

    return {
        "paper": "arXiv:2606.28050 (self-evaluation task asymmetry)",
        "self_eval_asymmetry_version": SELF_EVAL_ASYMMETRY_VERSION,
        "mode": mode,
        "provider": API_PROVIDER if mode == "live" else None,
        "model": model,
        "corpus": str(args.corpus),
        "n_cases": len(corpus),
        "overall": compute_asymmetry_metrics(list(rows)),
        "by_category": by_category,
        "rows": rows if args.include_rows else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH), help="path to the fixed corpus JSON")
    parser.add_argument("--model", default=None, help="model under test (default: JUDGE_MODEL)")
    parser.add_argument("--limit", type=int, help="cap the number of corpus cases")
    parser.add_argument("--concurrency", type=int, default=4, help="max in-flight cases")
    parser.add_argument("--include-rows", action="store_true", help="include per-case rows in output")
    parser.add_argument("--live", action="store_true", help="run against real provider models")
    parser.add_argument("--self-test", action="store_true", help="deterministic offline pipeline check")
    parser.add_argument("--output", help="optional path to write the JSON report")
    args = parser.parse_args()

    if not args.live and not args.self_test:
        parser.error("pass --live for a real run or --self-test for an offline check")

    report = asyncio.run(run(args))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
