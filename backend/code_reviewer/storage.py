"""Storage manager for code review sessions."""

import json
import re
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

# Valid review_id format: YYYY-MM-DD-NNN (e.g., 2025-01-15-001)
REVIEW_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{3}$")


def _validate_review_id(review_id: str) -> str:
    """Validate review_id format to prevent path traversal.

    Args:
        review_id: Review session ID to validate

    Returns:
        Validated review_id

    Raises:
        ValueError: If review_id format is invalid
    """
    if not review_id or not isinstance(review_id, str):
        raise ValueError("Review ID must be a non-empty string")

    review_id = review_id.strip()

    if not REVIEW_ID_PATTERN.match(review_id):
        raise ValueError(
            f"Invalid review_id format: {review_id}. "
            "Expected format: YYYY-MM-DD-NNN (e.g., 2025-01-15-001)"
        )

    return review_id


class ReviewStorageManager:
    """Manages storage for code review sessions in .reviews/ directory."""

    def __init__(self, base_path: str = ".reviews"):
        """Initialize storage manager.

        Args:
            base_path: Base directory for storing reviews (default: .reviews)
        """
        self.base_path = Path(base_path)
        self.sessions_path = self.base_path / "sessions"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.sessions_path.mkdir(parents=True, exist_ok=True)

    def _generate_review_id(self) -> str:
        """Generate a unique review ID in format YYYY-MM-DD-NNN.

        Returns:
            Unique review ID string
        """
        today = datetime.now().strftime("%Y-%m-%d")
        existing = list(self.sessions_path.glob(f"{today}-*"))
        next_num = len(existing) + 1
        return f"{today}-{next_num:03d}"

    def create_review_session(self, request: Dict) -> Dict:
        """Create a new review session.

        Args:
            request: Review request data

        Returns:
            Created session data including review_id
        """
        review_id = self._generate_review_id()
        session_dir = self.sessions_path / review_id
        session_dir.mkdir(parents=True, exist_ok=True)

        session = {
            "review_id": review_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "status": "in_progress",
            "rounds": [],
            "request": request,
        }

        self._save_session(review_id, session)
        return session

    def _save_session(self, review_id: str, session: Dict) -> None:
        """Save session data to JSON file.

        Args:
            review_id: Review session ID
            session: Session data to save
        """
        session_file = self.sessions_path / review_id / "session.json"
        with open(session_file, "w") as f:
            json.dump(session, f, indent=2)

    def get_review_session(self, review_id: str) -> Optional[Dict]:
        """Get a review session by ID.

        Args:
            review_id: Review session ID

        Returns:
            Session data or None if not found

        Raises:
            ValueError: If review_id format is invalid
        """
        review_id = _validate_review_id(review_id)
        session_file = self.sessions_path / review_id / "session.json"
        if not session_file.exists():
            return None
        with open(session_file) as f:
            return json.load(f)

    def save_review_result(self, review_id: str, result: Dict) -> Dict:
        """Save a review round result.

        Args:
            review_id: Review session ID
            result: Review result data

        Returns:
            Updated result with round number and timestamp

        Raises:
            ValueError: If session not found or review_id format is invalid
        """
        review_id = _validate_review_id(review_id)
        session = self.get_review_session(review_id)
        if not session:
            raise ValueError(f"Session {review_id} not found")

        round_num = len(session["rounds"]) + 1
        result["round"] = round_num
        result["review_id"] = review_id
        result["timestamp"] = datetime.now().isoformat()

        # Save round file
        round_dir = self.sessions_path / review_id / f"round-{round_num}"
        round_dir.mkdir(exist_ok=True)
        with open(round_dir / "review.json", "w") as f:
            json.dump(result, f, indent=2)

        # Update session
        session["rounds"].append(result)
        session["updated_at"] = datetime.now().isoformat()
        session["status"] = result.get("status", "in_progress")
        self._save_session(review_id, session)

        return result

    def save_git_diff(self, review_id: str, diff: str) -> None:
        """Save git diff to file.

        Args:
            review_id: Review session ID
            diff: Git diff content

        Raises:
            ValueError: If review_id format is invalid
        """
        review_id = _validate_review_id(review_id)
        diff_file = self.sessions_path / review_id / "changes.diff"
        with open(diff_file, "w") as f:
            f.write(diff)

    def get_review_history(self, limit: int = 5) -> List[Dict]:
        """Get recent review sessions.

        Args:
            limit: Maximum number of sessions to return

        Returns:
            List of session data, most recent first
        """
        sessions = []
        session_dirs = sorted(self.sessions_path.iterdir(), reverse=True)

        for session_dir in session_dirs:
            if session_dir.is_dir():
                try:
                    session = self.get_review_session(session_dir.name)
                    if session:
                        sessions.append(session)
                except ValueError:
                    # Skip directories with invalid names (e.g., malformed or malicious)
                    continue
                if len(sessions) >= limit:
                    break

        return sessions

    def mark_review_complete(
        self,
        review_id: str,
        status: str,
        notes: Optional[str] = None,
    ) -> Dict:
        """Mark a review session as complete.

        Args:
            review_id: Review session ID
            status: Final status ('approved', 'abandoned', 'merged')
            notes: Optional final notes

        Returns:
            Updated session data

        Raises:
            ValueError: If session not found or review_id format is invalid
        """
        review_id = _validate_review_id(review_id)
        session = self.get_review_session(review_id)
        if not session:
            raise ValueError(f"Session {review_id} not found")

        session["status"] = status
        session["updated_at"] = datetime.now().isoformat()

        if notes:
            notes_file = self.sessions_path / review_id / "final-notes.txt"
            with open(notes_file, "w") as f:
                f.write(notes)

        self._save_session(review_id, session)
        return session
