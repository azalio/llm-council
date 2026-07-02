"""SQLite-based storage for conversations."""

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DB_PATH

# Thread-local storage for SQLite connections
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def _ensure_schema():
    """Create tables if they don't exist (idempotent)."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id            TEXT PRIMARY KEY,
            created_at    TEXT NOT NULL,
            title         TEXT NOT NULL DEFAULT 'New Conversation',
            message_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            position        INTEGER NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT,
            stage1          TEXT,
            stage2          TEXT,
            stage3          TEXT,
            stage2a         TEXT,
            stage2b         TEXT,
            metadata        TEXT,
            UNIQUE(conversation_id, position)
        );

        CREATE TABLE IF NOT EXISTS runs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id        TEXT NOT NULL,
            conversation_id   TEXT,
            deliberation_mode TEXT NOT NULL,
            duration_ms       INTEGER NOT NULL,
            stage1_ms         INTEGER,
            stage2_ms         INTEGER,
            stage3_ms         INTEGER,
            stage2a_ms        INTEGER,
            stage2b_ms        INTEGER,
            started_at_epoch  REAL NOT NULL,
            completed         INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_runs_mode_epoch
            ON runs(deliberation_mode, started_at_epoch DESC);

        CREATE INDEX IF NOT EXISTS idx_runs_request_id
            ON runs(request_id);
    """)
    conn.commit()

    # Idempotent migration: add summary column
    try:
        conn.execute("ALTER TABLE conversations ADD COLUMN summary TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Idempotent migration: add assistant message metadata column
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN metadata TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists


# Initialize schema on module import
_ensure_schema()


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    conn = _get_conn()
    created_at = datetime.utcnow().isoformat()
    title = "New Conversation"

    conn.execute(
        "INSERT INTO conversations (id, created_at, title, message_count) VALUES (?, ?, ?, 0)",
        (conversation_id, created_at, title),
    )
    conn.commit()

    return {
        "id": conversation_id,
        "created_at": created_at,
        "title": title,
        "messages": [],
    }


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a conversation from storage.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        Conversation dict or None if not found
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, created_at, title, summary FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()

    if row is None:
        return None

    messages = conn.execute(
        "SELECT role, content, stage1, stage2, stage3, stage2a, stage2b, metadata "
        "FROM messages WHERE conversation_id = ? ORDER BY position",
        (conversation_id,),
    ).fetchall()

    msg_list = []
    for m in messages:
        if m["role"] == "user":
            msg_list.append({"role": "user", "content": m["content"]})
        else:
            msg = {"role": "assistant"}
            for field in ("stage1", "stage2", "stage3", "stage2a", "stage2b"):
                val = m[field]
                if val is not None:
                    msg[field] = json.loads(val)
            if m["metadata"] is not None:
                msg["metadata"] = json.loads(m["metadata"])
            msg_list.append(msg)

    result = {
        "id": row["id"],
        "created_at": row["created_at"],
        "title": row["title"],
        "messages": msg_list,
    }
    if row["summary"] is not None:
        result["summary"] = row["summary"]
    return result


def _loads_json(value: Optional[str], fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


def find_completed_answer_candidates(limit: int = 200) -> List[Dict[str, Any]]:
    """Return recent first-turn pairs that have a completed Stage 3 answer."""
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT
            u.conversation_id,
            u.position AS user_position,
            u.content AS question,
            a.stage1,
            a.stage2,
            a.stage3,
            a.stage2a,
            a.stage2b,
            a.metadata
        FROM messages AS u
        JOIN messages AS a
            ON a.conversation_id = u.conversation_id
           AND a.position = u.position + 1
           AND a.role = 'assistant'
        WHERE u.role = 'user'
          AND u.position = 0
          AND u.content IS NOT NULL
          AND a.stage3 IS NOT NULL
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "conversation_id": row["conversation_id"],
            "user_position": row["user_position"],
            "question": row["question"],
            "stage1": _loads_json(row["stage1"], []),
            "stage2": _loads_json(row["stage2"], []),
            "stage3": _loads_json(row["stage3"], {}),
            "stage2a": _loads_json(row["stage2a"], None),
            "stage2b": _loads_json(row["stage2b"], None),
            "metadata": _loads_json(row["metadata"], {}),
        }
        for row in rows
    ]


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only).

    Returns:
        List of conversation metadata dicts sorted newest first
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, created_at, title, message_count "
        "FROM conversations ORDER BY created_at DESC"
    ).fetchall()

    return [
        {
            "id": r["id"],
            "created_at": r["created_at"],
            "title": r["title"],
            "message_count": r["message_count"],
        }
        for r in rows
    ]


def add_user_message(conversation_id: str, content: str):
    """
    Add a user message to a conversation.

    Args:
        conversation_id: Conversation identifier
        content: User message content
    """
    conn = _get_conn()

    # Get next position
    row = conn.execute(
        "SELECT message_count FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    position = row["message_count"]

    conn.execute(
        "INSERT INTO messages (conversation_id, position, role, content) VALUES (?, ?, 'user', ?)",
        (conversation_id, position, content),
    )
    conn.execute(
        "UPDATE conversations SET message_count = message_count + 1 WHERE id = ?",
        (conversation_id,),
    )
    conn.commit()


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    stage2a: Optional[List[Dict[str, Any]]] = None,
    stage2b: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Add an assistant message with all stages to a conversation.

    Args:
        conversation_id: Conversation identifier
        stage1: List of individual model responses
        stage2: List of model rankings
        stage3: Final synthesized response
        stage2a: Optional list of critiques (thorough mode)
        stage2b: Optional list of revisions (thorough mode)
        metadata: Optional persisted UI metadata for the assistant turn
    """
    conn = _get_conn()

    row = conn.execute(
        "SELECT message_count FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    position = row["message_count"]

    conn.execute(
        "INSERT INTO messages (conversation_id, position, role, stage1, stage2, stage3, stage2a, stage2b, metadata) "
        "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?)",
        (
            conversation_id,
            position,
            json.dumps(stage1),
            json.dumps(stage2),
            json.dumps(stage3),
            json.dumps(stage2a) if stage2a is not None else None,
            json.dumps(stage2b) if stage2b is not None else None,
            json.dumps(metadata) if metadata is not None else None,
        ),
    )
    conn.execute(
        "UPDATE conversations SET message_count = message_count + 1 WHERE id = ?",
        (conversation_id,),
    )
    conn.commit()


def update_conversation_title(conversation_id: str, title: str):
    """
    Update the title of a conversation.

    Args:
        conversation_id: Conversation identifier
        title: New title for the conversation
    """
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE conversations SET title = ? WHERE id = ?",
        (title, conversation_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"Conversation {conversation_id} not found")
    conn.commit()


def update_conversation_summary(conversation_id: str, summary: str):
    """
    Update the rolling summary of a conversation.

    Args:
        conversation_id: Conversation identifier
        summary: New rolling summary text
    """
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE conversations SET summary = ? WHERE id = ?",
        (summary, conversation_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"Conversation {conversation_id} not found")
    conn.commit()


def get_conversation_summary(conversation_id: str) -> Optional[str]:
    """
    Get just the summary of a conversation (lightweight, no messages).

    Args:
        conversation_id: Conversation identifier

    Returns:
        Summary text or None if not found or no summary
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT summary FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return row["summary"]


def delete_conversation(conversation_id: str):
    """
    Delete a conversation and all its messages.

    Args:
        conversation_id: Conversation identifier
    """
    conn = _get_conn()
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    conn.commit()


# Import here to avoid a config/storage import cycle at module load. The pruning
# cap is read lazily so storage remains importable before config is fully wired.
def _eta_max_run_rows() -> int:
    from .config import COUNCIL_ETA_MAX_RUN_ROWS
    return COUNCIL_ETA_MAX_RUN_ROWS


def record_run_timing(row: Dict[str, Any]) -> None:
    """
    Persist one council-run timing row to the durable `runs` table.

    Idempotent on `request_id` (first measurement wins) so a call-site regression
    cannot silently double-write. Best-effort: swallows operational errors so a
    stats write never breaks a council run. Amortized pruning keeps the table
    bounded by COUNCIL_ETA_MAX_RUN_ROWS.
    """
    try:
        conn = _get_conn()
        request_id = row.get("request_id")
        if request_id:
            existing = conn.execute(
                "SELECT 1 FROM runs WHERE request_id = ? LIMIT 1",
                (request_id,),
            ).fetchone()
            if existing is not None:
                return  # first measurement wins
        conn.execute(
            """
            INSERT INTO runs (
                request_id, conversation_id, deliberation_mode, duration_ms,
                stage1_ms, stage2_ms, stage3_ms, stage2a_ms, stage2b_ms,
                started_at_epoch, completed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("request_id"),
                row.get("conversation_id"),
                row.get("deliberation_mode"),
                int(row.get("duration_ms") or 0),
                row.get("stage1_ms"),
                row.get("stage2_ms"),
                row.get("stage3_ms"),
                row.get("stage2a_ms"),
                row.get("stage2b_ms"),
                float(row.get("started_at_epoch") or 0.0),
                1 if row.get("completed", True) else 0,
            ),
        )
        conn.commit()

        # Amortized prune: only trim once we exceed 110% of the cap so steady
        # state doesn't pay a DELETE on every insert.
        cap = _eta_max_run_rows()
        try:
            count = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
            if count > int(cap * 1.1):
                conn.execute(
                    "DELETE FROM runs WHERE id NOT IN "
                    "(SELECT id FROM runs ORDER BY started_at_epoch DESC LIMIT ?)",
                    (cap,),
                )
                conn.commit()
        except sqlite3.Error:
            pass  # pruning is best-effort
    except sqlite3.Error:
        pass  # ETA is observability; never break a council run over a stats write


def fetch_recent_run_durations(
    deliberation_mode: str,
    *,
    limit: int,
    completed_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Return the last `limit` run rows for a deliberation mode, newest first.

    Used by the ETA read path to compute percentile expected-wait. Each row
    carries whole-run `duration_ms` and per-stage ms (nullable when a stage did
    not run for that mode).
    """
    conn = _get_conn()
    if completed_only:
        rows = conn.execute(
            """
            SELECT request_id, deliberation_mode, duration_ms,
                   stage1_ms, stage2_ms, stage3_ms, stage2a_ms, stage2b_ms,
                   started_at_epoch, completed
            FROM runs
            WHERE deliberation_mode = ? AND completed = 1
            ORDER BY started_at_epoch DESC
            LIMIT ?
            """,
            (deliberation_mode, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT request_id, deliberation_mode, duration_ms,
                   stage1_ms, stage2_ms, stage3_ms, stage2a_ms, stage2b_ms,
                   started_at_epoch, completed
            FROM runs
            WHERE deliberation_mode = ?
            ORDER BY started_at_epoch DESC
            LIMIT ?
            """,
            (deliberation_mode, limit),
        ).fetchall()
    return [dict(r) for r in rows]
