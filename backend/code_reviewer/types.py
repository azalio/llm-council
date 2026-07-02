"""Pydantic models for code review functionality."""

from pydantic import BaseModel
from typing import Optional, List, Dict, Literal


class ReviewRequest(BaseModel):
    """Request for code review from the council."""
    summary: str
    relevant_docs: Optional[List[str]] = None
    focus_areas: Optional[List[str]] = None
    previous_review_id: Optional[str] = None
    test_command: Optional[str] = None
    working_directory: Optional[str] = None
    base_branch: Optional[str] = None  # Compare against this branch (e.g., 'main')
    files: Optional[List[str]] = None  # Filter to specific files (for large diffs)


class DesignIssue(BaseModel):
    """Individual design compliance issue."""
    severity: Literal["critical", "major", "minor"]
    description: str
    location: Optional[str] = None
    suggestion: Optional[str] = None


class DesignCompliance(BaseModel):
    """Design compliance assessment result."""
    compliant: bool
    issues: List[DesignIssue] = []


class ReviewComment(BaseModel):
    """Individual code review comment."""
    file: str
    line: Optional[int] = None
    severity: Literal["critical", "major", "minor", "nitpick"]
    category: Literal["correctness", "security", "design", "performance", "testing", "quality"]
    title: str
    description: str
    suggestion: Optional[str] = None
    # For synthesized reviews - which reviewers flagged this
    sources: Optional[List[str]] = None
    # Confidence level for synthesized issues
    confidence: Optional[Literal["high", "medium", "low"]] = None


class MissingRequirement(BaseModel):
    """Missing requirement identified during review."""
    requirement: str
    severity: Literal["critical", "major", "minor"]


class TestResults(BaseModel):
    """Test coverage and quality assessment."""
    new_tests_present: bool
    issues: List[str] = []


class OverallAssessment(BaseModel):
    """Overall assessment verdict for a review."""
    verdict: Literal["approve", "request_changes", "needs_discussion"]
    risk_level: Literal["low", "medium", "high"]
    confidence: Literal["high", "medium", "low"]


class ConflictResolution(BaseModel):
    """Resolution of conflicting opinions between reviewers."""
    topic: str
    positions: Dict[str, str]
    resolution: str


class SynthesisMeta(BaseModel):
    """Metadata about the synthesis process."""
    reviewers_count: int
    consensus_level: Literal["high", "medium", "low"]
    recommendation: Literal["approve", "request_changes", "needs_discussion"]


class FinalVerdict(BaseModel):
    """Final decision from the chairman."""
    decision: Literal["approve", "request_changes", "needs_discussion"]
    rationale: str


class IndividualReview(BaseModel):
    """Individual reviewer's complete response."""
    summary: str
    design_compliance: DesignCompliance
    comments: List[ReviewComment] = []
    missing_requirements: List[MissingRequirement] = []
    test_results: Optional[TestResults] = None
    positive_aspects: List[str] = []
    overall_assessment: OverallAssessment


class SynthesizedReview(BaseModel):
    """Chairman's synthesized review from all council members."""
    meta: SynthesisMeta
    consolidated_issues: List[ReviewComment] = []
    conflicts_resolved: List[ConflictResolution] = []
    unanimous_positives: List[str] = []
    blocking_issues: List[str] = []
    final_verdict: FinalVerdict


class ReviewResult(BaseModel):
    """Full review result including council data."""
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
    """Complete review session with all rounds."""
    review_id: str
    created_at: str
    updated_at: str
    status: Literal["in_progress", "approved", "needs_changes", "abandoned", "merged"]
    rounds: List[ReviewResult] = []
    request: ReviewRequest
    git_diff: Optional[str] = None
    branch: Optional[str] = None
