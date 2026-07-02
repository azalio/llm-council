"""Code reviewer using a single LLM model (gpt-5.1 by default)."""

import json
import os
from typing import Optional, List, Dict

from .types import ReviewRequest
from .prompts import build_review_prompt
from .git_utils import GitUtils
from .storage import ReviewStorageManager

from backend.openrouter import query_model
from backend.config import CODE_REVIEW_MODEL


class CouncilCodeReviewer:
    """Code reviewer using single model (gpt-5.1) for fast, high-quality reviews."""

    def __init__(self, working_dir: str):
        """Initialize code reviewer.

        Args:
            working_dir: Working directory for git operations and review storage
        """
        self.working_dir = working_dir
        self.git = GitUtils(working_dir)
        self.storage = ReviewStorageManager(os.path.join(working_dir, ".reviews"))

    async def request_review(self, request: ReviewRequest) -> Dict:
        """Request a code review using single model (gpt-5.1).

        Args:
            request: Review request parameters

        Returns:
            Complete review result

        Raises:
            ValueError: If not a git repository or no changes to review
        """
        # Check git repository
        if not self.git.is_git_repository():
            raise ValueError(f"Not a git repository: {self.working_dir}")

        # Get git info - use base_branch if provided for committed changes
        # Use files filter if provided (for large diffs)
        base_branch = request.base_branch
        file_filter = request.files
        git_diff = self.git.get_git_diff(base_branch, file_filter)
        if not git_diff.strip():
            if file_filter:
                raise ValueError(f"No changes in specified files: {', '.join(file_filter)}")
            if base_branch:
                raise ValueError(f"No changes between {base_branch} and HEAD")
            raise ValueError("No changes to review (git diff is empty). Use base_branch parameter for committed changes.")

        changed_files = self.git.get_changed_files(base_branch, file_filter)
        branch = self.git.get_current_branch()

        # Create or continue session
        if request.previous_review_id:
            session = self.storage.get_review_session(request.previous_review_id)
            if not session:
                raise ValueError(
                    f"Previous review not found: {request.previous_review_id}"
                )
            review_id = request.previous_review_id
        else:
            session = self.storage.create_review_session(request.model_dump())
            review_id = session["review_id"]
            self.storage.save_git_diff(review_id, git_diff)

        # Build review prompt
        previous_rounds = (
            session.get("rounds", []) if request.previous_review_id else None
        )

        review_prompt = build_review_prompt(
            summary=request.summary,
            git_diff=git_diff,
            changed_files=changed_files,
            focus_areas=request.focus_areas,
            relevant_docs=request.relevant_docs,
            test_command=request.test_command,
            previous_rounds=previous_rounds,
        )

        # Single model review using CODE_REVIEW_MODEL (gpt-5.1)
        response = await query_model(
            CODE_REVIEW_MODEL, [{"role": "user", "content": review_prompt}]
        )

        if not response or not response.get("content"):
            raise ValueError(f"No response from {CODE_REVIEW_MODEL}")

        # Parse JSON response
        review_data = self._parse_review_response(response)

        # Determine status from review data
        status = self._determine_status(review_data)

        # Build review result
        result = {
            "review_id": review_id,
            "status": status,
            "branch": branch,
            "model": CODE_REVIEW_MODEL,
            **review_data,
            "summary": {
                "total_comments": len(review_data.get("comments", [])),
                "by_severity": self._count_by_severity(
                    review_data.get("comments", [])
                ),
                "critical_issues": [
                    c.get("title", c.get("description", "Unknown"))
                    for c in review_data.get("comments", [])
                    if c.get("severity") == "critical"
                ],
            },
        }

        # Save result
        saved_result = self.storage.save_review_result(review_id, result)

        return saved_result

    def _parse_review_response(self, response: Optional[Dict]) -> Dict:
        """Parse the model's JSON response.

        Args:
            response: Raw response from query_model

        Returns:
            Parsed review data or fallback structure
        """
        if not response or not response.get("content"):
            return self._fallback_review_data("No response from model")

        content = response["content"].strip()

        # Try to extract JSON from markdown code blocks
        # The JSON may contain nested ``` in suggestion fields, so we need
        # to find the LAST closing ``` for the outer block
        if content.startswith("```json"):
            # Find the last ``` which should be the closing marker
            last_marker = content.rfind("```")
            if last_marker > 7:  # Must be after opening ```json
                content = content[7:last_marker].strip()
        elif content.startswith("```"):
            last_marker = content.rfind("```")
            if last_marker > 3:
                content = content[3:last_marker].strip()

        # Try direct JSON parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Fallback: try to find JSON object in content
        # Look for first { and last } to extract JSON
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(content[first_brace:last_brace + 1])
            except json.JSONDecodeError:
                pass

        return self._fallback_review_data(content[:500])

    def _fallback_review_data(self, raw_response: str) -> Dict:
        """Create fallback review structure when JSON parsing fails.

        Args:
            raw_response: Raw response text for reference

        Returns:
            Minimal review structure matching expected format
        """
        return {
            "summary": "Could not parse structured review. See raw response.",
            "design_compliance": {"compliant": True, "issues": []},
            "comments": [],
            "missing_requirements": [],
            "test_results": {"new_tests_present": False, "issues": []},
            "positive_aspects": [],
            "overall_assessment": {
                "verdict": "needs_discussion",
                "risk_level": "medium",
                "confidence": "low",
            },
            "raw_response": raw_response,
        }

    def _determine_status(self, review_data: Dict) -> str:
        """Determine review status from review data.

        Args:
            review_data: Parsed review data

        Returns:
            Status string: 'approved', 'needs_changes', or 'in_progress'
        """
        assessment = review_data.get("overall_assessment", {})
        verdict = assessment.get("verdict", "needs_discussion")

        if verdict == "approve":
            return "approved"
        elif verdict == "request_changes":
            return "needs_changes"
        else:
            return "in_progress"

    def _count_by_severity(self, comments: List[Dict]) -> Dict[str, int]:
        """Count comments by severity level.

        Args:
            comments: List of review comments

        Returns:
            Dict mapping severity to count
        """
        counts = {"critical": 0, "major": 0, "minor": 0, "nitpick": 0}
        for c in comments:
            severity = c.get("severity", "minor")
            counts[severity] = counts.get(severity, 0) + 1
        return counts

    def get_review_history(self, limit: int = 5) -> List[Dict]:
        """Get recent review sessions.

        Args:
            limit: Maximum number of sessions to return

        Returns:
            List of review sessions
        """
        return self.storage.get_review_history(limit)

    def get_review_session(self, review_id: str) -> Optional[Dict]:
        """Get a specific review session.

        Args:
            review_id: Review session ID

        Returns:
            Session data or None if not found
        """
        return self.storage.get_review_session(review_id)

    def mark_complete(
        self,
        review_id: str,
        status: str,
        notes: Optional[str] = None,
    ) -> Dict:
        """Mark a review as complete.

        Args:
            review_id: Review session ID
            status: Final status ('approved', 'abandoned', 'merged')
            notes: Optional final notes

        Returns:
            Updated session data
        """
        return self.storage.mark_review_complete(review_id, status, notes)
