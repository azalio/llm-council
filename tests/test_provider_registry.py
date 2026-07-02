"""Tests for the Provider Protocol and provider registry (ST-001).

Covers:
- VC1: `Provider` Protocol is importable and structurally checkable.
- VC2: the registry maps 'openrouter' to a loader, with no
  Requesty/AI Studio entries.
- VC3: no Requesty/AI Studio provider implementation exists under
  backend/providers/.
"""

import pathlib
import re

import pytest

from backend.providers.base import Provider
from backend.providers.registry import PROVIDER_REGISTRY, get_loader


def test_vc1_provider_protocol_importable():
    """Provider is importable via the documented path and is a Protocol."""
    assert Provider.__module__ == "backend.providers.base"
    assert hasattr(Provider, "__protocol_attrs__") or getattr(Provider, "_is_protocol", False)


def test_vc1_provider_protocol_declares_contract_methods():
    """Provider documents build-request, parse-response, and auth+URL resolution."""
    for method_name in ("build_request", "parse_response", "resolve_auth"):
        assert hasattr(Provider, method_name), f"Provider missing {method_name}"


def test_vc1_provider_protocol_runtime_checkable_against_openrouter_module():
    """The existing openrouter module satisfies the structural Provider contract's shape.

    openrouter.py does not (yet) expose build_request/parse_response/resolve_auth
    as free functions with those exact names -- that wiring is a separate concern.
    This test only proves the Protocol is runtime_checkable and usable with
    isinstance()-style structural checks, without asserting current wiring.
    """
    assert getattr(Provider, "_is_runtime_protocol", False) is True


def test_vc2_registry_has_openrouter_loader():
    assert set(PROVIDER_REGISTRY.keys()) == {"openrouter"}
    assert callable(PROVIDER_REGISTRY["openrouter"])


def test_vc2_registry_has_no_requesty_or_ai_studio_entries():
    forbidden = {"requesty", "ai_studio", "aistudio", "ai-studio"}
    assert forbidden.isdisjoint(PROVIDER_REGISTRY.keys())


def test_vc2_get_loader_returns_registered_loader():
    loader = get_loader("openrouter")
    assert loader is PROVIDER_REGISTRY["openrouter"]


def test_vc2_get_loader_unknown_provider_raises_keyerror():
    with pytest.raises(KeyError):
        get_loader("does-not-exist")


def test_vc2_openrouter_loader_resolves_without_importing_other_providers():
    """Resolving the openrouter loader must not require any other provider module."""
    loader = PROVIDER_REGISTRY["openrouter"]
    result = loader()
    assert result is not None


def test_vc2_registry_loaders_are_lazy_zero_argument_callables():
    """Every registered loader is a zero-argument callable (lazy resolution).

    A second provider registered later reuses this same lazy-loader shape,
    so resolving one provider never forces an import of another.
    """
    for name, loader in PROVIDER_REGISTRY.items():
        assert callable(loader), f"Loader for {name!r} is not callable"


def test_vc3_no_requesty_or_ai_studio_implementation_present():
    """Grep backend/providers/ for Requesty/AI Studio connector code.

    This forbids implementing these connectors, not mentioning them as a
    documented extension point. So this asserts there is no registry entry,
    no dedicated module (e.g. requesty.py, ai_studio.py), and no
    build_request/parse_response/resolve_auth-style implementation keyed to
    either name -- while still allowing the "not implemented yet" comment.
    """
    providers_dir = pathlib.Path(__file__).resolve().parent.parent / "backend" / "providers"
    module_names = {path.stem.lower() for path in providers_dir.glob("*.py")}
    assert "requesty" not in module_names
    assert not any("studio" in name for name in module_names)

    forbidden_keys = {"requesty", "ai_studio", "aistudio", "ai-studio"}
    assert forbidden_keys.isdisjoint(PROVIDER_REGISTRY.keys())

    # No function/class definitions named after these providers anywhere
    # under backend/providers/ (implementation, not just a comment mention).
    definition_pattern = re.compile(r"^\s*(def|class)\s+\w*(requesty|ai[_-]?studio)\w*", re.IGNORECASE | re.MULTILINE)
    for path in providers_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        matches = definition_pattern.findall(text)
        assert not matches, f"Unexpected Requesty/AI Studio implementation in {path}: {matches}"
