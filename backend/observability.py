"""Request-scoped observability helpers for council runs."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar, Token
from typing import Any, Optional

_request_id_var: ContextVar[Optional[str]] = ContextVar(
    "llm_council_request_id", default=None
)


def bind_request_id(request_id: Optional[str] = None) -> tuple[str, Token]:
    """Bind a request ID to the current async context."""
    request_id = request_id or uuid.uuid4().hex
    token = _request_id_var.set(request_id)
    return request_id, token


def ensure_request_id() -> str:
    """Return the current request ID, creating one if needed."""
    current = _request_id_var.get()
    if current:
        return current

    current, _ = bind_request_id()
    return current


def reset_request_id(token: Token) -> None:
    """Reset the request ID binding for the current async context."""
    _request_id_var.reset(token)


def get_request_id() -> Optional[str]:
    """Return the current request ID if one is bound."""
    return _request_id_var.get()


def log_event(
    logger: logging.Logger,
    event: str,
    level: str = "info",
    **fields: Any,
) -> None:
    """Emit a structured log line with the active request ID attached."""
    payload = {
        "event": event,
        "request_id": ensure_request_id(),
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    getattr(logger, level)("event=%s", json.dumps(payload, sort_keys=True))


def round_duration_ms(started_at: float) -> float:
    """Return elapsed time in milliseconds rounded for logging/metadata."""
    return round((time.perf_counter() - started_at) * 1000, 2)
