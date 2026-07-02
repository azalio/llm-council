#!/usr/bin/env python3
"""MCP Server for LLM Council - Direct Backend Integration.

This server exposes LLM Council functionality via MCP protocol,
allowing Claude Desktop and other MCP clients to interact with
the council deliberation system.
"""

import asyncio
import json
import logging
import re
import sys
import os
import time as _time
import uuid
from pathlib import Path
from typing import Callable, Optional

# Get project root directory (parent of mcp_server/)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Add project root to path for backend imports (no chdir - use absolute paths)
sys.path.insert(0, str(PROJECT_ROOT))

# Set environment for backend modules to find data directory
os.environ.setdefault("LLM_COUNCIL_ROOT", str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP, Context  # noqa: E402

# Import backend modules at module level with error handling
try:
    from backend.council import (
        build_persisted_message_metadata,
        run_full_council,
        build_conversation_context,
        generate_conversation_title,
        generate_conversation_summary,
    )
    from backend.answer_cache import (
        build_cached_title,
        find_answer_cache_hit_with_validation,
        is_cache_eligible,
    )
    from backend.config import (
        API_PROVIDER,
        CHAIRMAN_MODEL,
        CHAIRMAN_MODEL_FAMILY,
        COUNCIL_MODEL_FAMILIES,
        COUNCIL_MODELS,
        validate_chairman_heterogeneity,
    )
    from backend.metrics import get_council_metrics_snapshot, record_answer_cache_bypass
    from backend.eta import estimate_council_wait
    from backend.council import resolve_deliberation_mode
    from backend.observability import bind_request_id, log_event, reset_request_id
    from backend.storage import (
        list_conversations as storage_list,
        get_conversation as storage_get,
        create_conversation as storage_create,
        add_user_message as storage_add_user,
        add_assistant_message as storage_add_assistant,
        update_conversation_title as storage_update_title,
        update_conversation_summary as storage_update_summary,
    )
except ImportError as e:
    sys.stderr.write(f"Critical Import Error: {e}\nCheck your dependencies and PYTHONPATH.\n")
    sys.exit(1)

# Configure logging to stderr (MCP requirement - stdout is for protocol)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("llm-council")

# Constants
MAX_QUESTION_LENGTH = 100_000
# Timeouts: Using asyncio.wait_for() instead of asyncio.timeout() context manager
# because asyncio.timeout is Python 3.11+ only, and we target Python 3.9+
def _council_timeout_default() -> float:
    """Full-council deliberation budget in seconds.

    Defaults to 60 minutes (deep mode runs critique + revision stages across
    several reasoning models, which routinely exceeds 20 minutes under load).
    Overridable via the COUNCIL_TIMEOUT_SECONDS env var; invalid/non-positive
    values fall back to the default.
    """
    raw = os.getenv("COUNCIL_TIMEOUT_SECONDS")
    if raw is None:
        return 3600.0
    try:
        value = float(raw)
    except ValueError:
        return 3600.0
    return value if value > 0 else 3600.0


COUNCIL_TIMEOUT = _council_timeout_default()  # 60 minutes for full council (reasoning models are slow)
# Interval for transport-level heartbeat. Even when a council stage is
# legitimately slow (Stage 1 with reasoning models can hold the connection
# silent for >60 s), MCP clients need to see *some* activity or they will
# abort the call with a transport timeout. We piggyback on
# Context.report_progress() - no custom protocol - so any conformant client
# will keep the channel alive.
HEARTBEAT_INTERVAL_SECONDS = 25.0

# ---------------------------------------------------------------------------
# Async start/poll task store
# ---------------------------------------------------------------------------
# Keyed by task_id (UUID4 str). Values:
#   status:  "pending" | "running" | "done" | "error"
#   result:  str | None   — populated when done
#   error:   str | None   — populated on error
#   started: float        — _time.monotonic() when task was created
_async_tasks: dict[str, dict] = {}
_ASYNC_TASK_LIMIT = 50  # retain only the most recent N tasks


def _prune_task_store() -> None:
    """Drop oldest entries when the store exceeds _ASYNC_TASK_LIMIT."""
    if len(_async_tasks) <= _ASYNC_TASK_LIMIT:
        return
    sorted_ids = sorted(_async_tasks, key=lambda k: _async_tasks[k].get("started", 0.0))
    for old_id in sorted_ids[: len(_async_tasks) - _ASYNC_TASK_LIMIT]:
        del _async_tasks[old_id]


async def _run_task_background(task_id: str, **kwargs) -> None:
    """Execute council deliberation in background; write outcome to task store."""
    _async_tasks[task_id]["status"] = "running"

    def _record_conversation_id(conversation_id: str) -> None:
        # Recorded as soon as it's known (before deliberation finishes) so a
        # poller can learn it even mid-run, and so it's a structured JSON
        # field rather than something the caller has to parse out of `result`.
        _async_tasks[task_id]["conversation_id"] = conversation_id

    try:
        result = await _execute_council_deliberation(
            **kwargs, ctx=None, on_conversation_resolved=_record_conversation_id
        )
        _async_tasks[task_id]["status"] = "done"
        _async_tasks[task_id]["result"] = result
    except Exception as exc:
        _async_tasks[task_id]["status"] = "error"
        _async_tasks[task_id]["error"] = sanitize_error(exc)
        logger.error(
            f"Async council task {task_id} failed: {sanitize_error(exc)}",
            exc_info=True,
        )


def sanitize_error(error: Exception) -> str:
    """Remove potentially sensitive information from error messages."""
    error_str = str(error)

    # Common patterns that might contain secrets
    patterns = [
        (r'Bearer\s+[A-Za-z0-9_-]+', 'Bearer [REDACTED]'),
        (r'OAuth\s+[A-Za-z0-9_-]+', 'OAuth [REDACTED]'),
        (r'sk-[A-Za-z0-9]+', '[REDACTED]'),
        (r'y[0-9]_[A-Za-z0-9_-]+', '[REDACTED]'),
        (r'(?i)(api[_-]?key|token|password|secret)["\']?\s*[:=]\s*["\']?[^\s,}"\']+', r'\1=[REDACTED]'),
    ]

    for pattern, replacement in patterns:
        error_str = re.sub(pattern, replacement, error_str)

    return error_str


def extract_response(result: dict, default: str = "No response") -> str:
    """Extract response content from result dictionary."""
    return result.get('response') or result.get('content') or default


def validate_question(question: str) -> str:
    """Validate and normalize question input."""
    if not isinstance(question, str):
        raise ValueError("Question must be a string")

    question = question.strip()
    if not question:
        raise ValueError("Question cannot be empty")

    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(
            f"Question too long ({len(question):,} chars). "
            f"Maximum is {MAX_QUESTION_LENGTH:,}."
        )

    return question


def validate_environment():
    """Validate required environment variables on startup."""
    logger.info(f"PROJECT_ROOT: {PROJECT_ROOT}")
    logger.info(f"API_PROVIDER: {API_PROVIDER}")

    # Check for API keys based on provider (log presence only, not values)
    if API_PROVIDER == 'openrouter':
        has_key = bool(os.environ.get('OPENROUTER_API_KEY'))
        logger.info(f"OPENROUTER_API_KEY: {'configured' if has_key else 'NOT SET'}")

    if not COUNCIL_MODELS:
        raise ValueError("No council models configured")
    if not CHAIRMAN_MODEL:
        raise ValueError("No chairman model configured")
    validate_chairman_heterogeneity(
        COUNCIL_MODELS,
        CHAIRMAN_MODEL,
        provider=API_PROVIDER,
    )


def format_recovery_feedback(stage3_result: dict) -> str:
    """Render a machine-readable recovery_feedback block for agent callers.

    Returns an empty string when the stage result carries no structured
    feedback, so normal answers are unaffected.
    """
    recovery = (stage3_result or {}).get("recovery_feedback")
    if not recovery:
        return ""
    payload = json.dumps({"recovery_feedback": recovery}, indent=2)
    return (
        "### Machine-readable recovery\n"
        "An agent can act on this without a human in the loop:\n\n"
        f"```json\n{payload}\n```"
    )


def format_council_output(
    stage1_results: list,
    metadata: dict,
    stage3_result: dict,
) -> str:
    """Format council results as markdown."""
    if metadata.get("clarification"):
        body = (
            "## Clarification Requested\n\n"
            f"{extract_response(stage3_result, 'Clarification needed')}"
        )
        recovery = format_recovery_feedback(stage3_result)
        if recovery:
            body = f"{body}\n\n{recovery}"
        return body

    parts = []

    # Stage 1
    parts.append("## Stage 1: Individual Model Responses\n")
    for result in stage1_results:
        model = result.get('model', 'Unknown')
        content = extract_response(result)
        parts.append(f"### {model}\n{content}\n")

    # Stage 2
    parts.append("\n## Stage 2: Peer Rankings\n")
    aggregate = metadata.get('aggregate_rankings', [])
    if aggregate:
        parts.append("### Aggregate Rankings (by average position):\n")
        for i, rank_info in enumerate(aggregate, 1):
            model = rank_info.get('model', 'Unknown')
            avg = rank_info.get('average_rank', 0)
            votes = rank_info.get('rankings_count', 0)
            parts.append(f"{i}. **{model}** - Avg position: {avg:.2f} ({votes} votes)\n")
    else:
        parts.append("*No rankings available*\n")

    confidence = metadata.get('council_confidence')
    if confidence:
        if not confidence.get('available'):
            label = "Confidence unavailable"
        elif confidence.get('low_confidence'):
            label = "Low confidence"
        else:
            label = "Confidence"
        parts.append(f"\n### {label}\n")
        parts.append(f"{confidence.get('summary', 'No confidence summary available.')}\n")
        if confidence.get('available'):
            parts.append(
                "Top-1 stability: "
                f"{confidence.get('top1_stability')} | "
                f"Rank agreement: {confidence.get('rank_agreement')} | "
                f"Disagreement score: {confidence.get('disagreement_score')}\n"
            )

    confidence_escalation = metadata.get('confidence_escalation')
    if confidence_escalation and confidence_escalation.get('triggered'):
        parts.append("\n### Confidence escalation\n")
        parts.append(f"{confidence_escalation.get('reason', 'Escalated after Stage 2.')}\n")

    agent_routing = metadata.get('agent_routing')
    if agent_routing:
        parts.append("\n### Agent routing\n")
        parts.append(f"{agent_routing.get('reason', 'Routing decision unavailable.')}\n")
        if agent_routing.get('applied'):
            selected = ", ".join(agent_routing.get('selected_models') or [])
            parts.append(
                f"Initial models: {agent_routing.get('initial_model_count')} | "
                f"Final models: {agent_routing.get('final_model_count')} | "
                f"Expanded: {agent_routing.get('expanded')}\n"
            )
            if selected:
                parts.append(f"Selected models: {selected}\n")

    # Stage 2a: Critiques (thorough mode)
    stage2a = metadata.get('stage2a')
    if stage2a:
        parts.append("\n## Stage 2a: Peer Critiques\n")
        for result in stage2a:
            model = result.get('model', 'Unknown')
            critiques = result.get('critiques', '')
            parts.append(f"### {model}\n{critiques}\n")

    # Stage 2b: Revisions (thorough mode)
    stage2b = metadata.get('stage2b')
    if stage2b:
        parts.append("\n## Stage 2b: Revised Responses\n")
        for result in stage2b:
            model = result.get('model', 'Unknown')
            label = result.get('original_label', '')
            revision = result.get('revision', '')
            parts.append(f"### {model} ({label})\n{revision}\n")

    # Stage 3
    parts.append("\n## Stage 3: Chairman's Final Synthesis\n")
    chairman_content = extract_response(stage3_result, "No synthesis available")
    parts.append(f"*Chairman: {CHAIRMAN_MODEL}*\n")

    label_to_model = metadata.get('label_to_model') or {}
    if label_to_model:
        parts.append("\n### Attribution Key\n")
        for label, model in sorted(label_to_model.items()):
            marker = label.replace('Response ', '')
            parts.append(f"- `[{marker}]` = {model}")

    attribution = stage3_result.get('attribution') or {}
    if attribution.get('unattributed_claim_count', 0) > 0:
        parts.append("\n### Attribution Warning\n")
        parts.append(f"{attribution.get('summary', 'Some chairman claims lack attribution.')}\n")
        for claim in attribution.get('unattributed_claims', []):
            parts.append(f"- {claim}")

    parts.append(f"\n{chairman_content}")

    return "\n".join(parts)


def format_brief_attribution_output(metadata: dict, stage3_result: dict) -> str:
    """Render compact attribution context for the default MCP answer path."""
    parts = []

    label_to_model = metadata.get('label_to_model') or {}
    if label_to_model:
        key_items = []
        for label, model in sorted(label_to_model.items()):
            marker = label.replace('Response ', '')
            key_items.append(f"`[{marker}]` = {model}")
        parts.append("Attribution key: " + "; ".join(key_items))

    attribution = stage3_result.get('attribution') or {}
    if attribution.get('unattributed_claim_count', 0) > 0:
        warning_parts = [
            "Attribution warning: "
            + attribution.get('summary', 'Some chairman claims lack attribution.')
        ]
        warning_parts.extend(
            f"- {claim}" for claim in attribution.get('unattributed_claims', [])
        )
        parts.append("\n".join(warning_parts))

    return "\n\n".join(parts)


def format_debug_output(debug: dict) -> str:
    """Render request-scoped debug metadata as markdown."""
    if not debug:
        return ""

    parts = [
        "## Debug\n",
        f"- Request ID: `{debug.get('request_id', 'unknown')}`",
        f"- Provider: `{API_PROVIDER}`",
        f"- Duration: `{debug.get('duration_ms', 0)} ms`",
        (
            "- Council responses: "
            f"`{debug.get('successful_council_models', 0)}` succeeded, "
            f"`{debug.get('failed_council_models', 0)}` failed"
        ),
    ]

    usage = debug.get("usage")
    if usage:
        parts.append(
            "- Tokens: "
            f"`{usage.get('prompt_tokens', 0)}` prompt + "
            f"`{usage.get('completion_tokens', 0)}` completion = "
            f"`{usage.get('total_tokens', 0)}` total"
        )

    for stage_name, stage_debug in debug.get("stages", {}).items():
        parts.append(
            f"- {stage_name}: `{stage_debug.get('successful_models', 0)}`/"
            f"`{stage_debug.get('requested_models', 0)}` succeeded in "
            f"`{stage_debug.get('duration_ms', 0)} ms`"
        )
        stage_usage = stage_debug.get("usage")
        if stage_usage:
            parts.append(f"  Tokens: `{stage_usage.get('total_tokens', 0)}` total")
        failed_models = stage_debug.get("failed_models", [])
        if failed_models:
            failed_summary = ", ".join(
                f"{item.get('model')} ({item.get('failure_type')})"
                for item in failed_models
            )
            parts.append(f"  Failed: {failed_summary}")

    return "\n".join(parts)


async def _execute_council_deliberation(
    question: str,
    full_output: bool = False,
    thorough: bool = False,
    mode: str = "auto",
    conversation_id: str = "",
    include_debug: bool = False,
    clarify_when_unclear: bool = False,
    bypass_cache: bool = False,
    ctx: Optional[Context] = None,
    on_conversation_resolved: Optional[Callable[[str], None]] = None,
) -> str:
    """Internal helper for council deliberation with unified error handling.

    Supports multi-turn conversations: when conversation_id is provided, loads
    history and builds context for Stage 0 reformulation and chairman synthesis.
    When empty, creates a new conversation automatically.

    When ``ctx`` is provided (FastMCP tool invocation), per-stage progress is
    forwarded via ``ctx.report_progress`` and a background heartbeat task
    re-emits the last known progress every ``HEARTBEAT_INTERVAL_SECONDS`` so
    MCP transport-level liveness is preserved even during a single long stage.

    Args:
        question: The question to ask the council
        full_output: If True, return full deliberation chain; if False, return only chairman's answer
        thorough: Deprecated alias for deep mode when mode is auto
        mode: auto, quick, standard, or deep deliberation policy
        conversation_id: Optional conversation ID for multi-turn. Empty = new conversation.
        clarify_when_unclear: If True, first-turn ambiguous questions return one
            clarifying question before the full council runs.
        bypass_cache: If True, force a fresh council run even when a matching
            first-turn answer is cached.
        ctx: Optional FastMCP Context for progress reporting + transport keepalive.
        on_conversation_resolved: Optional callback invoked once with the
            resolved conversation_id (loaded or freshly created), as soon as
            it is known — before deliberation starts. Lets a caller that
            doesn't get to parse the final text (e.g. the async task store)
            still learn the conversation_id as a structured value instead of
            depending on the caller/model to notice the trailing text marker.
            Exceptions from the callback are logged and swallowed, never
            allowed to abort the deliberation.

    Returns:
        Council response with conversation_id appended
    """
    log_prefix = "Council full deliberation" if full_output else "Council deliberation"
    if thorough:
        log_prefix += " (thorough)"

    try:
        question = validate_question(question)
    except ValueError as e:
        return f"Error: {e}"

    request_id, token = bind_request_id()
    logger.info(f"{log_prefix} started")
    log_event(
        logger,
        "mcp_deliberation_start",
        thorough=thorough,
        mode=mode,
        full_output=full_output,
        include_debug=include_debug,
        clarify_when_unclear=clarify_when_unclear,
        has_conversation_id=bool(conversation_id),
    )

    # Build progress plumbing.
    # ``last_progress`` is shared state between the per-stage callback (driven
    # from inside run_full_council) and the heartbeat task (driven from here).
    # The heartbeat just keeps re-emitting whatever the most recent stage said,
    # so transport sees activity within HEARTBEAT_INTERVAL_SECONDS regardless
    # of how slow any individual stage is.
    last_progress = {"value": 0.0, "total": 1.0, "message": "Starting council deliberation"}
    progress_callback = None
    heartbeat_task = None

    if ctx is not None:
        async def progress_callback(progress: float, total: float, message: str) -> None:
            last_progress["value"] = progress
            last_progress["total"] = total
            last_progress["message"] = message
            try:
                await ctx.report_progress(progress=progress, total=total, message=message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never let a transport hiccup abort the council run; just log.
                logger.warning(f"ctx.report_progress failed: {exc}")

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                try:
                    await ctx.report_progress(
                        progress=last_progress["value"],
                        total=last_progress["total"],
                        message=f"{last_progress['message']} (still working...)",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f"heartbeat report_progress failed: {exc}")

        # Emit an immediate progress=0 so the client sees activity right away
        # (some clients refresh their idle timer only on the first notification).
        try:
            await ctx.report_progress(
                progress=0.0,
                total=1.0,
                message="Starting council deliberation",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"initial ctx.report_progress failed: {exc}")
        heartbeat_task = asyncio.create_task(_heartbeat())

    # Resolve or create conversation
    conversation_context = None
    is_new_conversation = False

    try:
        if conversation_id:
            conversation = storage_get(conversation_id)
            if not conversation:
                return f"Error: Conversation {conversation_id} not found."
            conversation_context = build_conversation_context(conversation)
            logger.info(f"Loaded conversation {conversation_id} (context: {'yes' if conversation_context else 'no'})")
        else:
            conversation_id = str(uuid.uuid4())
            storage_create(conversation_id)
            is_new_conversation = True
            logger.info(f"Created new conversation {conversation_id}")

        if on_conversation_resolved is not None:
            try:
                on_conversation_resolved(conversation_id)
            except Exception as exc:
                logger.warning(f"on_conversation_resolved callback failed: {exc}")

        cache_hit = None
        if bypass_cache:
            record_answer_cache_bypass()
        if is_cache_eligible(
            bypass_cache=bypass_cache,
            conversation_context=conversation_context,
            mode=mode,
            thorough=thorough,
            clarify_when_unclear=clarify_when_unclear,
        ):
            cache_hit = await find_answer_cache_hit_with_validation(question)

        # Store user message
        storage_add_user(conversation_id, question)

        if cache_hit:
            storage_add_assistant(
                conversation_id,
                cache_hit["stage1"],
                cache_hit["stage2"],
                cache_hit["stage3"],
                stage2a=cache_hit.get("stage2a"),
                stage2b=cache_hit.get("stage2b"),
                metadata=build_persisted_message_metadata(cache_hit["metadata"]),
            )

            if is_new_conversation:
                storage_update_title(conversation_id, build_cached_title(question))

            if full_output:
                result = format_council_output(
                    cache_hit["stage1"],
                    cache_hit["metadata"],
                    cache_hit["stage3"],
                )
            else:
                result = extract_response(cache_hit["stage3"], "No synthesis available")
                attribution_output = format_brief_attribution_output(
                    cache_hit["metadata"],
                    cache_hit["stage3"],
                )
                if attribution_output:
                    result = f"{result}\n\n{attribution_output}"

            if progress_callback is not None:
                await progress_callback(1.0, 1.0, "Answer served from cache")

            return f"{result}\n\n---\n*Council conversation: {conversation_id}*"

        stage1_results, stage2_results, stage3_result, metadata = await asyncio.wait_for(
            run_full_council(
                question,
                thorough=thorough,
                mode=mode,
                conversation_context=conversation_context,
                progress_callback=progress_callback,
                clarify_when_unclear=clarify_when_unclear,
            ),
            timeout=COUNCIL_TIMEOUT,
        )

        # Store assistant message
        storage_add_assistant(
            conversation_id,
            stage1_results,
            stage2_results,
            stage3_result,
            stage2a=metadata.get("stage2a"),
            stage2b=metadata.get("stage2b"),
            metadata=build_persisted_message_metadata(metadata),
        )

        # Generate title for new conversations (non-blocking)
        if is_new_conversation:
            try:
                title = await generate_conversation_title(question)
                storage_update_title(conversation_id, title)
            except Exception as e:
                logger.warning(f"Title generation failed: {e}")

        # Generate/update conversation summary (non-blocking)
        council_answer = extract_response(stage3_result, "")
        if council_answer and not metadata.get("clarification"):
            try:
                previous_summary = None
                if not is_new_conversation and conversation_context:
                    previous_summary = conversation_context.get("summary")
                summary = await generate_conversation_summary(previous_summary, question, council_answer)
                if summary:
                    storage_update_summary(conversation_id, summary)
            except Exception as e:
                logger.warning(f"Summary generation failed: {e}")

        if full_output:
            result = format_council_output(stage1_results, metadata, stage3_result)
        else:
            result = extract_response(stage3_result, "No synthesis available")
            attribution_output = format_brief_attribution_output(metadata, stage3_result)
            if attribution_output:
                result = f"{result}\n\n{attribution_output}"
            recovery_output = format_recovery_feedback(stage3_result)
            if recovery_output:
                result = f"{result}\n\n{recovery_output}"

        if include_debug and metadata.get("debug"):
            result = f"{result}\n\n{format_debug_output(metadata['debug'])}"

        log_event(
            logger,
            "mcp_deliberation_complete",
            conversation_id=conversation_id,
            request_id=request_id,
            include_debug=include_debug,
        )
        logger.info(f"{log_prefix} completed successfully")
        return f"{result}\n\n---\n*Council conversation: {conversation_id}*"

    except asyncio.TimeoutError:
        logger.error(f"{log_prefix} timed out after {COUNCIL_TIMEOUT}s")
        return f"Error: Council deliberation timed out after {int(COUNCIL_TIMEOUT // 60)} minutes. The models may be overloaded."
    except asyncio.CancelledError:
        logger.info(f"{log_prefix} was cancelled")
        raise
    except Exception as e:
        logger.error(f"{log_prefix} failed: {sanitize_error(e)}", exc_info=True)
        return "Error: Council deliberation failed. Check server logs for details."
    finally:
        # Stop the heartbeat first so it can't race with reset_request_id /
        # subsequent calls. Cancel + await catches the CancelledError raised
        # inside the task; any other exception inside the task has already
        # been logged.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"heartbeat task exited with error: {exc}")
        reset_request_id(token)


@mcp.tool()
async def ask_council(
    question: str,
    thorough: bool = False,
    mode: str = "auto",
    conversation_id: str = "",
    include_debug: bool = False,
    clarify_when_unclear: bool = False,
    bypass_cache: bool = False,
    ctx: Optional[Context] = None,
) -> str:
    """
    Submit a question to the LLM Council for deliberation.

    Use this when you can block on the full deliberation synchronously. If your
    MCP client has a short tool-call timeout (e.g. < 2 minutes), use
    start_council_async + poll_council_task instead.

    The council consists of multiple LLMs that:
    1. Each provide their own response to your question
    2. Anonymously rank each other's responses
    3. A chairman synthesizes the final answer

    mode controls the deliberation policy: auto picks quick, standard, or deep;
    quick answers simple prompts with the chairman only; standard runs the
    normal 3-stage council; deep runs the thorough critique/revision workflow.

    When thorough=True, it is treated as a deprecated alias for deep mode when
    mode is auto:
    2a. Each model critiques all responses
    2b. Each model revises its own response based on peer critiques
    This produces higher-quality answers for complex questions but takes longer.

    Multi-turn conversations: pass conversation_id from a previous call to ask
    follow-up questions with context. The council will reformulate your follow-up
    into a standalone question (Stage 0), and the chairman will consider the full
    conversation history when synthesizing. The response always includes the
    conversation_id for subsequent calls.

    IMPORTANT — this tool is stateful across calls: every response ends with a
    line like "Council conversation: <id>". Copy that id and pass it back as
    conversation_id on your NEXT call in the same conversation. If you drop it
    (e.g. leave conversation_id empty on what was meant to be a follow-up), the
    council has no way to detect that and will silently start a brand-new
    conversation with no memory of this one — it will not error. If you are
    not sure whether a conversation_id already exists for this topic, call
    list_conversations() first to check before asking a follow-up.

    Args:
        question: The question or topic for the council to discuss. Must be
                  self-contained with full context, as the council has no memory
                  of previous conversations.
        thorough: If True, run additional critique/revision stages for higher
                  quality output. Default is False.
        mode: "auto", "quick", "standard", or "deep". Default is auto.
        conversation_id: Optional ID from a previous council call to continue
                         the conversation. Leave empty for a new conversation.
        include_debug: If True, append request diagnostics including request ID,
                        stage timings, and failed model counts.
        clarify_when_unclear: If True, first-turn ambiguous questions return one
                              clarifying question before the full council runs.
        bypass_cache: If True, force a fresh council run even when a matching
                      first-turn answer is cached.

    Returns:
        The chairman's final synthesized answer
    """
    return await _execute_council_deliberation(
        question,
        full_output=False,
        thorough=thorough,
        mode=mode,
        conversation_id=conversation_id,
        include_debug=include_debug,
        clarify_when_unclear=clarify_when_unclear,
        bypass_cache=bypass_cache,
        ctx=ctx,
    )


@mcp.tool()
async def start_council_async(
    question: str,
    mode: str = "auto",
    conversation_id: str = "",
    include_debug: bool = False,
    clarify_when_unclear: bool = False,
    bypass_cache: bool = False,
) -> str:
    """
    Start a council deliberation in the background and return immediately.

    Use this instead of ask_council when the MCP client has a short tool-call
    timeout (e.g. < 2 minutes). The council runs asynchronously; poll for the
    result with poll_council_task(task_id).

    Typical flow:
        response = start_council_async(question=..., mode="standard")
        task_id  = json.loads(response)["task_id"]
        # wait ~30 s, then repeat until status == "done":
        result   = poll_council_task(task_id=task_id)
        conversation_id = result["conversation_id"]  # save this for follow-ups

    IMPORTANT — this tool is stateful across calls, same as ask_council: save
    the conversation_id field poll_council_task returns (available as soon as
    status leaves "pending", even before the run finishes) and pass it back on
    your NEXT call in the same conversation. If you leave conversation_id
    empty on what was meant to be a follow-up, there is no error — the council
    silently starts a brand-new conversation with no memory of the old one.

    Args:
        question: The question for the council.
        mode: "auto", "quick", "standard", or "deep". Default "auto".
        conversation_id: Optional prior conversation ID for multi-turn context.
        include_debug: Append request diagnostics to the final result.
        clarify_when_unclear: Return a clarifying question for ambiguous prompts.
        bypass_cache: Force a fresh council run, ignore answer cache.

    Returns:
        JSON: {"task_id": "<uuid>", "status": "pending"}
    """
    try:
        question = validate_question(question)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    task_id = str(uuid.uuid4())
    # Resolve the deliberation mode up front so poll_council_task can report an
    # ETA. This mirrors the ≤2.2s (often zero-call) classification get_council_eta
    # makes; the background task resolves it again internally, which is fine —
    # the ETA is advisory. Best-effort: never block kickoff on a classification
    # failure.
    eta_snapshot = None
    try:
        mode_selection = await resolve_deliberation_mode(question, mode=mode, thorough=False)
        eta_snapshot = estimate_council_wait(mode_selection)
    except Exception:
        eta_snapshot = None
    _async_tasks[task_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "started": _time.monotonic(),
        "eta": eta_snapshot,
        "conversation_id": None,
    }
    _prune_task_store()

    asyncio.create_task(
        _run_task_background(
            task_id,
            question=question,
            full_output=False,
            thorough=False,
            mode=mode,
            conversation_id=conversation_id,
            include_debug=include_debug,
            clarify_when_unclear=clarify_when_unclear,
            bypass_cache=bypass_cache,
        )
    )

    return json.dumps({"task_id": task_id, "status": "pending"})


@mcp.tool()
async def poll_council_task(task_id: str) -> str:
    """
    Poll the status of an async council task started with start_council_async.

    Call this every 15-30 seconds until status is "done" or "error".

    Args:
        task_id: The task_id returned by start_council_async.

    Returns:
        JSON with fields:
          status:          "pending" | "running" | "done" | "error"
          result:          council answer string (present when status=="done")
          error:           error message (present when status=="error")
          elapsed_seconds: seconds since the task was started
          conversation_id: the conversation this run belongs to, set as soon as
                            it's known (usually before status=="done"). Save
                            this and pass it as conversation_id on your next
                            call in the same conversation — do not rely on
                            parsing it back out of `result`.
    """
    task_id = (task_id or "").strip()
    if not task_id:
        return json.dumps({"error": "task_id cannot be empty"})

    task = _async_tasks.get(task_id)
    if task is None:
        return json.dumps({"error": f"Task {task_id!r} not found (may have expired)."})

    elapsed = round(_time.monotonic() - task.get("started", 0.0), 1)
    eta = task.get("eta")
    return json.dumps(
        {
            "status": task["status"],
            "result": task.get("result"),
            "error": task.get("error"),
            "elapsed_seconds": elapsed,
            "eta_seconds": eta.get("expected_wait_seconds") if eta else None,
            "eta": eta,
            "conversation_id": task.get("conversation_id"),
        }
    )


@mcp.tool()
async def list_conversations() -> str:
    """
    List all saved council conversations.

    Use this when you need a conversation_id to pass to get_conversation, to
    show the user what past council discussions are available, or to recover
    when you suspect you lost track of a conversation_id from ask_council /
    start_council_async (e.g. the user is asking a follow-up but you're not
    sure which prior conversation_id to pass) — sorted newest first, so the
    conversation you want is usually right at the top.

    Returns:
        List of conversation IDs with creation dates
    """
    try:
        conversations = storage_list()

        if not conversations:
            return "No conversations found."

        output_parts = ["## Saved Conversations\n"]
        for conv in conversations:
            conv_id = conv.get('id', 'Unknown')
            created = conv.get('created_at', 'Unknown date')
            title = conv.get('title', 'Untitled')
            output_parts.append(f"- **{conv_id}**: {title} (created: {created})")

        return "\n".join(output_parts)

    except Exception as e:
        logger.error(f"Failed to list conversations: {sanitize_error(e)}", exc_info=True)
        return "Error: Failed to list conversations. Check server logs for details."


@mcp.tool()
async def get_conversation(conversation_id: str) -> str:
    """
    Get details of a specific conversation.

    Use this when you already have a conversation_id (from ask_council,
    start_council_async, or list_conversations) and need the full stored
    history rather than just the latest answer.

    Args:
        conversation_id: The ID of the conversation to retrieve

    Returns:
        Full conversation history including all stages
    """
    if not conversation_id or not conversation_id.strip():
        return "Error: Conversation ID cannot be empty"

    conversation_id = conversation_id.strip()

    try:
        conv = storage_get(conversation_id)

        if not conv:
            return f"Conversation {conversation_id} not found."

        output_parts = [f"## Conversation: {conversation_id}\n"]
        output_parts.append(f"Created: {conv.get('created_at', 'Unknown')}\n")

        summary = conv.get('summary')
        if summary:
            output_parts.append(f"**Summary:** {summary}\n")

        messages = conv.get('messages', [])
        for msg in messages:
            role = msg.get('role', 'unknown')
            if role == 'user':
                output_parts.append(f"### User:\n{msg.get('content', '')}\n")
            elif role == 'assistant':
                stage3 = msg.get('stage3', {})
                if stage3:
                    content = extract_response(stage3)
                    output_parts.append(f"### Council Response:\n{content}\n")

        return "\n".join(output_parts)

    except Exception as e:
        logger.error(f"Failed to get conversation: {sanitize_error(e)}", exc_info=True)
        return "Error: Failed to get conversation. Check server logs for details."


@mcp.tool()
async def get_available_models() -> str:
    """
    Get list of available models in the current configuration.

    Use this before asking the council if you need to tell the user which
    models will be consulted, or to sanity-check the deployment's provider.

    Returns:
        List of council models and chairman model
    """
    output_parts = [
        f"## Available Models (Provider: {API_PROVIDER})\n",
        "### Council Members:",
    ]

    for model in COUNCIL_MODELS:
        family = COUNCIL_MODEL_FAMILIES.get(model, "unknown")
        output_parts.append(f"- {model} (family: {family})")

    output_parts.append(
        f"\n### Chairman:\n- {CHAIRMAN_MODEL}\n"
        f"- Chairman family: {CHAIRMAN_MODEL_FAMILY}"
    )

    return "\n".join(output_parts)


@mcp.tool()
async def get_council_metrics() -> str:
    """
    Get rolling process-local council KPIs in JSON format.

    Use this to check reliability/latency before deciding whether to retry a
    degraded run or trust a fresh answer's timing. Counters reset on process
    restart and are not shared across MCP/backend processes.

    Returns:
        JSON string with success/degradation counters and stage latency percentiles
    """
    return json.dumps(get_council_metrics_snapshot(), indent=2, sort_keys=True)


@mcp.tool()
async def get_council_eta(question: str, mode: str = "auto", thorough: bool = False) -> str:
    """
    Estimate the expected wait time for a council run, from durable statistics.

    Resolves the deliberation mode for the question (same classifier ask_council
    uses — typically zero model calls, at most ~2s) and returns a percentile
    expected-wait estimate from the persisted runs table. The estimate survives
    process restarts (it is NOT the in-memory rolling window).

    When fewer than the minimum samples have been collected for the resolved mode,
    expected_wait_seconds is null (basis="insufficient_data") and an advisory
    fallback_seconds is provided for callers that opt in to a static estimate.

    Args:
        question: The question you intend to ask the council.
        mode: "auto", "quick", "standard", or "deep". Default "auto".
        thorough: Deprecated alias for deep mode.

    Returns:
        JSON with expected_wait_seconds (or null), per_stage_estimates,
        confidence, basis, sample_count, fallback_seconds, note.
    """
    try:
        question = validate_question(question)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    try:
        mode_selection = await resolve_deliberation_mode(question, mode=mode, thorough=thorough)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    estimate = estimate_council_wait(mode_selection)
    return json.dumps(estimate, indent=2, sort_keys=True)


def main():
    """Run the MCP server."""
    try:
        validate_environment()
        logger.info("Starting LLM Council MCP Server...")
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.critical(f"Server failed to start: {sanitize_error(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
