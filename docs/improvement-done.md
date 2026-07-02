# LLM Council Improvement Done

## Evidence-gated deep-mode revisions for Critique-Induced Confusion [2606.02866]

Shipped 2026-06-03.

Deep-mode and confidence-escalated runs no longer treat peer critiques as authoritative instructions. Stage 2b now builds an evidence-gated revision prompt that tells each model to accept critique points only when they cite specific, checkable evidence from the question, original answer, or another council response; unsupported, vague, or unverifiable objections must be ignored, and the original answer should be preserved when the critique cannot be verified.

The chairman no longer receives Stage 2b revisions as an unconditional primary source. Stage 3 now treats revisions as stronger synthesis evidence only when they preserve the original answer or make an evidence-backed correction, and it compares revisions against the original Stage 1 responses and Stage 2 rankings before using them. Stage 2b results and debug metadata carry `revision_policy="evidence_gated"` so saved/reloaded thorough artifacts remain auditable.

This shipped the core user-value part of the plan item: reducing the chance that a hallucinated critique rewrites a good answer before final synthesis. The broader learned deep-benefit predicate and CIC-incidence benchmark remain future work because this PR changed the live answer path directly without adding a default-off router or telemetry-only mechanism.

Validation shipped with the slice:

- Focused revision-policy coverage in `tests/test_stage2b_revision_policy.py` for the evidence-gated prompt contract, Stage 2b result/debug metadata, and the Stage 3 guardrail that revisions are not unconditional primary evidence
- Cross-entry deep/confidence regression sweep: `pytest -q tests/test_stage2b_revision_policy.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_progress_callback.py`
- Full Python regression sweep: `pytest -q`

## Verification-First chairman synthesis and quick-mode self-check using the council's leading candidate [2511.21734]

Rejected 2026-06-03.

This looked attractive from the paper alone: Stage 3 already has a top-ranked Stage 1 candidate, so a verify-first chairman prompt would be cheap to try, and quick mode already has a self-check slot. Before implementing it, we ran a live A/B on the current model `gpt-5.5` instead of relying on the paper's aggregate benchmark claims.

The live probe compared the existing forward chairman synthesis against a verify-first variant, then judged the pair with the same `gpt-5.5` judge path. Probe cases covered `bat-ball`, `race-2nd`, `number-compare`, and a practical false-choice prompt: "Мне надо помыть машину, и до мойки 3 минуты пешком от моего дома. Что лучше сделать — поехать на машине или дойти пешком?"

Result: baseline and verify-first were both correct on the probe. The judge mostly preferred verify-first only for concision/equivalence, not for a demonstrated correctness uplift, and the car-wash prompt was a `tie` with both variants correctly answering that the user should drive the car. That means the proposed change adds prompt complexity and likely extra synthesis tokens without current evidence of core answer-quality improvement.

The live check did uncover and unblock a separate real issue: OpenAI GPT-5 judge calls rejected `max_tokens`, `temperature=0`, and explicit `top_p`. That provider payload bug was fixed separately in commit `c565830` by using `max_completion_tokens` for `gpt-5.*` and omitting unsupported sampling params.

## Adaptive sparse agent routing for council fan-out [2605.009]

Shipped 2026-05-29.

Added a deterministic adaptive router for routine `mode="auto"` requests that resolve to standard mode. Routine prompts now start Stage 1 and Stage 2 with a sparse council subset chosen from configured model metadata; high-risk prompts use the full council immediately, and sparse runs expand to the full pool when routed Stage 1 produces too few answers, Stage 2 produces too few rankings, council confidence is unavailable, or council confidence is low.

The shipped boundary is default-on for eligible auto-standard requests, not dormant plumbing. Routing metadata is returned and persisted as `agent_routing`, appears in reload-safe `run_status`, is rendered in MCP full-output mode, and is included in process-local metrics with eligible/applied/expanded/sparse-completed counters, saved initial model calls, model-count averages, and expansion reasons. The FastAPI streaming path mirrors the direct orchestrator so browser users and saved conversations see the same route decision.

Validation shipped with the slice:

- Focused routing coverage in `tests/test_agent_routing.py` for deterministic sparse selection, high-risk full routing, direct-orchestrator sparse completion, low-confidence full-pool expansion, metrics, stream metadata, and saved metadata
- Cross-entry regression sweep: `pytest -q tests/test_agent_routing.py tests/test_deliberation_mode.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_stage1_backoff.py tests/test_progress_callback.py tests/test_multi_turn.py tests/test_judge_eval.py`
- Full Python regression sweep: `pytest -q`
- Operator benchmark smoke: `python scripts/agent_routing_benchmark.py --output /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-agent-routing-benchmark.json`, with artifact inspection confirming the current provider pool saves 2 of 6 Stage 1/2 model calls (33.3%) on routine sparse cases, keeps high-risk cases full, expands low-confidence cases back to full, and reports mocked judge quality delta `0.0` within the 5% gate

## LLM-as-a-judge evaluation pipeline [2604.011]

Shipped 2026-05-28.

Added an operator-facing LLM-as-a-judge evaluation path for comparing a chairman synthesis with the best available Stage 1 baseline answer. The shipped boundary is deliberately not default-on for normal `ask_council` requests; it gives future routing, cache, cascade, and benchmark work a structured quality measurement surface without adding latency to user answers.

The evaluation pipeline now lives in `backend/eval/judge.py`. It builds a constrained `judge.v1` JSON prompt, validates every configured rubric score, selects a Stage 1 baseline from aggregate rankings when available, and returns `available=false` for failed or unparseable judge calls instead of turning bad judge output into metrics. Judge model and deterministic generation controls are configured in `backend/config.py`, and the provider client now accepts optional `temperature`, `top_p`, and `max_tokens` values for evaluator-style calls.

Validation shipped with the slice:

- Focused judge coverage in `tests/test_judge_eval.py` for fenced JSON parsing, missing-score rejection, provider failure handling, deterministic request options, aggregate-ranking baseline selection, and chairman-vs-baseline comparison
- Provider/config regression sweep: `pytest -q tests/test_judge_eval.py tests/test_chairman_heterogeneity.py tests/test_resiliency_debug.py`
- Existing confidence/mode baseline remained green: `pytest -q tests/test_council_confidence.py tests/test_deliberation_mode.py`
- Offline operator smoke: `python scripts/judge_eval_smoke.py --output /tmp/llm-council-judge-eval-smoke.json`, with artifact inspection confirming `available=true`, `winner=candidate`, candidate/baseline sources, rubric scores, and deterministic generation metadata

## Confidence-aware selective fallback to stronger remote models [2604.093]

Shipped 2026-05-18.

Added a selective fallback for the core answer-quality path: when `mode="auto"` initially chooses standard mode but Stage 2 rankings are low-confidence, the same run now escalates into the deep peer critique and revision stages before chairman synthesis. This uses the already-shipped council confidence signal rather than adding a dormant stronger-model tier, and explicit `standard` requests still do not escalate so callers who deliberately cap cost keep the 3-stage boundary.

The shipped boundary persists and exposes `confidence_escalation` metadata in direct backend runs, FastAPI streaming events, saved conversation metadata, run status, and MCP full-output formatting. The chairman receives revised responses when escalation triggers, so users asking ambiguous or contested questions get a more scrutinized final answer instead of only a low-confidence warning.

Validation shipped with the slice:

- Focused confidence escalation coverage in `tests/test_council_confidence.py` for auto-standard low-confidence escalation, Stage 2a/2b execution, revised-response synthesis, persisted metadata, run-status propagation, and MCP full-output rendering
- Streaming regression coverage in `tests/test_council_metrics.py` proving the FastAPI stream emits Stage 2a/2b events, persists `confidence_escalation`, and records the escalated run as thorough

## Reuse cached answers for safe first-turn paraphrases [2605.007-2]

Shipped 2026-05-18.

Expanded the answer cache from exact/high-overlap repeat matching to safe first-turn paraphrase reuse. The cache now computes a deterministic local semantic embedding over normalized question terms, serves high-confidence near-duplicate hits without full council fan-out, and runs a single chairman validation call for borderline semantic matches before reusing a prior council-backed answer.

The shipped boundary keeps the existing safety contract: no cache reuse for context-bearing follow-ups, follow-up answers as cache sources, clarification-gated requests, explicit non-auto modes, deep/thorough requests, too-short prompts, already-cached assistant turns, differing numeric-token questions, malformed validation responses, or `bypass_cache=true`. Cache metadata now records match type, token similarity, semantic similarity, source question, and validation details when a chairman check was required.

Validation shipped with the slice:

- Focused semantic cache coverage in `tests/test_answer_cache.py` for paraphrase similarity, numeric-collision rejection, high-confidence semantic direct API hits without model calls, borderline chairman validation pass/fail, malformed validation response rejection, context-dependent follow-up source rejection, bypass, streaming cache-hit events, and MCP schema exposure
- Cross-entry regression sweep: `pytest -q tests/test_answer_cache.py tests/test_deliberation_mode.py tests/test_multi_turn.py tests/test_council_metrics.py`

## Measure answer-cache hit quality and expose cache KPIs [2605.007-3]

Shipped 2026-05-18.

Added answer-cache KPIs to the existing process-local metrics surface used by FastAPI and MCP. Eligible first-turn cache lookups now record hit, miss, match type, similarity sample, validation attempt/approval/rejection, and lookup latency; explicit `bypass_cache=true` requests are counted separately. Cache-hit latency is reported apart from full council stage latency so operators can verify that active cache reuse is reducing user wait time without hiding fresh council runs.

Added `scripts/answer_cache_replay.py`, an offline replay over stored first-turn answers that reports hit rate, match-type mix, top similarities, and manual-review samples without making model calls. `docs/answer-cache-metrics.md` documents how to interpret runtime KPIs, replay output, and threshold decisions. The shipped boundary deliberately does not change cache thresholds or broaden cache eligibility.

Validation shipped with the slice:

- Focused cache metrics coverage in `tests/test_answer_cache.py` for direct FastAPI hits, streaming hits, bypass counters, borderline validation counters, and chronological replay samples
- Focused council metrics regression coverage in `tests/test_council_metrics.py` proving existing council KPI snapshots still work with the new `answer_cache` section
- Operator replay probe: `python scripts/answer_cache_replay.py --limit 20 --samples 3`
- Full Python regression sweep: `pytest -q`

## Reuse cached answers for safe first-turn repeat questions [2605.007-1]

Shipped 2026-05-18.

Added a conservative answer-cache path before model fan-out for context-free default-auto first-turn requests. The cache scans recently completed stored user/assistant pairs, matches substantive exact normalized repeats or very high-overlap near-duplicates, and reuses the prior Stage 1/2/3 artifacts without calling council models. Cache hits prepend an explicit "Served from answer cache" line to the chairman markdown, set `stage3.cached=true`, persist sanitized `metadata.answer_cache`, and expose `bypass_cache` on FastAPI and MCP callers for forced fresh runs.

The shipped boundary intentionally does not handle context-bearing follow-ups, clarification-gated requests, explicit non-auto modes, or broader embedding similarity. Those remain active under `2605.007-2` and `2605.007-3`.

Validation shipped with the slice:

- Focused cache coverage in `tests/test_answer_cache.py` for conservative matching, storage candidate lookup, direct FastAPI cache hits, `bypass_cache`, streaming cache-hit events, deterministic no-model title handling, and MCP schema exposure
- Cross-entry regression sweep: `pytest -q tests/test_answer_cache.py tests/test_deliberation_mode.py tests/test_multi_turn.py tests/test_council_metrics.py`

## Replace the binary `thorough` flag with a complexity-aware deliberation mode [2605.006]

Shipped 2026-05-18.

Added a public `mode` contract for MCP and FastAPI callers: `auto`, `quick`, `standard`, and `deep`. Browser and MCP callers default to `auto`; existing internal orchestration still defaults to standard for compatibility, and `thorough=True` remains a deprecated alias for deep mode on auto/internal compatibility paths.

Auto mode now combines deterministic complexity heuristics with a capped 2-second `TITLE_MODEL` classifier. High-confidence simple factual or arithmetic prompts route directly to quick mode without the classifier call; uncertain classifier failures fall back to standard mode instead of risking under-deliberation. Quick mode makes one chairman-model call with explicit self-check instructions, skips peer ranking, records a `quick_answer` debug stage, and persists the selected mode and reason into safe assistant metadata. Browser users see a Quick mode notice on the final answer so the missing Stage 2 panel is intentional rather than silent.

Validation shipped with the slice:

- Focused mode coverage in `tests/test_deliberation_mode.py` for heuristic quick routing, classifier fallback, `thorough` aliasing, quick-mode orchestration, direct API persistence, streaming quick-mode behavior, and MCP schema exposure
- Regression sweep: `pytest -q tests/test_deliberation_mode.py tests/test_progress_callback.py tests/test_council_metrics.py tests/test_clarification_gate.py`
- Full `pytest -q`
- MCP operator schema smoke proving `mode` is exposed and internal `ctx` remains hidden
- Frontend `npm run lint`
- Frontend production build: `npm run build -- --outDir /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-mode-build --emptyOutDir`

## Detect ambiguous questions and open a one-turn clarification before spending the full council [2605.005]

Shipped 2026-05-18.

Added an opt-in first-turn clarification gate for MCP and FastAPI callers. When `clarify_when_unclear=true`, `stage_minus_1_intent_check()` asks the cheap `TITLE_MODEL` to classify the question as `CLEAR` or `AMBIGUOUS: ...`; ambiguous questions short-circuit before Stage 1 and return one focused clarifying question instead of spending the full council on a likely-wrong intent.

Clarification turns are stored as normal assistant messages with sanitized `clarification` metadata, so the user's reply continues in the same conversation. Follow-up questions skip the gate and keep using Stage 0 reformulation. The default remains off to preserve existing tool behavior until a real ambiguous/unambiguous probe set measures false positives.

Validation shipped with the slice:

- Focused clarification coverage in `tests/test_clarification_gate.py` for classifier parsing, clear-question fallback, orchestrator short-circuit, direct FastAPI persistence, streaming FastAPI short-circuit events, and MCP tool-schema exposure
- Regression sweep: `pytest -q tests/test_progress_callback.py tests/test_council_metrics.py tests/test_multi_turn.py tests/test_resiliency_debug.py`
- Full `pytest -q`
- MCP operator-flow smoke through the FastMCP tool schema proving `clarify_when_unclear` is exposed and internal `ctx` remains hidden

## Mark every chairman-level claim with its supporting council members (or omit it) [2605.003]

Shipped 2026-05-18.

The chairman prompt now requires closed-set attribution markers like `[A]` and `[A, C]` after verifiable claims, and instructs the chairman to omit unsupported facts or explicitly say when no council member discussed the requested fact. Stage 3 runs a deterministic post-hoc validator over the synthesis, counts verifiable claims that lack valid council markers, and exposes that validation in both the Stage 3 payload and reload-safe `run_status` metadata.

Browser users now see an attribution key that maps `[A]`/`[B]` markers back to de-anonymized council models, plus an attribution warning when the validator catches unsupported-looking precise claims. MCP users get a compact attribution key and warning on the default `ask_council` path, and the full-output formatter renders the same information as dedicated sections before the chairman synthesis.

Validation shipped with the slice:

- Focused attribution coverage in `tests/test_chairman_attribution.py` for missing markers, named entities, command flags, compound claims, marker-at-claim-end enforcement, fenced code blocks, explicit abstention lines, Stage 3 prompt requirements, run-status propagation, and MCP brief/full formatting
- Regression sweep: `pytest -q tests/test_chairman_attribution.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_multi_turn.py`
- Full `pytest -q`
- Frontend `npm run lint`
- Frontend production build: `npm run build -- --outDir /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-attribution-build --emptyOutDir`
- Local Vite smoke fetched `http://127.0.0.1:5173`; real-browser DevTools inspection was blocked by the existing Chrome profile lock

## Surface council disagreement and let the chairman abstain or escalate when confidence is too low [2605.002]

Shipped 2026-05-17.

Added a persisted `council_confidence` signal derived from complete Stage 2 rankings. The signal includes top-1 stability, Kendall-like rank agreement, disagreement score, top-model rank spread, incomplete-ranking counts, and a configurable `COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD`. Ties at the threshold and mostly incomplete ranking evidence are treated as low confidence.

The chairman prompt now receives this signal before synthesis. Low-confidence runs instruct the chairman to start with a split-council warning, separate shared findings from contested claims, and avoid overconfident precise conclusions. The direct backend path, streaming FastAPI path, and MCP storage path persist the same sanitized confidence metadata so reloaded conversations keep the signal.

The React Stage 3 view now renders a low-confidence banner next to the existing degraded-run banner. MCP full deliberation formatting also renders the confidence summary for internal/full-output callers.

Validation shipped with the slice:

- Focused confidence coverage in `tests/test_council_confidence.py` for unanimous rankings, three-way split rankings, single-ranking unavailable state, all-incomplete rankings, mixed complete/incomplete rankings, metadata persistence, chairman prompt injection, and MCP full-output formatting
- Stream-path confidence persistence coverage in `tests/test_council_metrics.py`
- Review pass caught and fixed exact-threshold tie handling, malformed-ranking trust handling, and docs drift
- Regression sweep: `pytest -q tests/test_council_confidence.py tests/test_council_metrics.py tests/test_resiliency_debug.py tests/test_multi_turn.py tests/test_progress_callback.py`
- Full `pytest -q`
- Frontend `npm run lint`
- Frontend production build: `npm run build -- --outDir /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-frontend-build`

## Stream MCP council progress notifications and heartbeat liveness [2605.004-1]

Shipped 2026-05-17.

Added an optional `progress_callback` to `run_full_council()` and wired `_execute_council_deliberation()` to pass FastMCP `Context.report_progress()` through each council stage. MCP clients now receive an immediate start notification, per-stage progress for Stage 0/1/2/2a/2b/3 as applicable, and a heartbeat that re-emits the latest progress during long-running stages so the transport does not look idle for minutes.

The slice deliberately does not claim full cancellation or richer frontend progress rendering; parent item `2605.004` now remains as a non-executable anchor with follow-up slices for those user-visible contracts.

Validation shipped with the slice:

- Focused progress coverage in `tests/test_progress_callback.py`
- MCP tool schema inspection proving `ctx` is injected internally and not exposed as a user argument
- Focused regression command: `pytest -q tests/test_progress_callback.py`

## Show richer frontend stream progress and low-confidence transitions [2605.004-2]

Shipped 2026-05-18.

Browser users now see a live council-progress card during streamed runs instead of only coarse Stage 1/2/3 spinners. The card consumes the existing SSE events for Stage 0, Stage 1, Stage 2, Stage 2a, Stage 2b, and Stage 3; preserves the Stage 0 standalone-query metadata; and warns during the wait when Stage 2 detects low confidence so users know the chairman is synthesizing with split-council guardrails.

The frontend now renders thorough-mode peer critiques and revised responses in de-anonymized, keyboard-accessible tab panels. Stream updates are scoped by conversation id plus per-stream id so switching conversations mid-run does not mutate the wrong chat. The SSE client now buffers split chunks, treats backend `error` events as terminal, marks interrupted streams visibly, and avoids leaving unrelated conversations disabled while another conversation is running.

Validation shipped with the slice:

- Focused backend stream regression sweep: `pytest -q tests/test_council_metrics.py tests/test_multi_turn.py`
- Full Python regression sweep: `pytest -q`
- Frontend lint: `npm run lint`
- Frontend production build: `npm run build -- --outDir /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-frontend-progress-build --emptyOutDir`
- Review pass caught and fixed conversation-switch state leakage, transport-error rollback boundaries, malformed SSE handling, duplicated loading indicators, and tab accessibility drift
- Real-browser DevTools smoke was blocked by an existing Chrome profile lock; Vite app HTML was fetched successfully earlier in the loop, and the final production bundle was inspected through the build artifact output

## Propagate MCP cancellation into in-flight council work [2605.004-3]

Shipped 2026-05-18.

MCP users can now cancel a runaway council run with explicit cleanup guarantees: provider fan-out and thorough-mode revision fan-out create tracked tasks, cancel every in-flight model call when the parent task is cancelled, await their cleanup, and re-raise `asyncio.CancelledError` so caller cancellation stays visible instead of being converted into a degraded answer.

The MCP execution path already avoided storing assistant output until the council completed. This slice adds regression coverage proving a cancelled MCP run stores the user turn but no partial assistant message, resets request context, and lets heartbeat cleanup finish without masking the cancellation.

Validation shipped with the slice:

- Focused cancellation and progress coverage in `pytest -q tests/test_progress_callback.py`
- Full Python regression sweep: `pytest -q`
- Review pass checked cancellation propagation, storage boundaries, request-context cleanup, and provider fan-out task cleanup

## Use a heterogeneous (out-of-kinship) chairman to avoid the consensus paradox [2605.001]

Shipped 2026-05-01.

Enforced chairman/council heterogeneity for the configured provider. OpenRouter keeps `google/gemini-3-pro-preview` as chairman and removes the Google family from the council.

Added `infer_model_family()` and `validate_chairman_heterogeneity()` in `backend/config.py`; config import and MCP startup now reject exact chairman/council overlap and same-family overlap with an operator-readable error. `get_available_models()` now displays model families so MCP users can inspect the invariant directly.

Validation shipped with the slice:

- TDD red/green coverage in `tests/test_chairman_heterogeneity.py`
- learned regression sweep: `pytest -q tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py tests/test_chairman_heterogeneity.py`
- full `pytest -q`
- MCP operator checks for `API_PROVIDER=openrouter` through `get_available_models()`

The plan's live evaluation-probe measurement remains paired with the future judge/evaluation work in `2604.011`; this slice shipped the enforceable configuration invariant and visibility surface.

## Add bounded concurrency and provider backoff to Stage 1 fan-out [2604.017-3]

Shipped 2026-05-01.

Added configurable per-run Stage 1 fan-out controls. `COUNCIL_STAGE1_MAX_CONCURRENCY` now caps in-flight provider calls, and `COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS` reserves staggered start times for later calls on a provider path after that path fails during the run. The scheduler releases the global concurrency slot while a provider-path call is waiting, so unrelated provider paths are not blocked by another path's backoff.

Updated Stage 1 debug metadata and logs to include the active limiter and backoff settings, and refreshed `CLAUDE.md` plus `docs/architecture.md` so future sessions do not assume Stage 1 is an unbounded `asyncio.gather()`.

Validation shipped with the slice:

- TDD red/green coverage in `tests/test_stage1_backoff.py`
- learned regression sweep: `pytest -q tests/test_stage1_backoff.py tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py`
- full `pytest -q`
- Stage 1 operator smoke with mocked provider failure, confirming a 20 ms configured backoff produced a measured 21.11 ms delay before the next same-provider call
- Codex review found scheduler and config validation issues; the follow-up tests and fixes are included in this slice

## Request-scoped debug metadata for degraded council runs [2604.017-1]

Shipped 2026-04-14.

Added request-scoped observability for degraded council runs across the backend and MCP entrypoint. Provider calls now return typed `_debug` metadata with request ID, provider, duration, and failure classification; `run_full_council()` returns per-stage debug metadata with failed-model counts; and `ask_council(..., include_debug=True)` appends a human-readable debug section without changing the default answer path.

Validation shipped with the slice:

- pytest coverage for provider timeout classification in `backend/openrouter.py`
- pytest coverage for partial Stage 1 and Stage 2 failures still producing a final synthesis
- pytest coverage for MCP debug rendering through `_execute_council_deliberation()`

## Aggregate council-run KPIs from per-stage debug events [2604.017-4]

Shipped 2026-04-15.

Added a shared in-memory KPI collector in `backend/metrics.py` that aggregates the existing council `debug` payload into rolling process-local success/degradation counters and per-stage latency percentiles. `run_full_council()` and the FastAPI streaming route now feed the same collector via `build_council_run_debug()`, the backend exposes the snapshot at `GET /api/metrics/council`, and MCP clients can read the same shape through `get_council_metrics()`.

Validation shipped with the slice:

- pytest coverage for clean and degraded `run_full_council()` metric updates
- pytest coverage for the streaming FastAPI route recording KPIs and exposing them through `/api/metrics/council`
- pytest coverage for the MCP `get_council_metrics()` tool plus a final `pytest -q` regression sweep

## Ship degraded-run status into the frontend and stored conversation metadata [2604.017-2]

Shipped 2026-04-15.

Added a sanitized `run_status` summary derived from the canonical council `debug` payload so degraded runs are visible in the React UI without exposing provider internals. Assistant messages now persist safe metadata (`label_to_model`, aggregate rankings, Stage 0 standalone query, degraded-run status), the streaming FastAPI path merges the final run summary into the Stage 3 event, and both the HTTP and MCP storage paths save the same reload-safe metadata subset.

Validation shipped with the slice:

- pytest coverage for `run_full_council()` emitting both raw debug and sanitized `run_status`
- pytest coverage for direct FastAPI `/message` persistence plus the streaming `/message/stream` path keeping saved-conversation metadata in sync
- frontend `npm run lint`
- frontend production build via `npm run build -- --outDir /tmp/llm-council-frontend-build`
- real-browser Playwright check of the saved degraded-run conversation, with screenshot artifacts under `output/playwright/`
