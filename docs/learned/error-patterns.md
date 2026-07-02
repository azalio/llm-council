# Error Patterns (Learned)

<!-- IMPROVEMENT-PLAN-LOOP: promoted from loop learnings. Edit freely and commit with the project. -->

- **Self-Bind Request IDs For Direct Calls** (2026-04-14): When a workflow exposes request-scoped metadata, always bind a fresh request ID inside the core orchestration function if the caller did not bind one, because utilities and tests may call the orchestration layer directly outside the normal API or MCP wrappers. [workflow: improvement-plan-loop]

- **Live And Reloaded Metadata Drift** (2026-04-15): When the stream path emits metadata in multiple stage events, always merge later metadata into the existing assistant message because replacing or dropping one stage payload will make the saved conversation disagree with the live run. [workflow: improvement-plan-loop]

