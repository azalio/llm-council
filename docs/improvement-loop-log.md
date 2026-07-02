## 2026-05-29 - Adaptive sparse agent routing [2605.009]

- Decision: `implemented`
- Branch: `codex/2605.009-adaptive-routing`
- Baseline: Auto-standard council runs used the same full model pool for every Stage 1 and Stage 2 call. Routine prompts paid the full fan-out cost even when the existing mode classifier, confidence signal, metrics surface, and judge evaluator could support a safer sparse-start policy.
- Forward Change: Added `backend/agent_router.py` with a deterministic sparse router for routine auto-standard prompts, model metadata in `backend/config.py`, route-aware Stage 1/2/2a helpers, full-pool expansion on under-response/unavailable-or-low confidence, persisted `agent_routing` metadata, MCP full-output rendering, and process-local routing KPIs.
- Decisive Validation: `pytest -q tests/test_agent_routing.py` covers deterministic routing, high-risk full routing, sparse completion, low-confidence expansion, metrics, stream metadata, and saved metadata. The regression sweep `pytest -q tests/test_agent_routing.py tests/test_deliberation_mode.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_stage1_backoff.py tests/test_progress_callback.py tests/test_multi_turn.py tests/test_judge_eval.py` passed, full `pytest -q` passed, and `python scripts/agent_routing_benchmark.py --output /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-agent-routing-benchmark.json` showed 33.3% routine model-call savings with mocked judge quality delta `0.0`.
- Next Trigger: Reuse this when changing model metadata, adaptive routing heuristics, Stage 1/2 model selection, confidence escalation, stream metadata, or routing-quality benchmarks.
- Reusable Learnings:
  - command: `pytest -q tests/test_agent_routing.py tests/test_deliberation_mode.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_stage1_backoff.py tests/test_progress_callback.py tests/test_multi_turn.py tests/test_judge_eval.py`
  - command: `python scripts/agent_routing_benchmark.py --output /tmp/llm-council-agent-routing-benchmark.json`
  - invariant: `Sparse routing is eligible only for default-auto requests selected as standard; explicit modes, quick, deep, high-risk prompts, unavailable confidence, low confidence, and routed under-response must preserve or expand to the full deliberation boundary.`
  - gotcha: `The FastAPI streaming endpoint manually calls stage helpers, so routing metadata, expansion, saved assistant metadata, and metrics must be wired there separately from run_full_council().`
  - review-check: `When reviewing routing changes, inspect direct orchestration, streaming events, saved metadata, run_status, MCP full-output rendering, metrics, and benchmark output together.`

## 2026-05-18 - Semantic first-turn answer cache [2605.007-2]

- Decision: `implemented`
- Branch: `codex/2605.007-2-semantic-cache`
- Baseline: The shipped answer cache reused exact normalized repeats or very high-overlap first-turn questions only. Users who asked the same question with different wording still paid the full Stage 1, Stage 2, and Stage 3 council cost.
- Forward Change: Added a deterministic local semantic embedding for question lookup, high-confidence semantic hits, and a chairman validation path for borderline semantic matches. FastAPI direct, FastAPI stream, and MCP now use the async validated lookup before full council fan-out while preserving the existing context-free default-auto eligibility boundary.
- Decisive Validation: `pytest -q tests/test_answer_cache.py` covers paraphrase scoring, numeric-collision rejection, semantic direct API cache hits without model calls, and borderline validation pass/fail. `pytest -q tests/test_answer_cache.py tests/test_deliberation_mode.py tests/test_multi_turn.py tests/test_council_metrics.py` kept routing, storage, streaming, and metrics regressions green.
- Next Trigger: Reuse this learning when changing semantic cache thresholds, adding real embedding storage, cache validation prompts, or cache metrics/KPIs.
- Reusable Learnings:
  - command: `pytest -q tests/test_answer_cache.py tests/test_deliberation_mode.py tests/test_multi_turn.py tests/test_council_metrics.py`
  - invariant: `Semantic answer-cache hits must keep the same first-turn/default-auto safety boundary as exact cache hits; follow-up requests, follow-up answers as cache sources, clarification-gated requests, explicit modes, deep/thorough requests, already-cached assistant turns, and bypass_cache=true stay uncached.`
  - gotcha: `Question embeddings must not treat bare numeric tokens as semantic evidence, or arithmetic questions like "What is 2 + 2 in Python?" and "What is 2 + 3 in Python?" can look falsely similar.`
  - review-check: `When broadening cache matching, verify the high-confidence no-model path, structured borderline chairman-validation path, direct API, stream API, MCP schema, and persisted cache metadata together.`

## 2026-05-18 - Conservative first-turn answer cache [2605.007-1]

- Decision: `implemented`, after splitting umbrella `2605.007` into executable child slices.
- Branch: `codex/2605.007-1-answer-cache`
- Baseline: Repeat first-turn questions entered the full council path every time, even though completed Stage 3 answers were already persisted in SQLite. Users paid the same latency and model cost for exact repeats as for new questions.
- Forward Change: Added `backend/answer_cache.py` and a storage candidate query over completed user/assistant pairs. FastAPI direct, FastAPI stream, and MCP now check the cache before model fan-out for substantive context-free default-auto first-turn requests. Cache hits reuse stored stage artifacts, mark the chairman markdown and metadata as cached, expose `bypass_cache`, and use deterministic titles so no model call is made on the hit path.
- Decisive Validation: `pytest -q tests/test_answer_cache.py` covers matching, storage lookup, direct API hits, bypass, stream events, no-model cache-hit titles, and MCP schema. `pytest -q tests/test_answer_cache.py tests/test_deliberation_mode.py tests/test_multi_turn.py tests/test_council_metrics.py` kept routing, storage, streaming, and metrics regressions green.
- Next Trigger: Reuse this learning when broadening cache matching, adding embedding indexes, changing FastAPI/MCP tool schemas, or deciding whether cache hits should affect council metrics.
- Reusable Learnings:
  - command: `pytest -q tests/test_answer_cache.py tests/test_deliberation_mode.py tests/test_multi_turn.py tests/test_council_metrics.py`
  - invariant: `Answer-cache hits must be skipped for context-bearing follow-ups, clarification-gated requests, explicit non-auto modes, thorough/deep requests, and bypass_cache=true because those callers asked for context resolution or a specific deliberation policy.`
  - gotcha: `Very short prompts like "test" can collide with local history and bypass unrelated MCP/progress paths; require a substantive minimum token count before checking stored exact repeats.`
  - gotcha: `Do not use previously cached assistant turns as cache sources, or repeated hits will stack cache notices and drift away from the original council-backed answer.`
  - gotcha: `A cache-hit path can accidentally burn a model call for title or summary generation; keep cache-hit title/metadata deterministic if the promised payoff is lower latency and cost.`
  - review-check: `When adding a pre-council short-circuit, verify direct FastAPI, streaming FastAPI, MCP schema, persisted metadata, and no-model-call behavior together.`

## 2026-05-18 - Complexity-aware deliberation mode [2605.006]

- Decision: `implemented`
- Branch: `codex/2605.006-deliberation-mode`
- Baseline: MCP and browser calls defaulted to the full standard council unless callers manually set `thorough=True`; simple questions paid Stage 1 + Stage 2 cost and users had no saved signal explaining why a cheaper path was or was not used.
- Forward Change: Added public `mode` values `auto`, `quick`, `standard`, and `deep`. Auto uses high-confidence deterministic heuristics plus a capped 2-second `TITLE_MODEL` classifier with standard fallback. Quick mode calls only the chairman model with self-check instructions, records a `quick_answer` debug stage, persists the mode decision, and shows a Quick mode notice in Stage 3. `thorough=True` remains a deprecated deep-mode alias.
- Decisive Validation: `pytest -q tests/test_deliberation_mode.py` covers classifier routing, fallback, aliasing, quick orchestration, API persistence, stream behavior, and MCP schema. The regression sweep `pytest -q tests/test_deliberation_mode.py tests/test_progress_callback.py tests/test_council_metrics.py tests/test_clarification_gate.py`, full `pytest -q`, MCP schema smoke, frontend `npm run lint`, and production build all stayed green.
- Next Trigger: Reuse this learning whenever future work changes request routing, prompt classification, quick/deep mode defaults, MCP tool schemas, streaming metadata, or saved assistant run status.
- Reusable Learnings:
  - command: `pytest -q tests/test_deliberation_mode.py tests/test_progress_callback.py tests/test_council_metrics.py tests/test_clarification_gate.py`
  - invariant: `Auto mode must fall back to standard, not quick or deep, when the classifier fails or returns unparseable output because standard is the safest compatibility-preserving council path.`
  - gotcha: `The streaming FastAPI endpoint bypasses run_full_council(), so mode selection, quick answer debug, run_status persistence, and stage2/deep gating must be wired there separately.`
  - review-check: `When adding a public MCP/FastAPI argument, verify direct API, stream API, MCP schema, persisted metadata, and frontend final-answer rendering together.`

## 2026-05-18 - Optional first-turn clarification gate [2605.005]

- Decision: `implemented`
- Branch: `codex/2605.005-clarify-ambiguous`
- Baseline: First-turn `ask_council` requests always entered Stage 1, even when the prompt was too underspecified for the council to know the user's intended task. Ambiguous prompts could burn five model calls and still synthesize the wrong intent.
- Forward Change: Added `clarify_when_unclear` as an opt-in MCP/FastAPI flag. First-turn calls with the flag run `stage_minus_1_intent_check()` through `TITLE_MODEL`; ambiguous classifier output returns one focused clarifying question and stores it as an assistant turn, while clear classifier output continues into the normal council pipeline. Follow-up questions keep using Stage 0 reformulation and do not run the gate.
- Decisive Validation: `pytest -q tests/test_clarification_gate.py` covers parser behavior, short-circuit orchestration, direct and streaming API persistence, and MCP schema exposure. `pytest -q tests/test_progress_callback.py tests/test_council_metrics.py tests/test_multi_turn.py tests/test_resiliency_debug.py` kept progress, stream metadata, storage, and debug regressions green. Full `pytest -q` stayed green.
- Next Trigger: Reuse this learning when changing first-turn request routing, ambiguity classification, conversation storage, MCP tool schemas, or any future default-on decision for clarification.
- Reusable Learnings:
  - command: `pytest -q tests/test_clarification_gate.py`
  - invariant: `Clarification gates must run only on first-turn requests; follow-up questions should use Stage 0 reformulation so conversation context, not a context-free ambiguity classifier, resolves pronouns and ellipses.`
  - gotcha: `A clarification turn is an assistant message with empty Stage 1/2 arrays, so persisted metadata needs a dedicated sanitized clarification payload and summary generation should not spend another model call on the clarifying question.`
  - review-check: `When adding a new MCP tool argument, inspect FastMCP schema so the public flag is exposed while internal Context injection remains hidden.`

## 2026-05-18 - MCP cancellation stops in-flight council work [2605.004-3]

- Decision: `implemented`
- Branch: `codex/2605.004-3-mcp-cancellation`
- Baseline: MCP cancellation propagated through `asyncio.wait_for()` and heartbeat cleanup, but provider fan-out relied on implicit `asyncio.gather()` cancellation and had no regression test proving in-flight model calls stopped or that cancelled runs avoided partial assistant storage.
- Forward Change: Provider fan-out and thorough revision fan-out now create explicit child tasks, cancel and await them on parent cancellation, then re-raise `asyncio.CancelledError`. The MCP test path cancels `_execute_council_deliberation()` mid-run and verifies request-context reset plus user-only storage.
- Decisive Validation: `pytest -q tests/test_progress_callback.py` covers MCP progress, heartbeat liveness, provider fan-out cancellation, Stage 2b revision cancellation, `run_full_council()` stage cancellation, and no partial assistant message on MCP cancellation. Full `pytest -q` stayed green.
- Next Trigger: Reuse this learning whenever future work wraps provider calls, adds new fan-out stages, changes MCP heartbeat cleanup, or stores assistant messages earlier in the request lifecycle.
- Reusable Learnings:
  - command: `pytest -q tests/test_progress_callback.py`
  - invariant: `Any council fan-out that owns provider tasks must explicitly cancel and await child tasks before re-raising asyncio.CancelledError.`
  - gotcha: `MCP cancellation should leave a durable user turn but no assistant message; storing assistant output before Stage 3 completes would create a misleading partial conversation.`
  - review-check: `When reviewing cancellation changes, verify provider task cleanup, heartbeat cleanup, request-context reset, and storage boundaries in the same test path.`

## 2026-05-18 - Rich frontend stream progress and low-confidence waiting state [2605.004-2]

- Decision: `implemented`
- Branch: `codex/2605.004-2-frontend-progress`
- Baseline: The FastAPI stream already emitted Stage 0, Stage 1, Stage 2, Stage 2a, Stage 2b, Stage 3, confidence metadata, and terminal events, but the React UI ignored Stage 0 and thorough-stage progress, showed only coarse spinners, and only surfaced low confidence after the final answer appeared.
- Forward Change: Treat the existing SSE event stream as the browser progress contract. The frontend now keeps a per-message progress card keyed by conversation id and stream id, shows low-confidence transition text as soon as Stage 2 metadata arrives, renders Stage 2a/2b artifacts, and hardens the SSE client against split chunks, malformed events, terminal backend errors, and missing completion events.
- Decisive Validation: `pytest -q tests/test_council_metrics.py tests/test_multi_turn.py` preserved backend stream metadata behavior, full `pytest -q` stayed green, and `npm run lint` plus a production `npm run build -- --outDir /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-frontend-progress-build --emptyOutDir` validated the frontend. Review passes caught conversation-switch leakage, parser edge cases, duplicate loading indicators, and tab accessibility drift before commit.
- Next Trigger: Reuse this learning whenever future work changes browser stream consumption, SSE parsing, Stage 0 metadata, thorough-mode UI, or conversation switching during long-running requests.
- Reusable Learnings:
  - command: `pytest -q tests/test_council_metrics.py tests/test_multi_turn.py`
  - command: `npm run lint && npm run build -- --outDir /var/folders/3j/zmvdy5_56bjcg1kmrx05dltcf7yldq/T/opencode/llm-council-frontend-progress-build --emptyOutDir`
  - invariant: `Browser stream updates must be scoped by both conversation id and per-stream assistant id because users can switch conversations while a long council run is still emitting events.`
  - gotcha: `SSE chunks can split a data line, and backend error events are terminal even though they are not named complete; the client must buffer chunks and distinguish malformed/ended streams from server-declared errors.`
  - review-check: `When adding a new staged UI panel, keep de-anonymization, keyboard tab behavior, and live/reloaded metadata behavior consistent with Stage 1 and Stage 2.`

## 2026-05-17 - Surface council disagreement and low-confidence synthesis [2605.002]

- Decision: `implemented`
- Branch: `codex/2605.002-council-confidence`
- Baseline: Stage 2 already produced anonymous rankings and aggregate average ranks, but Stage 3 did not receive a structured disagreement signal and users saw no reload-safe warning when the council split.
- Forward Change: A sanitized `council_confidence` object became the cross-entry-point contract. It is computed from complete rankings, persisted with assistant metadata, passed into the chairman prompt, emitted by the streaming path, rendered in Stage 3, and included in MCP full-output formatting.
- Decisive Validation: `pytest -q tests/test_council_confidence.py tests/test_council_metrics.py` covered the pure confidence helper, chairman prompt injection, persisted metadata, MCP formatting, and FastAPI stream metadata. A full `pytest -q`, frontend `npm run lint`, and production build stayed green.
- Next Trigger: Reuse this learning whenever a future slice changes ranking parsing, confidence escalation, thorough-mode routing, chairman prompt trust boundaries, or saved assistant metadata.
- Reusable Learnings:
  - command: `pytest -q tests/test_council_confidence.py tests/test_council_metrics.py`
  - invariant: `Only complete Stage 2 rankings should count as agreement evidence; incomplete or malformed rankings must not make confidence look stronger than it is.`
  - gotcha: `A 50/50 top-vote split is still split for users, so threshold checks should treat equality as low confidence when the threshold is expressed as a minimum acceptable vote share.`
  - review-check: `When adding a new assistant metadata field, verify run_full_council(), backend/main.py streaming, MCP storage, and saved-conversation reload behavior all preserve the same sanitized shape.`

## 2026-04-14 - Request-scoped debug metadata for degraded council runs [2604.017-1]

- Decision: `implemented`
- Branch: `codex/2604.017-resiliency-debug`
- Baseline: The council already degraded gracefully when some models failed, but it dropped the failure details and emitted only ad hoc logs. `run_full_council()` returned ranking metadata only, provider helpers collapsed failures to `None`, and the MCP tool had no opt-in way to show request diagnostics.
- Forward Change: Making provider responses carry typed `_debug` metadata unlocked the whole slice. Once failures had request ID, provider, duration, and failure type attached, the council stages could summarize degraded runs without breaking existing content flow, and the MCP tool could expose the data behind an `include_debug` flag.
- Decisive Validation: `pytest -q tests/test_resiliency_debug.py tests/test_multi_turn.py` proved the slice end-to-end: provider timeouts classify correctly, partial Stage 1 and Stage 2 failures still produce a final synthesis, and `_execute_council_deliberation()` appends the debug section only when requested. A final `pytest -q` confirmed no broader regressions.
- Next Trigger: Reuse these learnings whenever a future slice touches degraded-run behavior, provider adapters, or council stage metadata. The next likely trigger is any work on `2604.017-2` through `2604.017-4`, especially frontend debug surfacing or Stage 1 concurrency controls.
- Reusable Learnings:
  - command: `pytest -q tests/test_resiliency_debug.py tests/test_multi_turn.py`
  - invariant: `When consuming provider results, always treat _debug.ok == false or content is None as failure because degraded runs now use typed failure payloads instead of plain None.`
  - gotcha: `Bind request IDs at entrypoints, but also self-bind inside run_full_council() when no request ID exists, otherwise direct calls can silently reuse a stale request ID from the current async context.`
  - review-check: `When a council stage return shape changes, always update both run_full_council() tests and the streaming FastAPI endpoint, because the stream path calls the stage helpers directly.`

## 2026-04-15 - Aggregate council-run KPIs from per-stage debug events [2604.017-4]

- Decision: `implemented`
- Branch: `codex/2604.017-4-kpi-metrics`
- PR: `https://github.com/azalio/llm-council-mcp/pull/2`
- Baseline: The repo already emitted request-scoped `metadata.debug` for degraded runs, but nothing aggregated those per-run facts into release-over-release KPIs. There was no machine-readable metrics surface, no process-local success/degradation counters, and the streaming FastAPI path bypassed `run_full_council()`, so even a naive collector would have missed the frontend's main execution path.
- Forward Change: Treating the existing `debug` payload as the single observability contract unlocked the slice. A shared `build_council_run_debug()` helper made `run_full_council()` and the streaming FastAPI route emit the same run summary, which let one in-memory collector power both `/api/metrics/council` and the new MCP `get_council_metrics()` tool without inventing another event schema.
- Decisive Validation: `pytest -q tests/test_council_metrics.py tests/test_resiliency_debug.py tests/test_multi_turn.py` proved the new collector in three ways: direct `run_full_council()` metrics updated across clean and degraded runs, the streaming HTTP route recorded KPIs and exposed them through `/api/metrics/council`, and the MCP `get_council_metrics()` tool returned the same snapshot shape. A final `pytest -q` confirmed no broader regressions.
- Next Trigger: Reuse this learning for any follow-up on `2604.017-2`, `2604.017-3`, future dashboard/export work, or any change that adds new council stages or telemetry fields.
- Reusable Learnings:
  - command: `pytest -q tests/test_council_metrics.py tests/test_resiliency_debug.py tests/test_multi_turn.py`
  - invariant: `When exposing aggregate council KPIs, always derive them from the canonical run debug payload so the direct orchestrator, streaming API, and MCP surfaces stay in lockstep.`
  - gotcha: `The frontend uses the streaming FastAPI endpoint, not run_full_council() directly, so metrics work that only patches the top-level orchestrator will silently miss the main user path.`
  - review-check: `When observability changes touch council execution, always verify both run_full_council() and backend/main.py stream handlers update the same metric sink.`
## 2026-04-15 - Ship degraded-run status into the frontend and stored conversation metadata [2604.017-2]

- Decision: `implemented`
- Branch: `codex/2604.017-2-degraded-run-status`
- PR: `https://github.com/azalio/llm-council-mcp/pull/3`
- Baseline: The backend already emitted request-scoped degraded-run debug metadata, but the frontend stream path dropped the final run summary and saved conversations lost all assistant metadata on reload. Users could not tell from the UI when only a subset of council members responded unless they inspected logs or opted into MCP debug output.
- Forward Change: Deriving one sanitized `run_status` object from the canonical `debug` payload was the key move. That let the direct FastAPI path, the streaming path, and the MCP storage path share the same persistence rule: keep ranking metadata and degraded-run status, but never persist provider, status-code, request-ID, or timing internals.
- Decisive Validation: `pytest -q tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py` proved the backend contract and both storage paths. `npm run lint` and `npm run build -- --outDir /tmp/llm-council-frontend-build` kept the frontend clean. A Playwright browser check against a mocked saved conversation confirmed the Stage 3 degraded-run banner rendered the persisted metadata after reload, with screenshot artifacts in `output/playwright/`.
- Next Trigger: Reuse these learnings whenever a future slice changes council debug fields, saved conversation rendering, UI exports, or any surface that needs to inspect degraded runs after the original request has finished.
- Reusable Learnings:
  - command: `pytest -q tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py`
  - invariant: `When persisting council assistant metadata, always derive the saved degraded-run summary from the canonical debug payload and strip provider/status-code/request-ID details before storage.`
  - gotcha: `The stream path emits ranking metadata at Stage 2 and the final degraded-run summary at Stage 3, so the frontend must merge assistant metadata across events or the saved/reloaded view will drift from the live run.`
  - review-check: `When assistant-message metadata changes, always verify the direct FastAPI path, the streaming FastAPI path, and the MCP storage path all use the same sanitization helper.`
## 2026-05-01 - Use a heterogeneous chairman [2605.001]

- Decision: `implemented`
- Branch: `codex/2605.001-chairman-heterogeneity`
- PR: `https://github.com/azalio/llm-council-mcp/pull/4`
- Baseline: The provider config allowed the chairman model to also be a council member. OpenRouter used `google/gemini-3-pro-preview` as chairman and as a council member. The MCP model-list tool also showed ids only, so operators could not inspect chairman family separation at a glance.
- Forward Change: Treating model family as a first-class config invariant kept the slice small. The config now infers model families, rejects exact chairman/council overlap, rejects same-family overlap, and exposes the active family summary through MCP `get_available_models()`.
- Decisive Validation: `pytest -q tests/test_chairman_heterogeneity.py` covered family inference, the configured provider, exact-overlap rejection, family-overlap rejection, and MCP model-list visibility. The learned regression sweep and full pytest run stayed green. MCP operator checks for `API_PROVIDER=openrouter` showed disjoint council/chairman families.
- Next Trigger: Reuse this learning whenever a future slice changes model lists, adds model discovery, adds UI model selection, or changes `get_available_models()`.
- Reusable Learnings:
  - command: `pytest -q tests/test_chairman_heterogeneity.py`
  - invariant: `When changing council or chairman model config, always keep the chairman outside every active council family because Stage 3 is the terminal synthesis boundary.`
  - gotcha: `Keeping the same preferred chairman is safer than choosing a new unverified model; remove or replace same-family council members first when that preserves the operator preference.`
  - review-check: `When reviewing model-config changes, always verify exact ids and inferred provider families, not only list membership.`
## 2026-05-01 - Add bounded concurrency and provider backoff to Stage 1 fan-out [2604.017-3]

- Decision: `implemented`
- Branch: `codex/2604.017-3-stage1-backoff`
- PR: `https://github.com/azalio/llm-council-mcp/pull/5`
- Baseline: Stage 1 fanned out to every council model with an unbounded `asyncio.gather()`. Repeated provider trouble could start several same-provider calls together and turn one failing provider path into a burst of timeouts.
- Forward Change: The key change was moving Stage 1 onto the shared `query_models_parallel()` helper with explicit `COUNCIL_STAGE1_MAX_CONCURRENCY` and `COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS` controls. The review pass caught that sleeping inside the global semaphore and non-serialized backoff waiters would still let bursts through, so the final scheduler reserves provider-path start times and releases global concurrency while waiting.
- Decisive Validation: `pytest -q tests/test_stage1_backoff.py` first failed, then passed with coverage for limiter wiring, max in-flight calls, same-provider backoff, serialized same-provider waiters, stale reservation revalidation, unrelated-provider progress, and config validation. The learned regression sweep `pytest -q tests/test_stage1_backoff.py tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py` passed, full `pytest -q` passed, and a Stage 1 operator smoke with mocked provider failure measured a 21.11 ms delay for a configured 20 ms provider-path backoff. Codex review found actionable scheduler/config issues; follow-up tests and fixes landed before docs and plan closure.
- Next Trigger: Reuse this learning whenever a future slice changes provider fan-out, retry policy, model routing, or any council-stage scheduler.
- Reusable Learnings:
  - command: `pytest -q tests/test_stage1_backoff.py tests/test_resiliency_debug.py tests/test_council_metrics.py tests/test_multi_turn.py`
  - invariant: `When throttling provider fan-out, reserve per-provider start times separately from global concurrency because provider backoff must not consume slots needed by unrelated provider paths.`
  - gotcha: `If multiple tasks sleep until the same provider backoff deadline without reserving staggered starts, they wake together and recreate the burst the backoff was meant to prevent.`
  - review-check: `When reviewing async provider throttling, verify both same-provider serialization and unrelated-provider progress under max_concurrency > 1 and max_concurrency == 1 cases.`

## 2026-05-17 - Stream MCP council progress notifications and heartbeat liveness [2605.004-1]

- Decision: `implemented`, with parent `2605.004` split into executable follow-up slices.
- Branch: `codex/2605.004-mcp-progress`
- Baseline: MCP `ask_council` calls could stay silent for several minutes because `_execute_council_deliberation()` waited for one final `run_full_council()` result. FastAPI already had SSE stage events, but MCP users and operators had no stage-level progress or transport heartbeat while reasoning-model calls were in flight.
- Forward Change: The useful boundary was a generic optional `progress_callback` on `run_full_council()`. That kept the core orchestrator transport-agnostic while letting the MCP server forward stage boundaries through `Context.report_progress()` and run a heartbeat that re-emits the latest progress during long stages.
- Decisive Validation: `pytest -q tests/test_progress_callback.py` covers default, thorough, and multi-turn progress order; callback failure isolation; cancellation propagation from progress reporters; Stage 1 zero-success abort notification; MCP context forwarding; heartbeat re-emission during a simulated long stage; no-context fallback; and the MCP schema invariant that `ctx` is not exposed as a tool argument.
- Next Trigger: Reuse this learning whenever a future slice changes council stage orchestration, MCP liveness, cancellation, or frontend stream progress.
- Reusable Learnings:
  - command: `pytest -q tests/test_progress_callback.py`
  - invariant: `Progress reporting must be optional and best-effort for ordinary callback failures, but asyncio.CancelledError must always propagate so caller cancellation and timeouts can stop the council run.`
  - gotcha: `Stage-boundary progress is not enough for MCP transport liveness; a single long provider stage still needs heartbeat re-emission of the latest progress.`
  - review-check: `When adding FastMCP Context parameters to a tool, inspect the generated tool schema and verify the context parameter is injected internally rather than exposed to callers.`
## 2026-05-18 - Chairman claim attribution [2605.003]

- Decision: `implemented`
- Branch: `codex/2605.003-chairman-attribution`
- PR: `https://github.com/azalio/llm-council-mcp/pull/17`
- Baseline: The chairman prompt synthesized anonymous council responses without a source discipline, so a final answer could introduce precise facts that no council member supported. Users could inspect Stage 1 and Stage 2, but Stage 3 had no marker-level bridge back to the supporting response labels.
- Forward Change: The useful boundary was a closed-set attribution contract, not a broad hallucination detector. Stage 3 now tells the chairman to cite only anonymous council labels like `[A]` and `[A, C]`, omit unsupported facts, and use an explicit abstention line when no council member discussed a requested fact. A deterministic validator then checks the final synthesis for verifiable-looking claims without valid markers and exposes the result in Stage 3, run status, the React UI, MCP brief output, and MCP full output.
- Decisive Validation: `pytest -q tests/test_chairman_attribution.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_multi_turn.py` covered the new validator, prompt contract, run-status propagation, MCP brief/full rendering, and metadata regressions. Full `pytest -q`, `npm run lint`, and a production build stayed green. Local Vite responded at `http://127.0.0.1:5173`; real-browser DevTools inspection was blocked by an existing Chrome profile lock.
- Next Trigger: Reuse this learning whenever future work changes chairman prompts, Stage 3 metadata, saved assistant metadata, claim validation, MCP full-output formatting, or frontend trust banners.
- Reusable Learnings:
  - command: `pytest -q tests/test_chairman_attribution.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_multi_turn.py`
  - invariant: `When Stage 3 asks the chairman to abstain with "No council member discussed ...", the attribution validator must exempt that exact abstention form or the product warns on its own safe fallback.`
  - gotcha: `A prompt-only attribution rule is not enough; persist the validator result in the Stage 3 payload and run status so live, reloaded, UI, and MCP users see the same trust signal.`
  - review-check: `When changing chairman synthesis prompts, verify the prompt contract, deterministic post-hoc validator, frontend key/warning, and both MCP brief and full-output rendering together.`
## 2026-05-18 - Confidence-aware selective fallback to stronger remote models [2604.093]

- Decision: `implemented`
- Branch: `codex/2604.093-confidence-escalation`
- PR: `pending`
- Baseline: Auto mode could choose standard, Stage 2 could then prove the council was split, and the chairman would only receive a low-confidence warning before synthesis. Users got calibrated uncertainty, but not extra critique/revision work on the contested answer path.
- Forward Change: Added a `confidence_escalation` decision after Stage 2. When the request was `mode="auto"`, the selected mode was `standard`, and `compute_council_confidence()` reported low confidence, the same run now executes Stage 2a critiques and Stage 2b revisions before Stage 3. Explicit `standard` remains a hard cost boundary.
- Decisive Validation: `pytest -q tests/test_council_confidence.py tests/test_council_metrics.py` covered direct orchestration, explicit-standard non-escalation, MCP full-output formatting, FastAPI streaming events, saved metadata, and run-status propagation. Full `pytest -q` passed.
- Next Trigger: Reuse this when changing deliberation mode routing, council confidence thresholds, Stage 2a/2b behavior, streaming metadata, or any future sparse routing / early stopping policy that conditionally changes stages after Stage 2.
- Reusable Learnings:
  - command: `pytest -q tests/test_council_confidence.py tests/test_council_metrics.py`
  - invariant: `Confidence escalation must run only for auto requests that selected standard and then produced low-confidence Stage 2 rankings; explicit standard and already-deep requests must not be silently upgraded.`
  - gotcha: `The FastAPI streaming endpoint manually calls stage helpers, so post-Stage-2 escalation must be wired there separately from run_full_council().`
  - review-check: `When adding a conditional council stage after Stage 2, verify direct orchestration, streaming events, saved metadata, run_status, MCP formatting, and revised-response synthesis all agree.`

## 2026-05-18 - Answer-cache runtime KPIs and replay probe [2605.007-3]

- Decision: `implemented`
- Branch: `codex/2605.007-3-cache-kpis`
- PR: `pending`
- Baseline: The answer cache was active for first-turn default-auto questions and could reuse token, semantic, and validated semantic matches, but operators could not see hit/miss rates, validation outcomes, or cache-hit latency separately from full council latency. Threshold changes would have relied on anecdotes or local debugging.
- Forward Change: Runtime cache lookups now record hit, miss, bypass, match type, similarity, validation, and latency KPIs into the existing process-local metrics surface. A read-only replay script walks stored first-turn answers chronologically and reports hit rate plus manual-review samples without making model calls.
- Decisive Validation: `pytest -q tests/test_answer_cache.py` covers direct and streaming cache-hit metrics, bypass counters, validation counters, and chronological replay samples. `pytest -q tests/test_council_metrics.py` confirms the existing metrics snapshot still works. `python scripts/answer_cache_replay.py --limit 20 --samples 3` exercised the operator replay against local stored conversations.
- Next Trigger: Reuse this learning before changing answer-cache thresholds, semantic matching, validation prompts, eligibility boundaries, or any future persisted cache index.
- Reusable Learnings:
  - command: `pytest -q tests/test_answer_cache.py tests/test_council_metrics.py`
  - command: `python scripts/answer_cache_replay.py --limit 200 --samples 10`
  - invariant: `Cache threshold or matching-policy changes need both runtime KPI assertions and an offline replay sample because hit rate alone cannot prove answer quality.`
  - gotcha: `An answer-cache hit can look successful while hiding validation rejection or slow validation latency unless validation outcomes and cache-hit latency are tracked separately from full council stages.`
  - review-check: `When reviewing cache observability, verify direct FastAPI, streaming FastAPI, MCP metrics exposure, bypass counting, validation counters, and replay samples together.`

## 2026-05-28 - LLM-as-a-judge evaluation pipeline [2604.011]

- Decision: `implemented`
- Branch: `codex/2604.011-judge-eval`
- PR: `pending`
- Baseline: The repo had no `backend/eval` package, no strict judge-result schema, and no operator artifact for measuring whether chairman synthesis improves on a Stage 1 baseline. Future routing and cascade items referenced `[2604.011]`, but there was no reusable quality measurement surface.
- Forward Change: Added `backend/eval/judge.py` as an operator-facing path that compares a chairman synthesis with the best available Stage 1 answer selected from aggregate rankings. The judge prompt requires `judge.v1` JSON, validates every rubric score, exposes deterministic generation settings, and returns `available=false` for failed or unparseable judge calls. The default answer path stays unchanged.
- Decisive Validation: `pytest -q tests/test_judge_eval.py tests/test_chairman_heterogeneity.py tests/test_resiliency_debug.py` validated the focused judge behavior and nearby provider/config surfaces. `python scripts/judge_eval_smoke.py --output /tmp/llm-council-judge-eval-smoke.json` produced an inspectable artifact with `available=true`, `winner=candidate`, candidate/baseline sources, rubric scores, and generation metadata.
- Next Trigger: Reuse this when implementing adaptive routing, cascaded ranking, cache threshold changes, or any benchmark that claims answer quality moved.
- Reusable Learnings:
  - command: `pytest -q tests/test_judge_eval.py`
  - command: `python scripts/judge_eval_smoke.py --output /tmp/llm-council-judge-eval-smoke.json`
  - invariant: `Judge scores are measurement artifacts, not part of the default ask_council answer path until a fixed benchmark proves stability.`
  - gotcha: `A judge call can fail or return malformed JSON; treat that as available=false rather than silently converting it into a quality metric.`
  - review-check: `When changing judge behavior, verify parser rejection, provider failure handling, deterministic request options, baseline selection, and the smoke artifact shape together.`
## 2026-06-03 - Evidence-gated deep-mode revisions [2606.02866]

- Decision: `implemented`
- Branch: `pinyon-bajada`
- PR: `https://github.com/azalio/llm-council-mcp/pull/20`
- Baseline: Deep-mode and confidence-escalated council runs treated Stage 2b revisions as primary synthesis input even though Stage 2b told models to broadly improve answers from peer critiques. Unsupported critiques could therefore rewrite a correct Stage 1 answer and flow into the chairman answer.
- Forward Change: The useful boundary was a trust-boundary change, not a new telemetry surface: Stage 2b now tells revisers to treat critiques as untrusted suggestions and preserve the original answer unless a critique cites checkable evidence. Stage 3 now compares revisions against originals and rankings instead of treating revisions as unconditional primary evidence.
- Decisive Validation: `pytest -q tests/test_stage2b_revision_policy.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_progress_callback.py` passed, proving the prompt contract, Stage 3 guardrail, direct/stream confidence-escalation path, and cancellation boundary. Full `pytest -q` passed.
- Next Trigger: Reuse this learning when changing Stage 2a critique prompts, Stage 2b revision prompts, confidence escalation, deep-mode early stopping, Stage 3 synthesis of revisions, or any future CIC/benefit-predicate work.
- Reusable Learnings:
  - command: `pytest -q tests/test_stage2b_revision_policy.py tests/test_council_confidence.py tests/test_council_metrics.py tests/test_progress_callback.py`
  - invariant: `Peer critiques are untrusted suggestions; Stage 2b must preserve correct original content unless a critique cites checkable evidence.`
  - gotcha: `Evidence-gating only in the revision prompt is insufficient if Stage 3 still calls revisions the primary source; review both prompts together.`
  - review-check: `When reviewing deep-mode revisions, verify Stage 2b prompt wording, revision_policy metadata, Stage 3 revised-response instructions, and direct/stream confidence-escalation paths.`
