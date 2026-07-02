"""Deterministic sparse routing for routine council runs."""

from __future__ import annotations

import re
from typing import Any

from .config import (
    COUNCIL_ADAPTIVE_ROUTING_ENABLED,
    COUNCIL_MODEL_METADATA,
    COUNCIL_MODELS,
)

HIGH_RISK_KEYWORDS = {
    "architecture",
    "audit",
    "compliance",
    "debug",
    "diagnose",
    "legal",
    "medical",
    "migration",
    "production",
    "review",
    "root cause",
    "security",
    "threat",
}


def _query_features(user_query: str) -> dict[str, Any]:
    text = (user_query or "").strip()
    lower = text.lower()
    words = re.findall(r"[A-Za-z0-9_]+", text)
    keyword_hits = [keyword for keyword in sorted(HIGH_RISK_KEYWORDS) if keyword in lower]
    return {
        "word_count": len(words),
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "char_count": len(text),
        "keyword_hits": keyword_hits,
    }


def _is_high_risk(features: dict[str, Any]) -> bool:
    return (
        features["word_count"] >= 120
        or features["line_count"] >= 6
        or bool(features["keyword_hits"])
    )


def _initial_subset_size(full_pool_size: int) -> int:
    if full_pool_size <= 2:
        return full_pool_size
    if full_pool_size == 3:
        return 2
    return 3


def _prioritized_models(models: list[str]) -> list[str]:
    indexed = list(enumerate(models))
    return [
        model
        for _, model in sorted(
            indexed,
            key=lambda item: (
                -int(COUNCIL_MODEL_METADATA.get(item[1], {}).get("routing_priority", 0)),
                item[0],
            ),
        )
    ]


def build_agent_route(
    user_query: str,
    mode_selection: dict[str, Any],
    *,
    full_pool: list[str] | None = None,
    enabled: bool = COUNCIL_ADAPTIVE_ROUTING_ENABLED,
) -> dict[str, Any]:
    """Return the model subset and audit metadata for a council request."""
    models = list(full_pool or COUNCIL_MODELS)
    features = _query_features(user_query)
    base = {
        "enabled": enabled,
        "eligible": False,
        "applied": False,
        "expanded": False,
        "expansion_reason": None,
        "full_pool": models,
        "selected_models": models,
        "skipped_models": [],
        "initial_model_count": len(models),
        "final_model_count": len(models),
        "saved_initial_model_calls": 0,
        "reason": "Adaptive routing did not run.",
        "features": features,
    }

    if not enabled:
        return {**base, "reason": "Adaptive routing is disabled by configuration."}
    if mode_selection.get("requested_mode") != "auto":
        return {**base, "reason": "Only default auto-mode requests are routed."}
    if mode_selection.get("selected_mode") != "standard":
        return {**base, "reason": "Only auto requests selected as standard are routed."}
    if len(models) <= 2:
        return {**base, "reason": "Council pool is already too small to route sparsely."}
    eligible_base = {**base, "eligible": True}
    if _is_high_risk(features):
        return {**eligible_base, "reason": "Question looks high-risk or complex; using full council."}

    subset_size = _initial_subset_size(len(models))
    selected = _prioritized_models(models)[:subset_size]
    skipped = [model for model in models if model not in selected]
    return {
        **eligible_base,
        "applied": bool(skipped),
        "selected_models": selected,
        "skipped_models": skipped,
        "initial_model_count": len(selected),
        "final_model_count": len(selected),
        "saved_initial_model_calls": (len(models) - len(selected)) * 2,
        "reason": "Routine auto-standard request started with a sparse council subset.",
    }


def should_expand_route(
    route: dict[str, Any],
    *,
    stage1_results: list[dict[str, Any]],
    stage1_debug: dict[str, Any] | None = None,
    stage2_results: list[dict[str, Any]] | None = None,
    stage2_debug: dict[str, Any] | None = None,
    council_confidence: dict[str, Any] | None = None,
) -> str | None:
    """Return an expansion reason when a sparse route needs the full council."""
    if not route.get("applied") or route.get("expanded"):
        return None
    if int((stage1_debug or {}).get("failed_models_count", 0) or 0) > 0:
        return "routed_stage1_model_failed"
    if not stage1_results:
        return "all_routed_stage1_models_failed"
    if len(stage1_results) < 2:
        return "fewer_than_two_routed_stage1_responses"
    if stage2_results is None or council_confidence is None:
        return None
    if int((stage2_debug or {}).get("failed_models_count", 0) or 0) > 0:
        return "routed_stage2_model_failed"
    if len(stage2_results) < 2:
        return "fewer_than_two_routed_rankings"
    if not council_confidence.get("available"):
        return "routed_confidence_unavailable"
    if council_confidence.get("low_confidence"):
        return "routed_confidence_low"
    return None


def mark_route_expanded(route: dict[str, Any], reason: str) -> dict[str, Any]:
    """Return route metadata after falling back to the full council pool."""
    full_pool = list(route.get("full_pool") or [])
    return {
        **route,
        "expanded": True,
        "expansion_reason": reason,
        "selected_models": full_pool,
        "skipped_models": [],
        "final_model_count": len(full_pool),
        "saved_initial_model_calls": 0,
    }
