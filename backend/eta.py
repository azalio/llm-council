"""Durable expected-wait-time (ETA) estimates for council runs.

Reads from the persisted SQLite `runs` table (survives process restarts), NOT
from the in-memory process-local collector in `backend.metrics`. When sample
counts are insufficient we return `null` plus an advisory fallback rather than
fabricating a measurement.
"""

from __future__ import annotations

from typing import Any, Optional

from . import config as _config
from . import storage as _storage

# metrics._percentile is a private helper; import it explicitly here so the ETA
# read path reuses the exact same rounding/single-sample handling as the
# in-memory collector instead of re-implementing percentile math.
from .metrics import _percentile

_FALLBACK_BY_MODE = {
    "quick": _config.COUNCIL_ETA_QUICK_FALLBACK_SECONDS,
    "standard": _config.COUNCIL_ETA_STANDARD_FALLBACK_SECONDS,
    "deep": _config.COUNCIL_ETA_DEEP_FALLBACK_SECONDS,
}

_STAGE_NAMES = ("stage1", "stage2", "stage3", "stage2a", "stage2b")


def _fallback_for_mode(mode: str) -> Optional[float]:
    return _FALLBACK_BY_MODE.get(mode)


def estimate_council_wait(mode_selection: dict[str, Any]) -> dict[str, Any]:
    """
    Given a resolved mode_selection dict (from resolve_deliberation_mode), return
    an ETA estimate from durable runs-table statistics.

    Returns a dict with:
      deliberation_mode, expected_wait_seconds (float|None), per_stage_estimates
      (dict|None), confidence ("high"|"low"|"insufficient"), basis
      ("measured_p50"|"insufficient_data"|"disabled"), sample_count, min_samples,
      percentile, window, fallback_seconds (float|None, advisory), note (str|None).
    """
    mode = mode_selection.get("selected_mode")
    percentile = _config.COUNCIL_ETA_PERCENTILE
    window = _config.COUNCIL_ETA_SAMPLE_WINDOW
    min_samples = _config.COUNCIL_ETA_MIN_SAMPLES

    base = {
        "deliberation_mode": mode,
        "expected_wait_seconds": None,
        "per_stage_estimates": None,
        "confidence": "insufficient",
        "basis": "disabled",
        "sample_count": 0,
        "min_samples": min_samples,
        "percentile": percentile,
        "window": window,
        "fallback_seconds": _fallback_for_mode(mode) if mode else None,
        "note": None,
    }

    if not _config.COUNCIL_ETA_ENABLED:
        base["note"] = "ETA is disabled (COUNCIL_ETA_ENABLED=false)."
        return base

    if mode not in _FALLBACK_BY_MODE:
        base["basis"] = "insufficient_data"
        base["note"] = f"Unknown deliberation mode: {mode!r}."
        return base

    rows = _storage.fetch_recent_run_durations(
        mode,
        limit=window,
        completed_only=not _config.COUNCIL_ETA_INCLUDE_FAILED,
    )
    sample_count = len(rows)
    base["sample_count"] = sample_count

    if sample_count < min_samples:
        base["basis"] = "insufficient_data"
        needed = min_samples - sample_count
        base["note"] = (
            f"Need {needed} more {mode} run(s) for a measured estimate "
            f"(have {sample_count}, need {min_samples})."
        )
        return base

    durations_ms = [float(r["duration_ms"]) for r in rows if r.get("duration_ms") is not None]
    if not durations_ms:
        base["basis"] = "insufficient_data"
        base["note"] = f"No duration_ms values available for {mode} runs."
        return base

    p = _percentile(durations_ms, percentile)
    base["expected_wait_seconds"] = round(p / 1000.0, 1)
    base["basis"] = "measured_p50" if percentile == 0.5 else f"measured_p{int(percentile * 100)}"
    base["confidence"] = "high" if sample_count >= window else "low"

    per_stage: dict[str, float | None] = {}
    for stage_name in _STAGE_NAMES:
        column = f"{stage_name}_ms"
        values = [
            float(r[column])
            for r in rows
            if r.get(column) is not None
        ]
        if len(values) >= min_samples:
            per_stage[stage_name] = round(_percentile(values, percentile) / 1000.0, 1)
        else:
            per_stage[stage_name] = None
    base["per_stage_estimates"] = per_stage

    return base
