#!/usr/bin/env python3
"""Benchmark top OpenRouter flagships and recommend a council line-up.

Methodology ("Standard" depth):
  1. Generation: every candidate answers each benchmark prompt.
  2. Anonymized peer-ranking: every candidate ranks the anonymized answer set.
     A ranker's vote for *its own* answer is dropped, so self-preference cannot
     inflate a model's score. Aggregated as a normalized Borda score (1 = best).
  3. Neutral pairwise judge: a strong model OUTSIDE the candidate pool scores the
     per-prompt peer top-4 in an anonymized round-robin (candidate vs baseline),
     reusing backend.eval.judge's validated judge.v1 schema. Order is alternated
     per pair to neutralize position bias. Yields a head-to-head win-rate.

Composite = 0.65 * peer_norm + 0.35 * judge_winrate (judge term used only where
available). Final pick: top-5 by composite -> #1 chairman, #2..#5 council. The 9
candidates span 9 distinct model families, so any 5 keep chairman/council
heterogeneity automatically.

Everything is routed through the production query_model() path. Read-only
w.r.t. repo files.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import infer_model_family  # noqa: E402
from backend.council import parse_ranking_from_text  # noqa: E402
from backend.eval.answer_check import check_answer  # noqa: E402
from backend.eval.judge import (  # noqa: E402
    JudgeResponseError,
    build_judge_prompt,
    parse_judge_response,
)
from backend.openrouter import query_model  # noqa: E402
from backend.provider_results import response_failed  # noqa: E402

CANDIDATES = [
    "openai/gpt-5.5",
    "anthropic/claude-opus-4.8",
    "x-ai/grok-4.3",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3.7-max",
    "google/gemini-3.1-pro-preview",
    "z-ai/glm-5.1",
    "moonshotai/kimi-k2.6",
    "minimax/minimax-m3",
]

# Strong instruction-follower OUTSIDE the candidate pool. Anonymized + applied
# uniformly, so residual vendor lean is a constant offset across all pairs.
NEUTRAL_JUDGE = "openai/gpt-5.4"

# Generous output budget so reasoning models emit visible content (the max_tokens=16
# smoke proved tight budgets starve them to empty output).
GEN_MAX_TOKENS = 3000
RANK_MAX_TOKENS = 3000
JUDGE_MAX_TOKENS = 4000
GEN_TIMEOUT = 200.0
GEN_TEMPERATURE = 0.3
CONCURRENCY = 6
JUDGE_CONCURRENCY = 4
JUDGE_TOP_K = 4

PROMPTS: list[dict[str, str]] = [
    {
        "id": "reasoning_logic",
        "domain": "reasoning",
        "prompt": "Three switches outside a windowless room each control one of three "
                  "incandescent bulbs inside. You may flip switches as much as you like, "
                  "but may enter the room only once. Describe a procedure that tells you "
                  "with certainty which switch controls which bulb, and explain why it works.",
    },
    {
        "id": "reasoning_fermi",
        "domain": "reasoning",
        "prompt": "Estimate how many piano tuners work in Chicago today. State your "
                  "assumptions explicitly, show the arithmetic, and give a final range. "
                  "Flag which assumption your estimate is most sensitive to.",
    },
    {
        "id": "code_implement",
        "domain": "code",
        "prompt": "Implement an LRU cache in Python with O(1) get and put. Provide the full "
                  "class, handle the capacity-eviction edge cases, and add a short docstring. "
                  "Then list two edge cases your implementation handles correctly.",
    },
    {
        "id": "code_debug",
        "domain": "code",
        "prompt": "This Python function is supposed to return the first non-repeating "
                  "character in a string (in order of appearance), or None if there is "
                  "none. It has a bug:\n\n"
                  "def first_unique(s):\n    counts = {}\n    for c in s:\n        counts[c] = counts.get(c, 0) + 1\n"
                  "    for c in sorted(counts):\n        if counts[c] == 1:\n            return c\n    return None\n\n"
                  "Identify the bug, explain why it fails (give a concrete input where it "
                  "returns the wrong character), and give a corrected version.",
    },
    {
        "id": "math_exact",
        "domain": "math",
        "prompt": "Find all real solutions to the equation x^4 - 5x^2 + 4 = 0. Show your "
                  "work and state the complete solution set.",
    },
    {
        "id": "math_probability",
        "domain": "math",
        "prompt": "A fair six-sided die is rolled four times. What is the exact probability "
                  "that at least one six appears? Give the closed-form fraction and a decimal, "
                  "and explain the reasoning.",
    },
    {
        "id": "factual_concept",
        "domain": "factuality",
        "prompt": "Explain how the Raft consensus algorithm elects a leader and what "
                  "guarantees it provides about log consistency. Be precise about terms, "
                  "election timeouts, and the role of the commit index.",
    },
    {
        "id": "factual_uncertainty",
        "domain": "factuality",
        "prompt": "What is the tight asymptotic time complexity of building a binary heap "
                  "from an unsorted array of n elements, and why is the common O(n log n) "
                  "intuition loose rather than tight? If any part of the common intuition is "
                  "misleading, say so explicitly.",
    },
    {
        "id": "writing_explain",
        "domain": "writing",
        "prompt": "Explain what a database index is and the trade-off it introduces, to a "
                  "junior backend engineer, in under 150 words. Be concrete and avoid filler.",
    },
    {
        "id": "instruction_following",
        "domain": "instruction_following",
        "prompt": "Output exactly three bullet points, each starting with a different verb in "
                  "the imperative mood, summarizing best practices for handling secrets in a "
                  "codebase. Do not add a heading, intro, or closing line. Each bullet must be "
                  "one sentence and under 20 words.",
    },
    {
        "id": "multilingual_ru",
        "domain": "multilingual",
        "prompt": "Объясни на русском языке, в чём разница между процессом и потоком (thread) "
                  "в операционной системе, и приведи один практический пример, когда потоки "
                  "предпочтительнее процессов. Будь технически точным.",
    },
    {
        "id": "false_premise",
        "domain": "abstention",
        "prompt": "Summarize the main argument of Albert Einstein's 1953 paper proving that "
                  "P equals NP. If the premise of this question is flawed, say so clearly and "
                  "explain what is actually true instead of inventing content.",
    },
]

# v2: harder, less-memorized prompts with checkable answers. Designed to be sharper
# discriminators than v1 (which leaned on textbook patterns: LRU, biquadratic, the
# 3-switch riddle, dice probability — all of which every flagship knows cold).
PROMPTS_V2: list[dict[str, str]] = [
    {
        "id": "bayes_base_rate",
        "domain": "reasoning",
        "prompt": "A disease affects 1 in 1,000 people. A screening test is 99% sensitive "
                  "(positive when the person is sick) and 95% specific (negative when the "
                  "person is healthy). A randomly chosen person tests positive. What is the "
                  "probability they actually have the disease? Show the calculation and "
                  "explain why the answer is much lower than most people expect.",
    },
    {
        "id": "fermi_whale_heartbeats",
        "domain": "reasoning",
        "prompt": "Estimate the total number of heartbeats in the lifetime of an average "
                  "blue whale. State your assumptions explicitly (lifespan, typical heart "
                  "rate), show the arithmetic, give a final range, and flag which assumption "
                  "your estimate is most sensitive to.",
    },
    {
        "id": "code_hidden_bug",
        "domain": "code",
        "prompt": "This Python function is meant to split a list into `n` contiguous chunks "
                  "that together contain ALL the original items:\n\n"
                  "def chunk_list(items, n):\n    size = len(items) // n\n    chunks = []\n"
                  "    for i in range(n):\n        chunks.append(items[i*size:(i+1)*size])\n"
                  "    return chunks\n\n"
                  "It has a subtle bug. Identify it, show a concrete input where it loses "
                  "data, explain the cause, and give a corrected version that keeps every item.",
    },
    {
        "id": "code_rate_limiter",
        "domain": "code",
        "prompt": "Implement a token-bucket rate limiter in Python: a class "
                  "`RateLimiter(rate_per_sec, capacity)` with a method `allow() -> bool` that "
                  "refills tokens based on elapsed monotonic time and returns whether the "
                  "current call is permitted. Explain the refill math and call out one edge "
                  "case your implementation handles (e.g. bursts, or capping accumulated tokens).",
    },
    {
        "id": "math_inclusion_exclusion",
        "domain": "math",
        "prompt": "How many integers from 1 to 1000 inclusive are divisible by at least one "
                  "of 6, 10, or 15? Use inclusion-exclusion, show every term of the "
                  "computation, and state the exact final count.",
    },
    {
        "id": "math_irrationality_proof",
        "domain": "math",
        "prompt": "Prove that sqrt(2) + sqrt(3) is irrational. Give a complete, rigorous "
                  "proof and justify each step.",
    },
    {
        "id": "factual_mvcc",
        "domain": "factuality",
        "prompt": "Explain how PostgreSQL's MVCC works: what a tuple's xmin and xmax mean, "
                  "how a transaction's snapshot decides which row versions are visible, and "
                  "exactly what problem VACUUM solves. Be precise about dead tuples and "
                  "transaction-ID wraparound.",
    },
    {
        "id": "factual_float_assoc",
        "domain": "factuality",
        "prompt": "Is IEEE-754 double-precision floating-point addition associative? Answer "
                  "yes or no, prove your answer with a concrete example using specific values, "
                  "and explain the underlying cause.",
    },
    {
        "id": "abstention_fake_paper",
        "domain": "abstention",
        "prompt": "Summarize the methodology and headline result of the 2021 DeepMind paper "
                  "'Chinchilla-2: Scaling Laws for Trillion-Parameter Retrieval Models.' If "
                  "you are not certain this specific paper exists, say so plainly instead of "
                  "guessing or reconstructing plausible-sounding content.",
    },
    {
        "id": "multilingual_isolation_ru",
        "domain": "multilingual",
        "prompt": "Объясни на русском языке, что гарантирует и чего НЕ гарантирует уровень "
                  "изоляции транзакций READ COMMITTED по стандарту SQL. Приведи конкретный "
                  "пример аномалии, возможной при READ COMMITTED, но невозможной при "
                  "REPEATABLE READ, и отметь, если в конкретной СУБД (например, PostgreSQL) "
                  "реальное поведение строже стандарта. Будь технически точным.",
    },
    {
        "id": "instruction_regex_ipv4",
        "domain": "instruction_following",
        "prompt": "Output a single line containing only a POSIX extended regular expression "
                  "that matches a valid IPv4 octet (an integer from 0 to 255) and rejects "
                  "everything else. No explanation, no code fence, no surrounding text — "
                  "just the regular expression on one line.",
    },
    {
        "id": "reasoning_bat_ball",
        "domain": "reasoning",
        "prompt": "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the "
                  "ball. A glove costs three times as much as the ball. What does each of the "
                  "ball, the bat, and the glove cost? Show your reasoning step by step.",
    },
]

PROMPT_SETS = {"v1": PROMPTS, "v2": PROMPTS_V2}


async def _gather_limited(coros: list[Any], limit: int) -> list[Any]:
    sem = asyncio.Semaphore(limit)

    async def run(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*[run(c) for c in coros])


async def generate_answers(prompt_text: str) -> dict[str, dict[str, Any]]:
    async def one(model: str) -> tuple[str, dict[str, Any]]:
        resp = await query_model(
            model,
            [{"role": "user", "content": prompt_text}],
            timeout=GEN_TIMEOUT,
            temperature=GEN_TEMPERATURE,
            max_tokens=GEN_MAX_TOKENS,
        )
        debug = (resp or {}).get("_debug", {})
        if response_failed(resp) or not (resp or {}).get("content"):
            return model, {
                "ok": False,
                "content": None,
                "ms": debug.get("duration_ms"),
                "error": debug.get("failure_type") or "empty_content",
            }
        return model, {
            "ok": True,
            "content": resp["content"],
            "ms": debug.get("duration_ms"),
        }

    pairs = await _gather_limited([one(m) for m in CANDIDATES], CONCURRENCY)
    return dict(pairs)


def build_rank_prompt(question: str, answers_text: str) -> str:
    return f"""You are evaluating different responses to the following question:

Question: {question}

Here are the responses from different models (anonymized):

{answers_text}

Your task:
1. First, briefly evaluate each response: what it does well and poorly.
2. Then provide a final ranking from best to worst.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line is: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text after the ranking section

Now provide your evaluation and ranking:"""


async def peer_rank(
    question: str,
    answers: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Returns (per-ranker results, label->model, model->label) for OK answers only."""
    ok_models = [m for m in CANDIDATES if answers[m]["ok"]]
    labels = [chr(65 + i) for i in range(len(ok_models))]
    label_to_model = {f"Response {lab}": m for lab, m in zip(labels, ok_models)}
    model_to_label = {m: f"Response {lab}" for lab, m in zip(labels, ok_models)}

    answers_text = "\n\n".join(
        f"Response {lab}:\n{answers[m]['content']}" for lab, m in zip(labels, ok_models)
    )
    rank_prompt = build_rank_prompt(question, answers_text)

    async def one(ranker: str) -> dict[str, Any]:
        resp = await query_model(
            ranker,
            [{"role": "user", "content": rank_prompt}],
            timeout=GEN_TIMEOUT,
            temperature=0.0,
            max_tokens=RANK_MAX_TOKENS,
        )
        if response_failed(resp) or not (resp or {}).get("content"):
            return {"ranker": ranker, "ok": False, "parsed": []}
        parsed = parse_ranking_from_text(resp["content"])
        return {"ranker": ranker, "ok": True, "parsed": parsed}

    results = await _gather_limited([one(m) for m in ok_models], CONCURRENCY)
    return results, label_to_model, model_to_label


async def judge_pair(
    question: str,
    answer_a: str,
    answer_b: str,
) -> str | None:
    """Returns 'candidate' (a wins), 'baseline' (b wins), 'tie', or None on failure."""
    prompt = build_judge_prompt(
        question=question,
        candidate_answer=answer_a,
        baseline_answer=answer_b,
    )
    for _ in range(2):
        resp = await query_model(
            NEUTRAL_JUDGE,
            [{"role": "user", "content": prompt}],
            timeout=GEN_TIMEOUT,
            temperature=0.0,
            max_tokens=JUDGE_MAX_TOKENS,
        )
        if response_failed(resp) or not (resp or {}).get("content"):
            continue
        try:
            return parse_judge_response(resp["content"])["winner"]
        except JudgeResponseError:
            continue
    return None


async def run_benchmark(
    prompt_set: list[dict[str, str]] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    base = prompt_set if prompt_set is not None else PROMPTS
    prompts = base[:limit] if limit else base

    # Per-model accumulators
    peer_norms: dict[str, list[float]] = defaultdict(list)  # normalized borda per prompt
    judge_points: dict[str, float] = defaultdict(float)
    judge_games: dict[str, int] = defaultdict(int)
    gen_fail: dict[str, int] = defaultdict(int)
    gen_latency: dict[str, list[float]] = defaultdict(list)
    obj_correct: dict[str, int] = defaultdict(int)  # deterministic ground-truth checks
    obj_total: dict[str, int] = defaultdict(int)
    per_prompt_report: list[dict[str, Any]] = []

    for idx, item in enumerate(prompts, start=1):
        q = item["prompt"]
        print(f"[{idx}/{len(prompts)}] {item['id']} ({item['domain']}) — generating...",
              flush=True)
        answers = await generate_answers(q)
        for m in CANDIDATES:
            if answers[m]["ok"]:
                if answers[m]["ms"] is not None:
                    gen_latency[m].append(answers[m]["ms"])
            else:
                gen_fail[m] += 1

        # Deterministic ground-truth check (independent of peer-ranking / judge).
        prompt_objective: dict[str, dict[str, Any]] = {}
        for m in CANDIDATES:
            if not answers[m]["ok"]:
                continue
            res = check_answer(item["id"], answers[m]["content"])
            if not res.checkable:
                continue
            obj_total[m] += 1
            if res.correct:
                obj_correct[m] += 1
            prompt_objective[m] = {
                "correct": res.correct,
                "confidence": res.confidence,
                "detail": res.detail,
            }
        if prompt_objective:
            n_ok = sum(1 for v in prompt_objective.values() if v["correct"])
            print(f"    objective check ({item['id']}): {n_ok}/{len(prompt_objective)} correct",
                  flush=True)

        ok_models = [m for m in CANDIDATES if answers[m]["ok"]]
        if len(ok_models) < 2:
            print(f"    skip ranking: only {len(ok_models)} OK answers", flush=True)
            per_prompt_report.append({"id": item["id"], "ok_models": ok_models,
                                      "objective": prompt_objective,
                                      "note": "insufficient answers"})
            continue

        print(f"    {len(ok_models)} answers — peer ranking...", flush=True)
        rankings, label_to_model, model_to_label = await peer_rank(q, answers)
        n = len(ok_models)

        # Aggregate peer ranks, excluding each ranker's vote for its own answer.
        positions: dict[str, list[int]] = defaultdict(list)
        valid_labels = set(label_to_model)
        for r in rankings:
            if not r["ok"]:
                continue
            ranker_label = model_to_label.get(r["ranker"])
            seen = set()
            ordered = [lab for lab in r["parsed"] if lab in valid_labels and lab not in seen
                       and not seen.add(lab)]
            for pos, lab in enumerate(ordered, start=1):
                if lab == ranker_label:
                    continue  # drop self-vote
                positions[label_to_model[lab]].append(pos)

        prompt_peer = {}
        for m in ok_models:
            plist = positions.get(m, [])
            if plist:
                avg_rank = sum(plist) / len(plist)
                norm = (n - avg_rank) / (n - 1) if n > 1 else 1.0  # 1=best
                peer_norms[m].append(norm)
                prompt_peer[m] = {"avg_rank": round(avg_rank, 2),
                                  "norm": round(norm, 3), "votes": len(plist)}

        # Neutral judge: round-robin among the per-prompt peer top-K.
        top_models = sorted(prompt_peer, key=lambda m: prompt_peer[m]["avg_rank"])[:JUDGE_TOP_K]
        print(f"    judge round-robin among top-{len(top_models)}: "
              f"{[m.split('/')[-1] for m in top_models]}", flush=True)
        pairs = list(itertools.combinations(top_models, 2))

        async def judge_one(flip: int, ma: str, mb: str) -> dict[str, Any]:
            # alternate which side is 'candidate' to cancel position bias
            first, second = (ma, mb) if flip % 2 == 0 else (mb, ma)
            verdict = await judge_pair(
                q, answers[first]["content"], answers[second]["content"]
            )
            return {"ma": ma, "mb": mb, "first": first, "second": second, "verdict": verdict}

        raw = await _gather_limited(
            [judge_one(flip, ma, mb) for flip, (ma, mb) in enumerate(pairs)],
            JUDGE_CONCURRENCY,
        )

        judge_results = []
        for r in raw:
            ma, mb, first, second = r["ma"], r["mb"], r["first"], r["second"]
            judge_games[ma] += 1
            judge_games[mb] += 1
            if r["verdict"] == "candidate":
                judge_points[first] += 1.0
                judge_results.append({"a": first, "b": second, "winner": first})
            elif r["verdict"] == "baseline":
                judge_points[second] += 1.0
                judge_results.append({"a": first, "b": second, "winner": second})
            else:  # tie or unparseable
                judge_points[ma] += 0.5
                judge_points[mb] += 0.5
                judge_results.append({"a": first, "b": second,
                                      "winner": "tie" if r["verdict"] == "tie" else "no_verdict"})

        per_prompt_report.append({
            "id": item["id"],
            "domain": item["domain"],
            "peer": prompt_peer,
            "judge_top": top_models,
            "judge_pairs": judge_results,
            "objective": prompt_objective,
        })

    # Final composite
    leaderboard = []
    for m in CANDIDATES:
        peer_vals = peer_norms.get(m, [])
        peer_norm = sum(peer_vals) / len(peer_vals) if peer_vals else 0.0
        games = judge_games.get(m, 0)
        judge_winrate = (judge_points.get(m, 0.0) / games) if games else None
        if judge_winrate is not None:
            composite = 0.65 * peer_norm + 0.35 * judge_winrate
        else:
            composite = peer_norm
        latencies = gen_latency.get(m, [])
        checked = obj_total.get(m, 0)
        obj_acc = (obj_correct.get(m, 0) / checked) if checked else None
        leaderboard.append({
            "model": m,
            "family": infer_model_family(m),
            "peer_norm": round(peer_norm, 3),
            "peer_prompts": len(peer_vals),
            "judge_winrate": round(judge_winrate, 3) if judge_winrate is not None else None,
            "judge_games": games,
            "gen_failures": gen_fail.get(m, 0),
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
            "objective_accuracy": round(obj_acc, 3) if obj_acc is not None else None,
            "objective_checked": checked,
            "objective_correct": obj_correct.get(m, 0),
            "composite": round(composite, 4),
        })

    leaderboard.sort(key=lambda r: r["composite"], reverse=True)

    # Council recommendation: top-5 by composite -> #1 chairman, next 4 council.
    ranked_models = [r["model"] for r in leaderboard if r["gen_failures"] < len(prompts)]
    top5 = ranked_models[:5]
    recommendation = {
        "chairman": top5[0] if top5 else None,
        "council": top5[1:5],
        "all_distinct_families": len({infer_model_family(m) for m in top5}) == len(top5),
    }
    return {
        "leaderboard": leaderboard,
        "recommendation": recommendation,
        "per_prompt": per_prompt_report,
        "config": {
            "candidates": CANDIDATES,
            "neutral_judge": NEUTRAL_JUDGE,
            "prompts": [p["id"] for p in prompts],
            "judge_top_k": JUDGE_TOP_K,
            "composite_weights": {"peer_norm": 0.65, "judge_winrate": 0.35},
        },
    }


def print_leaderboard(report: dict[str, Any]) -> None:
    print("\n" + "=" * 92)
    print("COUNCIL MODEL BENCHMARK — LEADERBOARD (composite desc)")
    print("=" * 92)
    print(f"{'#':<3}{'model':<34}{'composite':>10}{'peer':>8}{'judgeW':>9}"
          f"{'objAcc':>8}{'games':>7}{'fails':>7}{'lat(ms)':>9}")
    print("-" * 92)
    for i, r in enumerate(report["leaderboard"], start=1):
        jw = f"{r['judge_winrate']:.3f}" if r["judge_winrate"] is not None else "  -  "
        oa = (f"{r['objective_accuracy']:.2f}({r['objective_checked']})"
              if r.get("objective_accuracy") is not None else "  -  ")
        lat = r["avg_latency_ms"] if r["avg_latency_ms"] is not None else "-"
        print(f"{i:<3}{r['model']:<34}{r['composite']:>10.4f}{r['peer_norm']:>8.3f}"
              f"{jw:>9}{oa:>8}{r['judge_games']:>7}{r['gen_failures']:>7}{str(lat):>9}")
    rec = report["recommendation"]
    print("-" * 92)
    print(f"Recommended chairman : {rec['chairman']}")
    print(f"Recommended council  : {rec['council']}")
    print(f"All distinct families: {rec['all_distinct_families']}")
    print("=" * 92)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None)
    parser.add_argument("--promptset", choices=sorted(PROMPT_SETS), default="v1",
                        help="Which benchmark prompt set to run (default: v1).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N prompts (smoke).")
    args = parser.parse_args()

    prompt_set = PROMPT_SETS[args.promptset]
    output = args.output or (
        "output/council-benchmark.json" if args.promptset == "v1"
        else f"output/council-benchmark-{args.promptset}.json"
    )

    started = time.perf_counter()
    report = asyncio.run(run_benchmark(prompt_set=prompt_set, limit=args.limit))
    report["prompt_set"] = args.promptset
    report["wall_clock_seconds"] = round(time.perf_counter() - started, 1)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_leaderboard(report)
    print(f"\nArtifact: {out}  ({report['wall_clock_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
