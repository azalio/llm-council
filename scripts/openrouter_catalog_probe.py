#!/usr/bin/env python3
"""Probe the live OpenRouter model catalog to see which flagship models exist.

Read-only: lists current OpenRouter model IDs (optionally filtered) plus pricing
so we can pick real council candidates instead of trusting stale config names.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import OPENROUTER_API_KEY  # noqa: E402

CATALOG_URL = "https://openrouter.ai/api/v1/models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--filter",
        default="",
        help="Comma-separated substrings to match against model ids (case-insensitive).",
    )
    parser.add_argument("--json", action="store_true", help="Dump raw matched entries as JSON.")
    args = parser.parse_args()

    # The catalog endpoint is public; auth is optional.
    headers = {}
    if OPENROUTER_API_KEY:
        headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
    resp = httpx.get(CATALOG_URL, headers=headers, timeout=60.0)
    resp.raise_for_status()
    data = resp.json().get("data", [])

    needles = [s.strip().lower() for s in args.filter.split(",") if s.strip()]

    rows = []
    for m in data:
        mid = m.get("id", "")
        low = mid.lower()
        if needles and not any(n in low for n in needles):
            continue
        pricing = m.get("pricing", {}) or {}
        rows.append({
            "id": mid,
            "name": m.get("name", ""),
            "context": m.get("context_length"),
            "prompt_price": pricing.get("prompt"),
            "completion_price": pricing.get("completion"),
            "created": m.get("created"),
        })

    rows.sort(key=lambda r: r["id"])

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"Total models in catalog: {len(data)}  |  matched: {len(rows)}\n")
        for r in rows:
            print(f"{r['id']:<48} ctx={r['context']:<8} "
                  f"in={r['prompt_price']} out={r['completion_price']}  {r['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
