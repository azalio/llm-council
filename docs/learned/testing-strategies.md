---
paths:
  - "**/test_*"
  - "**/tests/**"
  - "**/*_test.*"
  - "**/*.test.*"
---

# Testing Strategies (Learned)

<!-- IMPROVEMENT-PLAN-LOOP: promoted from loop learnings. Edit freely and commit with the project. -->

- **Degraded Run Coverage Must Cross Layers** (2026-04-14): When testing council resiliency, always cover provider failure classification, a degraded multi-stage run, and the caller-facing debug surface because each layer can pass in isolation while the end-to-end degraded path still breaks. [workflow: improvement-plan-loop]

- **Cover Direct And Streaming Council Paths** (2026-04-15): When testing council telemetry, always exercise both `run_full_council()` and the FastAPI streaming route because the frontend path bypasses the top-level orchestrator and can miss new instrumentation. [workflow: improvement-plan-loop]

- **Cover Fresh And Reloaded Degraded Runs** (2026-04-15): When testing degraded-run UX, always cover both the live response path and a saved-conversation reload because users need the same signal after the request is gone, not only while the stream is open. [workflow: improvement-plan-loop]

- **Test Provider Config Invariants** (2026-05-01): When testing model-selection invariants, always cover the active provider's constants because a stale invalid config can otherwise slip through unnoticed. [workflow: improvement-plan-loop]

- **Backoff Tests Need Contention** (2026-05-01): When testing provider backoff, always cover multiple same-provider waiters, a later in-flight failure invalidating an existing reservation, and one mixed-provider case because single-queued-call tests miss scheduler races. [workflow: improvement-plan-loop]

- **Progress Tests Need Idle-Time Simulation** (2026-05-17): When testing long-running MCP council calls, simulate a stage that sleeps longer than the heartbeat interval because stage-boundary-only tests miss the transport-liveness failure users experience during slow provider calls. [workflow: improvement-plan-loop]

- **Confidence Tests Need Malformed Ranking Cases** (2026-05-17): When testing council confidence, cover unanimous rankings, exact-threshold splits, all-incomplete rankings, and mixed complete/incomplete rankings because parse failures must not make the council look more certain than it is. [workflow: improvement-plan-loop]

- **Frontend Stream Tests Need Conversation Switching And Broken Chunks** (2026-05-18): When changing browser stream consumption, cover mid-stream conversation switching, chunk-split SSE lines, backend `error` events, missing `complete`, and thorough-mode Stage 2a/2b artifacts because unit-clean backend stream events can still produce stuck or cross-chat UI state. [workflow: improvement-plan-loop]
- **Attribution Tests Need Safe-Abstention Cases** (2026-05-18): When testing chairman attribution validators, cover both missing-marker claims and explicit `No council member discussed ...` abstentions because the safe fallback can contain acronyms or identifiers that otherwise look like uncited facts. [workflow: improvement-plan-loop]
- **Cancellation Tests Need Child Task Proof** (2026-05-18): When testing council cancellation, cancel the parent task after provider children have started and assert each child observed `asyncio.CancelledError`, because a cancelled wrapper can otherwise hide token-burning provider work still running in the background. [workflow: improvement-plan-loop]

- **Clarification Tests Need Storage And Schema Coverage** (2026-05-18): When changing first-turn clarification behavior, cover classifier parsing, orchestrator short-circuit, direct and streaming conversation persistence, and MCP schema exposure because the gate bypasses the usual Stage 1/2 metadata path. [workflow: improvement-plan-loop]

- **Mode Routing Tests Need Direct And Stream Coverage** (2026-05-18): When changing deliberation modes, test quick-mode orchestration and both FastAPI paths because the streaming endpoint manually runs stage helpers and can miss `run_full_council()` metadata or skip/deep gating changes. [workflow: improvement-plan-loop]
- **Cache Short-Circuit Tests Need No-Model Assertions** (2026-05-18): When testing a pre-council cache path, assert the normal council stages and cheap title generation are not called on cache hits because a visible cached response can still fail the latency/cost promise if side model calls remain. [workflow: improvement-plan-loop]
- **Semantic Cache Tests Need Numeric And Validation Cases** (2026-05-18): When testing semantic answer-cache matching, cover paraphrase hits, numeric non-matches with shared context words, high-confidence no-model reuse, context-dependent follow-up source rejection, structured validation parsing, and borderline validation pass/fail because each failure mode can independently break the latency or correctness promise. [workflow: improvement-plan-loop]
- **Post-Stage-2 Routing Tests Need Positive And Negative Cases** (2026-05-18): When testing confidence-based routing, cover auto-standard low-confidence escalation, explicit-standard non-escalation, revised-response synthesis, and streaming Stage 2a/2b events because each case can fail independently. [workflow: improvement-plan-loop]

- **Cache Metrics Tests Need Replay Evidence** (2026-05-18): When testing answer-cache metrics, cover both live runtime counters and a chronological replay sample because counters prove instrumentation while replay samples expose whether future threshold changes would reuse plausible sources. [workflow: improvement-plan-loop]

- **Judge Eval Tests Need Parser And Artifact Checks** (2026-05-28): When testing LLM-as-a-judge behavior, cover fenced JSON extraction, missing-score rejection, provider failure handling, deterministic request options, baseline selection from aggregate rankings, and a smoke artifact because a judge score is only useful if the schema is auditable. [workflow: improvement-plan-loop]

- **Adaptive Routing Tests Need Cross-path Expansion Cases** (2026-05-29): When testing sparse routing, cover routine sparse completion, high-risk full routing, low-confidence expansion, direct orchestration, FastAPI streaming, saved metadata, and metrics together because a router can save model calls in one path while silently under-deliberating or hiding the route in another. [workflow: improvement-plan-loop]

- **Stage 2b Revision Tests Need Trust-Boundary Assertions** (2026-06-03): When testing deep-mode critique/revision, assert both the Stage 2b prompt contract and the Stage 3 synthesis guardrail because a model can receive evidence-gated revision instructions while the chairman still treats every revision as unconditionally better than the original. [workflow: improvement-plan-loop]
