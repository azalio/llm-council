# CLAUDE.md - Technical Notes for LLM Council

This file contains technical details, architectural decisions, and important implementation notes for future development sessions.

## Project Overview

LLM Council is a deliberation system where multiple LLMs collaboratively answer user questions. The standard pipeline has 3 stages (generate → rank → synthesize). **Deep mode** extends this to 5 stages (generate → rank → critique → revise → synthesize) for higher-quality answers on complex questions, while **quick mode** answers simple low-risk prompts with one chairman-model call and self-check instructions. The public `mode` policy supports `auto`, `quick`, `standard`, and `deep`; `thorough=True` remains a deprecated alias for deep mode. The key innovation is anonymized peer review, preventing models from playing favorites.

**Multi-turn conversations** are supported via `conversation_id`. Follow-up questions go through Stage 0 (reformulation into standalone question), and the chairman receives full conversation context. Council members always see a standalone question (no history bias). Rolling summaries are maintained for efficient context building.

### API Provider Support

The system ships OpenRouter as its API provider (`API_PROVIDER=openrouter`, uses
`OPENROUTER_API_KEY`), resolved through a pluggable provider registry
(`backend/providers/registry.py`) rather than a hardcoded conditional. Adding
another provider means implementing `backend/providers/<name>.py` against the
`Provider` Protocol (`backend/providers/base.py`) and registering a loader for
it — see "API Provider Architecture" below.

## Architecture

### Backend Structure (`backend/`)

**`config.py`**
- `API_PROVIDER`: The active provider name; only `"openrouter"` is registered in this build
- `COUNCIL_MODELS`: List of model identifiers (format depends on provider)
- `CHAIRMAN_MODEL`: Model that synthesizes final answer
- `CHAIRMAN_MODEL_FAMILY`: Inferred provider family used to keep the chairman outside every active council family
- `TITLE_MODEL`: Fast/cheap model for conversation title generation
- `COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD`: Top-1 ranking vote-share threshold at or below which the council is treated as split
- `COUNCIL_CONFIDENCE_ESCALATION_ENABLED`: When true, `mode="auto"` runs that initially select standard mode escalate into deep critique/revision after Stage 2 if ranking confidence is low. Explicit `standard` requests do not escalate.
- `ANSWER_CACHE_SIMILARITY_THRESHOLD`: Conservative first-turn answer-cache threshold. Substantive exact normalized repeats score 1.0; near-duplicates need high token overlap and only run on context-free default-auto requests unless callers set `bypass_cache=True`.
- `ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD` and `ANSWER_CACHE_VALIDATION_THRESHOLD`: Local semantic cache thresholds. High-confidence first-turn paraphrases can reuse a prior council-backed answer without full fan-out; borderline semantic matches require one chairman validation call before reuse.
- Answer-cache KPIs are exposed through the existing process-local metrics surface. Use `GET /api/metrics/council`, MCP `get_council_metrics()`, and `python scripts/answer_cache_replay.py --limit 200 --samples 10` before changing cache thresholds.
- LLM-as-a-judge evaluation is operator-facing and does not run during normal `ask_council` requests. `DEFAULT_JUDGE_RUBRIC`, `JUDGE_MODEL`, `JUDGE_TEMPERATURE`, `JUDGE_TOP_P`, `JUDGE_MAX_TOKENS`, and `JUDGE_TIMEOUT_SECONDS` configure `backend.eval.judge`, which compares a chairman synthesis with the best available Stage 1 baseline under the strict `judge.v1` JSON schema.
- `JUDGE_BINARY_ENABLED` (default false) selects a BINEVAL-style hybrid judge: the `factuality` criterion is scored by an atomic yes/no checklist (`backend/eval/factuality_checklist.py`, `CHECKLIST_VERSION`) answered in one isolated call per answer, while the other criteria stay holistic. `JUDGE_BINARY_TIE_MARGIN` (default 0.05) sets the deterministic winner tie band; `JUDGE_BINARY_CRITICAL_CAP` (default 0.5) caps the factuality score on any failed `critical` question. The result keeps `judge.v1` and adds `judge_variant` + `experimental.binary_factuality`. See `docs/bineval-ab-plan.md` and `docs/judge-evaluation.md`; A/B harness is `scripts/judge_binary_ab.py`.
- `JUDGE_ORDER_SWAP_ENABLED` (default false) judges each pair in both orderings and combines them (agreement→winner, flip→tie, averaged scores), removing pairwise position bias at 2× cost while keeping holistic discrimination. Adds `judge_variant=holistic_order_swap` + `experimental.order_swap`. Ignored when `JUDGE_BINARY_ENABLED`/`JUDGE_ENSEMBLE_ENABLED`. The A/B harness (`scripts/judge_binary_ab.py`) measures `holistic`/`holistic_swap`/`binary` together.
- `COUNCIL_STAGE2_COUNTERBALANCE` (default false) makes Stage 2 peer-ranking show each ranker a rotated response order (Latin square over rankers), then relabels each ranker's output back to canonical labels so positional/label bias cancels in aggregate at no extra model cost. Relabeling keeps the canonical `label_to_model` contract, so aggregation, confidence, chairman, and UI are unchanged. Counterbalanced rankers use per-ranker prompts (`asyncio.gather` over `query_model`, like Stage 2b) instead of the shared-prompt `query_models_parallel`. Probe: `scripts/stage2_position_bias.py`. When changing it, run `pytest -q tests/test_stage2_counterbalance.py`. `JUDGE_TEMPERATURE=0.0` remains the default for reproducible calibration artifacts; `JUDGE_ENSEMBLE_ENABLED`, `JUDGE_ENSEMBLE_SAMPLES`, and `JUDGE_ENSEMBLE_TEMPERATURES` optionally run an operator-facing Ensemble Thermo-Judge that majority-votes parseable samples and records per-sample verdicts, ambiguity entropy, and flip-rate.
- Deliberation mode policy: API/MCP callers default to `mode="auto"`; direct `run_full_council()` defaults to `mode=None`, which resolves to standard for compatibility unless `thorough=True` selects deep. Auto uses deterministic heuristics plus a capped 2-second `TITLE_MODEL` classifier and falls back to standard on uncertainty or classifier failure.
- `DB_PATH`: SQLite database path (`data/council.db`)
- Uses environment variables: `API_PROVIDER`, `OPENROUTER_API_KEY`
- Stage 1 fan-out controls: `COUNCIL_STAGE1_MAX_CONCURRENCY` (default `3`) and `COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS` (default `0.25`)
- Stage 3 (chairman) `query_model()` timeout is mode-aware, not the hardcoded provider default: `COUNCIL_STAGE3_TIMEOUT_SECONDS` (default `600.0`) for standard/quick, `COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS` (default `1200.0`) whenever `stage2b_results` is present (deep mode, or auto-standard escalated into deep), since the deep-mode chairman prompt embeds Stage 1 + Stage 2 + Stage 2b + hedge/attribution instructions and is provably larger.
- `COUNCIL_ADAPTIVE_ROUTING_ENABLED`: When true, routine `mode="auto"` requests that resolve to standard mode start with a sparse council subset and expand to the full council on Stage 1 under-response, insufficient rankings, unavailable confidence, or low confidence.
- Backend runs on **port 8001** (NOT 8000 - user had another app on 8000)

**Key config for the OpenRouter provider:**
| Provider | Auth Header | Response Format | Model Format |
|----------|-------------|-----------------|--------------|
| OpenRouter | `Bearer <token>` | Standard OpenAI | `vendor/model` (e.g., `openai/gpt-5.1`) |

**`providers/` package** (provider registry and pluggable implementations)
- `providers/base.py`: Defines `Provider`, a `runtime_checkable` structural `typing.Protocol` documenting the three-call contract every provider satisfies — `build_request(model, messages, generation_options)`, `parse_response(vendor_or_none, data)`, and `resolve_auth()`. Implementations don't need to subclass it; any module/object exposing these callables qualifies. This module is documentation-only — it doesn't wire anything into the running app.
- `providers/registry.py`: `PROVIDER_REGISTRY` is a plain `Dict[str, ProviderLoader]` mapping `"openrouter"` to a zero-argument lazy loader callable (`_load_openrouter`). Loaders are lazy so resolving one provider never forces an import of another. `resolve_provider(name)` is the single point where a provider is actually loaded: it distinguishes an unregistered `API_PROVIDER` value (`KeyError`, a config typo) from a registered-but-missing provider module (`ModuleNotFoundError`/`ImportError`), converting the latter into an actionable `RuntimeError` that names `API_PROVIDER=openrouter` as the fix. **Adding a new provider**: write `backend/providers/<name>.py` satisfying the `Provider` Protocol, then register a loader for it in `PROVIDER_REGISTRY`. This is an extension point only — no additional connectors are implemented by the current registry.

**`openrouter.py`** (the OpenRouter provider, dispatched through the registry)
- `query_model()`: Single async model query via the configured provider. Resolves the query function via `_resolve_query_fn(API_PROVIDER)`, which calls `providers.registry.resolve_provider()`.
- `query_models_parallel()`: Parallel queries using `asyncio.gather()`, with optional in-flight concurrency caps and per-provider-path backoff after failures
- Returns dict with 'content', optional 'reasoning_details', and 'usage'
- Successful and failed provider calls now also return `_debug` metadata with request ID, provider, duration, and typed failure info. Callers should treat `_debug.ok == false` or `content is None` as failure.
- `usage` (top-level, sibling of `content`) is a normalized `{prompt_tokens, completion_tokens, total_tokens}` dict via `backend/usage.py`, or `None` when the provider reported no usage. For OpenRouter it comes from `data['usage']`.
- `_query_openrouter()` uses standard OpenAI format directly in this module (the registry's `"openrouter"` loader just returns this module itself)
- Graceful degradation: returns a typed failure result on provider errors, and the council continues with successful responses

**`usage.py`** (token usage normalization)
- `normalize_openai_usage(data)` / `normalize_anthropic_usage(data)` / `normalize_google_usage(data)`: Map each provider/vendor's raw usage shape (OpenAI-style `usage.prompt_tokens`/`completion_tokens`, Anthropic's `usage.input_tokens`/`output_tokens`, Google's `usageMetadata.promptTokenCount`/`candidatesTokenCount`) to a common `{prompt_tokens, completion_tokens, total_tokens}` dict. Returns `None` when the source has no usage data, so callers can tell "no data" from "zero tokens".
- `sum_usage(usages)`: Sums an iterable of normalized usage dicts, skipping `None` entries; returns `None` (not a zeroed dict) when nothing in the iterable had usage. Reflects only the calls that reported usage — if some models in a stage report it and others don't, the total is "reported tokens," not necessarily every requested model's tokens.
- All int coercion goes through `_coerce_int()`, which returns `None` instead of raising on a malformed field — a garbled usage value must not turn an otherwise-successful model call into a reported failure.
- `normalize_anthropic_usage()` deliberately does NOT add `cache_creation_input_tokens`/`cache_read_input_tokens` on top of `input_tokens`: Anthropic's `input_tokens` already includes cache reads/writes, so summing them would double-count.
- Used by `openrouter.py` (per-call), `council.py` (per-stage and `_combine_stage_debug` aggregation), and `metrics.py` (run-level rollup in `build_council_run_debug()`).

**`council.py`** - The Core Logic
- `build_conversation_context(conversation, max_recent_turns=3)`: Pure function that extracts context from a stored conversation for multi-turn support. Returns `{summary, recent_turns, previous_final_answer}` or `None` if no history. Truncates answers (2000 chars) and previous_final_answer (3000 chars).
- `stage0_reformulate(user_query, conversation_context)`: Uses `TITLE_MODEL` (30s timeout) to rewrite a follow-up question as a standalone question. Falls back to original query on failure.
- `stage1_collect_responses()`: Parallel queries to all council models using the configured Stage 1 concurrency cap and provider-path backoff
- `backend/agent_router.py`: Deterministic sparse router for routine auto-standard requests. It selects a small high-priority model subset from configured model metadata, keeps high-risk prompts on the full council, and records expansion metadata when confidence/failure conditions require the full pool.
- `stage2_collect_rankings()`:
  - Anonymizes responses as "Response A, B, C, etc."
  - Creates `label_to_model` mapping for de-anonymization
  - Prompts models to evaluate and rank (with strict format requirements)
  - Returns tuple: (rankings_list, label_to_model_dict)
  - Each ranking includes both raw text and `parsed_ranking` list
- `stage2a_collect_critiques()` *(thorough mode only)*: All models critique all anonymized responses in parallel. Prompt uses `## Critique of Response X` headers for structured output.
- `extract_critiques_for_response()`: Helper that transposes the critique matrix — extracts all critics' feedback for a single response label. Parses `## Critique of Response X` headers with regex fallback. Takes an optional `target_model` (the model that produced the Stage 1 response being critiqued) and, by default, excludes that model's own critique of itself — same-model self-critique is same-model self-evaluation, which arXiv:2606.28050 shows can be less reliable than generation (issue #32). Self-critique identity is exact model-string equality, matching how model identity is tracked everywhere else in the pipeline (no alias/canonicalization layer exists elsewhere either). `include_self=True` (wired from `STAGE2B_INCLUDE_SELF_CRITIQUES`, off by default) keeps it for explicit experiments; `target_model=None` disables filtering entirely (legacy/no-mapping callers). Remaining critics are relabeled `Critic A`, `Critic B`, ... via `_critic_label()` (Excel-column style past 26, avoiding `chr()` overflow) with no gaps after exclusion. An empty critique bundle (e.g. a single-model council excluding its own self-critique) is a safe degradation, not a bug: the evidence-gated revision prompt already treats "no critique points" as "keep the original answer with minimal edits." Returns `(critique_text, {"critics_available", "critics_included", "self_critiques_excluded"})`.
- `stage2b_collect_revisions()` *(thorough mode only)*: Each model revises its OWN response from aggregated **peer** critiques (self-critiques excluded by default per above) under an evidence-gated policy: critiques are untrusted suggestions, unsupported objections should be ignored, and correct original content should be preserved unless a critique cites checkable evidence. Uses `asyncio.gather` over individual `query_model()` calls (different prompts per model, unlike `query_models_parallel`). Stage debug carries `self_critique_policy` (`"excluded"`/`"included"`), `critics_available_total`, and `self_critiques_excluded_total`; each per-model result also carries its own `critique_stats`.
- `stage3_synthesize_final()`: Chairman synthesizes from all responses + rankings. Accepts optional `stage2b_results`, `conversation_context`, and `council_confidence` — when context provided, chairman prompt includes conversation history section; when confidence is low, the chairman is instructed to warn that the council was split and avoid overconfident contested claims. When Stage 2b revisions are present, the chairman compares them against the original Stage 1 responses and rankings instead of treating revisions as unconditional primary evidence. The chairman prompt also requires `[A]`/`[A, C]` attribution markers on verifiable claims, and the returned Stage 3 payload includes `attribution` validation metadata.
- `validate_chairman_attribution()`: Deterministic post-hoc validator for Stage 3 output. It scans claim-sized markdown lines/sentences for verifiable patterns (numbers, acronyms, code identifiers, quotes, backticked text) and counts claims missing valid council attribution markers. Explicit abstentions that start with `No council member discussed` are allowed without a marker.
- `backend/eval/judge.py`: Post-hoc evaluation helper for operator runs and benchmarks. It selects the best available Stage 1 baseline from aggregate rankings, asks the configured judge model for strict JSON scores, validates every rubric score, and returns `available=false` for failed or unparseable judge calls instead of treating them as product metrics. The optional Ensemble Thermo-Judge keeps failed/unparseable samples in the artifact but excludes them from the majority vote. The repo does not set explicit provider seeds, so ensemble diversity relies on provider-side nondeterminism. The judge prompt is deliberately blind: it scores a neutral "candidate vs baseline" with no hint that the candidate is the council's own synthesis, so the score is not biased toward the home team.
- `backend/eval/leakage_audit.py`: Answer-leakage audit (arXiv:2606.05037 analogue) that guards the eval surface before its numbers set thresholds. `audit_judge_prompt()`/`audit_live_judge_prompt()` scan the assembled judge prompt for verdict-priming language (response-channel leak); `audit_eval_fixture()`/`audit_eval_fixtures()` scan judge-visible fixture inputs for schema-only tokens that leak the verdict (task-channel leak). It also covers the binary-judge and BINEVAL-replication prompts/question banks (`audit_live_binary_judge_prompt()`, `audit_binary_checklist()`, `audit_live_bineval_prompts()`, `audit_bineval_questions()`). `scripts/audit_eval_leakage.py` runs all of them as a CI gate with a non-zero exit on any finding.
- `backend/eval/bineval.py` + `backend/eval/qags_dataset.py`: Faithful offline replication of the BINEVAL paper's evaluation-quality protocol (arXiv:2606.27226, Part I) — task-level meta-prompt question generation (`generate_binary_questions`, `F_LLM(T; M)`), source-grounded pointwise per-question scoring (`score_summary_decomposed`), and G-Eval-style holistic / single-Boolean baselines, run on QAGS with Spearman/Kendall/Pearson vs human labels. Driven by `scripts/bineval_replication.py`; nothing here is imported by `ask_council` or the production judge. **Measured verdict (`docs/bineval-results.md` §8): H1 does not reproduce — holistic scoring ties (strong model) or beats (weak model) 7-question decomposition at 1/7 the cost, so binary decomposition is not adopted.**
- `backend/eval/self_eval_asymmetry.py` (issue #31, arXiv:2606.28050): Offline self-evaluation asymmetry benchmark for quick/deep verifier surfaces. For each case in a small fixed local corpus (`tests/fixtures/self_eval_asymmetry.json`: short-answer/numeric/multi-hop/false-premise, each with a deterministic check), the model under test generates an answer, then separately self-evaluates it (`yes`/`no`), plus C-MASK (candidate redacted) and C-SWAP (candidate replaced by a plausible-wrong answer) ablations. `compute_asymmetry_metrics()` reports GA, EA, Delta=EA−GA, evaluation precision/recall/F1 (a rubber-stamp "always yes" evaluator scores recall=1.0 but precision no better than GA, exposing it as unreliable), and C-MASK flip-rate / C-SWAP rejection-rate (or an explicit `unavailable_reason`, never silently omitted). Driven by `scripts/self_eval_asymmetry.py --self-test` (deterministic, offline) / `--live`; operator-facing only, not imported by `ask_council`, and deliberately NOT wired into `scripts/audit_eval_leakage.py` since it isn't a comparative/pairwise judge prompt (see `docs/self-evaluation-asymmetry.md` for why).
- `parse_ranking_from_text()`: Extracts "FINAL RANKING:" section, handles both numbered lists and plain format
- `calculate_aggregate_rankings()`: Computes average rank position across all peer evaluations
- `compute_council_confidence()`: Builds a UI-safe confidence signal from Stage 2 rankings. It requires at least two complete rankings, computes top-1 stability, Kendall-like rank agreement, disagreement score, and top-model rank spread, and marks low confidence when top-1 stability is at or below `COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD`.
- `generate_conversation_summary(previous_summary, user_query, council_answer)`: Uses `TITLE_MODEL` (30s timeout) to produce/update a rolling summary (max 300 words). Returns `None` on failure (non-critical).
- `backend/answer_cache.py`: Pre-council cache for completed first-turn answers. It scans recent stored user/assistant pairs, reuses exact normalized repeats, high-overlap near-duplicates, and high-confidence local semantic paraphrases, runs chairman validation for borderline semantic matches, prepends a cached-answer notice to Stage 3 markdown, persists `metadata.answer_cache`, records hit/miss/validation/latency KPIs, and deliberately skips context-bearing follow-ups, clarification-gated requests, explicit non-auto modes, and thorough/deep requests.
- `stage_minus_1_intent_check(user_query)`: Optional first-turn clarification gate. Uses `TITLE_MODEL` (30s timeout) to return `None` for clear questions or a clarification payload for ambiguous questions. The payload carries the prose `question` plus a machine-readable `recovery_feedback` block (`INTENT_DISAMBIGUATION`, schema `recovery_feedback.v0_1`, built by `build_clarification_recovery_feedback()`): each parsed `INTERPRETATION:` line becomes a `RETRY_WITH_REFINED_QUESTION` suggestion an agent caller can resend directly, with a terminal `PROVIDE_CLARIFICATION` suggestion for the human path. `build_clarification_result()` surfaces this in the Stage 3 result and the MCP output (`format_recovery_feedback()`); the prose `question` is preserved for the UI. It is only used when callers pass `clarify_when_unclear=True`; follow-up questions skip it and use Stage 0 instead.
- `classify_deliberation_mode(user_query)` / `resolve_deliberation_mode(...)`: Select `quick`, `standard`, or `deep` for auto mode using deterministic prompt features plus a capped `TITLE_MODEL` classifier. High-confidence quick/deep heuristics skip the classifier; classifier failures and unparseable output fall back to standard.
- `stage_quick_answer(user_query, conversation_context=None, standalone_query=None)`: Quick mode path. Calls only the chairman model with explicit self-check instructions, records a `quick_answer` debug stage, and returns Stage 3-shaped output with `mode="quick"`.
- `run_full_council(user_query, thorough=False, mode=None, conversation_context=None, progress_callback=None, clarify_when_unclear=False)`: Orchestrates the full pipeline. When `clarify_when_unclear=True` and there is no conversation context, runs Stage -1 first and may return a `clarification-gate` assistant result before Stage 1. When `conversation_context` is provided, runs Stage 0 first; council members get standalone question, chairman gets original + context. Returns `(stage1, stage2, stage3, metadata)` where metadata includes `deliberation_mode`, `clarification` for short-circuits, `stage0_standalone_query` (when context provided), `council_confidence`, `confidence_escalation` when auto-standard low confidence triggers deep critique/revision, and `stage2a`/`stage2b` (when deep or escalated). The optional async progress callback receives `(progress, total, message)` at stage boundaries and is best-effort; callback failures are logged but never abort the run.
- `run_full_council()` metadata now includes `debug` with `request_id`, per-stage timings, success/failure counts, and failed model summaries for degraded runs.
- `run_full_council()` now also feeds a rolling in-memory KPI collector in `backend/metrics.py`, so aggregate success/degradation counters track the same debug payload used for per-request inspection.
- `run_full_council()` metadata includes `agent_routing` for routed runs. The streaming FastAPI endpoint mirrors this logic manually, so routing changes must update both paths.
- Token/cost accounting (issue #26, tokens only — no $-pricing table yet): every stage function (`stage1_collect_responses`, `stage2_collect_rankings`, `stage2a_collect_critiques`, `stage2b_collect_revisions`, `stage3_synthesize_final`, `stage_quick_answer`) stores each model's normalized `usage` on its per-model result dict via `_with_usage()` (which OMITS the `usage` key entirely rather than storing `usage: null`, so it rides cleanly into storage's existing stage1-2b JSON blobs with no schema change) and aggregates a stage-level `usage` total into `stage_debug` via `sum_usage()`. `_combine_stage_debug()` (adaptive-routing sparse→full expansion) also sums `usage` across the merged debug entries, computed after `**extra` is merged so a real summed value always wins. `build_run_status()` surfaces both the run-level and per-stage `usage` blocks as UI-safe metadata. All of this is additive: stages/runs where no provider reported usage simply omit the `usage` key rather than showing zeros. Quick mode's `stage1_results` reconstruction is duplicated in two places — `run_full_council()` in `council.py` and the streaming endpoint in `main.py` — both call `_with_usage(..., stage3_result.get("usage"))`; this is the same cross-path drift risk as `agent_routing` above, so usage changes touching quick mode must update both.

**`metrics.py`**
- `CouncilMetricsCollector`: Process-local rolling KPI collector derived from council `debug` metadata
- The metrics snapshot includes `agent_routing` counters for eligible, applied, expanded, sparse-completed runs, saved initial model calls, model-count averages, and expansion reasons.
- The metrics snapshot also includes a `tokens` block (`totals.runs_with_usage/prompt_tokens/completion_tokens/total_tokens`, `average_total_tokens_per_run`), accumulated from each run's `debug.get("usage")` in `record_run()`; runs with no usage data simply don't increment it.
- `build_council_run_debug()`: Shared helper so `run_full_council()` and the streaming FastAPI path emit the same top-level run summary. It also sums `usage` across all present stage debugs (via `backend/usage.py`'s `sum_usage()`) into a top-level `debug["usage"]`, omitted when no stage reported any.
- `get_council_metrics_snapshot()`: Returns machine-readable counters for success rate, Stage 1 degradation rate, per-stage latency percentiles, and rolling token totals

**`storage.py`**
- SQLite-based conversation storage in `data/council.db`
- WAL mode for concurrent reads, `threading.local()` for thread-safe connections
- Schema: `conversations` table (id, created_at, title, message_count, summary) + `messages` table (conversation_id, position, role, content, stage1-3, stage2a, stage2b)
- `summary` column added via idempotent ALTER TABLE migration in `_ensure_schema()`
- Stage data stored as JSON blobs in TEXT columns
- `add_assistant_message()` accepts optional `stage2a`/`stage2b` kwargs for thorough mode
- `update_conversation_summary(conversation_id, summary)`: Updates rolling summary
- `get_conversation_summary(conversation_id)`: Lightweight summary fetch (no messages)
- `get_conversation()` includes `summary` in returned dict when present
- Assistant messages persist safe UI metadata (label_to_model, aggregate_rankings, council_confidence, stage0_standalone_query, degraded-run status) so saved conversations can be reloaded faithfully; raw debug/provider internals remain response-only
- Per-model token `usage` needs no schema change: it rides inside the existing stage1-2b JSON blobs (each per-model dict gained a `usage` key), and the run-level/per-stage usage rollup rides inside the existing `run_status` entry of the `metadata` column

**`migrate_json_to_sqlite.py`**
- One-time migration script: reads `data/conversations/*.json`, inserts into SQLite
- Renames old directory to `data/conversations_backup/`
- Run with: `python -m backend.migrate_json_to_sqlite`

**`main.py`**
- FastAPI app with CORS enabled for localhost:5173 and localhost:3000
- `SendMessageRequest` has `content: str`, `mode: str = "auto"`, `thorough: bool = False`, and `clarify_when_unclear: bool = False`
- `SendMessageRequest` also has `bypass_cache: bool = False`; default-auto first-turn requests may return a cached answer before any model fan-out when a safe stored match exists.
- POST `/api/conversations/{id}/message` returns metadata in addition to stages; builds conversation context for follow-up messages, fires async summary update
- POST `/api/conversations/{id}/message/stream` emits SSE events including `stage0_start/complete` (multi-turn), `stage2a_start/complete` and `stage2b_start/complete` (thorough mode)
- GET `/api/metrics/council` returns rolling process-local KPI JSON derived from council runs
- Multi-turn: both endpoints call `build_conversation_context()` for non-first messages, pass `conversation_context` to `run_full_council()`, and generate rolling summaries after each turn
- Metadata includes: deliberation_mode, label_to_model mapping, aggregate_rankings, council_confidence, stage0_standalone_query (when context), and optionally stage2a/stage2b
- The streaming endpoint now records the same council KPI summaries as `run_full_council()` and short-circuits cleanly when Stage 1 returns zero successful members

### MCP Server (`mcp_server/`)

**`server.py`**
- Exposes council tools via MCP protocol (FastMCP) for Claude Desktop and other MCP clients
- `ask_council(question, thorough=False, mode="auto", conversation_id="", include_debug=False, clarify_when_unclear=False)`: Main tool — runs council deliberation, returns chairman's answer + conversation_id. Pass `conversation_id` from previous call for multi-turn. Empty = new conversation (auto-created with UUID). `mode="auto"` selects quick/standard/deep; `thorough=True` remains a deprecated alias for deep mode. When `clarify_when_unclear=True` on a first-turn question, ambiguous inputs return one clarifying question and store that assistant turn instead of running Stage 1. When `include_debug=True`, appends request diagnostics including stage timings and failed-model counts. Persists user/assistant messages, generates title on first message, maintains rolling summary.
- `ask_council` also accepts `bypass_cache=False`. On eligible first-turn cache hits, MCP stores the user turn plus cached assistant turn and returns the cached chairman answer with an explicit cache notice instead of running `run_full_council()`.
- `ask_council` receives FastMCP `Context` internally and forwards `run_full_council()` progress through `Context.report_progress()`. A heartbeat re-emits the latest progress every 25 seconds while a single stage is still running; `ctx` must remain absent from the public MCP tool schema.
- MCP cancellation must propagate as `asyncio.CancelledError` through `_execute_council_deliberation()`, `run_full_council()`, provider fan-out, and Stage 2b revision fan-out. Fan-out stages create explicit child tasks, cancel and await them on parent cancellation, and MCP storage should retain only the user turn, never a partial assistant message.
- `list_conversations()`, `get_conversation(id)`, `get_available_models()`: Read-only tools. `get_conversation` includes summary when present.
- `get_council_metrics()`: Returns the same rolling process-local KPI snapshot as formatted JSON for MCP clients, including the rolling `tokens` block
- `format_council_output()`: Formats full deliberation chain as markdown (includes Stage 2a/2b sections when present)
- `format_debug_output()`: When `include_debug=True`, renders a `- Tokens: prompt + completion = total` line under the run summary (from `debug["usage"]`) and a per-stage `Tokens: N total` line, both omitted when the run has no usage data
- Uses `LLM_COUNCIL_ROOT` env var for data directory resolution
- 60-minute timeout for council deliberation (reasoning models are slow); overridable via `COUNCIL_TIMEOUT_SECONDS`

**State & session contract (explicit, not implicit)**

llm-council is primarily a **Tool Orchestrator** (one call fans out to several models and hides the mechanics), layered with a **Stateful Session Server** for multi-turn: the state is `conversation_id`, backed by SQLite (`backend/storage.py`). This is called out explicitly here because per-session state that only shows up in a docstring, not in the tool-surface shape, is an easy blind spot — an outside description of "asks several models and synthesizes an answer" gives no hint that a session exists underneath.

- **Lifecycle**: empty `conversation_id` → new conversation auto-created (UUID), returned to the caller. Non-empty `conversation_id` → loaded; unknown id → explicit `"Error: Conversation {id} not found."` (never silently creates one). There is no session expiry — conversations persist in `data/council.db` indefinitely.
- **The undetectable failure mode**: the server cannot tell "caller intentionally wants a new conversation" apart from "caller forgot to pass back the id from a follow-up" — both look like an empty `conversation_id`. If a model drops the id, the council silently starts a fresh conversation with no history and no error. This is inherent to the interface, not a bug to "fix" — the mitigation is on the description/recovery side:
  - `ask_council` and `start_council_async`'s docstrings state directively that the id must be captured and passed back, and that dropping it fails silently (per the "tool description is a prompt for a model that can't ask a follow-up question" principle — matches Anthropic's/Block's engineering write-ups on MCP tool design).
  - `list_conversations()` is documented as the recovery path: if a caller suspects it lost track of a conversation_id, list conversations (sorted newest-first) and find it there.
  - `start_council_async`'s `conversation_id` is additionally surfaced as a **structured JSON field** by `poll_council_task()` (via `_execute_council_deliberation(..., on_conversation_resolved=callback)`, set as soon as the id is known — before the run finishes) rather than requiring the caller to regex the trailing `"Council conversation: <id>"` marker out of the prose `result` string. `ask_council`'s synchronous path returns plain text (an intentional MCP tool contract), so that trailing marker remains the only channel there.
- When changing conversation_id handling, run `pytest -q tests/test_mcp_conversation_state.py tests/test_multi_turn.py tests/test_resiliency_debug.py tests/test_mcp_surface.py` and, if any tool docstring changed, `python scripts/audit_mcp_surface.py --update-snapshot` to refresh the committed golden snapshot.

### Frontend Structure (`frontend/src/`)

**`App.jsx`**
- Main orchestration: manages conversations list and current conversation
- Handles message sending and metadata storage
- Important: the UI keeps full request debug in memory for fresh responses, while persisted assistant metadata stores only the safe subset needed to reconstruct rankings and degraded-run status after reload

**`components/ChatInterface.jsx`**
- Multiline textarea (3 rows, resizable)
- Enter to send, Shift+Enter for new line
- User messages wrapped in markdown-content class for padding

**`components/Stage1.jsx`**
- Tab view of individual model responses
- ReactMarkdown rendering with markdown-content wrapper

**`components/Stage2.jsx`**
- **Critical Feature**: Tab view showing RAW evaluation text from each model
- De-anonymization happens CLIENT-SIDE for display (models receive anonymous labels)
- Shows "Extracted Ranking" below each evaluation so users can validate parsing
- Aggregate rankings shown with average position and vote count
- Explanatory text clarifies that boldface model names are for readability only

**`components/Stage3.jsx`**
- Final synthesized answer from chairman
- Green-tinted background (#f0fff0) to highlight conclusion
- Shows degraded-run, quick-mode, and low-confidence banners from persisted assistant metadata
- Shows a Stage 3 attribution key (`[A]`/`[B]` → model) and an attribution warning when the post-hoc validator finds precise claims without council support markers

**Styling (`*.css`)**
- Light mode theme (not dark mode)
- Primary color: #4a90e2 (blue)
- Global markdown styling in `index.css` with `.markdown-content` class
- 12px padding on all markdown content to prevent cluttered appearance

## Key Design Decisions

### Stage 2 Prompt Format
The Stage 2 prompt is very specific to ensure parseable output:
```
1. Evaluate each response individually first
2. Provide "FINAL RANKING:" header
3. Numbered list format: "1. Response C", "2. Response A", etc.
4. No additional text after ranking section
```

This strict format allows reliable parsing while still getting thoughtful evaluations.

### De-anonymization Strategy
- Models receive: "Response A", "Response B", etc.
- Backend creates mapping: `{"Response A": "openai/gpt-5.1", ...}`
- Frontend displays model names in **bold** for readability
- Users see explanation that original evaluation used anonymous labels
- This prevents bias while maintaining transparency

### Error Handling Philosophy
- Continue with successful responses if some models fail (graceful degradation)
- Never fail the entire request due to single model failure
- Log errors but don't expose to user unless all models fail

### UI/UX Transparency
- All raw outputs are inspectable via tabs
- Parsed rankings shown below raw text for validation
- Users can verify system's interpretation of model outputs
- This builds trust and allows debugging of edge cases

## Important Implementation Details

### Relative Imports
All backend modules use relative imports (e.g., `from .config import ...`) not absolute imports. This is critical for Python's module system to work correctly when running as `python -m backend.main`.

### Port Configuration
- Backend: 8001 (changed from 8000 to avoid conflict)
- Frontend: 5173 (Vite default)
- Update both `backend/main.py` and `frontend/src/api.js` if changing

### Markdown Rendering
All ReactMarkdown components must be wrapped in `<div className="markdown-content">` for proper spacing. This class is defined globally in `index.css`.

### Frontend Stream State
The React streaming UI must scope incoming SSE updates by both `conversation_id` and a per-stream assistant id. Users can switch conversations while a council run is still emitting, so never apply stream events to "the last visible message" without verifying the originating conversation and stream. The SSE client must buffer partial `data:` lines and treat backend `error` events as terminal.

### Model Configuration
Models are hardcoded in `backend/config.py`. The chairman must be outside the active council by exact model id and inferred provider family; config import and MCP startup fail fast if this invariant is violated. The current default keeps Gemini (`google/gemini-3.1-pro-preview`) as the OpenRouter chairman.

## Common Gotchas

1. **Module Import Errors**: Always run backend as `python -m backend.main` from project root, not from backend directory
2. **CORS Issues**: Frontend must match allowed origins in `main.py` CORS middleware
3. **Ranking Parse Failures**: If models don't follow format, fallback regex extracts any "Response X" patterns in order
4. **Persisted Metadata Is Sanitized**: Saved assistant messages keep ranking metadata, council confidence, and degraded-run status, but raw debug fields (request IDs, providers, status codes, timings) remain response-only. Stage2a/2b data IS persisted.
5. **SQLite Thread Safety**: Each thread gets its own connection via `threading.local()`. Don't share connection objects across threads.
6. **Thorough Mode Latency**: `thorough=True` adds ~2x more LLM calls (critique + revision rounds). Use only for complex questions where quality justifies the wait.
7. **Stage 2b Critiques Are Untrusted**: Deep-mode revisions are evidence-gated because unsupported peer critiques can degrade correct answers. When changing Stage 2b prompts or Stage 3 synthesis, run `pytest -q tests/test_stage2b_revision_policy.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_progress_callback.py` and verify revisions are not treated as unconditional primary evidence.
8. **Metrics Are Process-local**: `backend/metrics.py` keeps rolling KPI state in memory only. Backend and MCP processes each expose their own snapshot, and all counters reset on process restart.
9. **Chairman Heterogeneity**: When changing `COUNCIL_MODELS_*` or `CHAIRMAN_MODEL_*`, run `pytest -q tests/test_chairman_heterogeneity.py` and inspect `get_available_models()` for the active provider because same-family chairman/council overlap now blocks startup.
10. **Stage 1 Fan-out Controls**: When changing provider fan-out, run `pytest -q tests/test_stage1_backoff.py tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py`; `query_models_parallel()` preserves model order but now reserves staggered provider-path starts after failures.
11. **MCP Progress Context**: When changing MCP progress or FastMCP `Context` injection, run `pytest -q tests/test_progress_callback.py` and inspect `mcp.list_tools()` so `ctx` stays internal and heartbeat liveness remains covered.
12. **MCP Cancellation Boundaries**: When changing provider fan-out, heartbeat cleanup, or assistant-message storage timing, run `pytest -q tests/test_progress_callback.py` because it covers child task cancellation, request-context reset, and the no-partial-assistant-message invariant.
13. **Answer Cache Eligibility**: Cache hits are intentionally first-turn/default-auto only, and cache sources must also be first-turn answers. Do not enable cache reuse for follow-ups, clarification-gated prompts, explicit standard/deep/quick requests, or `thorough=True` without adding tests that prove context and mode semantics remain correct. Semantic cache changes also need numeric-collision tests with shared context words, follow-up source rejection, structured validation parsing, borderline chairman-validation pass/fail coverage, runtime KPI checks, and an offline replay sample from `scripts/answer_cache_replay.py`.
14. **Judge Evaluation Boundary**: Judge scores are measurement artifacts, not part of the default answer contract. When changing `backend.eval.judge`, run `pytest -q tests/test_judge_eval.py tests/test_judge_binary.py` and `python scripts/judge_eval_smoke.py --judge both --output /tmp/llm-council-judge-eval-smoke.json`, then inspect the artifact for `available`, `winner`, `scores`, `candidate`, and `baseline` plus `generation`/`ensemble` when temperature ensembling is enabled, and `judge_variant`/`experimental.binary_factuality` when `JUDGE_BINARY_ENABLED`. For binary-path changes also run `python scripts/judge_binary_ab.py --self-test` (offline pipeline check) and the leakage audit, which now covers the binary prompt and checklist. Because these numbers can set thresholds, also run the leakage audit (`pytest -q tests/test_leakage_audit.py` and `python scripts/audit_eval_leakage.py`, exit 0 = clean) before trusting them: changing the judge prompt wording, rubric, temperature policy, or eval fixtures can reintroduce a verdict leak that biases the score. The audit must stay green.
15. **Adaptive Routing Cross-path Drift**: When changing `backend/agent_router.py`, Stage 1/2 model selection, or routing metadata, run `pytest -q tests/test_agent_routing.py tests/test_deliberation_mode.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_stage1_backoff.py tests/test_progress_callback.py tests/test_multi_turn.py tests/test_judge_eval.py` and `python scripts/agent_routing_benchmark.py --output /tmp/llm-council-agent-routing-benchmark.json`. Verify direct orchestration, FastAPI stream, saved metadata, metrics, and judge-smoke quality gates together.
16. **Stage 3 Chairman Timeout Is Mode-aware, Not the Provider Default**: `query_model()`'s own default (`backend/openrouter.py`) is a 600s fallback, not the effective Stage 3 budget — `stage3_synthesize_final()` always passes an explicit `timeout` selected from `COUNCIL_STAGE3_TIMEOUT_SECONDS`/`COUNCIL_STAGE3_DEEP_TIMEOUT_SECONDS` by presence of `stage2b_results`. A deep-mode chairman call previously timed out at the fixed 600s default because deep mode's prompt (Stage 1 + Stage 2 + Stage 2b + hedge/attribution text) is larger than standard mode's. When touching Stage 3 timeout/prompt-size behavior, run `pytest -q tests/test_stage3_chairman_timeout.py tests/test_stage2b_revision_policy.py tests/test_council_metrics.py tests/test_progress_callback.py`.
17. **Usage Is Additive, Never Fabricated**: Token usage (`backend/usage.py`'s `normalize_*_usage`/`sum_usage`) must return `None`, not a zeroed dict, whenever a provider/vendor/stage reports no usage data. Every layer that surfaces usage (`_build_stage_debug`'s `usage` extra, `_combine_stage_debug`, `build_council_run_debug`, `CouncilMetricsCollector`, `build_run_status`) follows this "omit, don't zero" contract so a mixed run doesn't silently under-report cost as zero. `$`-cost/pricing-table conversion is intentionally not implemented yet (tracked as a follow-up to issue #26); only token counts are wired end-to-end. When changing usage plumbing, run `pytest -q tests/test_usage_accounting.py tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py`.
18. **Stage 2b Excludes Self-Critique By Default**: `extract_critiques_for_response()` drops a model's own critique of its own anonymized Stage 1 response before that model revises it (arXiv:2606.28050, issue #32) — same-model self-evaluation can be less reliable than generation, so treating "my own peer said X" as trustworthy peer feedback would be wrong. Controlled by `STAGE2B_INCLUDE_SELF_CRITIQUES` (default `False`, matching the issue's own acceptance criteria); flipping it on is for explicit A/B experiments only, and the active policy is always visible in Stage 2b debug (`self_critique_policy`, `critics_available_total`, `self_critiques_excluded_total`) — never silently switch behavior without that metadata reflecting it. Nothing is destroyed by the default: the full unfiltered Stage 2a critiques (including whatever self-critique got excluded from the Stage 2b prompt) are still persisted verbatim as `metadata["stage2a"]` on the assistant message, so offline analysis of self- vs peer-critique effects remains possible without flipping the flag. When changing Stage 2a/2b critique routing, run `pytest -q tests/test_stage2b_revision_policy.py tests/test_council_confidence.py tests/test_progress_callback.py`.

## Future Enhancement Ideas

- Configurable council/chairman via UI instead of config file
- Export conversations to markdown/PDF
- Model performance analytics over time
- Custom ranking criteria (not just accuracy/insight)
- Support for reasoning models (o1, etc.) with special handling
- Frontend UI for thorough mode toggle and Stage 2a/2b display

## Testing Notes

Use `test_openrouter.py` to verify API connectivity and test different model identifiers before adding to council. The script tests both streaming and non-streaming modes.

## Data Flow Summary

### Default mode (`thorough=False`)
```
User Query (+ optional conversation_id)
    ↓
Load conversation from SQLite (if conversation_id provided)
    ↓
build_conversation_context() → {summary, recent_turns, previous_final_answer}
    ↓
Optional Stage -1: first-turn clarification gate when clarify_when_unclear=True → clarifying question or continue
    ↓
Stage 0: TITLE_MODEL reformulates follow-up → standalone question (multi-turn only)
    ↓
Stage 1: Parallel queries with standalone question → [individual responses]
    ↓
Stage 2: Anonymize → Parallel ranking queries → [evaluations + parsed rankings]
    ↓
Aggregate Rankings Calculation → [sorted by avg position]
    ↓
Council Confidence Calculation → {top1_stability, rank_agreement, low_confidence}
    ↓
Stage 3: Chairman synthesis (original question + conversation context + deliberation)
    ↓
Store result → async summary update
    ↓
Return: {stage1, stage2, stage3, metadata including council_confidence} + conversation_id
    ↓
Frontend: Display with tabs + validation UI
```

### Thorough mode (`thorough=True`)
```
User Query (+ optional conversation_id)
    ↓
Stage 0: Reformulate follow-up → standalone question (multi-turn only)
    ↓
Stage 1: Parallel queries with standalone question → [individual responses]
    ↓
Stage 2: Anonymize → Parallel ranking queries → [evaluations + parsed rankings]
    ↓
Council Confidence Calculation → feeds chairman hedge/abstention instruction
    ↓
Stage 2a: Parallel critique queries → [structured critiques per response]
    ↓
Stage 2b: Evidence-gated per-model revision (each gets own critiques) → [revised responses]
    ↓
Stage 3: Chairman synthesis (original question + context + revised responses checked against originals/rankings)
    ↓
Return: {stage1, stage2, stage3, metadata: {council_confidence, stage2a, stage2b, stage0_standalone_query, ...}}
```

### Asymmetric context strategy (multi-turn)
- **Council members** (Stages 1, 2, 2a, 2b): See only the standalone reformulated question — no history bias
- **Chairman** (Stage 3): Sees original follow-up question + conversation summary + recent turns + previous final answer
- **Stage 0** uses `TITLE_MODEL` (cheap/fast, 30s timeout) for reformulation

The entire flow is async/parallel where possible to minimize latency.
