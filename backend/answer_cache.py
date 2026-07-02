"""Answer cache for context-free repeat and near-duplicate council questions."""

from __future__ import annotations

import copy
import math
import re
import time
from typing import Any, Dict, Optional, Tuple

from . import storage
from .config import (
    ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD,
    ANSWER_CACHE_SIMILARITY_THRESHOLD,
    ANSWER_CACHE_VALIDATION_THRESHOLD,
    CHAIRMAN_MODEL,
)
from .observability import bind_request_id, get_request_id, reset_request_id
from .openrouter import query_model
from .metrics import record_answer_cache_lookup


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_CACHE_TOKENS = 3
_VALIDATION_TIMEOUT_S = 30.0
_MAX_VALIDATION_ANSWER_CHARS = 2500


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "could",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "please",
    "should",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "why",
    "with",
    "would",
}

_CANONICAL_TERMS = {
    "answers": "answer",
    "answered": "answer",
    "answering": "answer",
    "responses": "answer",
    "response": "answer",
    "reply": "answer",
    "replies": "answer",
    "cache": "cache",
    "cached": "cache",
    "caches": "cache",
    "caching": "cache",
    "reuse": "cache",
    "reused": "cache",
    "reusing": "cache",
    "similar": "repeat",
    "same": "repeat",
    "duplicate": "repeat",
    "duplicates": "repeat",
    "near": "repeat",
    "paraphrase": "repeat",
    "paraphrased": "repeat",
    "repeat": "repeat",
    "repeats": "repeat",
    "repeated": "repeat",
    "repeating": "repeat",
    "questions": "question",
    "question": "question",
    "queries": "question",
    "query": "question",
    "prompts": "question",
    "prompt": "question",
    "requests": "question",
    "request": "question",
    "council": "council",
    "llm": "llm",
    "llms": "llm",
    "models": "model",
    "model": "model",
    "confidence": "confidence",
    "uncertainty": "confidence",
    "disagreement": "confidence",
    "latency": "latency",
    "slow": "latency",
    "faster": "latency",
    "fast": "latency",
    "cost": "cost",
    "costs": "cost",
    "spend": "cost",
    "spending": "cost",
    "token": "cost",
    "tokens": "cost",
    "behavior": "behavior",
    "behaviour": "behavior",
    "work": "behavior",
    "works": "behavior",
    "working": "behavior",
    "explain": "explain",
    "describe": "explain",
    "summarize": "explain",
    "summarise": "explain",
}


def normalize_question(question: str) -> str:
    """Return a stable normalized form for safe repeat-question matching."""
    return " ".join(_TOKEN_RE.findall((question or "").lower()))


def build_cached_title(question: str) -> str:
    """Build a cheap deterministic title for a cache-hit conversation."""
    title = (question or "").strip() or "Cached Answer"
    title = " ".join(title.split())
    return title[:47] + "..." if len(title) > 50 else title


def question_similarity(left: str, right: str) -> float:
    """Score two questions for conservative cache matching."""
    normalized_left = normalize_question(left)
    normalized_right = normalize_question(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0

    left_tokens = set(normalized_left.split())
    right_tokens = set(normalized_right.split())
    if min(len(left_tokens), len(right_tokens)) < 5:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _canonicalize_token(token: str) -> str:
    if token in _CANONICAL_TERMS:
        return _CANONICAL_TERMS[token]
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    elif len(token) > 4 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s"):
        token = token[:-1]
    return _CANONICAL_TERMS.get(token, token)


def _numeric_tokens(question: str) -> Tuple[str, ...]:
    return tuple(token for token in normalize_question(question).split() if token.isdigit())


def question_embedding(question: str) -> Dict[str, float]:
    """Build a deterministic local semantic embedding for cache lookup."""
    canonical_tokens = []
    for token in normalize_question(question).split():
        if token in _STOPWORDS or token.isdigit():
            continue
        canonical = _canonicalize_token(token)
        if canonical not in _STOPWORDS:
            canonical_tokens.append(canonical)

    embedding: Dict[str, float] = {}
    for token in canonical_tokens:
        embedding[f"term:{token}"] = embedding.get(f"term:{token}", 0.0) + 1.0
    for left, right in zip(canonical_tokens, canonical_tokens[1:]):
        embedding[f"pair:{left}:{right}"] = embedding.get(f"pair:{left}:{right}", 0.0) + 0.15
    return embedding


def _cosine_similarity(left: Dict[str, float], right: Dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0.0) for key, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_question_similarity(left: str, right: str) -> float:
    """Score paraphrased questions using the local cache embedding."""
    left_numbers = _numeric_tokens(left)
    right_numbers = _numeric_tokens(right)
    if (left_numbers or right_numbers) and left_numbers != right_numbers:
        return 0.0
    return _cosine_similarity(question_embedding(left), question_embedding(right))


def is_cache_eligible(
    *,
    bypass_cache: bool,
    conversation_context: Optional[Dict[str, Any]],
    mode: str,
    thorough: bool,
    clarify_when_unclear: bool,
) -> bool:
    """Return whether this request can safely reuse a prior answer."""
    return (
        not bypass_cache
        and conversation_context is None
        and mode == "auto"
        and not thorough
        and not clarify_when_unclear
    )


def is_substantive_cache_question(question: str) -> bool:
    """Return whether a question is long enough for answer-cache lookup."""
    return len(normalize_question(question).split()) >= _MIN_CACHE_TOKENS


def _candidate_is_cache_source(candidate: Dict[str, Any]) -> bool:
    return not (
        candidate.get("stage3", {}).get("cached")
        or candidate.get("metadata", {}).get("answer_cache")
    )


def is_answer_cache_source(candidate: Dict[str, Any]) -> bool:
    """Return whether a stored first-turn answer may seed future cache hits."""
    return _candidate_is_cache_source(candidate)


def classify_answer_cache_match(question: str, source_question: str) -> Optional[Dict[str, Any]]:
    """Classify a question pair using the production cache threshold policy."""
    token_similarity = question_similarity(question, source_question)
    semantic_similarity = semantic_question_similarity(question, source_question)

    if token_similarity >= ANSWER_CACHE_SIMILARITY_THRESHOLD:
        priority = 3.0 + token_similarity
        match_type = "token"
        similarity = token_similarity
        requires_validation = False
    elif semantic_similarity >= ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD:
        priority = 2.0 + semantic_similarity
        match_type = "semantic"
        similarity = semantic_similarity
        requires_validation = False
    elif semantic_similarity >= ANSWER_CACHE_VALIDATION_THRESHOLD:
        priority = 1.0 + semantic_similarity
        match_type = "validated_semantic"
        similarity = semantic_similarity
        requires_validation = True
    else:
        return None

    return {
        "match_type": match_type,
        "similarity": similarity,
        "token_similarity": token_similarity,
        "semantic_similarity": semantic_similarity,
        "requires_validation": requires_validation,
        "priority": priority,
    }


def _select_answer_cache_candidate(question: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Select the best cache candidate and describe why it qualifies."""
    if not is_substantive_cache_question(question):
        return None

    best_candidate: Optional[Dict[str, Any]] = None
    best_match: Optional[Dict[str, Any]] = None
    best_priority = -1.0

    for candidate in storage.find_completed_answer_candidates():
        if not _candidate_is_cache_source(candidate):
            continue
        source_question = candidate.get("question", "")
        if not is_substantive_cache_question(source_question):
            continue
        match = classify_answer_cache_match(question, source_question)
        if match is None:
            continue

        priority = match["priority"]
        if priority > best_priority:
            best_priority = priority
            best_candidate = candidate
            best_match = match

    if best_candidate is None or best_match is None:
        return None

    return best_candidate, best_match


def _build_answer_cache_hit(
    candidate: Dict[str, Any],
    match: Dict[str, Any],
    validation: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    stage3 = copy.deepcopy(candidate.get("stage3") or {})
    original_response = stage3.get("response", "")
    if not original_response:
        return None

    cache_info = {
        "hit": True,
        "match_type": match["match_type"],
        "similarity": round(match["similarity"], 4),
        "token_similarity": round(match["token_similarity"], 4),
        "semantic_similarity": round(match["semantic_similarity"], 4),
        "source_conversation_id": candidate.get("conversation_id"),
        "source_user_position": candidate.get("user_position"),
        "source_question": candidate.get("question"),
    }
    if validation is not None:
        cache_info["validation"] = validation

    match_label = "semantically similar" if match["match_type"] != "token" else "similar"
    stage3["response"] = (
        f"_Served from answer cache: {match_label} first-turn question was answered "
        f"previously (similarity {match['similarity']:.2f}). Use `bypass_cache=true` "
        "for a fresh council run._\n\n"
        f"{original_response}"
    )
    stage3["cached"] = True

    metadata = copy.deepcopy(candidate.get("metadata") or {})
    metadata["answer_cache"] = cache_info
    metadata["run_status"] = {
        "degraded": False,
        "summary": "Served from answer cache; no full council fan-out was called.",
        "successful_council_models": 0,
        "failed_council_models": 0,
        "stages": {},
        "cached": True,
    }

    return {
        "stage1": copy.deepcopy(candidate.get("stage1") or []),
        "stage2": copy.deepcopy(candidate.get("stage2") or []),
        "stage3": stage3,
        "stage2a": copy.deepcopy(candidate.get("stage2a")),
        "stage2b": copy.deepcopy(candidate.get("stage2b")),
        "metadata": metadata,
    }


def find_answer_cache_hit(question: str) -> Optional[Dict[str, Any]]:
    """Find a completed answer that can be reused without validation."""
    selected = _select_answer_cache_candidate(question)
    if selected is None:
        return None

    candidate, match = selected
    if match["requires_validation"]:
        return None
    return _build_answer_cache_hit(candidate, match)


def _parse_cache_validation_response(content: str) -> Optional[Dict[str, Any]]:
    text = (content or "").strip()
    if not text:
        return None
    match = re.search(r"CACHE_MATCH\s*:\s*(yes|no)\b", text, re.IGNORECASE)
    if not match:
        return None

    reason = "Chairman validated cache applicability."
    reason_match = re.search(r"REASON\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    if reason_match:
        reason = reason_match.group(1).strip().splitlines()[0][:240]

    return {
        "approved": match.group(1).lower() == "yes",
        "model": CHAIRMAN_MODEL,
        "reason": reason,
    }


async def _validate_answer_cache_candidate(
    question: str,
    candidate: Dict[str, Any],
    match: Dict[str, Any],
) -> Dict[str, Any]:
    source_answer = (candidate.get("stage3") or {}).get("response", "")
    prompt = f"""Decide whether a cached LLM Council answer can safely answer a new first-turn question.

Source question:
{candidate.get("question", "")}

New question:
{question}

Cached final answer excerpt:
{source_answer[:_MAX_VALIDATION_ANSWER_CHARS]}

Semantic similarity: {match["semantic_similarity"]:.4f}

Return exactly:
CACHE_MATCH: yes|no
REASON: one short sentence"""

    token = None
    if get_request_id() is None:
        _, token = bind_request_id()
    try:
        response = await query_model(
            CHAIRMAN_MODEL,
            [{"role": "user", "content": prompt}],
            timeout=_VALIDATION_TIMEOUT_S,
        )
    finally:
        if token is not None:
            reset_request_id(token)

    debug = response.get("_debug", {}) if isinstance(response, dict) else {}
    if not debug.get("ok", True) or not response.get("content"):
        return {
            "approved": False,
            "model": CHAIRMAN_MODEL,
            "reason": "Chairman validation failed; cache reuse was skipped.",
        }

    parsed = _parse_cache_validation_response(response.get("content", ""))
    if parsed is None:
        return {
            "approved": False,
            "model": CHAIRMAN_MODEL,
            "reason": "Chairman validation response was unparseable; cache reuse was skipped.",
        }
    return parsed


async def find_answer_cache_hit_with_validation(question: str) -> Optional[Dict[str, Any]]:
    """Find a cache hit, using chairman validation for borderline semantic matches."""
    started_at = time.perf_counter()
    selected = _select_answer_cache_candidate(question)
    if selected is None:
        record_answer_cache_lookup(hit=False, latency_ms=_elapsed_ms(started_at))
        return None

    candidate, match = selected
    if not match["requires_validation"]:
        hit = _build_answer_cache_hit(candidate, match)
        record_answer_cache_lookup(
            hit=hit is not None,
            latency_ms=_elapsed_ms(started_at),
            match_type=match["match_type"],
            similarity=match["similarity"],
            token_similarity=match["token_similarity"],
            semantic_similarity=match["semantic_similarity"],
        )
        return hit

    validation = await _validate_answer_cache_candidate(question, candidate, match)
    if not validation["approved"]:
        record_answer_cache_lookup(
            hit=False,
            latency_ms=_elapsed_ms(started_at),
            match_type=match["match_type"],
            similarity=match["similarity"],
            token_similarity=match["token_similarity"],
            semantic_similarity=match["semantic_similarity"],
            validation=validation,
        )
        return None
    hit = _build_answer_cache_hit(candidate, match, validation)
    record_answer_cache_lookup(
        hit=hit is not None,
        latency_ms=_elapsed_ms(started_at),
        match_type=match["match_type"],
        similarity=match["similarity"],
        token_similarity=match["token_similarity"],
        semantic_similarity=match["semantic_similarity"],
        validation=validation,
    )
    return hit
