"""Tests for wiring the provider registry into config.py/openrouter.py.

Covers:
- VC2: with `API_PROVIDER=openrouter`, `import backend.main` and
  `import mcp_server.server` succeed (subprocess-based, real process
  isolation for env vars).
- VC3: resolving a registered-but-absent provider module produces an
  actionable `RuntimeError` naming `API_PROVIDER=openrouter` as the fix,
  not a bare `ModuleNotFoundError`/`KeyError`.
"""

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _run_import_check(module_name: str, tmp_env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=REPO_ROOT,
        env=tmp_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _openrouter_env(monkeypatch_env: dict) -> dict:
    env = dict(monkeypatch_env)
    env["API_PROVIDER"] = "openrouter"
    env.setdefault("OPENROUTER_API_KEY", "test-key-for-import-check")
    return env


def test_vc2_import_backend_main_succeeds():
    import os

    env = _openrouter_env(os.environ)
    result = _run_import_check("backend.main", env)
    assert result.returncode == 0, (
        f"import backend.main failed with API_PROVIDER=openrouter.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_vc2_import_mcp_server_succeeds():
    import os

    env = _openrouter_env(os.environ)
    result = _run_import_check("mcp_server.server", env)
    assert result.returncode == 0, (
        f"import mcp_server.server failed with API_PROVIDER=openrouter.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_vc3_resolve_provider_missing_module_raises_actionable_runtime_error(monkeypatch):
    """A registered provider whose backing module cannot be imported raises
    a RuntimeError naming API_PROVIDER=openrouter, not a bare
    ModuleNotFoundError/ImportError.
    """
    from backend.providers import registry

    def _raise_module_not_found():
        raise ModuleNotFoundError("No module named 'backend.providers.does_not_exist'")

    monkeypatch.setitem(registry.PROVIDER_REGISTRY, "does_not_exist_provider", _raise_module_not_found)

    with pytest.raises(RuntimeError) as exc_info:
        registry.resolve_provider("does_not_exist_provider")

    message = str(exc_info.value)
    assert "API_PROVIDER=openrouter" in message
    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)


def test_vc3_resolve_provider_unregistered_name_still_raises_keyerror():
    """A genuinely unregistered provider name is a different failure mode
    (configuration typo) and must not be swallowed into the RuntimeError path.
    """
    from backend.providers import registry

    with pytest.raises(KeyError):
        registry.resolve_provider("totally-unregistered-provider-name")
