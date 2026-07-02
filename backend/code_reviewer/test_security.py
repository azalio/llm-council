"""Security tests for code_reviewer module."""

import pytest
import tempfile

from .storage import ReviewStorageManager, _validate_review_id
from .git_utils import _sanitize_git_error


class TestReviewIdValidation:
    """Tests for review_id validation to prevent path traversal."""

    def test_valid_review_id(self):
        """Valid review IDs should pass validation."""
        assert _validate_review_id("2025-01-15-001") == "2025-01-15-001"
        assert _validate_review_id("2024-12-31-999") == "2024-12-31-999"
        assert _validate_review_id("  2025-01-15-001  ") == "2025-01-15-001"

    def test_path_traversal_attempt(self):
        """Path traversal attempts should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("../../../etc/passwd")

    def test_path_traversal_with_valid_prefix(self):
        """Path traversal disguised with valid-looking prefix should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("2025-01-15-001/../../../etc/passwd")

    def test_directory_traversal_dot_dot(self):
        """Directory traversal with .. should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("..%2F..%2Fetc%2Fpasswd")

    def test_empty_review_id(self):
        """Empty review ID should be rejected."""
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_review_id("")

    def test_none_review_id(self):
        """None review ID should be rejected."""
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_review_id(None)

    def test_whitespace_only_review_id(self):
        """Whitespace-only review ID should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("   ")

    def test_invalid_format_wrong_separator(self):
        """Wrong separator should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("2025/01/15/001")

    def test_invalid_format_wrong_length(self):
        """Wrong number length should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("2025-01-15-01")  # Too short
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("2025-01-15-0001")  # Too long

    def test_special_characters_rejected(self):
        """Special characters should be rejected."""
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("2025-01-15-001;ls")
        with pytest.raises(ValueError, match="Invalid review_id format"):
            _validate_review_id("2025-01-15-001|cat /etc/passwd")


class TestStorageManagerSecurity:
    """Tests for ReviewStorageManager security measures."""

    def test_get_session_with_path_traversal(self):
        """Getting session with path traversal should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ReviewStorageManager(tmpdir)
            with pytest.raises(ValueError, match="Invalid review_id format"):
                storage.get_review_session("../../../etc/passwd")

    def test_save_result_with_path_traversal(self):
        """Saving result with path traversal should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ReviewStorageManager(tmpdir)
            with pytest.raises(ValueError, match="Invalid review_id format"):
                storage.save_review_result("../malicious", {"data": "test"})

    def test_save_diff_with_path_traversal(self):
        """Saving diff with path traversal should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ReviewStorageManager(tmpdir)
            with pytest.raises(ValueError, match="Invalid review_id format"):
                storage.save_git_diff("../../../tmp/pwned", "malicious content")

    def test_mark_complete_with_path_traversal(self):
        """Marking complete with path traversal should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ReviewStorageManager(tmpdir)
            with pytest.raises(ValueError, match="Invalid review_id format"):
                storage.mark_review_complete("../../secret", "approved")


class TestGitErrorSanitization:
    """Tests for git error message sanitization."""

    def test_url_token_sanitization(self):
        """URLs with embedded tokens should be sanitized."""
        error = "fatal: Authentication failed for 'https://token123@github.com/repo.git'"
        sanitized = _sanitize_git_error(error)
        assert "token123" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_oauth_parameter_sanitization(self):
        """OAuth parameters should be sanitized."""
        error = "error: oauth=secrettoken123 in URL"
        sanitized = _sanitize_git_error(error)
        assert "secrettoken123" not in sanitized
        assert "oauth=[REDACTED]" in sanitized

    def test_password_parameter_sanitization(self):
        """Password parameters should be sanitized."""
        error = "error: password=mysecretpass123 in config"
        sanitized = _sanitize_git_error(error)
        assert "mysecretpass123" not in sanitized
        assert "password=[REDACTED]" in sanitized

    def test_bearer_token_sanitization(self):
        """Bearer tokens should be sanitized."""
        error = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        sanitized = _sanitize_git_error(error)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in sanitized
        assert "Bearer [REDACTED]" in sanitized

    def test_safe_message_unchanged(self):
        """Safe error messages should not be modified."""
        error = "fatal: not a git repository"
        sanitized = _sanitize_git_error(error)
        assert sanitized == error

    def test_multiple_sensitive_items(self):
        """Multiple sensitive items should all be sanitized."""
        error = "https://token@github.com oauth=secret password=pass123"
        sanitized = _sanitize_git_error(error)
        assert "token" not in sanitized or "[REDACTED]" in sanitized
        assert "secret" not in sanitized
        assert "pass123" not in sanitized


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
