---
paths:
  - "**/*.md"
  - "**/*.py"
---

# Implementation Patterns (Learned)

<!-- IMPROVEMENT-PLAN-LOOP: promoted from loop learnings. Edit freely and commit with the project. -->

- **Typed Failure Payloads Instead Of Bare None** (2026-04-14): When a provider call can fail but the workflow should continue, always return a structured failure payload with typed `_debug` fields because the orchestration layer can degrade gracefully without losing observability. [workflow: improvement-plan-loop]
  ```text
  {"content": None, "_debug": {"ok": False, "failure_type": "timeout", "request_id": "..."}}
  ```

- **Persist Only Sanitized Run Status** (2026-04-15): When saved conversations need degraded-run visibility, always derive a sanitized `run_status` from the canonical debug payload instead of storing raw debug internals because the UI only needs stable counts and failure summaries after reload. [workflow: improvement-plan-loop]
  ```text
  persisted_metadata = {
      "label_to_model": ...,
      "aggregate_rankings": ...,
      "run_status": build_run_status(debug),
  }
  ```

- **Chairman Outside Council Family** (2026-05-01): When changing model configuration, always keep the chairman outside every active council provider family because Stage 3 synthesis should not share the same model lineage as its critics. [workflow: improvement-plan-loop]
  ```text
  validate_chairman_heterogeneity(COUNCIL_MODELS, CHAIRMAN_MODEL)
  ```

- **Provider Backoff Outside Global Slots** (2026-05-01): When throttling provider calls, always reserve provider-path start times separately from the global concurrency semaphore and revalidate reservations after later failures because a sleeping backoff task must not block unrelated provider paths or ignore a newer provider failure. [workflow: improvement-plan-loop]
  ```text
  reserve provider-path start -> release global slot while waiting -> recheck provider failure version -> reacquire global slot for the actual call
  ```

- **Confidence From Complete Rankings Only** (2026-05-17): When deriving trust signals from Stage 2 rankings, count only complete rankings as agreement evidence and track incomplete rankings separately because malformed partial rankings can otherwise look falsely unanimous. [workflow: improvement-plan-loop]
  ```text
  confidence = compute_council_confidence(stage2_results, label_to_model)
  ```

- **Scope Browser Stream Updates By Conversation And Stream** (2026-05-18): When applying SSE updates to the React conversation view, key the in-flight assistant message by both conversation id and a per-stream id because the selected conversation can change before the provider run finishes. [workflow: improvement-plan-loop]
  ```text
  updateStreamingAssistantMessage(conversation, conversationId, streamId, update)
  ```
- **Closed-Set Chairman Attribution** (2026-05-18): When constraining chairman factual claims, use anonymous council response labels as the only valid citation source and pair the prompt rule with deterministic post-hoc validation because the council source set is small and already present in Stage 3 context. [workflow: improvement-plan-loop]

- **Explicit Fan-Out Cancellation** (2026-05-18): When a council stage fans out provider calls, create child tasks, cancel and await every child on `asyncio.CancelledError`, then re-raise so caller cancellation stops token burn instead of becoming a swallowed degraded run. [workflow: improvement-plan-loop]
  ```text
  except asyncio.CancelledError:
      for task in tasks:
          task.cancel()
      await asyncio.gather(*tasks, return_exceptions=True)
      raise
  ```

- **Clarification As A Stored Assistant Turn** (2026-05-18): When a pre-council gate asks the user for missing intent, return a normal assistant turn with empty Stage 1/2 arrays and a sanitized `metadata.clarification` payload containing only the user-facing question/model status so the next user reply continues in the same conversation without pretending a council synthesis happened. [workflow: improvement-plan-loop]

- **Auto Mode Falls Back To Standard** (2026-05-18): When adding cheaper or deeper deliberation routing, use deterministic high-confidence heuristics plus a bounded classifier, and fall back to standard mode on classifier failure because standard is the safest existing product path. [workflow: improvement-plan-loop]

- **Quick Mode Needs Explicit Run Status** (2026-05-18): When a request intentionally skips peer ranking, persist a mode decision and render a visible final-answer notice so users can distinguish a deliberate chairman-only answer from a missing Stage 2 result. [workflow: improvement-plan-loop]

- **Conservative Cache Eligibility Before Model Fan-Out** (2026-05-18): When reusing stored council answers, keep the first cache boundary context-free and opt-out-safe: only default-auto first-turn requests should hit, and cache-hit metadata/title generation should be deterministic so the short-circuit does not secretly spend another model call. [workflow: improvement-plan-loop]

- **Validate Borderline Semantic Cache Hits** (2026-05-18): When expanding answer-cache reuse beyond exact/high-overlap repeats, serve only high-confidence local semantic matches directly and send borderline matches through a single structured chairman applicability check because a near-duplicate false positive is worse than a cache miss. [workflow: improvement-plan-loop]

- **Cache Sources Must Be First-Turn Answers** (2026-05-18): When reusing stored answers for a new first-turn request, only use prior first-turn user/assistant pairs because follow-up answers may depend on hidden conversation context even when the follow-up text looks semantically similar. [workflow: improvement-plan-loop]
- **Auto Escalation Respects Explicit Cost Modes** (2026-05-18): When adding automatic escalation from council confidence, only escalate `mode="auto"` requests that selected standard; explicit `standard` and already-deep requests must keep their caller-selected boundary. [workflow: improvement-plan-loop]

- **Cache Metrics Beside Existing Metrics Surface** (2026-05-18): When adding cache observability, attach hit/miss/validation/latency counters to the existing process-local metrics surface instead of creating a second telemetry endpoint because FastAPI and MCP already consume that operator workflow. [workflow: improvement-plan-loop]

- **Judge Scores Stay Operator-Facing By Default** (2026-05-28): When adding LLM-as-a-judge evaluation, keep it outside the default `ask_council` answer path until a fixed benchmark proves the score is stable enough; failed or unparseable judge calls must return `available=false` rather than silently becoming quality metrics. [workflow: improvement-plan-loop]

- **Sparse Routing Must Expand Before Synthesis** (2026-05-29): When starting routine council runs with a sparse model subset, expand to the full pool before chairman synthesis whenever routed Stage 1 under-responds, Stage 2 has too few rankings, confidence is unavailable, or confidence is low because the router's user-value claim is lower latency without weaker contested answers. [workflow: improvement-plan-loop]
