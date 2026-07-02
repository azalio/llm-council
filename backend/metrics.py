"""Rolling in-memory KPI collection for council runs."""

from __future__ import annotations

import math
import os
from collections import deque
from threading import Lock
from typing import Any

from .observability import round_duration_ms
from .usage import sum_usage

DEFAULT_WINDOW_SIZE = int(os.getenv("COUNCIL_METRICS_WINDOW_SIZE", "200"))


def build_council_run_debug(
    *,
    request_id: str,
    thorough: bool,
    started_at: float,
    stage1_debug: dict[str, Any] | None = None,
    stage2_debug: dict[str, Any] | None = None,
    stage3_debug: dict[str, Any] | None = None,
    stage2a_debug: dict[str, Any] | None = None,
    stage2b_debug: dict[str, Any] | None = None,
    quick_debug: dict[str, Any] | None = None,
    deliberation_mode: str | None = None,
    mode_selection: dict[str, Any] | None = None,
    confidence_escalation: dict[str, Any] | None = None,
    agent_routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical council-run debug payload."""
    stages = {}
    if stage1_debug is not None:
        stages["stage1"] = stage1_debug
    if stage2_debug is not None:
        stages["stage2"] = stage2_debug
    if stage3_debug is not None:
        stages["stage3"] = stage3_debug
    if stage2a_debug is not None:
        stages["stage2a"] = stage2a_debug
    if stage2b_debug is not None:
        stages["stage2b"] = stage2b_debug
    if quick_debug is not None:
        stages["quick_answer"] = quick_debug

    council_debug = stage1_debug or quick_debug or {}

    debug = {
        "request_id": request_id,
        "thorough": thorough,
        "duration_ms": round_duration_ms(started_at),
        "successful_council_models": council_debug.get("successful_models", 0),
        "failed_council_models": council_debug.get("failed_models_count", 0),
        "stages": stages,
    }
    run_usage = sum_usage(stage_debug.get("usage") for stage_debug in stages.values())
    if run_usage is not None:
        debug["usage"] = run_usage
    if deliberation_mode is not None:
        debug["deliberation_mode"] = deliberation_mode
    if mode_selection is not None:
        debug["mode_selection"] = mode_selection
    if confidence_escalation is not None:
        debug["confidence_escalation"] = confidence_escalation
    if agent_routing is not None:
        debug["agent_routing"] = agent_routing

    return debug


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)

    sorted_values = sorted(values)
    rank = (len(sorted_values) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(sorted_values[lower], 2)

    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]
    return round(lower_value + (upper_value - lower_value) * (rank - lower), 2)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _classify_run(debug: dict[str, Any]) -> dict[str, bool]:
    stages = debug.get("stages", {})
    stage1 = stages.get("stage1", {})
    stage3 = stages.get("stage3", {})
    quick_answer = stages.get("quick_answer", {})

    stage1_requested = int(stage1.get("requested_models", 0) or 0)
    stage1_successful = int(stage1.get("successful_models", 0) or 0)
    stage1_degraded = stage1_requested > 0 and 0 < stage1_successful < stage1_requested

    completed = (
        int(stage3.get("successful_models", 0) or 0) > 0
        or int(quick_answer.get("successful_models", 0) or 0) > 0
    )
    degraded = completed and any(
        int(stage.get("failed_models_count", 0) or 0) > 0
        for stage in stages.values()
    )

    return {
        "completed": completed,
        "clean_success": completed and not degraded,
        "degraded": degraded,
        "failed": not completed,
        "stage1_degraded": stage1_degraded,
    }


class CouncilMetricsCollector:
    """Collect rolling council KPIs inside a single process."""

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE) -> None:
        self.window_size = window_size
        self._lock = Lock()
        self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._total_runs = 0
        self._successful_runs = 0
        self._clean_successful_runs = 0
        self._degraded_runs = 0
        self._failed_runs = 0
        self._stage1_degraded_runs = 0
        self._stage_durations: dict[str, deque[float]] = {}
        self._stage_failed_models: dict[str, deque[int]] = {}
        self._answer_cache_lookups = 0
        self._answer_cache_hits = 0
        self._answer_cache_misses = 0
        self._answer_cache_bypasses = 0
        self._answer_cache_validation_attempts = 0
        self._answer_cache_validation_approved = 0
        self._answer_cache_validation_rejected = 0
        self._answer_cache_match_types: dict[str, int] = {}
        self._answer_cache_lookup_latencies: deque[float] = deque(maxlen=self.window_size)
        self._answer_cache_hit_latencies: deque[float] = deque(maxlen=self.window_size)
        self._answer_cache_similarities: dict[str, deque[float]] = {
            "similarity": deque(maxlen=self.window_size),
            "token_similarity": deque(maxlen=self.window_size),
            "semantic_similarity": deque(maxlen=self.window_size),
        }
        self._routing_eligible_runs = 0
        self._routing_applied_runs = 0
        self._routing_expanded_runs = 0
        self._routing_sparse_completed_runs = 0
        self._routing_saved_initial_model_calls = 0
        self._routing_initial_model_counts: deque[int] = deque(maxlen=self.window_size)
        self._routing_final_model_counts: deque[int] = deque(maxlen=self.window_size)
        self._routing_expansion_reasons: dict[str, int] = {}
        self._token_runs_with_usage = 0
        self._token_prompt_total = 0
        self._token_completion_total = 0
        self._token_total_total = 0

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()

    def record_run(self, debug: dict[str, Any]) -> dict[str, Any]:
        classification = _classify_run(debug)

        with self._lock:
            self._total_runs += 1
            if classification["completed"]:
                self._successful_runs += 1
            if classification["clean_success"]:
                self._clean_successful_runs += 1
            if classification["degraded"]:
                self._degraded_runs += 1
            if classification["failed"]:
                self._failed_runs += 1
            if classification["stage1_degraded"]:
                self._stage1_degraded_runs += 1

            routing = debug.get("agent_routing") or {}
            if routing.get("eligible"):
                self._routing_eligible_runs += 1
            if routing.get("applied"):
                self._routing_applied_runs += 1
                self._routing_initial_model_counts.append(int(
                    routing.get("initial_model_count", 0) or 0
                ))
                self._routing_final_model_counts.append(int(
                    routing.get("final_model_count", 0) or 0
                ))
                self._routing_saved_initial_model_calls += int(
                    routing.get("saved_initial_model_calls", 0) or 0
                )
                if routing.get("expanded"):
                    self._routing_expanded_runs += 1
                    reason = str(routing.get("expansion_reason") or "unknown")
                    self._routing_expansion_reasons[reason] = (
                        self._routing_expansion_reasons.get(reason, 0) + 1
                    )
                else:
                    self._routing_sparse_completed_runs += 1

            for stage_name, stage_debug in debug.get("stages", {}).items():
                durations = self._stage_durations.setdefault(
                    stage_name,
                    deque(maxlen=self.window_size),
                )
                duration_ms = stage_debug.get("duration_ms")
                if duration_ms is not None:
                    durations.append(float(duration_ms))
                failed_models = self._stage_failed_models.setdefault(
                    stage_name,
                    deque(maxlen=self.window_size),
                )
                failed_models.append(int(
                    stage_debug.get("failed_models_count", 0) or 0
                ))

            usage = debug.get("usage")
            if usage:
                self._token_runs_with_usage += 1
                self._token_prompt_total += int(usage.get("prompt_tokens", 0) or 0)
                self._token_completion_total += int(usage.get("completion_tokens", 0) or 0)
                self._token_total_total += int(usage.get("total_tokens", 0) or 0)

            return self._snapshot_unlocked()

    def record_answer_cache_lookup(
        self,
        *,
        hit: bool,
        latency_ms: float,
        match_type: str | None = None,
        similarity: float | None = None,
        token_similarity: float | None = None,
        semantic_similarity: float | None = None,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._answer_cache_lookups += 1
            self._answer_cache_lookup_latencies.append(float(latency_ms))
            if hit:
                self._answer_cache_hits += 1
                self._answer_cache_hit_latencies.append(float(latency_ms))
                if match_type:
                    self._answer_cache_match_types[match_type] = (
                        self._answer_cache_match_types.get(match_type, 0) + 1
                    )
            else:
                self._answer_cache_misses += 1

            samples = {
                "similarity": similarity,
                "token_similarity": token_similarity,
                "semantic_similarity": semantic_similarity,
            }
            for name, value in samples.items():
                if value is not None:
                    self._answer_cache_similarities[name].append(float(value))

            if validation is not None:
                self._answer_cache_validation_attempts += 1
                if validation.get("approved"):
                    self._answer_cache_validation_approved += 1
                else:
                    self._answer_cache_validation_rejected += 1

            return self._snapshot_unlocked()

    def record_answer_cache_bypass(self) -> dict[str, Any]:
        with self._lock:
            self._answer_cache_bypasses += 1
            return self._snapshot_unlocked()

    def _latency_summary(self, values: deque[float]) -> dict[str, Any]:
        samples = list(values)
        return {
            "count": len(samples),
            "p50": _percentile(samples, 0.50),
            "p95": _percentile(samples, 0.95),
            "max": round(max(samples), 2) if samples else 0.0,
        }

    def _similarity_summary(self, values: deque[float]) -> dict[str, Any]:
        samples = list(values)
        return {
            "count": len(samples),
            "average": _average(samples),
            "p50": _percentile(samples, 0.50),
            "max": round(max(samples), 4) if samples else 0.0,
        }

    def _snapshot_unlocked(self) -> dict[str, Any]:
        stages: dict[str, Any] = {}
        for stage_name in sorted(self._stage_durations):
            durations = list(self._stage_durations[stage_name])
            stages[stage_name] = {
                "latency_ms": {
                    "count": len(durations),
                    "p50": _percentile(durations, 0.50),
                    "p95": _percentile(durations, 0.95),
                    "max": round(max(durations), 2) if durations else 0.0,
                },
                "failed_models_in_window": sum(
                    self._stage_failed_models.get(stage_name, ())
                ),
            }

        answer_cache = {
            "totals": {
                "lookups": self._answer_cache_lookups,
                "hits": self._answer_cache_hits,
                "misses": self._answer_cache_misses,
                "bypasses": self._answer_cache_bypasses,
                "validation_attempts": self._answer_cache_validation_attempts,
                "validation_approved": self._answer_cache_validation_approved,
                "validation_rejected": self._answer_cache_validation_rejected,
            },
            "rates": {
                "hit_rate": _ratio(self._answer_cache_hits, self._answer_cache_lookups),
                "miss_rate": _ratio(self._answer_cache_misses, self._answer_cache_lookups),
                "validation_approval_rate": _ratio(
                    self._answer_cache_validation_approved,
                    self._answer_cache_validation_attempts,
                ),
            },
            "match_types": dict(sorted(self._answer_cache_match_types.items())),
            "latency_ms": {
                "lookup": self._latency_summary(self._answer_cache_lookup_latencies),
                "hit": self._latency_summary(self._answer_cache_hit_latencies),
            },
            "similarity": {
                name: self._similarity_summary(values)
                for name, values in self._answer_cache_similarities.items()
            },
        }

        agent_routing = {
            "totals": {
                "eligible_runs": self._routing_eligible_runs,
                "applied_runs": self._routing_applied_runs,
                "expanded_runs": self._routing_expanded_runs,
                "sparse_completed_runs": self._routing_sparse_completed_runs,
                "saved_initial_model_calls": self._routing_saved_initial_model_calls,
            },
            "rates": {
                "applied_rate": _ratio(
                    self._routing_applied_runs,
                    self._routing_eligible_runs,
                ),
                "expansion_rate": _ratio(
                    self._routing_expanded_runs,
                    self._routing_applied_runs,
                ),
                "sparse_completion_rate": _ratio(
                    self._routing_sparse_completed_runs,
                    self._routing_applied_runs,
                ),
            },
            "model_counts": {
                "initial_average": _average(list(self._routing_initial_model_counts)),
                "final_average": _average(list(self._routing_final_model_counts)),
            },
            "expansion_reasons": dict(sorted(self._routing_expansion_reasons.items())),
        }

        tokens = {
            "totals": {
                "runs_with_usage": self._token_runs_with_usage,
                "prompt_tokens": self._token_prompt_total,
                "completion_tokens": self._token_completion_total,
                "total_tokens": self._token_total_total,
            },
            "average_total_tokens_per_run": _ratio(
                self._token_total_total,
                self._token_runs_with_usage,
            ),
        }

        return {
            "process_local": True,
            "window_size": self.window_size,
            "totals": {
                "total_runs": self._total_runs,
                "successful_runs": self._successful_runs,
                "clean_successful_runs": self._clean_successful_runs,
                "degraded_runs": self._degraded_runs,
                "failed_runs": self._failed_runs,
            },
            "rates": {
                "council_success_rate": _ratio(self._successful_runs, self._total_runs),
                "clean_success_rate": _ratio(
                    self._clean_successful_runs,
                    self._total_runs,
                ),
                "degraded_run_rate": _ratio(self._degraded_runs, self._total_runs),
                "stage1_degradation_rate": _ratio(
                    self._stage1_degraded_runs,
                    self._total_runs,
                ),
            },
            "stages": stages,
            "answer_cache": answer_cache,
            "agent_routing": agent_routing,
            "tokens": tokens,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()


council_metrics = CouncilMetricsCollector()


def record_council_metrics(debug: dict[str, Any]) -> dict[str, Any]:
    return council_metrics.record_run(debug)


def record_answer_cache_lookup(
    *,
    hit: bool,
    latency_ms: float,
    match_type: str | None = None,
    similarity: float | None = None,
    token_similarity: float | None = None,
    semantic_similarity: float | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return council_metrics.record_answer_cache_lookup(
        hit=hit,
        latency_ms=latency_ms,
        match_type=match_type,
        similarity=similarity,
        token_similarity=token_similarity,
        semantic_similarity=semantic_similarity,
        validation=validation,
    )


def record_answer_cache_bypass() -> dict[str, Any]:
    return council_metrics.record_answer_cache_bypass()


def get_council_metrics_snapshot() -> dict[str, Any]:
    return council_metrics.snapshot()


def _stage_ms(stages: dict[str, Any], name: str) -> int | None:
    """Extract a stage's duration_ms as an int, or None if the stage did not run."""
    stage_debug = stages.get(name) if stages else None
    if not isinstance(stage_debug, dict):
        return None
    value = stage_debug.get("duration_ms")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _run_timing_row(
    debug: dict[str, Any],
    *,
    conversation_id: str | None,
    completed: bool,
    started_at_epoch: float | None,
) -> dict[str, Any]:
    """Build a runs-table row dict from a council-run debug payload."""
    stages = debug.get("stages", {}) or {}
    return {
        "request_id": debug.get("request_id"),
        "conversation_id": conversation_id,
        "deliberation_mode": debug.get("deliberation_mode"),
        "duration_ms": int(debug.get("duration_ms") or 0),
        "stage1_ms": _stage_ms(stages, "stage1"),
        "stage2_ms": _stage_ms(stages, "stage2"),
        "stage3_ms": _stage_ms(stages, "stage3"),
        "stage2a_ms": _stage_ms(stages, "stage2a"),
        "stage2b_ms": _stage_ms(stages, "stage2b"),
        "started_at_epoch": started_at_epoch,
        "completed": completed,
    }


def record_run_timing(
    debug: dict[str, Any],
    *,
    conversation_id: str | None = None,
    completed: bool = True,
    started_at_epoch: float | None = None,
) -> None:
    """Persist one run's timing to the durable `runs` table. Best-effort, never raises.

    Placed next to `record_council_metrics` on each terminal run path. The
    in-memory collector is NOT touched — this is the durable counterpart that
    survives process restarts. conversation_id/completed/started_at_epoch are
    passed as kwargs (never stuffed into `debug`) so they cannot leak into the
    persisted `messages.metadata` allowlist.
    """
    from . import config as _config
    if not getattr(_config, "COUNCIL_ETA_ENABLED", True):
        return
    try:
        # Lazy import avoids a config/storage import cycle at module load.
        from . import storage as _storage
        row = _run_timing_row(
            debug,
            conversation_id=conversation_id,
            completed=completed,
            started_at_epoch=started_at_epoch,
        )
        _storage.record_run_timing(row)
    except Exception:
        return  # ETA is observability; never break a council run over a stats write
