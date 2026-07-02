# Architecture Patterns (Learned)

<!-- IMPROVEMENT-PLAN-LOOP: promoted from loop learnings. Edit freely and commit with the project. -->

- **One Debug Contract Across Providers And Stages** (2026-04-14): When adding observability to council runs, always normalize provider outcomes into a shared `_debug` contract before stage aggregation because Stage 1, Stage 2, Stage 3, FastAPI, and MCP all need the same failure semantics. [workflow: improvement-plan-loop]

- **One Run Debug Contract For Metrics** (2026-04-15): When adding aggregate observability to council runs, always derive counters and percentiles from the shared top-level `debug` payload because backend, streaming, and MCP entrypoints otherwise drift into incompatible telemetry shapes. [workflow: improvement-plan-loop]

- **One Sanitization Helper Across Entry Points** (2026-04-15): When a council metadata field is visible in saved conversations, always route FastAPI direct, FastAPI stream, and MCP storage through the same sanitization helper because persistence policy is a cross-entry-point contract, not a frontend detail. [workflow: improvement-plan-loop]

