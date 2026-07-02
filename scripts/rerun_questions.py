#!/usr/bin/env python3
"""Re-run specific benchmark prompts across all candidate models and show the
conclusion of each answer. Used to check whether the models themselves make a
given error (e.g. the whale-heartbeat order-of-magnitude slip).

Usage: python scripts/rerun_questions.py fermi_whale_heartbeats false_premise ...
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.eval.answer_check import check_answer  # noqa: E402
from backend.openrouter import query_model  # noqa: E402
from backend.provider_results import response_failed  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "council_model_benchmark", str(ROOT / "scripts" / "council_model_benchmark.py")
)
assert _spec and _spec.loader
_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bench)

PROMPTS = {p["id"]: p for p in (_bench.PROMPTS + _bench.PROMPTS_V2)}


def conclusion(text: str, n: int = 360) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[-n:]


async def run_one(model: str, prompt: str) -> tuple[str, str]:
    resp = await query_model(
        model, [{"role": "user", "content": prompt}],
        timeout=240.0, temperature=0.3, max_tokens=4000,
    )
    if response_failed(resp) or not (resp or {}).get("content"):
        return model, "<<FAILED/EMPTY>>"
    return model, resp["content"]


async def main_async(ids: list[str]) -> int:
    for qid in ids:
        item = PROMPTS.get(qid)
        if not item:
            print(f"!! unknown id {qid}"); continue
        print("\n" + "=" * 96)
        print(f"QUESTION: {qid}  ({item['domain']})")
        print(item["prompt"][:300])
        print("=" * 96)
        results = await asyncio.gather(*[run_one(m, item["prompt"]) for m in _bench.CANDIDATES])
        n_correct = n_checked = 0
        for model, content in results:
            chk = check_answer(qid, content)
            verdict = ""
            if chk.checkable:
                n_checked += 1
                if chk.correct:
                    n_correct += 1
                mark = "PASS" if chk.correct else "FAIL"
                verdict = f"  [objective: {mark} — {chk.detail}]"
            print(f"\n--- {model} ---{verdict}")
            print("…" + conclusion(content))
        if n_checked:
            print(f"\n>> objective accuracy for {qid}: {n_correct}/{n_checked}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", nargs="+")
    args = ap.parse_args()
    return asyncio.run(main_async(args.ids))


if __name__ == "__main__":
    raise SystemExit(main())
