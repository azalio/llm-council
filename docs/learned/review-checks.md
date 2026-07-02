# Review Checks (Learned)

<!-- IMPROVEMENT-PLAN-LOOP: promoted from loop learnings. Edit freely and commit with the project. -->

- **Check Both Orchestration Paths** (2026-04-14): When reviewing council orchestration changes, always verify both `run_full_council()` and `backend/main.py` streaming handlers because the HTTP stream path bypasses the top-level orchestration helper and can drift silently. [workflow: improvement-plan-loop]

- **Check Live And Stored Conversation Metadata Together** (2026-04-15): When reviewing degraded-run UI work, always verify both the live Stage 3 event payload and a reloaded saved conversation because the stream path can look correct while storage silently drops the same metadata. [workflow: improvement-plan-loop]

- **Review Model Family Separation** (2026-05-01): When reviewing council model config changes, always verify exact model ids and inferred provider families because a different id can still be the same model family as the chairman. [workflow: improvement-plan-loop]

- **Async Provider Throttling Review** (2026-05-01): When reviewing async provider throttling, always verify same-provider waiters cannot wake together, stale reservations are revalidated after later in-flight failures, and unrelated provider paths can still make progress because these bugs preserve the user-visible timeout burst under load. [workflow: improvement-plan-loop]

- **FastMCP Context Schema Review** (2026-05-17): When adding a FastMCP `Context` parameter to a tool, inspect `mcp.list_tools()` and verify the context parameter is not part of the public input schema, otherwise clients may see an impossible argument. [workflow: improvement-plan-loop]

- **Progress Callback Cancellation Review** (2026-05-17): When wrapping progress callbacks or `Context.report_progress()` in broad exception handlers, always re-raise `asyncio.CancelledError` before logging ordinary callback failures so MCP cancellation and `asyncio.wait_for` timeouts keep working. [workflow: improvement-plan-loop]

- **Confidence Metadata Cross-Entry Review** (2026-05-17): When adding a new assistant metadata field, verify `run_full_council()`, the FastAPI stream path, MCP storage, and reload-safe frontend rendering all preserve the same sanitized shape. [workflow: improvement-plan-loop]

- **Frontend Stream State Scope Review** (2026-05-18): When reviewing streamed React updates, verify events are scoped to the originating conversation and stream id, not whichever conversation is currently selected, because long council runs can keep emitting after the user navigates. [workflow: improvement-plan-loop]
- **Chairman Trust Boundary Review** (2026-05-18): When reviewing chairman synthesis changes, always verify the prompt rule, validator exemptions, Stage 3 payload, run-status propagation, frontend attribution key, MCP brief output, and MCP full-output warning because any one missing surface can make unsupported claims look trustworthy. [workflow: improvement-plan-loop]
- **Cancellation Boundary Review** (2026-05-18): When reviewing council cancellation changes, verify provider task cleanup, heartbeat cleanup, request-context reset, and storage boundaries together because each layer can pass while another still burns tokens or persists a partial answer. [workflow: improvement-plan-loop]

- **Clarification Boundary Review** (2026-05-18): When reviewing first-turn clarification changes, verify the gate is opt-in, skipped for follow-up context, persisted as assistant metadata, and does not trigger summary/debug paths that assume a completed Stage 1/2 council run. [workflow: improvement-plan-loop]

- **Deliberation Mode Cross-Entry Review** (2026-05-18): When reviewing mode-routing changes, verify `run_full_council()`, FastAPI direct, FastAPI stream, MCP schema, metrics, persisted metadata, and frontend Stage 3 rendering because quick mode intentionally skips Stage 2 and otherwise looks like a broken stream. [workflow: improvement-plan-loop]
- **Answer Cache Safety Review** (2026-05-18): When reviewing answer-cache changes, verify eligibility rejects context-bearing follow-ups, clarification-gated requests, explicit non-auto modes, deep/thorough requests, too-short prompts, already-cached assistant turns, and `bypass_cache=true`; then verify direct API, stream API, MCP schema, persisted metadata, and no-model cache-hit titles. [workflow: improvement-plan-loop]
- **Semantic Cache False-Positive Review** (2026-05-18): When reviewing semantic answer-cache matching, check numeric-token collisions, high-confidence no-model hits, structured borderline chairman validation pass/fail behavior, and persisted `match_type`/similarity metadata so paraphrase reuse does not silently serve the wrong prior answer. [workflow: improvement-plan-loop]
- **Answer Cache Source-Scope Review** (2026-05-18): When reviewing answer-cache storage queries, verify candidate sources are first-turn answers only because follow-up answers can carry unstored conversation assumptions that are unsafe to reuse for a new first-turn request. [workflow: improvement-plan-loop]
- **Conditional Stage Cross-Entry Review** (2026-05-18): When reviewing a new conditional council stage after Stage 2, verify `run_full_council()`, FastAPI streaming, saved metadata, run status, and MCP full-output formatting because the stream path bypasses the top-level orchestrator. [workflow: improvement-plan-loop]

- **Cache Metrics Cross-Entry Review** (2026-05-18): When reviewing cache observability, verify direct FastAPI, streaming FastAPI, MCP `get_council_metrics()`, bypass counting, validation counters, and replay output because each path can silently miss the cache event. [workflow: improvement-plan-loop]

- **Adaptive Routing Cross-Entry Review** (2026-05-29): When reviewing routing changes, verify `run_full_council()`, FastAPI streaming, saved metadata, `run_status`, MCP full-output formatting, metrics counters, and the benchmark artifact because the stream path manually mirrors routing and can drift from the direct orchestrator. [workflow: improvement-plan-loop]

- **Stage 2b Evidence-Gating Review** (2026-06-03): When reviewing deep-mode revision changes, verify peer critiques remain untrusted suggestions, unsupported objections tell the reviser to preserve the original answer, Stage 2b result/debug metadata remains auditable, and Stage 3 does not call revisions the unconditional primary synthesis source. [workflow: improvement-plan-loop]
