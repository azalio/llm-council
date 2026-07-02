"""API client for making LLM requests via the configured provider (OpenRouter)."""

import asyncio
import logging
import math
import time
from typing import Any, Dict, List

import httpx

from .config import API_PROVIDER, API_URL, AUTH_HEADER
from .observability import ensure_request_id, log_event, round_duration_ms
from .provider_results import response_failed
from .providers.registry import resolve_provider
from .usage import normalize_openai_usage

logger = logging.getLogger(__name__)


def _success_response(
    model: str,
    provider: str,
    content: Any,
    reasoning_details: Any,
    started_at: float,
    response_bytes: int | None = None,
    status_code: int | None = None,
    usage: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "content": content,
        "reasoning_details": reasoning_details,
        "usage": usage,
        "_debug": {
            "ok": True,
            "request_id": ensure_request_id(),
            "provider": provider,
            "model": model,
            "duration_ms": round_duration_ms(started_at),
            "response_bytes": response_bytes,
            "status_code": status_code,
        },
    }


def _failure_response(
    model: str,
    provider: str,
    started_at: float,
    failure_type: str,
    error_message: str | None = None,
    status_code: int | None = None,
    response_bytes: int | None = None,
) -> Dict[str, Any]:
    return {
        "content": None,
        "reasoning_details": None,
        "_debug": {
            "ok": False,
            "request_id": ensure_request_id(),
            "provider": provider,
            "model": model,
            "duration_ms": round_duration_ms(started_at),
            "failure_type": failure_type,
            "error_message": error_message,
            "status_code": status_code,
            "response_bytes": response_bytes,
        },
    }


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 600.0,  # 10 minutes for reasoning models
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
) -> Dict[str, Any]:
    """
    Query a single model via the configured API provider.

    Args:
        model: Model identifier (format depends on API_PROVIDER in config)
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds
        temperature: Optional generation temperature for evaluator-style calls
        top_p: Optional nucleus sampling value
        max_tokens: Optional output token cap

    Returns:
        Response dict with 'content', optional 'reasoning_details', and `_debug`
        metadata describing either success or failure.
    """
    log_event(
        logger,
        "provider_call_start",
        provider=API_PROVIDER,
        model=model,
        timeout_s=timeout,
        message_count=len(messages),
    )
    query_fn = _resolve_query_fn(API_PROVIDER)
    return await query_fn(
        model,
        messages,
        timeout,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )


def _resolve_query_fn(provider_name: str):
    """Resolve `API_PROVIDER` to the query coroutine function to call.

    Routes through `backend.providers.registry.resolve_provider()` so the
    registry is exercised at the point the provider is actually chosen
    (`get_loader()`/`PROVIDER_REGISTRY` membership is checked, and an
    unregistered `provider_name` still raises `KeyError` here, same as it
    would from the registry directly). This also converts a registered but
    absent provider module into `resolve_provider()`'s actionable
    `RuntimeError`.

    `resolve_provider()` is called for its module-presence/error-shaping
    side effect even for 'openrouter' (whose built-in loader just returns
    this module, so it never touches anything absent). A future second
    provider would add a branch here mapping its name to its own adapted
    query function.
    """
    resolve_provider(provider_name)
    return _query_openrouter


async def _query_openrouter(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float,
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
) -> Dict[str, Any]:
    """Query model via OpenRouter API."""
    headers = {
        "Authorization": AUTH_HEADER,
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                API_URL,
                headers=headers,
                json=payload
            )
            response.raise_for_status()

            data = response.json()
            message = data['choices'][0]['message']

            result = _success_response(
                model=model,
                provider="openrouter",
                content=message.get("content"),
                reasoning_details=message.get("reasoning_details"),
                started_at=started_at,
                response_bytes=len(response.content),
                status_code=response.status_code,
                usage=normalize_openai_usage(data),
            )
            log_event(
                logger,
                "provider_call_success",
                provider="openrouter",
                model=model,
                duration_ms=result["_debug"]["duration_ms"],
                status_code=response.status_code,
            )
            return result

    except httpx.TimeoutException as e:
        logger.error(f"Timeout querying {model} via openrouter: {e}")
        result = _failure_response(
            model=model,
            provider="openrouter",
            started_at=started_at,
            failure_type="timeout",
            error_message=str(e),
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error querying {model} via openrouter: {e.response.status_code} {e.response.text[:500]}")
        result = _failure_response(
            model=model,
            provider="openrouter",
            started_at=started_at,
            failure_type="http_status",
            error_message=e.response.text[:500],
            status_code=e.response.status_code,
            response_bytes=len(e.response.content),
        )
    except httpx.RequestError as e:
        logger.error(f"Request error querying {model} via openrouter: {type(e).__name__}: {e}")
        result = _failure_response(
            model=model,
            provider="openrouter",
            started_at=started_at,
            failure_type="request_error",
            error_message=str(e),
        )
    except Exception as e:
        logger.error(f"Error querying model {model} via openrouter: {type(e).__name__}: {e}")
        result = _failure_response(
            model=model,
            provider="openrouter",
            started_at=started_at,
            failure_type=type(e).__name__.lower(),
            error_message=str(e),
        )

    log_event(
        logger,
        "provider_call_failed",
        provider="openrouter",
        model=model,
        failure_type=result["_debug"]["failure_type"],
        duration_ms=result["_debug"]["duration_ms"],
        status_code=result["_debug"].get("status_code"),
    )
    return result


def _model_provider_path(model: str) -> str:
    return "openrouter"


def _effective_concurrency(model_count: int, max_concurrency: int | None) -> int:
    if max_concurrency is None:
        return model_count
    if max_concurrency <= 0:
        raise ValueError("max_concurrency must be a positive integer")
    return min(max_concurrency, model_count)


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
    *,
    max_concurrency: int | None = None,
    failure_backoff_seconds: float = 0.0,
) -> Dict[str, Dict[str, Any]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of model identifiers
        messages: List of message dicts to send to each model
        max_concurrency: Optional cap on in-flight model calls
        failure_backoff_seconds: Delay before starting another call on a provider path
            that has already failed during this fan-out

    Returns:
        Dict mapping model identifier to response dict with success/failure debug metadata
    """
    if not models:
        return {}

    concurrency = _effective_concurrency(len(models), max_concurrency)
    if failure_backoff_seconds < 0 or not math.isfinite(failure_backoff_seconds):
        raise ValueError("failure_backoff_seconds must be non-negative")

    semaphore = asyncio.Semaphore(concurrency)
    provider_next_allowed_at: Dict[str, float] = {}
    provider_failure_versions: Dict[str, int] = {}
    provider_backoff_lock = asyncio.Lock()

    async def reserve_provider_backoff(provider_path: str) -> tuple[float, int] | None:
        if failure_backoff_seconds <= 0:
            return None

        async with provider_backoff_lock:
            next_allowed_at = provider_next_allowed_at.get(provider_path, 0.0)
            if next_allowed_at <= time.perf_counter():
                return None
            failure_version = provider_failure_versions.get(provider_path, 0)
            provider_next_allowed_at[provider_path] = (
                next_allowed_at + failure_backoff_seconds
            )
            return next_allowed_at, failure_version

    async def reservation_is_stale(
        provider_path: str,
        reservation: tuple[float, int],
    ) -> bool:
        _, reserved_failure_version = reservation
        async with provider_backoff_lock:
            current_failure_version = provider_failure_versions.get(provider_path, 0)
            next_allowed_at = provider_next_allowed_at.get(provider_path, 0.0)
            return (
                current_failure_version > reserved_failure_version
                and next_allowed_at > time.perf_counter()
            )

    async def wait_for_provider_backoff(
        provider_path: str,
        reservation: tuple[float, int],
    ) -> None:
        reserved_at, _ = reservation
        delay = max(0.0, reserved_at - time.perf_counter())
        if delay > 0:
            log_event(
                logger,
                "provider_backoff_wait",
                provider_path=provider_path,
                delay_ms=round(delay * 1000, 2),
            )
            await asyncio.sleep(delay)

    async def record_provider_failure(provider_path: str, response: Dict[str, Any]) -> None:
        if failure_backoff_seconds <= 0 or not response_failed(response):
            return

        async with provider_backoff_lock:
            provider_failure_versions[provider_path] = (
                provider_failure_versions.get(provider_path, 0) + 1
            )
            provider_next_allowed_at[provider_path] = max(
                provider_next_allowed_at.get(provider_path, 0.0),
                time.perf_counter() + failure_backoff_seconds,
            )

    async def query_with_limits(model: str) -> Dict[str, Any]:
        provider_path = _model_provider_path(model)
        reservation = None

        while True:
            wait_reservation = None
            async with semaphore:
                if reservation is None:
                    reservation = await reserve_provider_backoff(provider_path)
                    if reservation is not None:
                        wait_reservation = reservation
                    else:
                        response = await query_model(model, messages)
                        await record_provider_failure(provider_path, response)
                        return response
                elif await reservation_is_stale(provider_path, reservation):
                    reservation = await reserve_provider_backoff(provider_path)
                    if reservation is not None:
                        wait_reservation = reservation
                    else:
                        response = await query_model(model, messages)
                        await record_provider_failure(provider_path, response)
                        return response
                else:
                    response = await query_model(model, messages)
                    await record_provider_failure(provider_path, response)
                    return response

            if wait_reservation is not None:
                await wait_for_provider_backoff(provider_path, wait_reservation)

    tasks = [asyncio.create_task(query_with_limits(model)) for model in models]

    try:
        responses = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
