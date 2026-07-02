---
paths:
  - "**/*.md"
  - "**/*.py"
---

# Performance Patterns (Learned)

<!-- IMPROVEMENT-PLAN-LOOP: promoted from loop learnings. Edit freely and commit with the project. -->
- **Separate Cache-Hit Latency From Council Latency** (2026-05-18): When measuring pre-council cache value, always record cache-hit latency separately from Stage 1/2/3 latency because the user-visible payoff is avoiding the full council path, not making council stages faster. [workflow: improvement-plan-loop]
