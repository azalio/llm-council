"""Shared pytest fixtures for the llm-council test suite.

The autouse `isolated_db` fixture points storage at a per-test temp SQLite DB so
that direct run_full_council / record_run_timing / record_council_metrics calls
(used by tests that don't go through the api_client TestClient) cannot leak
zero-duration rows into the real data/council.db and corrupt durable ETA
statistics. Every test file inherits this isolation without opt-in.
"""

from __future__ import annotations

import importlib
import sys
import threading

import pytest


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Isolate the SQLite DB for every test (autouse).

    Per-test tmp_path; closes any stale thread-local connection bound to a
    previous DB_PATH, re-creates the schema, and restores the thread-local on
    teardown. This is the single source of DB isolation — test modules no longer
    need their own api_client/iso fixture for direct council/storage calls.
    """
    db_path = tmp_path / "data" / "council.db"
    monkeypatch.setenv("LLM_COUNCIL_ROOT", str(tmp_path))

    import backend.config as backend_config

    monkeypatch.setattr(backend_config, "DB_PATH", str(db_path))

    storage = importlib.import_module("backend.storage")
    existing_conn = getattr(storage._local, "conn", None)
    if existing_conn is not None:
        existing_conn.close()
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    storage._local = threading.local()
    storage._ensure_schema()

    yield

    conn = getattr(storage._local, "conn", None)
    if conn is not None:
        conn.close()
    storage._local = threading.local()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """A FastAPI TestClient against the isolated DB.

    Reloads backend.main so the app picks up the patched DB_PATH, then yields
    (client, backend_main). Defined here so any test module can use it without
    re-implementing the DB-isolation boilerplate.
    """
    db_path = tmp_path / "data" / "council.db"
    monkeypatch.setenv("LLM_COUNCIL_ROOT", str(tmp_path))

    import backend.config as backend_config

    monkeypatch.setattr(backend_config, "DB_PATH", str(db_path))

    storage = importlib.import_module("backend.storage")
    existing_conn = getattr(storage._local, "conn", None)
    if existing_conn is not None:
        existing_conn.close()
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    storage._local = threading.local()
    storage._ensure_schema()

    if "backend.main" in sys.modules:
        backend_main = importlib.reload(sys.modules["backend.main"])
    else:
        backend_main = importlib.import_module("backend.main")

    from fastapi.testclient import TestClient

    with TestClient(backend_main.app) as client:
        yield client, backend_main

    conn = getattr(storage._local, "conn", None)
    if conn is not None:
        conn.close()
    storage._local = threading.local()
