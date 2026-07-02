#!/usr/bin/env python3
"""Faithful BINEVAL replication on QAGS (arXiv:2606.27226, Part I).

Tests the paper's central evaluation-quality claim on its own protocol:
decomposed binary questions, answered pointwise against the source document,
correlate with human factual-consistency ratings at least as well as a holistic
G-Eval-style score -- and better for hallucination-prone XSum.

Unlike the earlier pairwise probe (``docs/bineval-results.md``), this run is
inside the paper's design envelope: task-level question generation, source
grounding, pointwise scoring, and Spearman/Kendall/Pearson correlation with
human labels.

This is operator-facing and offline; it does not touch ``ask_council`` or the
production judge. Requires ``API_PROVIDER=openrouter`` with
``OPENROUTER_API_KEY`` configured.

    python scripts/bineval_replication.py \\
        --splits cnndm,xsum --limit 40 --model anthropic/claude-sonnet-4-5 \\
        --questions generate --concurrency 6 --output output/bineval-replication.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scipy import stats  # noqa: E402

from backend.config import API_PROVIDER, JUDGE_MODEL  # noqa: E402
from backend.eval.bineval import (  # noqa: E402
    BINEVAL_VERSION,
    CONSISTENCY,
    PAPER_CONSISTENCY_QUESTIONS,
    BinevalQuestion,
    generate_binary_questions,
    score_summary_decomposed,
    score_summary_holistic,
    score_summary_single_boolean,
)
from backend.eval.qags_dataset import (  # noqa: E402
    QAGS_RAW_URL,
    QAGS_SPLITS,
    default_qags_path,
    load_qags,
)
from backend.openrouter import query_model  # noqa: E402

ALL_VARIANTS = ("bineval", "holistic", "single_boolean")


def _ensure_qags(split: str, root: Path) -> Path:
    path = default_qags_path(split, root=root)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    url = QAGS_RAW_URL.format(split=split)
    print(f"[qags] downloading {split} -> {path}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (trusted host)
        path.write_bytes(response.read())
    return path


def _stat(value: Any) -> float:
    """Coerce a scipy correlation statistic (tuple[0] across versions) to float."""
    return round(float(value), 4)


def _correlations(method_scores: list[float], human: list[float]) -> dict[str, Any]:
    """Spearman/Kendall/Pearson between method and human, guarding constants."""
    n = len(method_scores)
    if n < 3:
        return {"n": n, "spearman": None, "kendall": None, "pearson": None}
    if len(set(method_scores)) < 2 or len(set(human)) < 2:
        return {"n": n, "spearman": None, "kendall": None, "pearson": None,
                "note": "constant series; correlation undefined"}
    # Index [0] (statistic) works across scipy versions (tuple or *Result).
    return {
        "n": n,
        "spearman": _stat(stats.spearmanr(method_scores, human)[0]),
        "kendall": _stat(stats.kendalltau(method_scores, human)[0]),
        "pearson": _stat(stats.pearsonr(method_scores, human)[0]),
    }


async def _resolve_questions(
    mode: str,
    *,
    model: str,
    query_fn: Any,
    cache_path: Path,
) -> tuple[list[BinevalQuestion], dict[str, Any]]:
    """Generate the consistency question bank once, or load the paper's bank."""
    if mode == "paper":
        return list(PAPER_CONSISTENCY_QUESTIONS), {"source": "paper_table_10"}

    result = await generate_binary_questions(
        CONSISTENCY, judge_model=model, query_fn=query_fn
    )
    if result["status"] != "ok":
        print(
            f"[questions] generation failed ({result.get('error')}); "
            "falling back to paper bank",
            file=sys.stderr,
        )
        return list(PAPER_CONSISTENCY_QUESTIONS), {
            "source": "paper_table_10_fallback",
            "generation_error": result.get("error"),
        }
    questions = result["questions"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            [{"id": q.id, "text": q.text, "violation_example": q.violation_example} for q in questions],
            indent=2,
        )
        + "\n"
    )
    return questions, {"source": "generated", "cache": str(cache_path)}


async def _score_one(
    summary: Any,
    *,
    variants: tuple[str, ...],
    questions: list[BinevalQuestion],
    model: str,
    query_fn: Any,
) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    if "bineval" in variants:
        tasks["bineval"] = score_summary_decomposed(
            source=summary.source, summary=summary.summary, questions=questions,
            judge_model=model, query_fn=query_fn, concurrency=len(questions),
        )
    if "holistic" in variants:
        tasks["holistic"] = score_summary_holistic(
            source=summary.source, summary=summary.summary,
            judge_model=model, query_fn=query_fn,
        )
    if "single_boolean" in variants:
        tasks["single_boolean"] = score_summary_single_boolean(
            source=summary.source, summary=summary.summary,
            judge_model=model, query_fn=query_fn,
        )
    results = await asyncio.gather(*tasks.values())
    by_variant = dict(zip(tasks.keys(), results))
    return {
        "split": summary.split,
        "index": summary.index,
        "human": summary.human_score,
        "scores": {name: res.get("score") for name, res in by_variant.items()},
        "calls": {
            "bineval": len(questions) if "bineval" in variants else 0,
            "holistic": 1 if "holistic" in variants else 0,
            "single_boolean": 1 if "single_boolean" in variants else 0,
        },
    }


def _scorecard(rows: list[dict[str, Any]], variants: tuple[str, ...]) -> dict[str, Any]:
    human = [r["human"] for r in rows]
    card: dict[str, Any] = {"n_total": len(rows)}
    for variant in variants:
        paired = [(r["scores"][variant], r["human"]) for r in rows if r["scores"].get(variant) is not None]
        dropped = len(rows) - len(paired)
        if paired:
            scores = [p[0] for p in paired]
            humans = [p[1] for p in paired]
            corr = _correlations(scores, humans)
            mean_score = round(sum(scores) / len(scores), 4)
        else:
            corr = {"n": 0, "spearman": None, "kendall": None, "pearson": None}
            mean_score = None
        card[variant] = {**corr, "dropped": dropped, "mean_score": mean_score}
    card["human_mean"] = round(sum(human) / len(human), 4) if human else None
    return card


async def run(args: argparse.Namespace) -> dict[str, Any]:
    splits = tuple(s.strip() for s in args.splits.split(",") if s.strip())
    for split in splits:
        if split not in QAGS_SPLITS:
            raise SystemExit(f"unknown split {split!r}; choose from {QAGS_SPLITS}")
    variants = tuple(v.strip() for v in args.variants.split(",") if v.strip())
    for variant in variants:
        if variant not in ALL_VARIANTS:
            raise SystemExit(f"unknown variant {variant!r}; choose from {ALL_VARIANTS}")

    model = args.model or JUDGE_MODEL
    root = PROJECT_ROOT / "output" / "qags"

    # One global semaphore caps in-flight model calls across all summaries.
    global_sem = asyncio.Semaphore(max(1, args.concurrency))
    completed = {"n": 0}

    async def bounded_query(*call_args: Any, **call_kwargs: Any) -> dict[str, Any]:
        async with global_sem:
            return await query_model(*call_args, **call_kwargs)

    cache_path = PROJECT_ROOT / "output" / "bineval_questions_consistency.json"
    questions, question_meta = await _resolve_questions(
        args.questions, model=model, query_fn=bounded_query, cache_path=cache_path
    )
    print(f"[questions] {question_meta['source']}: {len(questions)} questions", file=sys.stderr)
    for q in questions:
        print(f"  {q.id}: {q.text}", file=sys.stderr)

    rng = random.Random(args.seed)
    sampled: list[Any] = []
    for split in splits:
        path = _ensure_qags(split, root)
        records = load_qags(path, split=split)
        if args.limit and args.limit < len(records):
            records = rng.sample(records, args.limit)
        sampled.extend(records)
    print(f"[data] scoring {len(sampled)} summaries across {splits}", file=sys.stderr)

    total = len(sampled)

    async def process(summary: Any) -> dict[str, Any]:
        row = await _score_one(
            summary, variants=variants, questions=questions, model=model, query_fn=bounded_query
        )
        completed["n"] += 1
        if completed["n"] % 5 == 0 or completed["n"] == total:
            print(f"[progress] {completed['n']}/{total}", file=sys.stderr, flush=True)
        return row

    # Depth-first worker pool over summaries: at most ``summary_workers``
    # summaries are in flight, so early summaries finish quickly and progress is
    # meaningful. The global ``bounded_query`` semaphore still caps total
    # in-flight model calls regardless of worker count.
    results: list[Any] = [None] * total
    cursor = {"i": 0}
    cursor_lock = asyncio.Lock()

    async def worker() -> None:
        while True:
            async with cursor_lock:
                i = cursor["i"]
                if i >= total:
                    return
                cursor["i"] += 1
            results[i] = await process(sampled[i])

    await asyncio.gather(*[worker() for _ in range(max(1, args.summary_workers))])
    rows = results

    by_split = {
        split: _scorecard([r for r in rows if r["split"] == split], variants)
        for split in splits
    }
    combined = _scorecard(list(rows), variants) if len(splits) > 1 else None
    total_calls = {
        variant: sum(r["calls"][variant] for r in rows) for variant in ALL_VARIANTS
    }

    return {
        "paper": "arXiv:2606.27226 (BINEVAL, Part I evaluation quality)",
        "bineval_version": BINEVAL_VERSION,
        "provider": API_PROVIDER,
        "model": model,
        "seed": args.seed,
        "limit": args.limit,
        "variants": list(variants),
        "questions": {**question_meta, "count": len(questions),
                      "ids": [q.id for q in questions]},
        "metric_target": "QAGS summary-level human factual-consistency score",
        "by_split": by_split,
        "combined": combined,
        "total_calls": total_calls,
        "rows": rows if args.include_rows else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--splits", default="cnndm,xsum", help="comma list of QAGS splits")
    parser.add_argument("--limit", type=int, default=40, help="summaries per split (0 = all)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default=None, help="evaluator model (default: JUDGE_MODEL)")
    parser.add_argument("--questions", choices=("generate", "paper"), default="generate")
    parser.add_argument("--variants", default="bineval,holistic,single_boolean")
    parser.add_argument("--concurrency", type=int, default=8, help="max in-flight model calls")
    parser.add_argument("--summary-workers", type=int, default=4, help="summaries processed in parallel")
    parser.add_argument("--include-rows", action="store_true", help="include per-summary rows in output")
    parser.add_argument("--output", help="optional path to write the JSON report")
    args = parser.parse_args()

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
