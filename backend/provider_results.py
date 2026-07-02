"""Shared helpers for provider response payloads."""

from typing import Any, Dict, Optional


def response_failed(response: Optional[Dict[str, Any]]) -> bool:
    """Return whether a provider response should be treated as failed."""
    if response is None:
        return True
    debug = response.get("_debug", {})
    if debug.get("ok") is False:
        return True
    return response.get("content") is None
