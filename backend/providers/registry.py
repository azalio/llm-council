"""Provider registry: name -> lazy loader for pluggable API providers.

This module exposes a plain dict mapping a provider name (matching
`API_PROVIDER`) to a zero-argument loader callable that returns the
provider implementation module/object satisfying the `Provider` Protocol
in `backend/providers/base.py`.

Loaders are lazy so that resolving a provider name does not force an
import of every registered provider -- adding a second provider later
must not require this module to eagerly import it. This keeps provider
resolution robust even when an optional provider's module is absent from
a given build/snapshot.

Only 'openrouter' is registered here today. Additional providers are an
intentionally unimplemented extension point -- do not add entries for
them without new connector code (adding a new provider is "write
`backend/providers/<name>.py` implementing `Provider`, then register a
loader for it here").

`resolve_provider()` is the entry point `backend/openrouter.py` uses at the
point the provider is actually chosen: it looks up the loader via
`get_loader()` and invokes it, converting a missing provider module into
an actionable `RuntimeError` instead of letting a bare
`ModuleNotFoundError`/`ImportError` surface. A genuinely unregistered
provider name still raises `KeyError` unchanged -- that is a configuration
mistake (typo in `API_PROVIDER`), not a "module not shipped in this build"
situation, so it gets a different failure mode on purpose.
"""

from typing import Any, Callable, Dict

ProviderLoader = Callable[[], Any]


def _load_openrouter() -> Any:
    """Built-in, always-importable loader for the OpenRouter provider."""
    from backend import openrouter

    return openrouter


# Extension point: additional providers register a loader here once their
# `backend/providers/<name>.py` implementation exists.
PROVIDER_REGISTRY: Dict[str, ProviderLoader] = {
    "openrouter": _load_openrouter,
}


def get_loader(name: str) -> ProviderLoader:
    """Look up the loader for a registered provider name.

    Args:
        name: Provider name, matching `API_PROVIDER` (e.g. "openrouter").

    Returns:
        The zero-argument loader callable for that provider.

    Raises:
        KeyError: If `name` is not registered. Callers that need an
            actionable, user-facing error for a missing/unimplemented
            provider module (as opposed to an unregistered name) add that
            handling via `resolve_provider()`.
    """
    return PROVIDER_REGISTRY[name]


def resolve_provider(name: str) -> Any:
    """Resolve a provider name to its loaded implementation module/object.

    This is the single point where a provider actually gets loaded. It
    keeps `KeyError` (unregistered `API_PROVIDER` value -- a configuration
    typo) distinct from "registered but its module file is missing",
    which raises an actionable `RuntimeError` instead of a bare
    `ModuleNotFoundError`/`ImportError`.

    Args:
        name: Provider name, matching `API_PROVIDER` (e.g. "openrouter").

    Returns:
        The loaded provider module/object satisfying the `Provider`
        Protocol in `backend/providers/base.py`.

    Raises:
        KeyError: If `name` is not registered in `PROVIDER_REGISTRY`.
        RuntimeError: If `name` is registered but its loader fails because
            the backing module is not present in this build.
    """
    loader = get_loader(name)
    try:
        return loader()
    except (ModuleNotFoundError, ImportError) as exc:
        raise RuntimeError(
            f"Provider '{name}' is not available in this build "
            f"(underlying module failed to import: {exc}). "
            "Set API_PROVIDER=openrouter to use the built-in provider, "
            f"or vendor backend/providers/{name}.py yourself."
        ) from exc
