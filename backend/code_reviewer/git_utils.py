"""Git utilities for code review functionality."""

import re
import subprocess
from pathlib import Path
from typing import List


def _sanitize_git_error(error_msg: str) -> str:
    """Sanitize git error messages to remove sensitive information.

    Args:
        error_msg: Raw error message from git

    Returns:
        Sanitized error message
    """
    # Remove URLs with embedded tokens
    sanitized = re.sub(r"https://[^@\s]+@", "https://[REDACTED]@", error_msg)
    # Remove oauth/token parameters
    sanitized = re.sub(r"(oauth|token|key|password)=[^\s&]+", r"\1=[REDACTED]", sanitized, flags=re.IGNORECASE)
    # Remove Bearer tokens
    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9_-]+", "Bearer [REDACTED]", sanitized)
    return sanitized


class GitUtils:
    """Utilities for interacting with git repositories."""

    def __init__(self, working_dir: str):
        """Initialize GitUtils with a working directory.

        Args:
            working_dir: Path to the working directory (can be subdirectory of git repo)
        """
        self.working_dir = Path(working_dir).resolve()

    def _run_git(self, *args: str) -> str:
        """Run a git command and return stdout.

        Args:
            *args: Git command arguments (e.g., 'status', '--short')

        Returns:
            Command stdout as string

        Raises:
            RuntimeError: If git command fails, includes stderr content
        """
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=str(self.working_dir),
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() if e.stderr else "Git command failed"
            sanitized_msg = _sanitize_git_error(error_msg)
            raise RuntimeError(f"Git error: {sanitized_msg}") from e
        except FileNotFoundError:
            raise RuntimeError("Git executable not found. Is git installed?") from None

    def is_git_repository(self) -> bool:
        """Check if the working directory is inside a git repository.

        Returns:
            True if inside a git repo, False otherwise
        """
        try:
            self._run_git("rev-parse", "--git-dir")
            return True
        except RuntimeError:
            return False

    def get_changed_files(
        self, base_branch: str = None, file_filter: List[str] = None
    ) -> List[str]:
        """Get all changed files.

        If base_branch is provided, returns files changed between base_branch and HEAD.
        Otherwise, returns staged + unstaged + new (untracked) files.

        Args:
            base_branch: Optional base branch to compare against (e.g., 'main', 'master')
            file_filter: Optional list of specific files/paths to include

        Returns:
            List of file paths relative to repository root

        Raises:
            RuntimeError: If not in a git repository or git command fails
        """
        if base_branch:
            # Compare with base branch (for committed changes)
            try:
                if file_filter:
                    output = self._run_git(
                        "diff", "--name-only", f"{base_branch}...HEAD", "--", *file_filter
                    ).strip()
                else:
                    output = self._run_git(
                        "diff", "--name-only", f"{base_branch}...HEAD"
                    ).strip()
                return sorted(output.split("\n")) if output else []
            except RuntimeError:
                # Fallback to current changes if base_branch doesn't exist
                pass

        # Use git status --porcelain to get all changed files including new ones
        # Format: XY filename (X=staged, Y=unstaged)
        status_output = self._run_git("status", "--porcelain").strip()

        files = set()
        if status_output:
            for line in status_output.split("\n"):
                if line:
                    # Skip the status prefix (2 chars + space)
                    # Handle renamed files (format: "R  old -> new")
                    filename = line[3:]
                    if " -> " in filename:
                        filename = filename.split(" -> ")[1]
                    # Apply file filter if provided
                    if file_filter:
                        if any(filename == f or filename.startswith(f + "/") for f in file_filter):
                            files.add(filename)
                    else:
                        files.add(filename)
        return sorted(list(files))

    def get_git_diff(
        self, base_branch: str = None, file_filter: List[str] = None
    ) -> str:
        """Get diff of changes.

        If base_branch is provided, compares base_branch...HEAD (committed changes).
        Otherwise, returns staged + unstaged + new file contents.

        Args:
            base_branch: Optional base branch to compare against (e.g., 'main', 'master')
            file_filter: Optional list of specific files/paths to include

        Returns:
            Complete diff content as string

        Raises:
            RuntimeError: If not in a git repository or git command fails
        """
        if base_branch:
            # Compare with base branch (for committed changes)
            try:
                if file_filter:
                    return self._run_git(
                        "diff", f"{base_branch}...HEAD", "--", *file_filter
                    )
                return self._run_git("diff", f"{base_branch}...HEAD")
            except RuntimeError:
                # Fallback to current changes if base_branch doesn't exist
                pass

        # Get staged changes
        if file_filter:
            staged = self._run_git("diff", "--cached", "--", *file_filter)
            unstaged = self._run_git("diff", "--", *file_filter)
        else:
            staged = self._run_git("diff", "--cached")
            unstaged = self._run_git("diff")

        # Get new (untracked) files and their content
        new_files_diff = self._get_new_files_diff(file_filter)

        parts = []
        if staged:
            parts.append(f"=== STAGED CHANGES ===\n{staged}")
        if unstaged:
            parts.append(f"=== UNSTAGED CHANGES ===\n{unstaged}")
        if new_files_diff:
            parts.append(f"=== NEW FILES ===\n{new_files_diff}")

        return "\n\n".join(parts) if parts else ""

    def _get_new_files_diff(self, file_filter: List[str] = None) -> str:
        """Get diff-like content for new (untracked) files.

        Args:
            file_filter: Optional list of specific files/paths to include

        Returns:
            Diff-formatted content for new files
        """
        # Get untracked files (status code '??')
        status_output = self._run_git("status", "--porcelain").strip()
        new_files = []
        if status_output:
            for line in status_output.split("\n"):
                if line.startswith("??"):
                    # Skip the '?? ' prefix
                    filename = line[3:]
                    # Apply file filter if provided
                    if file_filter:
                        if not any(filename == f or filename.startswith(f + "/") for f in file_filter):
                            continue
                    new_files.append(filename)

        if not new_files:
            return ""

        # Generate diff-like output for each new file
        parts = []
        for filename in new_files:
            try:
                # Use git diff with --no-index to compare against /dev/null
                diff = self._run_git(
                    "diff", "--no-index", "/dev/null", filename
                )
                parts.append(diff)
            except RuntimeError:
                # git diff --no-index returns exit code 1 when files differ
                # which is normal, so we need to handle this differently
                try:
                    result = subprocess.run(
                        ["git", "diff", "--no-index", "/dev/null", filename],
                        cwd=str(self.working_dir),
                        capture_output=True,
                        text=True,
                    )
                    if result.stdout:
                        parts.append(result.stdout)
                except Exception:
                    # Skip files we can't read
                    pass

        return "\n".join(parts)

    def get_current_branch(self) -> str:
        """Get the current branch name.

        Returns:
            Current branch name

        Raises:
            RuntimeError: If not in a git repository or git command fails
        """
        return self._run_git("branch", "--show-current").strip()

    def get_recent_commits(self, count: int = 5) -> List[str]:
        """Get recent commit messages.

        Args:
            count: Number of recent commits to retrieve (default: 5)

        Returns:
            List of commit messages (most recent first)

        Raises:
            RuntimeError: If not in a git repository or git command fails
        """
        output = self._run_git("log", f"-{count}", "--oneline")
        return output.strip().split("\n") if output.strip() else []
