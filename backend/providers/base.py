"""Provider Protocol: the minimal contract every API provider satisfies.

This module defines the abstraction surface only. It documents, as a
structural `typing.Protocol`, the three-part contract the built-in
OpenRouter path (`backend/openrouter.py`) implements today:

1. Build a provider-specific request payload from a list of chat messages.
2. Parse a provider-specific response back into the common
   `{"content", "reasoning_details", "usage"}` shape used throughout the
   council pipeline.
3. Resolve the auth header, base API URL, and (optionally) the field name
   the provider wraps its response in.

Nothing in this module wires a provider into the running application --
see `backend/providers/registry.py` for provider lookup and `config.py` /
`openrouter.py` for how the OpenRouter provider implements this Protocol.
"""

from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Structural contract for a pluggable LLM API provider.

    Implementations are not required to subclass this Protocol -- any
    object (module, class instance, etc.) exposing these three callables
    satisfies it. `openrouter` is the initial implementation; the registry
    in `backend/providers/registry.py` is sized to add more providers
    without further changes to this Protocol.
    """

    def build_request(
        self,
        model: str,
        messages: List[Dict[str, str]],
        generation_options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """Build the provider-specific request payload.

        Args:
            model: Model identifier as configured for this provider.
            messages: Chat messages with 'role' and 'content' keys.
            generation_options: Optional generation knobs, e.g.
                {"temperature", "top_p", "max_tokens"}.

        Returns:
            A (model_id_or_none, payload) tuple. The first element carries
            a provider-specific model id when the endpoint needs it (e.g.
            embedded in a URL); otherwise None.
        """
        ...

    def parse_response(
        self,
        vendor_or_none: Optional[str],
        data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Parse a raw provider response into the common result shape.

        Args:
            vendor_or_none: Vendor discriminator for providers that proxy
                multiple upstream formats; None for providers with a
                single response shape.
            data: Raw, already-unwrapped response payload.

        Returns:
            Dict with 'content', optional 'reasoning_details', and
            'usage' (normalized via `backend/usage.py`, or None), or None
            on parse failure.
        """
        ...

    def resolve_auth(self) -> Tuple[str, str, Optional[str]]:
        """Resolve this provider's auth header, base API URL, and response wrapper.

        Returns:
            A (auth_header_value, api_base_url, response_wrapper_field)
            tuple. `response_wrapper_field` is the top-level key the
            provider nests its response under, or None when the response
            is unwrapped.
        """
        ...
