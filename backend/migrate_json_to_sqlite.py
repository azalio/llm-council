#!/usr/bin/env python3
"""One-time migration: JSON conversation files → SQLite.

Usage:
    python -m backend.migrate_json_to_sqlite
"""

import json
import os
import shutil
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "LLM_COUNCIL_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from backend.config import DATA_DIR, DB_PATH  # noqa: E402
from backend import storage  # noqa: E402


def migrate():
    if not os.path.isdir(DATA_DIR):
        print(f"No JSON directory found at {DATA_DIR}, nothing to migrate.")
        return

    json_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
    if not json_files:
        print("No JSON files found, nothing to migrate.")
        return

    print(f"Found {len(json_files)} JSON conversation files in {DATA_DIR}")
    print(f"Target database: {DB_PATH}")

    migrated = 0
    skipped = 0

    for filename in sorted(json_files):
        path = os.path.join(DATA_DIR, filename)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  SKIP {filename}: {e}")
            skipped += 1
            continue

        conv_id = data.get("id", filename.replace(".json", ""))
        created_at = data.get("created_at", "")
        title = data.get("title", "New Conversation")
        messages = data.get("messages", [])

        # Check if already migrated
        existing = storage.get_conversation(conv_id)
        if existing is not None:
            print(f"  SKIP {conv_id}: already exists in SQLite")
            skipped += 1
            continue

        # Insert conversation
        conn = storage._get_conn()
        conn.execute(
            "INSERT INTO conversations (id, created_at, title, message_count) VALUES (?, ?, ?, ?)",
            (conv_id, created_at, title, len(messages)),
        )

        # Insert messages
        for position, msg in enumerate(messages):
            role = msg.get("role", "user")
            if role == "user":
                conn.execute(
                    "INSERT INTO messages (conversation_id, position, role, content) VALUES (?, ?, ?, ?)",
                    (conv_id, position, role, msg.get("content", "")),
                )
            else:
                conn.execute(
                    "INSERT INTO messages (conversation_id, position, role, stage1, stage2, stage3) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        conv_id,
                        position,
                        role,
                        json.dumps(msg.get("stage1")) if "stage1" in msg else None,
                        json.dumps(msg.get("stage2")) if "stage2" in msg else None,
                        json.dumps(msg.get("stage3")) if "stage3" in msg else None,
                    ),
                )

        conn.commit()
        migrated += 1
        print(f"  OK   {conv_id} ({len(messages)} messages)")

    print(f"\nMigration complete: {migrated} migrated, {skipped} skipped")

    # Rename old directory
    backup_dir = DATA_DIR.rstrip("/") + "_backup"
    if migrated > 0 and not os.path.exists(backup_dir):
        shutil.move(DATA_DIR, backup_dir)
        print(f"Renamed {DATA_DIR} → {backup_dir}")
    elif migrated > 0:
        print(f"Backup directory {backup_dir} already exists, leaving JSON files in place")


if __name__ == "__main__":
    migrate()
