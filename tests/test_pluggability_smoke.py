"""Smoke test: OpenRouter-only startup works end to end.

This repo only ships the OpenRouter provider (see
`backend/providers/registry.py`), so this test proves module imports and the
full test suite pass with `API_PROVIDER=openrouter` in a fresh interpreter,
isolated from the current process's already-imported module cache.
"""

import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

SUBPROCESS_TIMEOUT_SECONDS = 120


def _openrouter_env() -> dict:
    env = dict(os.environ)
    env["API_PROVIDER"] = "openrouter"
    env.setdefault("OPENROUTER_API_KEY", "test-key-for-pluggability-smoke")
    return env


def _run(args, env, timeout=SUBPROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_openrouter_only_startup_and_full_suite():
    """`import backend.main` / `import mcp_server.server` and the full test
    suite succeed with API_PROVIDER=openrouter in a fresh interpreter.
    """
    env = _openrouter_env()

    main_result = _run([sys.executable, "-c", "import backend.main"], env)
    assert main_result.returncode == 0, (
        "`import backend.main` failed with API_PROVIDER=openrouter.\n"
        f"stdout: {main_result.stdout}\nstderr: {main_result.stderr}"
    )

    mcp_result = _run([sys.executable, "-c", "import mcp_server.server"], env)
    assert mcp_result.returncode == 0, (
        "`import mcp_server.server` failed with API_PROVIDER=openrouter.\n"
        f"stdout: {mcp_result.stdout}\nstderr: {mcp_result.stderr}"
    )

    pytest_result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "tests/",
            "--ignore",
            str(pathlib.Path(__file__)),
        ],
        env,
        timeout=300,
    )
    assert pytest_result.returncode == 0, (
        "Full test suite failed with API_PROVIDER=openrouter.\n"
        f"stdout: {pytest_result.stdout}\nstderr: {pytest_result.stderr}"
    )
