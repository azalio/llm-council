"""FastAPI backend for LLM Council."""

import asyncio
import json
import time
import uuid
from contextlib import suppress
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import storage
from .answer_cache import (
    build_cached_title,
    find_answer_cache_hit_with_validation,
    is_cache_eligible,
)
from .agent_router import build_agent_route, mark_route_expanded, should_expand_route
from .council import (
    build_clarification_result,
    build_confidence_escalation,
    build_persisted_message_metadata,
    build_run_status,
    run_full_council,
    resolve_deliberation_mode,
    stage_quick_answer,
    validate_deliberation_mode,
    generate_conversation_title,
    generate_conversation_summary,
    build_conversation_context,
    stage_minus_1_intent_check,
    stage0_reformulate,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage2a_collect_critiques,
    stage2b_collect_revisions,
    stage3_synthesize_final,
    calculate_aggregate_rankings,
    compute_council_confidence,
    _combine_stage_debug,
    _with_usage,
)
from .metrics import (
    build_council_run_debug,
    get_council_metrics_snapshot,
    record_answer_cache_bypass,
    record_council_metrics,
    record_run_timing,
)
from .eta import estimate_council_wait
from .observability import bind_request_id, reset_request_id
from .config import COUNCIL_MODELS

app = FastAPI(title="LLM Council API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str
    mode: str = "auto"
    thorough: bool = False
    clarify_when_unclear: bool = False
    bypass_cache: bool = False


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/metrics/council")
async def get_council_metrics():
    """Return rolling process-local council KPIs."""
    return get_council_metrics_snapshot()


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        validate_deliberation_mode(request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Build conversation context for multi-turn (if not first message)
    conversation_context = None
    if not is_first_message:
        conversation_context = build_conversation_context(conversation)

    cache_hit = None
    if request.bypass_cache:
        record_answer_cache_bypass()
    if is_cache_eligible(
        bypass_cache=request.bypass_cache,
        conversation_context=conversation_context,
        mode=request.mode,
        thorough=request.thorough,
        clarify_when_unclear=request.clarify_when_unclear,
    ):
        cache_hit = await find_answer_cache_hit_with_validation(request.content)

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    if cache_hit:
        if is_first_message:
            storage.update_conversation_title(conversation_id, build_cached_title(request.content))
        storage.add_assistant_message(
            conversation_id,
            cache_hit["stage1"],
            cache_hit["stage2"],
            cache_hit["stage3"],
            stage2a=cache_hit.get("stage2a"),
            stage2b=cache_hit.get("stage2b"),
            metadata=build_persisted_message_metadata(cache_hit["metadata"]),
        )
        return {
            "stage1": cache_hit["stage1"],
            "stage2": cache_hit["stage2"],
            "stage3": cache_hit["stage3"],
            "metadata": cache_hit["metadata"],
        }

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    request_id, token = bind_request_id()
    try:
        # Run the council process (with context for multi-turn)
        stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
            request.content,
            thorough=request.thorough,
            mode=request.mode,
            conversation_context=conversation_context,
            clarify_when_unclear=request.clarify_when_unclear,
        )
    finally:
        reset_request_id(token)

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
        stage2a=metadata.get("stage2a"),
        stage2b=metadata.get("stage2b"),
        metadata=build_persisted_message_metadata(metadata),
    )

    # Generate/update conversation summary (non-blocking)
    council_answer = stage3_result.get("response", "")
    if council_answer and not metadata.get("clarification"):
        try:
            previous_summary = conversation.get("summary") if not is_first_message else None
            summary = await generate_conversation_summary(previous_summary, request.content, council_answer)
            if summary:
                storage.update_conversation_summary(conversation_id, summary)
        except Exception:
            pass  # summary is an optimization, not critical

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        validate_deliberation_mode(request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Build conversation context for multi-turn (if not first message)
    conversation_context = None
    if not is_first_message:
        conversation_context = build_conversation_context(conversation)

    async def event_generator():
        request_id, token = bind_request_id()
        started_at = time.perf_counter()
        started_at_epoch = time.time()
        stage1_debug = None
        stage2_debug = None
        stage2a_debug = None
        stage2b_debug = None
        stage3_debug = None
        metrics_recorded = False
        title_task = None
        try:
            cache_hit = None
            if request.bypass_cache:
                record_answer_cache_bypass()
            if is_cache_eligible(
                bypass_cache=request.bypass_cache,
                conversation_context=conversation_context,
                mode=request.mode,
                thorough=request.thorough,
                clarify_when_unclear=request.clarify_when_unclear,
            ):
                cache_hit = await find_answer_cache_hit_with_validation(request.content)

            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            if is_first_message and not cache_hit:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            if cache_hit:
                yield f"data: {json.dumps({'type': 'answer_cache_hit', 'metadata': cache_hit['metadata']})}\n\n"
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': cache_hit['stage3'], 'metadata': cache_hit['metadata']})}\n\n"

                if is_first_message:
                    title = build_cached_title(request.content)
                    storage.update_conversation_title(conversation_id, title)
                    yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

                storage.add_assistant_message(
                    conversation_id,
                    cache_hit["stage1"],
                    cache_hit["stage2"],
                    cache_hit["stage3"],
                    stage2a=cache_hit.get("stage2a"),
                    stage2b=cache_hit.get("stage2b"),
                    metadata=build_persisted_message_metadata(cache_hit["metadata"]),
                )
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            # Stage 0: Reformulate follow-up (multi-turn only)
            effective_query = request.content
            if request.clarify_when_unclear and not conversation_context:
                yield f"data: {json.dumps({'type': 'stage_minus_1_start'})}\n\n"
                clarification = await stage_minus_1_intent_check(request.content)
                yield f"data: {json.dumps({'type': 'stage_minus_1_complete', 'data': clarification or {'needed': False}})}\n\n"
                if clarification:
                    stage3_result = build_clarification_result(clarification)
                    final_metadata = {"clarification": clarification}

                    if title_task:
                        title = await title_task
                        storage.update_conversation_title(conversation_id, title)
                        yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

                    storage.add_assistant_message(
                        conversation_id,
                        [],
                        [],
                        stage3_result,
                        metadata=build_persisted_message_metadata(final_metadata),
                    )
                    yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result, 'metadata': final_metadata})}\n\n"
                    yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                    return

            mode_selection = await resolve_deliberation_mode(
                request.content,
                mode=request.mode,
                thorough=request.thorough,
            )
            selected_mode = mode_selection["selected_mode"]
            effective_thorough = selected_mode == "deep"
            eta_estimate = estimate_council_wait(mode_selection)
            yield f"data: {json.dumps({'type': 'mode_selection_complete', 'metadata': {'deliberation_mode': mode_selection, 'eta': eta_estimate}})}\n\n"

            if conversation_context:
                yield f"data: {json.dumps({'type': 'stage0_start'})}\n\n"
                effective_query = await stage0_reformulate(request.content, conversation_context)
                yield f"data: {json.dumps({'type': 'stage0_complete', 'data': {'standalone_query': effective_query}})}\n\n"

            if selected_mode == "quick":
                yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
                stage3_result, quick_debug = await stage_quick_answer(
                    request.content,
                    conversation_context=conversation_context,
                    standalone_query=effective_query,
                )
                run_debug = build_council_run_debug(
                    request_id=request_id,
                    thorough=False,
                    started_at=started_at,
                    quick_debug=quick_debug,
                    deliberation_mode=selected_mode,
                    mode_selection=mode_selection,
                )
                record_council_metrics(run_debug)
                record_run_timing(
                    run_debug,
                    conversation_id=conversation_id,
                    completed=True,
                    started_at_epoch=started_at_epoch,
                )
                metrics_recorded = True
                stage1_results = [_with_usage({
                    "model": stage3_result.get("model", "quick"),
                    "response": stage3_result.get("response", ""),
                    "mode": "quick",
                }, stage3_result.get("usage"))]
                final_metadata = {
                    "deliberation_mode": mode_selection,
                    "debug": run_debug,
                    "run_status": build_run_status(run_debug),
                }
                if conversation_context:
                    final_metadata["stage0_standalone_query"] = effective_query

                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result, 'metadata': final_metadata})}\n\n"

                if title_task:
                    title = await title_task
                    storage.update_conversation_title(conversation_id, title)
                    yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

                storage.add_assistant_message(
                    conversation_id,
                    stage1_results,
                    [],
                    stage3_result,
                    metadata=build_persisted_message_metadata(final_metadata),
                )

                council_answer = stage3_result.get("response", "")
                if council_answer:
                    try:
                        previous_summary = conversation.get("summary") if not is_first_message else None
                        summary = await generate_conversation_summary(previous_summary, request.content, council_answer)
                        if summary:
                            storage.update_conversation_summary(conversation_id, summary)
                    except Exception:
                        pass

                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            agent_routing = build_agent_route(
                effective_query,
                mode_selection,
                full_pool=COUNCIL_MODELS,
            )
            active_models = agent_routing["selected_models"]

            # Stage 1: Collect responses (council sees standalone question)
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results, stage1_debug = await stage1_collect_responses(
                effective_query,
                models=active_models,
            )
            expansion_reason = should_expand_route(
                agent_routing,
                stage1_results=stage1_results,
                stage1_debug=stage1_debug,
            )
            if expansion_reason:
                expansion_models = list(agent_routing.get("skipped_models") or [])
                agent_routing = mark_route_expanded(agent_routing, expansion_reason)
                skipped_results, skipped_debug = await stage1_collect_responses(
                    effective_query,
                    models=expansion_models or agent_routing["full_pool"],
                )
                stage1_results = stage1_results + skipped_results
                stage1_debug = _combine_stage_debug(
                    "stage1",
                    [stage1_debug, skipped_debug],
                    requested_models=len(agent_routing["full_pool"]),
                )
                active_models = agent_routing["selected_models"]
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results, 'metadata': {'debug': stage1_debug}})}\n\n"

            if not stage1_results:
                run_debug = build_council_run_debug(
                    request_id=request_id,
                    thorough=effective_thorough,
                    started_at=started_at,
                    stage1_debug=stage1_debug,
                    deliberation_mode=selected_mode,
                    mode_selection=mode_selection,
                    agent_routing=agent_routing,
                )
                record_council_metrics(run_debug)
                record_run_timing(
                    run_debug,
                    conversation_id=conversation_id,
                    completed=False,
                    started_at_epoch=started_at_epoch,
                )
                metrics_recorded = True

                error_result = {
                    "model": "error",
                    "response": "All models failed to respond. Please try again.",
                }

                if title_task:
                    title = await title_task
                    storage.update_conversation_title(conversation_id, title)
                    yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

                storage.add_assistant_message(
                    conversation_id,
                    stage1_results,
                    [],
                    error_result,
                    metadata=build_persisted_message_metadata(
                        {
                            "debug": run_debug,
                            "run_status": build_run_status(run_debug),
                            "deliberation_mode": mode_selection,
                            "agent_routing": agent_routing,
                            "stage0_standalone_query": (
                                effective_query if conversation_context else None
                            ),
                        }
                    ),
                )
                yield f"data: {json.dumps({'type': 'stage3_complete', 'data': error_result, 'metadata': {'debug': run_debug, 'run_status': build_run_status(run_debug), 'deliberation_mode': mode_selection, 'agent_routing': agent_routing, 'stage0_standalone_query': effective_query if conversation_context else None}})}\n\n"
                yield f"data: {json.dumps({'type': 'complete'})}\n\n"
                return

            # Stage 2: Collect rankings (council sees standalone question)
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model, stage2_debug = await stage2_collect_rankings(
                effective_query,
                stage1_results,
                models=active_models,
            )
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            council_confidence = compute_council_confidence(stage2_results, label_to_model)
            expansion_reason = should_expand_route(
                agent_routing,
                stage1_results=stage1_results,
                stage1_debug=stage1_debug,
                stage2_results=stage2_results,
                stage2_debug=stage2_debug,
                council_confidence=council_confidence,
            )
            if expansion_reason:
                agent_routing = mark_route_expanded(agent_routing, expansion_reason)
                remaining_models = [
                    model for model in agent_routing["full_pool"]
                    if model not in {result.get("model") for result in stage1_results}
                ]
                if remaining_models:
                    skipped_results, skipped_debug = await stage1_collect_responses(
                        effective_query,
                        models=remaining_models,
                    )
                    stage1_results = stage1_results + skipped_results
                    stage1_debug = _combine_stage_debug(
                        "stage1",
                        [stage1_debug, skipped_debug],
                        requested_models=len(agent_routing["full_pool"]),
                    )
                    yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results, 'metadata': {'debug': stage1_debug, 'agent_routing': agent_routing}})}\n\n"
                active_models = agent_routing["selected_models"]
                stage2_results, label_to_model, stage2_debug = await stage2_collect_rankings(
                    effective_query,
                    stage1_results,
                    models=active_models,
                )
                aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
                council_confidence = compute_council_confidence(stage2_results, label_to_model)
            confidence_escalation = build_confidence_escalation(
                council_confidence,
                mode_selection,
                already_deep=effective_thorough,
            )
            if confidence_escalation["triggered"]:
                effective_thorough = True
            stage2_metadata = {
                "label_to_model": label_to_model,
                "aggregate_rankings": aggregate_rankings,
                "council_confidence": council_confidence,
                "agent_routing": agent_routing,
                "debug": stage2_debug,
            }
            if confidence_escalation["triggered"]:
                stage2_metadata["confidence_escalation"] = confidence_escalation
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': stage2_metadata})}\n\n"

            # Stages 2a/2b (thorough mode only, council sees standalone question)
            labels = [chr(65 + i) for i in range(len(stage1_results))]
            stage2a_results = None
            stage2b_results = None

            if effective_thorough:
                # Stage 2a: Critiques
                yield f"data: {json.dumps({'type': 'stage2a_start'})}\n\n"
                stage2a_results, stage2a_debug = await stage2a_collect_critiques(
                    effective_query,
                    stage1_results,
                    labels,
                    models=active_models,
                )
                yield f"data: {json.dumps({'type': 'stage2a_complete', 'data': stage2a_results, 'metadata': {'debug': stage2a_debug}})}\n\n"

                # Stage 2b: Revisions
                yield f"data: {json.dumps({'type': 'stage2b_start'})}\n\n"
                stage2b_results, stage2b_debug = await stage2b_collect_revisions(
                    effective_query, stage1_results, stage2a_results, labels, label_to_model
                )
                yield f"data: {json.dumps({'type': 'stage2b_complete', 'data': stage2b_results, 'metadata': {'debug': stage2b_debug}})}\n\n"

            # Stage 3: Chairman gets original question + conversation context
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result, stage3_debug = await stage3_synthesize_final(
                request.content, stage1_results, stage2_results, label_to_model,
                stage2b_results=stage2b_results,
                conversation_context=conversation_context,
                council_confidence=council_confidence,
            )
            run_debug = build_council_run_debug(
                request_id=request_id,
                thorough=effective_thorough,
                started_at=started_at,
                stage1_debug=stage1_debug,
                stage2_debug=stage2_debug,
                stage3_debug=stage3_debug,
                stage2a_debug=stage2a_debug,
                stage2b_debug=stage2b_debug,
                deliberation_mode=selected_mode,
                mode_selection=mode_selection,
                confidence_escalation=(
                    confidence_escalation if confidence_escalation["triggered"] else None
                ),
                agent_routing=agent_routing,
            )
            record_council_metrics(run_debug)
            record_run_timing(
                run_debug,
                conversation_id=conversation_id,
                completed=True,
                started_at_epoch=started_at_epoch,
            )
            metrics_recorded = True
            final_metadata = {
                "label_to_model": label_to_model,
                "aggregate_rankings": aggregate_rankings,
                "council_confidence": council_confidence,
                "deliberation_mode": mode_selection,
                "agent_routing": agent_routing,
                "debug": run_debug,
                "run_status": build_run_status(run_debug),
            }
            if confidence_escalation["triggered"]:
                final_metadata["confidence_escalation"] = confidence_escalation
            if conversation_context:
                final_metadata["stage0_standalone_query"] = effective_query
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result, 'metadata': final_metadata})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result,
                stage2a=stage2a_results,
                stage2b=stage2b_results,
                metadata=build_persisted_message_metadata(final_metadata),
            )

            # Generate/update conversation summary (non-blocking best-effort)
            council_answer = stage3_result.get("response", "")
            if council_answer and not final_metadata.get("clarification"):
                try:
                    previous_summary = conversation.get("summary") if not is_first_message else None
                    summary = await generate_conversation_summary(previous_summary, request.content, council_answer)
                    if summary:
                        storage.update_conversation_summary(conversation_id, summary)
                except Exception:
                    pass  # summary is an optimization, not critical

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            if not metrics_recorded and stage1_debug is not None:
                run_debug = build_council_run_debug(
                    request_id=request_id,
                    thorough=request.thorough,
                    started_at=started_at,
                    stage1_debug=stage1_debug,
                    stage2_debug=stage2_debug,
                    stage3_debug=stage3_debug,
                    stage2a_debug=stage2a_debug,
                    stage2b_debug=stage2b_debug,
                )
                record_council_metrics(run_debug)
                record_run_timing(
                    run_debug,
                    conversation_id=conversation_id,
                    completed=False,
                    started_at_epoch=started_at_epoch,
                )
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if title_task and not title_task.done():
                title_task.cancel()
                with suppress(asyncio.CancelledError):
                    await title_task
            reset_request_id(token)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
