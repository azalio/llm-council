#!/usr/bin/env python3
"""Replay stored first-turn questions against the local answer-cache policy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend import storage  # noqa: E402
from backend import answer_cache  # noqa: E402


def _public_match(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_type": match["match_type"],
        "similarity": round(match["similarity"], 4),
        "token_similarity": round(match["token_similarity"], 4),
        "semantic_similarity": round(match["semantic_similarity"], 4),
        "requires_validation": match["requires_validation"],
    }


def replay_answer_cache_candidates(
    *,
    limit: int = 200,
    sample_size: int = 10,
) -> dict[str, Any]:
    """Replay completed first-turn answers in chronological order without model calls."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")

    candidates = list(reversed(storage.find_completed_answer_candidates(limit=limit)))
    usable_candidates = [
        candidate
        for candidate in candidates
        if answer_cache.is_answer_cache_source(candidate)
        and answer_cache.is_substantive_cache_question(candidate.get("question", ""))
    ]

    prior_sources: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    validation_candidates: list[dict[str, Any]] = []
    misses = 0
    served_match_types: Counter[str] = Counter()
    validation_match_types: Counter[str] = Counter()

    for candidate in usable_candidates:
        question = candidate.get("question", "")
        best_match: dict[str, Any] | None = None
        best_source: dict[str, Any] | None = None

        # Production scans storage newest-first and keeps the first equal-priority
        # candidate. Reversing prior_sources mirrors that tie-break in replay.
        for source in reversed(prior_sources):
            match = answer_cache.classify_answer_cache_match(
                question,
                source.get("question", ""),
            )
            if match is None:
                continue
            if best_match is None or match["priority"] > best_match["priority"]:
                best_match = match
                best_source = source

        if best_match and best_source:
            replay_match = {
                "question": question,
                "source_question": best_source.get("question", ""),
                "source_conversation_id": best_source.get("conversation_id"),
                **_public_match(best_match),
            }
            if best_match["requires_validation"]:
                validation_match_types[best_match["match_type"]] += 1
                validation_candidates.append(replay_match)
            else:
                served_match_types[best_match["match_type"]] += 1
                hits.append(replay_match)
        elif prior_sources:
            misses += 1

        prior_sources.append(candidate)

    replayed = len(hits) + len(validation_candidates) + misses
    return {
        "policy": {
            "limit": limit,
            "token_hit_threshold": answer_cache.ANSWER_CACHE_SIMILARITY_THRESHOLD,
            "semantic_hit_threshold": answer_cache.ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD,
            "semantic_validation_threshold": answer_cache.ANSWER_CACHE_VALIDATION_THRESHOLD,
            "validation_note": (
                "validated_semantic samples are candidates that would require "
                "chairman validation in production; this replay makes no model calls."
            ),
        },
        "totals": {
            "inspected_first_turn_answers": len(candidates),
            "usable_sources": len(usable_candidates),
            "replayed_questions": replayed,
            "hits": len(hits),
            "validation_candidates": len(validation_candidates),
            "misses": misses,
        },
        "rates": {
            "hit_rate": round(len(hits) / replayed, 4) if replayed else 0.0,
            "validation_candidate_rate": (
                round(len(validation_candidates) / replayed, 4) if replayed else 0.0
            ),
        },
        "served_match_types": dict(sorted(served_match_types.items())),
        "validation_match_types": dict(sorted(validation_match_types.items())),
        "manual_review_samples": hits[:sample_size],
        "validation_review_samples": validation_candidates[:sample_size],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay stored first-turn answers against the answer-cache policy.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum stored answers to inspect",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Manual-review hit samples to include",
    )
    args = parser.parse_args()

    try:
        report = replay_answer_cache_candidates(limit=args.limit, sample_size=args.samples)
    except ValueError as exc:
        parser.error(str(exc))

    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
