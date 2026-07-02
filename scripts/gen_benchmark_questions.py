#!/usr/bin/env python3
"""Generate output/benchmark-questions.md from the benchmark harness.

Single source of truth: prompt text comes from scripts/council_model_benchmark.py
(PROMPTS / PROMPTS_V2); the reference-answer keys live here. Re-run after editing
either to keep the doc in sync:  python scripts/gen_benchmark_questions.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "council_model_benchmark", str(ROOT / "scripts" / "council_model_benchmark.py")
)
assert _spec is not None and _spec.loader is not None
_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bench)

OUT = ROOT / "output" / "benchmark-questions.md"

# Reference answers / scoring notes. Open-ended prompts get a rubric note.
ANSWERS: dict[str, str] = {
    # ---- v1 ----
    "reasoning_logic": "Heat trick: turn switch 1 on for several minutes, then off; turn "
        "switch 2 on; enter. Lit = switch 2, off+warm = switch 1, off+cold = switch 3.",
    "reasoning_fermi": "Open Fermi. Plausible ~25–100 tuners for Chicago; score the method "
        "(pianos × tunings/yr ÷ tuner output) and explicit assumptions, not the exact number.",
    "code_implement": "dict + doubly linked list with sentinels, O(1) get/put. Score: "
        "put on existing key must NOT grow size or evict; capacity<=0 handled; capacity==1 "
        "evicts correctly; get updates recency.",
    "code_debug": "Bug: the 2nd loop iterates `sorted(counts)`, returning the "
        "alphabetically-first unique char instead of the first by appearance. "
        "first_unique('bca') returns 'a' but should be 'b' (also 'stress' → 'e', should be "
        "'t'). Fix: iterate the string — `for c in s:`. (Genuine bug on all Python versions.)",
    "math_exact": "Solution set {-2, -1, 1, 2} (let u=x²: (u-1)(u-4)=0 → x²=1 or 4).",
    "math_probability": "1 − (5/6)^4 = 671/1296 ≈ 0.5177 (Chevalier de Méré). Already lowest "
        "terms (671 = 11·61, 1296 = 2⁴·3⁴).",
    "factual_concept": "Raft. Score: candidate increments term, votes ≤ once per term, "
        "majority required, up-to-date-log check, randomized election timeouts, log matching, "
        "commit index, current-term commit restriction (Fig. 8), leader completeness.",
    "factual_uncertainty": "Tight bound is Θ(n) (Floyd bottom-up build-heap; per-node cost ∝ "
        "height, Σ k/2^k = 2). O(n log n) is a VALID but loose upper bound — it is not the "
        "tight Θ. The misconception conflates node depth with height (most nodes are near the "
        "leaves) and/or assumes top-down repeated insertion (which really is O(n log n)).",
    "writing_explain": "Open. Index = secondary structure (usually a B-tree) mapping keys to "
        "row locations for fast lookups; trade-off: faster reads, slower writes + extra "
        "storage. Under 150 words; count words carefully (watch punctuation / hyphenation).",
    "instruction_following": "Format test: exactly 3 bullets, distinct imperative verbs, "
        "each one sentence < 20 words, no heading/intro/closing line.",
    "multilingual_ru": "Процесс = единица ресурсов / изолированное адресное пространство; "
        "поток = единица исполнения внутри процесса (общая память, свой стек/регистры); "
        "пример — многопоточный веб-сервер с общим кэшем и пулом соединений.",
    "false_premise": "False premise: Einstein never proved P = NP. The P vs NP problem / "
        "NP-completeness framework was formalized in the early 1970s, especially by Cook "
        "(1971) and independently by Levin; P vs NP remains an open Millennium Prize problem. "
        "Correct response: flag the premise, don't invent content.",
    # ---- v2 ----
    "bayes_base_rate": "≈1.94%. P = (0.99·0.001)/(0.99·0.001 + 0.05·0.999) = 0.00099/0.05094 "
        "= 11/566 ≈ 0.0194 (base-rate neglect: the false positives swamp the rare true ones).",
    "fermi_whale_heartbeats": "Open Fermi. With assumptions ~8–10 bpm and ~80–90 yr, the "
        "arithmetic gives ≈0.34–0.47 billion heartbeats, i.e. hundreds of millions, not "
        "3–5 billion. A wider plausible range is ~0.3–1.5 billion depending on the assumed "
        "average heart rate. Most sensitive to the assumed average heart rate.",
    "code_hidden_bug": "Drops the remainder when len(items) % n != 0 — e.g. "
        "chunk_list(list(range(10)), 3) loses element 9. Also n > len(items) gives size==0 → "
        "n empty chunks, losing everything. Fix: a balanced split that distributes the "
        "remainder and keeps every item.",
    "code_rate_limiter": "Token bucket using time.monotonic(): "
        "tokens = min(capacity, tokens + elapsed·rate); allow() consumes 1 if ≥1 available. "
        "Edge: cap accumulated tokens at capacity (burst limit); monotonic time avoids "
        "backward jumps caused by wall-clock adjustments such as NTP or manual clock changes.",
    "math_inclusion_exclusion": "266. |6|=166, |10|=100, |15|=66; all pairwise lcm = 30 → 33 "
        "each; triple lcm = 30 → 33. 332 − 99 + 33 = 266.",
    "math_irrationality_proof": "Assume √2 + √3 = r ∈ ℚ (r > 0, so r ≠ 0). Then "
        "(r − √2)² = 3 ⇒ √2 = (r² − 1)/(2r) ∈ ℚ, contradicting √2 irrational. ∎",
    "factual_mvcc": "xmin = creating txid; xmax = deleting/updating/locking txid (can be a "
        "lock or MultiXact, not only DELETE). UPDATE writes a NEW tuple version and sets xmax "
        "on the old one. A version is visible iff its xmin is committed & visible to the "
        "snapshot (not in-progress/aborted) and xmax is absent/aborted/not-visible. VACUUM "
        "reclaims dead tuples (marks space reusable, cleans heap + indexes — not a full "
        "defrag) and, by freezing old XIDs (relfrozenxid), prevents transaction-ID "
        "wraparound; anti-wraparound autovacuum forces this freeze.",
    "factual_float_assoc": "No. e.g. (1e20 + −1e20) + 1 = 1.0, but 1e20 + (−1e20 + 1) = 0.0. "
        "Cause: finite precision — the small addend is absorbed/rounded away depending on "
        "grouping.",
    "abstention_fake_paper": "The paper does not exist (fabricated title — the real "
        "Chinchilla, 2022, is about compute-optimal scaling, not 'Trillion-Parameter "
        "Retrieval'). Correct response: decline / flag uncertainty instead of inventing it.",
    "multilingual_isolation_ru": "По стандарту SQL READ COMMITTED запрещает грязные чтения, "
        "но допускает non-repeatable reads и фантомы; REPEATABLE READ дополнительно запрещает "
        "non-repeatable reads. Пример: повторный SELECT той же строки в одной транзакции даёт "
        "разное значение (возможно при RC, невозможно при RR). Нюанс: в PostgreSQL REPEATABLE "
        "READ = snapshot isolation и строже стандарта — не допускает и фантомы при повторном "
        "SELECT внутри снапшота.",
    "instruction_regex_ipv4": "Octet 0–255, only the regex on one line, e.g. "
        "^(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$ — rejects leading zeros (01, 001) "
        "because the octet is an integer 0–255.",
    "reasoning_bat_ball": "ball = $0.05, bat = $1.05, glove = $0.15 (CRT trap: the ball is "
        "$0.05, not the intuitive $0.10).",
}

REVISIONS = """\
> **Правки после ревью (validation pass).** Скорректированы формулировки и эталоны,
> чтобы benchmark мерил качество, а не терпимость к кривой постановке:
> - `code_debug` (v1): функция заменена на реально багованную (`sorted(counts)`),
>   т.к. прежний вариант `for c in counts` на Python 3.7+ работает корректно
>   (dict хранит порядок вставки) — бага в целевой среде не было.
> - `factual_uncertainty` (v1): «why is it **not O(n log n)**» → «tight complexity и
>   почему O(n log n) — loose, а не tight» (O(n log n) формально верная верхняя граница).
> - `code_rate_limiter` (v2): «elapsed **wall-clock** time» → «elapsed **monotonic** time»
>   (wall-clock может прыгать из-за NTP/manual clock changes — для rate limiter некорректно).
> - Исправлен эталон `fermi_whale_heartbeats`: арифметика давала ≈0.34–0.47 млрд (сотни
>   миллионов), а не 3–5 млрд — был лишний порядок; расширен диапазон до ~0.3–1.5 млрд.
> - Расширены эталоны: `code_hidden_bug` — добавлен случай n > len(items);
>   `factual_mvcc` — xmax/MultiXact, новая версия при UPDATE, freeze/wraparound;
>   `multilingual_isolation_ru` — явно стандарт SQL + нюанс PostgreSQL (RR = snapshot).
>
> Замечание: записанные прогоны (`output/council-benchmark*.json`) выполнялись на
> ДО-ревизионном тексте `code_debug`/`factual_uncertainty`; выбор совета был устойчив
> на обоих наборах, так что правки нужны для будущих «чистых» прогонов, а не для
> пересмотра результата.

"""


def block(title: str, prompts: list[dict[str, str]]) -> str:
    out = [f"## {title}\n"]
    for i, p in enumerate(prompts, 1):
        out.append(f"### {i}. `{p['id']}`  — _{p['domain']}_\n")
        out.append(f"**Вопрос:**\n\n{p['prompt']}\n")
        ans = ANSWERS.get(p["id"])
        if ans:
            out.append(f"**Эталон / что считается верным:** {ans}\n")
        out.append("")
    return "\n".join(out)


def main() -> None:
    header = (
        "# Benchmark questions — council model selection (2026-06)\n\n"
        "Вопросы, на которых гонялся бенчмарк выбора совета "
        "(`scripts/council_model_benchmark.py`). Два набора по 12 задач: **v1** (общие) и "
        "**v2** (сложнее, менее «зазубренные», с проверяемыми ответами). Запуск: "
        "`python scripts/council_model_benchmark.py --promptset v1|v2`.\n\n"
        "Домены: reasoning, code, math, factuality, writing, instruction_following, "
        "multilingual, abstention.\n\n"
        + REVISIONS
        + "---\n\n"
    )
    text = (
        header
        + block("Набор v1 (12 задач)", _bench.PROMPTS)
        + "\n---\n\n"
        + block("Набор v2 (12 задач, сложнее)", _bench.PROMPTS_V2)
    )
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}  (v1={len(_bench.PROMPTS)}, v2={len(_bench.PROMPTS_V2)}, "
          f"answers={len(ANSWERS)})")


if __name__ == "__main__":
    main()
