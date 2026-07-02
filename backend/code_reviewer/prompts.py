"""Prompt builders for code review functionality."""

import re
from typing import List, Optional, Dict

# Language-specific checks
LANGUAGE_CHECKS = {
    "python": "- Type hints on public functions\n- No mutable default args\n- Proper exception handling",
    "javascript": "- Proper Promise/async handling\n- React hooks rules (if applicable)\n- No var, use const/let",
    "typescript": "- Strict type annotations\n- No `any` types without justification\n- Proper null handling",
    "go": "- Error handling (no ignored errors)\n- Goroutine leak potential\n- Context propagation",
    "rust": "- Proper error handling with Result/Option\n- No unnecessary clones\n- Lifetime annotations",
}


def detect_language(changed_files: List[str]) -> str:
    """Detect primary language from file extensions.

    Args:
        changed_files: List of changed file paths

    Returns:
        Detected language name or 'unknown'
    """
    ext_count: Dict[str, int] = {}
    for f in changed_files:
        ext = f.split(".")[-1] if "." in f else ""
        ext_count[ext] = ext_count.get(ext, 0) + 1

    ext_to_lang = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "jsx": "javascript",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "rb": "ruby",
    }

    if ext_count:
        top_ext = max(ext_count, key=lambda k: ext_count[k])
        return ext_to_lang.get(top_ext, "unknown")
    return "unknown"


def format_diff_with_lines(diff: str) -> str:
    """Add explicit line numbers to diff for better LLM parsing.

    Args:
        diff: Raw git diff content

    Returns:
        Diff with file:line annotations on added lines
    """
    lines = diff.split("\n")
    result = []
    current_file = ""
    line_num = 0

    for line in lines:
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
            result.append(line)
        elif line.startswith("@@"):
            # Parse line number from @@ -X,Y +Z,W @@
            match = re.search(r"\+(\d+)", line)
            if match:
                line_num = int(match.group(1)) - 1
            result.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            line_num += 1
            result.append(f"{current_file}:{line_num} | {line}")
        elif line.startswith("-") and not line.startswith("---"):
            result.append(f"{current_file}:- | {line}")
        else:
            if not line.startswith("---") and not line.startswith("+++"):
                line_num += 1
            result.append(line)

    return "\n".join(result)


def build_review_prompt(
    summary: str,
    git_diff: str,
    changed_files: List[str],
    focus_areas: Optional[List[str]] = None,
    relevant_docs: Optional[List[str]] = None,
    test_command: Optional[str] = None,
    previous_rounds: Optional[List[Dict]] = None,
) -> str:
    """Build the optimized code review prompt for the council.

    Args:
        summary: Description of the work completed
        git_diff: Git diff content
        changed_files: List of changed file paths
        focus_areas: Optional specific areas to focus on
        relevant_docs: Optional relevant documentation references
        test_command: Optional test command to run
        previous_rounds: Optional previous review rounds for follow-up

    Returns:
        Complete review prompt string
    """
    language = detect_language(changed_files)
    formatted_diff = format_diff_with_lines(git_diff)
    is_large_pr = len(git_diff) > 15000

    files_display = ", ".join(changed_files[:20])
    if len(changed_files) > 20:
        files_display += "..."

    prompt = f"""You are a senior software engineer performing a code review.
Analyze ONLY the provided diff and changed files. Do not speculate about code not shown.

## Context
**Language/Framework**: {language}
**Task**: {summary}
**Changed Files**: {files_display}

## Code Changes
```diff
{formatted_diff}
```
"""

    if focus_areas:
        areas_text = "\n".join(f"- {area}" for area in focus_areas)
        prompt += f"""
## Focus Areas
{areas_text}
"""

    if relevant_docs:
        docs_text = "\n".join(f"- {doc}" for doc in relevant_docs)
        prompt += f"""
## Relevant Documentation
{docs_text}
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
            round_num = round_data.get("round", "N/A")
            assessment = round_data.get("overall_assessment", "N/A")
            comments = round_data.get("comments", [])
            critical_major = len(
                [c for c in comments if c.get("severity") in ["critical", "major"]]
            )
            prompt += f"""
### Round {round_num} - {assessment}
Outstanding issues: {critical_major}
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
**Warning**: Large diff detected. Focus on: security, correctness, and specified focus areas. Skip style nitpicks.
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
3. If NO issues in a category - return empty array, do not invent problems
4. Include positive aspects worth noting (max 2-3)
5. Be constructive: explain WHY something matters
6. Limit nitpicks to 3 maximum

## Severity Definitions
- **critical**: BLOCKING. Actual security vulnerabilities exploitable in production, data loss, crashes. NOT theoretical edge cases.
- **major**: Should fix before merge. Real bugs, design violations, missing tests for new code
- **minor**: Nice to fix. Maintainability, minor improvements
- **nitpick**: Optional. Style preferences (limit to 3 max)

## What is NOT an issue
- Documented limitations (comments saying "best-effort", "does not cover X") - these are INTENTIONAL tradeoffs
- Theoretical edge cases already acknowledged in code comments
- Features explicitly marked as out of scope
- Python version compatibility when code already has compatibility helpers

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

## Verdict Decision Rules
- **approve**: No critical issues AND no more than 2 major issues. Minor issues and nitpicks are OK.
- **request_changes**: Any critical issue OR 3+ major issues
- **needs_discussion**: Unclear requirements or architectural questions needing clarification
"""

    return prompt


def build_synthesis_prompt(
    summary: str,
    council_responses: List[Dict],
    aggregate_rankings: List[Dict],
) -> str:
    """Build optimized prompt for synthesizing council reviews into final review.

    Args:
        summary: Original task summary
        council_responses: List of individual reviewer responses
        aggregate_rankings: Aggregated rankings from peer review

    Returns:
        Complete synthesis prompt string
    """
    responses_text = ""
    for i, resp in enumerate(council_responses, 1):
        model = resp.get("model", f"Reviewer {i}")
        response = resp.get("response", "No response")
        responses_text += f"""
### Reviewer {i} ({model})
{response}
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
| Single reviewer finds critical issue | KEEP IT - do not dismiss |

### 3. Prioritization Matrix
```
Issue Weight = Severity x Consensus
- Critical + Multiple reviewers = Must-fix (blocking)
- Major + Single top-reviewer = Should-fix (blocking)
- Minor + Multiple reviewers = Consider-fixing
- Nitpick + Single reviewer = Drop unless exceptional
```

### 4. Final Verdict Logic
- ANY critical blocker issue found -> request_changes
- MAJORITY say request_changes -> request_changes
- Unanswered questions remain -> needs_discussion
- Otherwise -> approve

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
4. Maximum 10 consolidated issues - prioritize by severity
"""
