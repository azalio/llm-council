"""Token usage normalization shared by the OpenRouter client and its vendor adapters.

Provider/vendor response shapes disagree on where usage lives and what the fields
are called (OpenAI-style `usage.prompt_tokens`, Anthropic's `usage.input_tokens`,
Google's `usageMetadata.promptTokenCount`), so every caller normalizes through here
to a single `{prompt_tokens, completion_tokens, total_tokens}` shape.
"""

from typing import Any, Dict, Iterable, Optional


def _coerce_int(value: Any) -> Optional[int]:
    """Best-effort int coercion. A malformed usage field must not fail an
    otherwise-successful model call, so this degrades to `None` instead of raising.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_openai_usage(data: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Normalize an OpenAI-compatible `usage` block (OpenRouter, OpenAI, xAI, DeepSeek, Alibaba)."""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_tokens = _coerce_int(usage.get("prompt_tokens"))
    completion_tokens = _coerce_int(usage.get("completion_tokens"))
    total_tokens = _coerce_int(usage.get("total_tokens"))
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens if total_tokens is not None else prompt_tokens + completion_tokens,
    }


def normalize_anthropic_usage(data: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Normalize an Anthropic Messages API `usage` block (`input_tokens`/`output_tokens`).

    Note: Anthropic's `input_tokens` already includes cache reads/writes: this
    intentionally does not add `cache_creation_input_tokens`/`cache_read_input_tokens`
    on top, since those are billing-rate breakdowns of `input_tokens`, not additive
    components (summing them would double-count prompt tokens).
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_tokens = _coerce_int(usage.get("input_tokens"))
    completion_tokens = _coerce_int(usage.get("output_tokens"))
    if prompt_tokens is None and completion_tokens is None:
        return None
    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def normalize_google_usage(data: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Normalize a Gemini `usageMetadata` block (`promptTokenCount`/`candidatesTokenCount`)."""
    usage = data.get("usageMetadata")
    if not isinstance(usage, dict):
        return None
    prompt_tokens = _coerce_int(usage.get("promptTokenCount"))
    completion_tokens = _coerce_int(usage.get("candidatesTokenCount"))
    total_tokens = _coerce_int(usage.get("totalTokenCount"))
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens if total_tokens is not None else prompt_tokens + completion_tokens,
    }


def sum_usage(usages: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, int]]:
    """Sum normalized usage dicts, skipping `None` entries.

    Returns `None` (not a zeroed dict) when none of the inputs carried usage data,
    so callers can distinguish "no usage available" from "zero tokens used". Note
    this sums only the calls that reported usage — if some models in a stage report
    usage and others don't, the total reflects "reported tokens", not necessarily
    every requested model's tokens.
    """
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    seen = False
    for usage in usages:
        if not usage:
            continue
        seen = True
        total["prompt_tokens"] += _coerce_int(usage.get("prompt_tokens")) or 0
        total["completion_tokens"] += _coerce_int(usage.get("completion_tokens")) or 0
        total["total_tokens"] += _coerce_int(usage.get("total_tokens")) or 0
    return total if seen else None
