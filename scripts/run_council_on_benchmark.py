#!/usr/bin/env python3
"""Run the configured council on the 12 benchmark prompts and save the syntheses.

Lets us eyeball the council's actual product output (chairman answer per question)
on the same prompts used to pick the line-up — and judge whether the prompts
themselves are any good. Standard mode = full fan-out (generate -> rank -> synthesize).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import CHAIRMAN_MODEL, COUNCIL_MODELS  # noqa: E402
from backend.council import calculate_aggregate_rankings, run_full_council  # noqa: E402

# Load PROMPTS from the sibling benchmark script (scripts/ is not a package).
_spec = importlib.util.spec_from_file_location(
    "council_model_benchmark", str(Path(__file__).with_name("council_model_benchmark.py"))
)
assert _spec is not None and _spec.loader is not None, "cannot load benchmark prompts module"
_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bench)
PROMPTS = _bench.PROMPTS


async def run_one(item: dict[str, str]) -> dict[str, Any]:
    stage1, stage2, stage3, meta = await run_full_council(item["prompt"], mode="standard")
    ok_models = [r["model"] for r in stage1 if r.get("response")]
    fail_models = [r["model"] for r in stage1 if not r.get("response")]
    label_to_model = meta.get("label_to_model") or {}
    aggregate = meta.get("aggregate_rankings")
    if aggregate is None and label_to_model:
        aggregate = calculate_aggregate_rankings(stage2, label_to_model)
    top_member = aggregate[0]["model"] if aggregate else None
    confidence = meta.get("council_confidence") or {}
    return {
        "id": item["id"],
        "domain": item["domain"],
        "question": item["prompt"],
        "answer": stage3.get("response") or "",
        "chairman": stage3.get("model"),
        "participants": ok_models,
        "failed": fail_models,
        "peer_top_member": top_member,
        "low_confidence": confidence.get("low_confidence"),
        "top1_stability": confidence.get("top1_stability"),
    }


def to_markdown(results: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Council answers on the benchmark questions",
        "",
        f"Generated: {generated_at}",
        f"Chairman: `{CHAIRMAN_MODEL}`",
        f"Council: {', '.join(f'`{m}`' for m in COUNCIL_MODELS)}",
        "Mode: standard (generate → rank → synthesize)",
        "",
        "---",
        "",
    ]
    for i, r in enumerate(results, start=1):
        lines.append(f"## {i}. {r['id']}  _({r['domain']})_")
        lines.append("")
        lines.append(f"**Question:** {r['question']}")
        lines.append("")
        conf = "LOW (council split)" if r["low_confidence"] else "ok"
        lines.append(
            f"**Participants:** {', '.join(r['participants'])}"
            + (f"  •  **failed:** {', '.join(r['failed'])}" if r["failed"] else "")
        )
        lines.append(
            f"**Peer top member:** `{r['peer_top_member']}`  •  "
            f"**Confidence:** {conf} (top1_stability={r['top1_stability']})"
        )
        lines.append("")
        lines.append("**Chairman synthesis:**")
        lines.append("")
        lines.append(r["answer"])
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


async def main_async(limit: int | None, md_path: Path, json_path: Path) -> int:
    prompts = PROMPTS[:limit] if limit else PROMPTS
    results = []
    for idx, item in enumerate(prompts, start=1):
        print(f"[{idx}/{len(prompts)}] {item['id']} ({item['domain']}) — running council...",
              flush=True)
        r = await run_one(item)
        flag = "LOW-CONF" if r["low_confidence"] else "ok"
        print(f"    done: {len(r['participants'])} answered, chairman={r['chairman']}, "
              f"conf={flag}, peer_top={r['peer_top_member']}", flush=True)
        results.append(r)

    generated_at = datetime.now(timezone.utc).isoformat()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps({"generated_at": generated_at, "results": results},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(to_markdown(results, generated_at), encoding="utf-8")
    print(f"\nMarkdown: {md_path}\nJSON: {json_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--md", default="output/council-on-benchmark.md")
    parser.add_argument("--json", default="output/council-on-benchmark.json")
    args = parser.parse_args()
    return asyncio.run(main_async(args.limit, Path(args.md), Path(args.json)))


if __name__ == "__main__":
    raise SystemExit(main())
