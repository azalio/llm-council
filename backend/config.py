"""Configuration for the LLM Council."""

import math
import os
from dotenv import load_dotenv

# Load .env file but DO NOT override existing environment variables
# This allows MCP to pass env vars that take precedence over .env
load_dotenv(override=False)

# API Provider selection. OpenRouter is the only provider in this build; the
# variable is kept so provider-selection code and downstream tooling keep a
# stable name to branch on if a second provider is registered later.
API_PROVIDER = os.getenv("API_PROVIDER", "openrouter")

# API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Council members
COUNCIL_MODELS_OPENROUTER = [
    "openai/gpt-5.5",
    "anthropic/claude-opus-4.8",
    "x-ai/grok-4.3",
    "deepseek/deepseek-v4-pro",
    "qwen/qwen3.7-max",
]

COUNCIL_MODEL_METADATA_OPENROUTER = {
    "openai/gpt-5.5": {
        "strengths": ["general", "code", "reasoning"],
        "routing_priority": 10,
    },
    "anthropic/claude-opus-4.8": {
        "strengths": ["general", "critique", "writing"],
        "routing_priority": 9,
    },
    "x-ai/grok-4.3": {
        "strengths": ["general", "reasoning", "current_events"],
        "routing_priority": 8,
    },
    "deepseek/deepseek-v4-pro": {
        "strengths": ["code", "math", "reasoning"],
        "routing_priority": 6,
    },
    "qwen/qwen3.7-max": {
        "strengths": ["general", "math", "multilingual"],
        "routing_priority": 5,
    },
}

# Chairman model
CHAIRMAN_MODEL_OPENROUTER = "google/gemini-3.1-pro-preview"

# Code review model (single model for faster/cheaper reviews)
CODE_REVIEW_MODEL_OPENROUTER = "openai/gpt-5.5"

# Fast/cheap model for title generation
TITLE_MODEL_OPENROUTER = "google/gemini-3.5-flash"


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _read_non_negative_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if value < 0 or not math.isfinite(value):
        raise ValueError(f"{name} must be a non-negative number")
    return value


def _read_positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0 or not math.isfinite(value):
        raise ValueError(f"{name} must be a positive number")
    return value


STAGE1_MAX_CONCURRENCY = _read_positive_int_env(
    "COUNCIL_STAGE1_MAX_CONCURRENCY",
    3,
)
STAGE1_PROVIDER_BACKOFF_SECONDS = _read_non_negative_float_env(
    "COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS",
    0.25,
)

# Stage 3 (chairman) query_model() timeout. Deep mode's prompt embeds Stage 1
# + Stage 2 + Stage 2b + hedge/attribution instructions across every council
# model, so it needs a larger budget than standard/quick mode's much smaller
# prompt. Selected by presence of stage2b_results, not by mode string, since
# escalated auto-standard runs can also produce Stage 2b output.
COUNCIL_STAGE3_TIMEOUT_SECONDS = _read_positive_float_env(
    "COUNCIL_STAGE3_TIMEOUT_SECONDS",
    600.0,
)
COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS = _read_positive_float_env(
    "COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS",
    1200.0,
)


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a finite number between 0 and 1")

    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{name} must be a finite number between 0 and 1")

    return parsed


def _read_temperature_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite temperature from 0.0 to 3.0") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 3.0:
        raise ValueError(f"{name} must be a finite temperature from 0.0 to 3.0")
    return value


def _read_temperature_list_env(name: str, default: list[float]) -> list[float]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)

    values = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            raise ValueError(f"{name} must be a comma-separated list of temperatures")
        try:
            value = float(item)
        except ValueError as exc:
            raise ValueError(
                f"{name} must be a comma-separated list of temperatures"
            ) from exc
        if not math.isfinite(value) or not 0.0 <= value <= 3.0:
            raise ValueError(f"{name} temperatures must be from 0.0 to 3.0")
        values.append(value)

    if not values:
        raise ValueError(f"{name} must include at least one temperature")
    return values


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


# At or below this top-1 vote share, the chairman treats rankings as split.
COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD = _get_float_env(
    "COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD",
    0.5,
)

# Stage 2 peer-ranking order counterbalancing. When enabled, each ranker sees the
# anonymized responses in a rotated order (a Latin-square over rankers) and the
# raw ranking is relabeled back to the canonical labels, so positional/label bias
# is cancelled in aggregate instead of being shared across all rankers. No extra
# model calls. Off by default pending the A/B decision; relabeling keeps the
# canonical label_to_model contract (aggregation, confidence, chairman, UI)
# unchanged.
STAGE2_COUNTERBALANCE_ENABLED = _get_bool_env("COUNCIL_STAGE2_COUNTERBALANCE", False)

# Stage 2b revision policy (arXiv:2606.28050): a model's own critique of its own
# anonymized Stage 1 response is same-model self-evaluation, which the paper
# shows can be worse than generation. Default excludes self-critiques from the
# critique bundle sent back to the model that wrote the target response, keeping
# only peer critiques. Set True only for explicit experiments comparing
# with/without self-critique; the experimental state is always recorded in
# Stage 2b debug metadata so it is visible in run debug output.
STAGE2B_INCLUDE_SELF_CRITIQUES = _get_bool_env("STAGE2B_INCLUDE_SELF_CRITIQUES", False)

# Auto mode starts in standard for many questions. If the standard council then
# proves split, spend the deep critique/revision stages before synthesis.
COUNCIL_CONFIDENCE_ESCALATION_ENABLED = _get_bool_env(
    "COUNCIL_CONFIDENCE_ESCALATION_ENABLED",
    True,
)

# Auto-mode sparse routing starts routine standard requests with a smaller
# council and expands to the full pool when confidence, failures, or risk demand it.
COUNCIL_ADAPTIVE_ROUTING_ENABLED = _get_bool_env(
    "COUNCIL_ADAPTIVE_ROUTING_ENABLED",
    True,
)

# Durable ETA / expected-wait-time estimates. Read from the persisted `runs`
# SQLite table (survives restarts), NOT from the in-memory process-local
# collector. When disabled, the ETA tool/SSE field still return a well-formed
# stub with basis="disabled". The per-mode *_FALLBACK_SECONDS are advisory
# only — surfaced as fallback_seconds when sample_count is below MIN_SAMPLES,
# never masquerading as the measured expected_wait_seconds.
COUNCIL_ETA_ENABLED = _get_bool_env("COUNCIL_ETA_ENABLED", True)
COUNCIL_ETA_SAMPLE_WINDOW = _read_positive_int_env("COUNCIL_ETA_SAMPLE_WINDOW", 50)
COUNCIL_ETA_MIN_SAMPLES = _read_positive_int_env("COUNCIL_ETA_MIN_SAMPLES", 5)
COUNCIL_ETA_PERCENTILE = _get_float_env("COUNCIL_ETA_PERCENTILE", 0.5)
COUNCIL_ETA_MAX_RUN_ROWS = _read_positive_int_env("COUNCIL_ETA_MAX_RUN_ROWS", 5000)
COUNCIL_ETA_INCLUDE_FAILED = _get_bool_env("COUNCIL_ETA_INCLUDE_FAILED", False)
COUNCIL_ETA_QUICK_FALLBACK_SECONDS = _read_non_negative_float_env(
    "COUNCIL_ETA_QUICK_FALLBACK_SECONDS",
    12.0,
)
COUNCIL_ETA_STANDARD_FALLBACK_SECONDS = _read_non_negative_float_env(
    "COUNCIL_ETA_STANDARD_FALLBACK_SECONDS",
    45.0,
)
COUNCIL_ETA_DEEP_FALLBACK_SECONDS = _read_non_negative_float_env(
    "COUNCIL_ETA_DEEP_FALLBACK_SECONDS",
    90.0,
)

# First-turn answer cache is intentionally conservative: substantive exact
# normalized repeats hit, near-duplicates need very high token overlap.
ANSWER_CACHE_SIMILARITY_THRESHOLD = _get_float_env(
    "ANSWER_CACHE_SIMILARITY_THRESHOLD",
    0.9,
)

# Semantic cache hits use a local lexical embedding over normalized question
# terms. High-confidence matches can be served without another model call;
# borderline matches require chairman validation before reuse.
ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD = _get_float_env(
    "ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD",
    0.86,
)
ANSWER_CACHE_VALIDATION_THRESHOLD = _get_float_env(
    "ANSWER_CACHE_VALIDATION_THRESHOLD",
    0.68,
)

DEFAULT_JUDGE_RUBRIC = [
    {
        "name": "factuality",
        "description": "The answer is correct, avoids unsupported factual claims, and handles uncertainty honestly.",
        "weight": 0.35,
    },
    {
        "name": "completeness",
        "description": "The answer covers the important parts of the question without omitting material constraints.",
        "weight": 0.25,
    },
    {
        "name": "reasoning",
        "description": "The answer explains the reasoning chain and tradeoffs clearly enough to audit.",
        "weight": 0.25,
    },
    {
        "name": "clarity",
        "description": "The answer is concise, well structured, and directly useful to the user.",
        "weight": 0.15,
    },
]

JUDGE_MODEL_OPENROUTER = os.getenv("JUDGE_MODEL_OPENROUTER", CODE_REVIEW_MODEL_OPENROUTER)
JUDGE_TEMPERATURE = _read_temperature_env("JUDGE_TEMPERATURE", 0.0)
JUDGE_TOP_P = _get_float_env("JUDGE_TOP_P", 1.0)
JUDGE_MAX_TOKENS = _read_positive_int_env("JUDGE_MAX_TOKENS", 1200)
JUDGE_TIMEOUT_SECONDS = _read_positive_float_env("JUDGE_TIMEOUT_SECONDS", 120.0)
JUDGE_ENSEMBLE_ENABLED = _get_bool_env("JUDGE_ENSEMBLE_ENABLED", False)
JUDGE_ENSEMBLE_SAMPLES = _read_positive_int_env("JUDGE_ENSEMBLE_SAMPLES", 10)
JUDGE_ENSEMBLE_TEMPERATURES = _read_temperature_list_env(
    "JUDGE_ENSEMBLE_TEMPERATURES",
    [0.01, 1.0, 1.5],
)

# BINEVAL-style binary factuality judge (operator-facing pilot, off by default).
# When enabled, the judge decomposes the `factuality` criterion into atomic
# yes/no checklist questions answered independently per answer, while the other
# rubric criteria stay holistic. See docs/bineval-ab-plan.md.
JUDGE_BINARY_ENABLED = _get_bool_env("JUDGE_BINARY_ENABLED", False)
# Minimum absolute `overall` score delta required to declare a pairwise winner
# in the binary path; smaller deltas resolve to a tie (avoids calling a winner
# inside normal judge jitter).
JUDGE_BINARY_TIE_MARGIN = _get_float_env("JUDGE_BINARY_TIE_MARGIN", 0.05)
# A single failed `critical` checklist question caps the factuality sub-score at
# this value (defeats the aggregation paradox where many trivial passes hide one
# catastrophic factual failure).
JUDGE_BINARY_CRITICAL_CAP = _get_float_env("JUDGE_BINARY_CRITICAL_CAP", 0.5)

# Order-swap symmetrization for the holistic pairwise judge (operator-facing,
# off by default). When enabled, each pair is judged in both candidate/baseline
# orderings and the verdicts are combined: agreement keeps the winner, a flip
# resolves to a tie. Kills pairwise position bias at 2x cost while keeping the
# holistic discrimination signal. Ignored when JUDGE_BINARY_ENABLED (the binary
# path already scores each answer in isolation).
JUDGE_ORDER_SWAP_ENABLED = _get_bool_env("JUDGE_ORDER_SWAP_ENABLED", False)


def infer_model_family(model: str) -> str:
    """Infer the model provider/family from a configured model identifier."""
    normalized = (model or "").strip().lower()
    if not normalized:
        return "unknown"

    vendor_aliases = {
        "x-ai": "xai",
        "xai": "xai",
        "google": "google",
        "openai": "openai",
        "anthropic": "anthropic",
        "deepseek": "deepseek",
        "qwen": "qwen",
        "mistralai": "mistral",
        "meta-llama": "meta",
        "perplexity": "perplexity",
    }

    if "/" in normalized:
        vendor = normalized.split("/", 1)[0]
        return vendor_aliases.get(vendor, vendor)

    direct_prefixes = (
        ("gpt-", "openai"),
        ("o1", "openai"),
        ("o3", "openai"),
        ("o4", "openai"),
        ("claude-", "anthropic"),
        ("grok-", "xai"),
        ("gemini-", "google"),
        ("deepseek", "deepseek"),
        ("qwen", "qwen"),
        ("sonar", "perplexity"),
        ("llama-", "meta"),
    )
    for prefix, family in direct_prefixes:
        if normalized.startswith(prefix):
            return family

    return normalized


def validate_chairman_heterogeneity(
    council_models,
    chairman_model,
    *,
    provider: str = API_PROVIDER,
):
    """Ensure the chairman is not a council member or in a council family."""
    if not chairman_model:
        raise ValueError(f"No chairman model configured for provider {provider}")

    normalized_chairman = chairman_model.strip().lower()
    exact_overlaps = [
        model for model in council_models
        if model.strip().lower() == normalized_chairman
    ]
    if exact_overlaps:
        raise ValueError(
            f"Chairman model {chairman_model!r} exactly matches a council model "
            f"for provider {provider}: {', '.join(exact_overlaps)}"
        )

    chairman_family = infer_model_family(chairman_model)
    council_families = {
        model: infer_model_family(model)
        for model in council_models
    }
    family_overlaps = [
        model for model, family in council_families.items()
        if family == chairman_family
    ]
    if family_overlaps:
        raise ValueError(
            f"Chairman model {chairman_model!r} uses the same model family "
            f"{chairman_family!r} as council model(s) for provider {provider}: "
            f"{', '.join(family_overlaps)}"
        )

    return {
        "chairman_family": chairman_family,
        "council_families": council_families,
    }


validate_chairman_heterogeneity(
    COUNCIL_MODELS_OPENROUTER,
    CHAIRMAN_MODEL_OPENROUTER,
    provider="openrouter",
)

# Active configuration. OpenRouter is the only registered provider in this
# build (see backend/providers/registry.py); the branch below is preserved so
# a future second provider can plug into the same selection point.
if API_PROVIDER == "openrouter":
    API_URL = OPENROUTER_API_URL
    API_TOKEN = OPENROUTER_API_KEY
    AUTH_HEADER = f"Bearer {OPENROUTER_API_KEY}"
    COUNCIL_MODELS = COUNCIL_MODELS_OPENROUTER
    COUNCIL_MODEL_METADATA = COUNCIL_MODEL_METADATA_OPENROUTER
    CHAIRMAN_MODEL = CHAIRMAN_MODEL_OPENROUTER
    TITLE_MODEL = TITLE_MODEL_OPENROUTER
    CODE_REVIEW_MODEL = CODE_REVIEW_MODEL_OPENROUTER
    JUDGE_MODEL = os.getenv("JUDGE_MODEL", JUDGE_MODEL_OPENROUTER)
    RESPONSE_WRAPPER = None  # OpenRouter returns standard OpenAI format
else:
    raise ValueError(
        f"Unsupported API_PROVIDER={API_PROVIDER!r}; only 'openrouter' is registered"
    )

CHAIRMAN_MODEL_FAMILY = infer_model_family(CHAIRMAN_MODEL)
COUNCIL_MODEL_FAMILIES = {
    model: infer_model_family(model)
    for model in COUNCIL_MODELS
}
validate_chairman_heterogeneity(
    COUNCIL_MODELS,
    CHAIRMAN_MODEL,
    provider=API_PROVIDER,
)

# Data directory for conversation storage
# Use LLM_COUNCIL_ROOT if set (for MCP server), otherwise use relative path
_project_root = os.getenv("LLM_COUNCIL_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(_project_root, "data", "conversations")

# SQLite database path
DB_PATH = os.path.join(_project_root, "data", "council.db")
