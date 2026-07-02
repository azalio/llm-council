# План миграции mcp-claude-reviewer → llm-council

## Обзор

Цель: Добавить в llm-council MCP инструменты для код-ревью, аналогичные mcp-claude-reviewer, чтобы заменить отдельный mcp-claude-reviewer на единый llm-council сервер.

### Текущее состояние

**mcp-claude-reviewer (TypeScript):**
- 3 MCP tools: `request_review`, `get_review_history`, `mark_review_complete`
- Stateless архитектура с per-request инициализацией
- Поддержка Claude CLI, Gemini CLI, Mock reviewer
- Хранение ревью в `.reviews/` директории (JSON)
- Git интеграция через `simple-git`

**llm-council (Python):**
- 8 MCP tools: `ask_council`, `get_council_stage1`, `query_single_model`, etc.
- FastMCP сервер
- Поддержка OpenRouter API
- Хранение в `data/conversations/` (JSON)

---

## План реализации

### Этап 1: Создание модуля code_reviewer в backend

**1.1 Создать структуру файлов:**

```
llm-council/
├── backend/
│   ├── code_reviewer/
│   │   ├── __init__.py
│   │   ├── git_utils.py       # Git операции
│   │   ├── storage.py         # Хранение ревью сессий
│   │   ├── reviewer.py        # Логика ревью через council
│   │   ├── prompts.py         # Промпты для ревью
│   │   └── types.py           # Pydantic модели
│   └── ...
```

**1.2 Файл: `backend/code_reviewer/types.py`**

> **ОБНОВЛЕНО**: Типы соответствуют оптимизированной JSON структуре

```python
from pydantic import BaseModel
from typing import Optional, List, Dict, Literal
from datetime import datetime

class ReviewRequest(BaseModel):
    summary: str
    relevant_docs: Optional[List[str]] = None
    focus_areas: Optional[List[str]] = None
    previous_review_id: Optional[str] = None
    test_command: Optional[str] = None
    working_directory: Optional[str] = None


class DesignIssue(BaseModel):
    severity: Literal["critical", "major", "minor"]
    description: str
    location: Optional[str] = None
    suggestion: Optional[str] = None


class DesignCompliance(BaseModel):
    compliant: bool
    issues: List[DesignIssue] = []


class ReviewComment(BaseModel):
    file: str
    line: Optional[int] = None
    severity: Literal["critical", "major", "minor", "nitpick"]
    category: Literal["correctness", "security", "design", "performance", "testing", "quality"]
    title: str
    description: str
    suggestion: Optional[str] = None
    # For synthesized reviews
    sources: Optional[List[str]] = None
    confidence: Optional[Literal["high", "medium", "low"]] = None


class MissingRequirement(BaseModel):
    requirement: str
    severity: Literal["critical", "major", "minor"]


class TestResults(BaseModel):
    new_tests_present: bool
    issues: List[str] = []


class OverallAssessment(BaseModel):
    verdict: Literal["approve", "request_changes", "needs_discussion"]
    risk_level: Literal["low", "medium", "high"]
    confidence: Literal["high", "medium", "low"]


class ConflictResolution(BaseModel):
    topic: str
    positions: Dict[str, str]
    resolution: str


class SynthesisMeta(BaseModel):
    reviewers_count: int
    consensus_level: Literal["high", "medium", "low"]
    recommendation: Literal["approve", "request_changes", "needs_discussion"]


class FinalVerdict(BaseModel):
    decision: Literal["approve", "request_changes", "needs_discussion"]
    rationale: str


class IndividualReview(BaseModel):
    """Individual reviewer's response"""
    summary: str
    design_compliance: DesignCompliance
    comments: List[ReviewComment] = []
    missing_requirements: List[MissingRequirement] = []
    test_results: Optional[TestResults] = None
    positive_aspects: List[str] = []
    overall_assessment: OverallAssessment


class SynthesizedReview(BaseModel):
    """Chairman's synthesized review"""
    meta: SynthesisMeta
    consolidated_issues: List[ReviewComment] = []
    conflicts_resolved: List[ConflictResolution] = []
    unanimous_positives: List[str] = []
    blocking_issues: List[str] = []
    final_verdict: FinalVerdict


class ReviewResult(BaseModel):
    """Full review result including council data"""
    review_id: str
    timestamp: str
    status: Literal["in_progress", "approved", "needs_changes"]
    round: int
    # Synthesized review (from Chairman)
    synthesized: Optional[SynthesizedReview] = None
    # Individual reviews (from council)
    council_responses: Optional[List[Dict]] = None
    aggregate_rankings: Optional[List[Dict]] = None
    # Summary stats
    summary: Optional[Dict] = None


class ReviewSession(BaseModel):
    review_id: str
    created_at: str
    updated_at: str
    status: Literal["in_progress", "approved", "needs_changes", "abandoned", "merged"]
    rounds: List[ReviewResult] = []
    request: ReviewRequest
    git_diff: Optional[str] = None
    branch: Optional[str] = None
```

**1.3 Файл: `backend/code_reviewer/git_utils.py`**

```python
import subprocess
import os
from typing import List, Optional

class GitUtils:
    def __init__(self, working_dir: str):
        self.working_dir = working_dir

    def _run_git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.working_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git error: {result.stderr}")
        return result.stdout

    def is_git_repository(self) -> bool:
        try:
            self._run_git("rev-parse", "--git-dir")
            return True
        except:
            return False

    def get_changed_files(self) -> List[str]:
        """Get all changed files (staged + unstaged)"""
        staged = self._run_git("diff", "--cached", "--name-only").strip()
        unstaged = self._run_git("diff", "--name-only").strip()

        files = set()
        if staged:
            files.update(staged.split("\n"))
        if unstaged:
            files.update(unstaged.split("\n"))
        return list(files)

    def get_git_diff(self) -> str:
        """Get combined staged and unstaged diff"""
        staged = self._run_git("diff", "--cached")
        unstaged = self._run_git("diff")
        return staged + unstaged

    def get_current_branch(self) -> str:
        return self._run_git("branch", "--show-current").strip()

    def get_recent_commits(self, count: int = 5) -> List[str]:
        output = self._run_git("log", f"-{count}", "--oneline")
        return output.strip().split("\n") if output.strip() else []
```

**1.4 Файл: `backend/code_reviewer/prompts.py`**

> **ОБНОВЛЕНО**: Промпты оптимизированы на основе рекомендаций LLM Council

```python
from typing import List, Optional, Dict
import re

# Language-specific checks
LANGUAGE_CHECKS = {
    "python": "- Type hints on public functions\n- No mutable default args\n- Proper exception handling",
    "javascript": "- Proper Promise/async handling\n- React hooks rules (if applicable)\n- No var, use const/let",
    "typescript": "- Strict type annotations\n- No `any` types without justification\n- Proper null handling",
    "go": "- Error handling (no ignored errors)\n- Goroutine leak potential\n- Context propagation",
    "rust": "- Proper error handling with Result/Option\n- No unnecessary clones\n- Lifetime annotations",
}


def detect_language(changed_files: List[str]) -> str:
    """Detect primary language from file extensions"""
    ext_count = {}
    for f in changed_files:
        ext = f.split('.')[-1] if '.' in f else ''
        ext_count[ext] = ext_count.get(ext, 0) + 1

    ext_to_lang = {
        'py': 'python', 'js': 'javascript', 'ts': 'typescript',
        'go': 'go', 'rs': 'rust', 'java': 'java', 'rb': 'ruby'
    }

    if ext_count:
        top_ext = max(ext_count, key=ext_count.get)
        return ext_to_lang.get(top_ext, 'unknown')
    return 'unknown'


def format_diff_with_lines(diff: str) -> str:
    """Add explicit line numbers to diff for better LLM parsing"""
    lines = diff.split('\n')
    result = []
    current_file = ""
    line_num = 0

    for line in lines:
        if line.startswith('diff --git'):
            match = re.search(r'b/(.+)$', line)
            if match:
                current_file = match.group(1)
            result.append(line)
        elif line.startswith('@@'):
            # Parse line number from @@ -X,Y +Z,W @@
            match = re.search(r'\+(\d+)', line)
            if match:
                line_num = int(match.group(1)) - 1
            result.append(line)
        elif line.startswith('+') and not line.startswith('+++'):
            line_num += 1
            result.append(f"{current_file}:{line_num} | {line}")
        elif line.startswith('-') and not line.startswith('---'):
            result.append(f"{current_file}:- | {line}")
        else:
            if not line.startswith('---') and not line.startswith('+++'):
                line_num += 1
            result.append(line)

    return '\n'.join(result)


def build_review_prompt(
    summary: str,
    git_diff: str,
    changed_files: List[str],
    focus_areas: Optional[List[str]] = None,
    relevant_docs: Optional[List[str]] = None,
    test_command: Optional[str] = None,
    previous_rounds: Optional[List[Dict]] = None
) -> str:
    """Build the optimized code review prompt for the council"""

    language = detect_language(changed_files)
    formatted_diff = format_diff_with_lines(git_diff)
    is_large_pr = len(git_diff) > 15000

    prompt = f"""You are a senior software engineer performing a code review.
Analyze ONLY the provided diff and changed files. Do not speculate about code not shown.

## Context
**Language/Framework**: {language}
**Task**: {summary}
**Changed Files**: {', '.join(changed_files[:20])}{'...' if len(changed_files) > 20 else ''}

## Code Changes
```diff
{formatted_diff}
```
"""

    if focus_areas:
        prompt += f"""
## Focus Areas
{chr(10).join(f"- {area}" for area in focus_areas)}
"""

    if relevant_docs:
        prompt += f"""
## Relevant Documentation
{chr(10).join(f"- {doc}" for doc in relevant_docs)}
"""

    if test_command:
        prompt += f"""
## Test Command Available
`{test_command}`
"""

    if previous_rounds:
        prompt += """
## Previous Review Rounds (address remaining issues)
"""
        for round_data in previous_rounds:
            prompt += f"""
### Round {round_data.get('round', 'N/A')} - {round_data.get('overall_assessment', 'N/A')}
Outstanding issues: {len([c for c in round_data.get('comments', []) if c.get('severity') in ['critical', 'major']])}
"""

    # Language-specific checks
    if language in LANGUAGE_CHECKS:
        prompt += f"""
## Language-Specific Checks ({language})
{LANGUAGE_CHECKS[language]}
"""

    # Large PR warning
    if is_large_pr:
        prompt += """
⚠️ Large diff detected. Focus on: security, correctness, and specified focus areas. Skip style nitpicks.
"""

    prompt += """
## Review Criteria

### 1. Correctness & Security (BLOCKING if violated)
- Logic errors, null handling, race conditions
- OWASP Top 10: injection, XSS, hardcoded secrets, input validation

### 2. Design & Architecture
- SOLID principles adherence
- Consistency with project patterns
- API design implications

### 3. Code Quality
- Readability (naming, complexity)
- Error handling completeness
- Performance (N+1 queries, unbounded allocations)

### 4. Testing
- New functionality covered
- Edge cases tested
- Existing tests not broken

## Response Rules
1. Reference specific files and lines when possible
2. Every issue MUST include a concrete suggestion
3. If NO issues in a category — return empty array, do not invent problems
4. Include positive aspects worth noting (max 2-3)
5. Be constructive: explain WHY something matters
6. Limit nitpicks to 3 maximum

## Severity Definitions
- **critical**: Must fix. Security holes, data loss, crashes
- **major**: Should fix. Bugs, design violations, missing tests
- **minor**: Nice to fix. Maintainability, minor improvements
- **nitpick**: Optional. Style preferences (limit to 3 max)

## Output Format (strict JSON only, no additional text)

```json
{
  "summary": "1-2 sentence overall assessment",
  "design_compliance": {
    "compliant": true|false,
    "issues": []
  },
  "comments": [
    {
      "file": "path/to/file.py",
      "line": 42,
      "severity": "critical|major|minor|nitpick",
      "category": "correctness|security|design|performance|testing|quality",
      "title": "Brief issue title",
      "description": "What's wrong and why it matters",
      "suggestion": "Specific fix with code example if applicable"
    }
  ],
  "missing_requirements": [
    {"requirement": "...", "severity": "critical|major|minor"}
  ],
  "test_results": {
    "new_tests_present": true|false,
    "issues": []
  },
  "positive_aspects": ["Well-structured error handling", "Good test coverage"],
  "overall_assessment": {
    "verdict": "approve|request_changes|needs_discussion",
    "risk_level": "low|medium|high",
    "confidence": "high|medium|low"
  }
}
```
"""

    return prompt


def build_synthesis_prompt(
    summary: str,
    council_responses: List[Dict],
    aggregate_rankings: List[Dict]
) -> str:
    """Build optimized prompt for synthesizing council reviews into final review"""

    responses_text = ""
    for i, resp in enumerate(council_responses, 1):
        responses_text += f"""
### Reviewer {i} ({resp['model']})
{resp['response']}
---
"""

    rankings_text = "\n".join(
        f"- {r['model']}: avg rank {r['average_rank']:.2f} ({r['rankings_count']} votes)"
        for r in aggregate_rankings
    )

    reviewer_count = len(council_responses)

    return f"""You are the Lead Engineer synthesizing {reviewer_count} code reviews into a final decision.

## Context
**Task**: {summary}

## Individual Reviews
{responses_text}

## Reviewer Rankings (by reliability/expertise, lower is better)
{rankings_text}

## Synthesis Rules

### 1. Deduplication
- Merge identical issues (same file/line/problem)
- Keep the most detailed description and best suggestion
- Track sources: which reviewers flagged each issue

### 2. Conflict Resolution
| Scenario | Resolution |
|----------|------------|
| Severity disagreement | Use higher severity if any top-ranked reviewer flagged it |
| Contradictory suggestions | Include both as alternatives with tradeoffs |
| Single reviewer finds critical issue | KEEP IT — do not dismiss |

### 3. Prioritization Matrix
```
Issue Weight = Severity × Consensus
- Critical + Multiple reviewers = Must-fix (blocking)
- Major + Single top-reviewer = Should-fix (blocking)
- Minor + Multiple reviewers = Consider-fixing
- Nitpick + Single reviewer = Drop unless exceptional
```

### 4. Final Verdict Logic
- ANY critical blocker issue found → request_changes
- MAJORITY say request_changes → request_changes
- Unanswered questions remain → needs_discussion
- Otherwise → approve

## Output Format (strict JSON only)

```json
{{
  "meta": {{
    "reviewers_count": {reviewer_count},
    "consensus_level": "high|medium|low",
    "recommendation": "approve|request_changes|needs_discussion"
  }},
  "consolidated_issues": [
    {{
      "severity": "critical|major|minor",
      "category": "...",
      "file": "...",
      "line": 42,
      "title": "...",
      "description": "Merged description",
      "suggestion": "Best suggestion or alternatives",
      "sources": ["reviewer_1", "reviewer_3"],
      "confidence": "high|medium|low"
    }}
  ],
  "conflicts_resolved": [
    {{
      "topic": "Use of singleton pattern",
      "positions": {{"reviewer_1": "...", "reviewer_2": "..."}},
      "resolution": "Chose X because..."
    }}
  ],
  "unanimous_positives": ["All praised error handling"],
  "blocking_issues": ["Issue titles that must be fixed"],
  "final_verdict": {{
    "decision": "request_changes",
    "rationale": "2 security issues identified by 3/5 reviewers"
  }}
}}
```

## Rules
1. Never introduce new issues not present in any review
2. Never drop a critical/major issue even if only 1 reviewer found it
3. Preserve file/line references for IDE integration
4. Maximum 10 consolidated issues — prioritize by severity
"""
```

**1.5 Файл: `backend/code_reviewer/storage.py`**

```python
import os
import json
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

class ReviewStorageManager:
    def __init__(self, base_path: str = ".reviews"):
        self.base_path = Path(base_path)
        self.sessions_path = self.base_path / "sessions"
        self._ensure_directories()

    def _ensure_directories(self):
        self.sessions_path.mkdir(parents=True, exist_ok=True)

    def _generate_review_id(self) -> str:
        """Generate ID in format YYYY-MM-DD-NNN"""
        today = datetime.now().strftime("%Y-%m-%d")
        existing = list(self.sessions_path.glob(f"{today}-*"))
        next_num = len(existing) + 1
        return f"{today}-{next_num:03d}"

    def create_review_session(self, request: Dict) -> Dict:
        """Create a new review session"""
        review_id = self._generate_review_id()
        session_dir = self.sessions_path / review_id
        session_dir.mkdir(parents=True, exist_ok=True)

        session = {
            "review_id": review_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "status": "in_progress",
            "rounds": [],
            "request": request
        }

        self._save_session(review_id, session)
        return session

    def _save_session(self, review_id: str, session: Dict):
        session_file = self.sessions_path / review_id / "session.json"
        with open(session_file, "w") as f:
            json.dump(session, f, indent=2)

    def get_review_session(self, review_id: str) -> Optional[Dict]:
        session_file = self.sessions_path / review_id / "session.json"
        if not session_file.exists():
            return None
        with open(session_file) as f:
            return json.load(f)

    def save_review_result(self, review_id: str, result: Dict):
        """Save a review round result"""
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

    def save_git_diff(self, review_id: str, diff: str):
        diff_file = self.sessions_path / review_id / "changes.diff"
        with open(diff_file, "w") as f:
            f.write(diff)

    def get_review_history(self, limit: int = 5) -> List[Dict]:
        """Get recent review sessions"""
        sessions = []
        for session_dir in sorted(self.sessions_path.iterdir(), reverse=True):
            if session_dir.is_dir():
                session = self.get_review_session(session_dir.name)
                if session:
                    sessions.append(session)
                if len(sessions) >= limit:
                    break
        return sessions

    def mark_review_complete(
        self,
        review_id: str,
        status: str,
        notes: Optional[str] = None
    ) -> Dict:
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
```

**1.6 Файл: `backend/code_reviewer/reviewer.py`**

```python
import json
import os
from typing import Optional, List, Dict

from .types import ReviewRequest, ReviewResult
from .prompts import build_review_prompt, build_synthesis_prompt
from .git_utils import GitUtils
from .storage import ReviewStorageManager

# Import council functions
from backend.council import (
    stage1_collect_responses,
    stage2_collect_rankings,
    calculate_aggregate_rankings
)
from backend.openrouter import query_model
from backend.config import CHAIRMAN_MODEL


class CouncilCodeReviewer:
    """Code reviewer that uses LLM Council for multi-perspective reviews"""

    def __init__(self, working_dir: str):
        self.working_dir = working_dir
        self.git = GitUtils(working_dir)
        self.storage = ReviewStorageManager(
            os.path.join(working_dir, ".reviews")
        )

    async def request_review(
        self,
        request: ReviewRequest
    ) -> Dict:
        """Request a code review from the council"""

        # Check git repository
        if not self.git.is_git_repository():
            raise ValueError(f"Not a git repository: {self.working_dir}")

        # Get git info
        git_diff = self.git.get_git_diff()
        if not git_diff.strip():
            raise ValueError("No changes to review (git diff is empty)")

        changed_files = self.git.get_changed_files()
        branch = self.git.get_current_branch()

        # Create or continue session
        if request.previous_review_id:
            session = self.storage.get_review_session(request.previous_review_id)
            if not session:
                raise ValueError(f"Previous review not found: {request.previous_review_id}")
            review_id = request.previous_review_id
        else:
            session = self.storage.create_review_session(request.model_dump())
            review_id = session["review_id"]
            self.storage.save_git_diff(review_id, git_diff)

        # Build review prompt
        previous_rounds = session.get("rounds", []) if request.previous_review_id else None

        review_prompt = build_review_prompt(
            summary=request.summary,
            git_diff=git_diff,
            changed_files=changed_files,
            focus_areas=request.focus_areas,
            relevant_docs=request.relevant_docs,
            test_command=request.test_command,
            previous_rounds=previous_rounds
        )

        # Stage 1: Get individual reviews from council
        stage1_results = await stage1_collect_responses(review_prompt)

        # Stage 2: Cross-evaluate reviews
        stage2_results, label_to_model = await stage2_collect_rankings(
            review_prompt,
            stage1_results
        )

        # Calculate aggregate rankings
        aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

        # Stage 3: Synthesize final review
        synthesis_prompt = build_synthesis_prompt(
            summary=request.summary,
            council_responses=stage1_results,
            aggregate_rankings=aggregate_rankings
        )

        final_response = await query_model(CHAIRMAN_MODEL, [
            {"role": "user", "content": synthesis_prompt}
        ])

        # Parse JSON response
        try:
            review_data = json.loads(
                final_response["content"]
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )
        except json.JSONDecodeError:
            # If parsing fails, create minimal review structure
            review_data = {
                "design_compliance": {"follows_architecture": True, "major_violations": []},
                "comments": [],
                "missing_requirements": [],
                "overall_assessment": "needs_changes",
                "raw_response": final_response["content"]
            }

        # Build review result
        result = {
            "review_id": review_id,
            "status": "needs_changes" if review_data.get("overall_assessment") == "needs_changes" else "in_progress",
            **review_data,
            "council_responses": stage1_results,
            "aggregate_rankings": aggregate_rankings,
            "summary": {
                "total_comments": len(review_data.get("comments", [])),
                "by_severity": self._count_by_severity(review_data.get("comments", [])),
                "critical_issues": [
                    c["comment"] for c in review_data.get("comments", [])
                    if c.get("severity") == "critical"
                ]
            }
        }

        # Save result
        saved_result = self.storage.save_review_result(review_id, result)

        return saved_result

    def _count_by_severity(self, comments: List[Dict]) -> Dict[str, int]:
        counts = {"critical": 0, "major": 0, "minor": 0, "suggestion": 0}
        for c in comments:
            severity = c.get("severity", "suggestion")
            counts[severity] = counts.get(severity, 0) + 1
        return counts

    def get_review_history(self, limit: int = 5) -> List[Dict]:
        return self.storage.get_review_history(limit)

    def get_review_session(self, review_id: str) -> Optional[Dict]:
        return self.storage.get_review_session(review_id)

    def mark_complete(
        self,
        review_id: str,
        status: str,
        notes: Optional[str] = None
    ) -> Dict:
        return self.storage.mark_review_complete(review_id, status, notes)
```

---

### Этап 2: Добавление MCP tools в server.py

**2.1 Добавить imports в `mcp_server/server.py`:**

```python
# Добавить после существующих imports
from backend.code_reviewer.reviewer import CouncilCodeReviewer
from backend.code_reviewer.types import ReviewRequest
```

**2.2 Добавить новые MCP tools:**

```python
# === CODE REVIEW TOOLS ===

@mcp.tool()
async def request_review(
    summary: str,
    relevant_docs: list[str] | None = None,
    focus_areas: list[str] | None = None,
    previous_review_id: str | None = None,
    test_command: str | None = None,
    working_directory: str | None = None
) -> str:
    """
    Request a code review from the LLM Council.

    The council will review your git changes with multiple perspectives,
    cross-evaluate each other's reviews, and synthesize a final comprehensive review.

    Args:
        summary: Description of the work completed (required)
        relevant_docs: List of relevant design docs or specs
        focus_areas: Specific areas to focus the review on
        previous_review_id: ID of previous review for follow-up
        test_command: Command to run tests (e.g., "npm test", "pytest")
        working_directory: Directory to review (defaults to MCP_CLIENT_CWD)

    Returns:
        Comprehensive code review with comments, suggestions, and overall assessment
    """
    # Determine working directory
    work_dir = working_directory or os.environ.get("MCP_CLIENT_CWD") or os.getcwd()

    try:
        reviewer = CouncilCodeReviewer(work_dir)
        request = ReviewRequest(
            summary=summary,
            relevant_docs=relevant_docs,
            focus_areas=focus_areas,
            previous_review_id=previous_review_id,
            test_command=test_command,
            working_directory=work_dir
        )

        result = await reviewer.request_review(request)

        # Format output
        output = f"""# Code Review: {result['review_id']}

## Overall Assessment: {result.get('overall_assessment', 'N/A').upper()}

## Summary
- **Total Comments:** {result['summary']['total_comments']}
- **Critical Issues:** {result['summary']['by_severity'].get('critical', 0)}
- **Major Issues:** {result['summary']['by_severity'].get('major', 0)}
- **Minor Issues:** {result['summary']['by_severity'].get('minor', 0)}
- **Suggestions:** {result['summary']['by_severity'].get('suggestion', 0)}

"""

        if result['summary'].get('critical_issues'):
            output += "### Critical Issues\n"
            for issue in result['summary']['critical_issues']:
                output += f"- ⚠️ {issue}\n"
            output += "\n"

        if result.get('comments'):
            output += "## Comments\n\n"
            for c in result['comments']:
                severity_icon = {
                    'critical': '🔴',
                    'major': '🟠',
                    'minor': '🟡',
                    'suggestion': '💡'
                }.get(c.get('severity'), '📝')

                output += f"### {severity_icon} {c.get('file', 'General')}"
                if c.get('line'):
                    output += f":{c['line']}"
                output += f" ({c.get('category', 'general')})\n"
                output += f"{c.get('comment', '')}\n"
                if c.get('suggestion'):
                    output += f"\n**Suggestion:** {c['suggestion']}\n"
                output += "\n"

        if result.get('missing_requirements'):
            output += "## Missing Requirements\n\n"
            for req in result['missing_requirements']:
                output += f"- **{req.get('requirement', 'Unknown')}**\n"
                output += f"  - Impact: {req.get('impact', 'N/A')}\n"
                output += f"  - Suggestion: {req.get('suggestion', 'N/A')}\n"

        # Council info
        if result.get('aggregate_rankings'):
            output += "\n## Council Rankings\n\n"
            output += "| Rank | Model | Avg Position | Votes |\n"
            output += "|------|-------|--------------|-------|\n"
            for i, r in enumerate(result['aggregate_rankings'], 1):
                output += f"| {i} | {r['model']} | {r['average_rank']:.2f} | {r['rankings_count']} |\n"

        return output

    except Exception as e:
        return f"Error during code review: {sanitize_error(str(e))}"


@mcp.tool()
async def get_review_history(
    limit: int = 5,
    review_id: str | None = None,
    working_directory: str | None = None
) -> str:
    """
    Get review history or a specific review session.

    Args:
        limit: Number of recent reviews to return (default: 5)
        review_id: Specific review session ID to retrieve
        working_directory: Directory containing .reviews folder

    Returns:
        Review history or specific review details
    """
    work_dir = working_directory or os.environ.get("MCP_CLIENT_CWD") or os.getcwd()

    try:
        from backend.code_reviewer.storage import ReviewStorageManager
        storage = ReviewStorageManager(os.path.join(work_dir, ".reviews"))

        if review_id:
            session = storage.get_review_session(review_id)
            if not session:
                return f"Review session not found: {review_id}"

            output = f"# Review Session: {review_id}\n\n"
            output += f"- **Created:** {session.get('created_at', 'N/A')}\n"
            output += f"- **Status:** {session.get('status', 'N/A')}\n"
            output += f"- **Rounds:** {len(session.get('rounds', []))}\n\n"

            for round_data in session.get('rounds', []):
                output += f"## Round {round_data.get('round', 'N/A')}\n"
                output += f"- Assessment: {round_data.get('overall_assessment', 'N/A')}\n"
                output += f"- Comments: {round_data.get('summary', {}).get('total_comments', 0)}\n\n"

            return output
        else:
            sessions = storage.get_review_history(limit)

            if not sessions:
                return "No review history found."

            output = "# Review History\n\n"
            output += "| ID | Created | Status | Rounds |\n"
            output += "|----|---------|--------|--------|\n"
            for s in sessions:
                output += f"| {s['review_id']} | {s['created_at'][:10]} | {s['status']} | {len(s.get('rounds', []))} |\n"

            return output

    except Exception as e:
        return f"Error getting review history: {sanitize_error(str(e))}"


@mcp.tool()
async def mark_review_complete(
    review_id: str,
    final_status: str,
    notes: str | None = None,
    working_directory: str | None = None
) -> str:
    """
    Mark a review session as complete.

    Args:
        review_id: The review session ID to complete
        final_status: Final status ('approved', 'abandoned', or 'merged')
        notes: Optional final notes or summary
        working_directory: Directory containing .reviews folder

    Returns:
        Confirmation of completion
    """
    if final_status not in ['approved', 'abandoned', 'merged']:
        return f"Invalid status: {final_status}. Must be 'approved', 'abandoned', or 'merged'."

    work_dir = working_directory or os.environ.get("MCP_CLIENT_CWD") or os.getcwd()

    try:
        from backend.code_reviewer.storage import ReviewStorageManager
        storage = ReviewStorageManager(os.path.join(work_dir, ".reviews"))

        session = storage.mark_review_complete(review_id, final_status, notes)

        return f"""# Review Completed

- **Review ID:** {review_id}
- **Final Status:** {final_status}
- **Updated:** {session.get('updated_at', 'N/A')}

{f'**Notes:** {notes}' if notes else ''}
"""
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error completing review: {sanitize_error(str(e))}"
```

---

### Этап 3: Обновление конфигурации

**3.1 Обновить pyproject.toml:**

```toml
[project]
dependencies = [
    # ... existing deps ...
    "gitpython>=3.1.0",  # Опционально, если хотите заменить subprocess
]
```

**3.2 Создать `backend/code_reviewer/__init__.py`:**

```python
from .reviewer import CouncilCodeReviewer
from .types import ReviewRequest, ReviewResult, ReviewSession
from .storage import ReviewStorageManager
from .git_utils import GitUtils

__all__ = [
    "CouncilCodeReviewer",
    "ReviewRequest",
    "ReviewResult",
    "ReviewSession",
    "ReviewStorageManager",
    "GitUtils"
]
```

---

### Этап 4: Миграция настроек Claude Code

**4.1 Обновить settings.json:**

Заменить:
```json
{
  "mcpServers": {
    "claude-reviewer": {
      "command": "/path/to/mcp-claude-reviewer/mcp-wrapper.sh"
    }
  }
}
```

На:
```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uv",
      "args": [
        "--directory", "/Users/azalio/gitroot/azalio/llm-council",
        "run", "mcp_server/server.py"
      ],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-v1-...",
        "API_PROVIDER": "openrouter"
      }
    }
  }
}
```

---

## Преимущества новой архитектуры

1. **Мульти-перспективный ревью** - Вместо одной модели, несколько LLM дают независимые оценки
2. **Анонимная кросс-валидация** - Модели оценивают ответы друг друга без знания авторства
3. **Агрегированный рейтинг** - Видно, какие модели дали лучшие ревью
4. **Единый сервер** - Не нужно поддерживать два отдельных MCP сервера
5. **Python вместо TypeScript** - Если предпочитаете Python

## Различия с оригиналом

| Аспект | mcp-claude-reviewer | llm-council reviewer |
|--------|---------------------|---------------------|
| Язык | TypeScript | Python |
| Рецензент | Claude CLI / Gemini CLI | OpenRouter API |
| Модели | Одна модель | Council (6+ моделей) |
| Перспективы | Одна | Множественные + синтез |
| Resume | Поддерживается | Не реализовано |
| Test runner | Через Claude CLI | Через промпт |

## Что НЕ переносится

1. **Claude CLI resume** - Council не поддерживает продолжение сессии
2. **Test execution** - Тесты выполняются только если модель сама их вызовет
3. **Prompt persistence** - Отладочное сохранение промптов

## План тестирования

1. Unit tests для git_utils.py
2. Unit tests для storage.py
3. Integration test для полного флоу ревью
4. Manual testing через Claude Code

---

## Резюме

Для миграции нужно:

1. ✅ Создать `backend/code_reviewer/` модуль (5 файлов)
2. ✅ Добавить 3 MCP tools в `mcp_server/server.py`
3. ✅ Обновить настройки Claude Code
4. ⏳ Удалить mcp-claude-reviewer из settings (после тестирования)
