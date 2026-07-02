# llm-council Architecture Document

## Overview

llm-council is an MCP (Model Context Protocol) server, FastAPI backend, and local React/Vite frontend for multi-LLM deliberation. Instead of querying a single LLM, the system can classify a request into quick, standard, or deep mode, send standard/deep questions to multiple models, collect anonymous peer rankings, optionally run critique/revision stages, and use a chairman model to synthesize a final answer. Context-free first-turn questions can also be served from a validated answer cache when a prior council-backed answer is sufficiently similar. Long MCP calls can run either synchronously with progress/heartbeat notifications or through an in-process async start/poll task store for short-timeout clients. The project is built with Python and uses OpenRouter as its API provider, behind a pluggable provider registry that supports adding more providers.

## Scope

**In Scope:**
- MCP server implementation exposing council deliberation, async start/poll, conversation, model, and metrics tools
- FastAPI backend and local React/Vite frontend for conversation-oriented use
- Pluggable provider architecture (OpenRouter shipped; extension point for more) for LLM API calls
- Mode selection across quick, standard, and deep deliberation paths
- Three-stage deliberation workflow (parallel query, anonymous ranking, chairman synthesis), with optional critique/revision stages for deep or escalated runs
- Council-confidence calculation from anonymous rankings, surfaced to users when the council is split
- Configuration management for council members and chairman
- SQLite-backed conversation storage, summaries, assistant metadata, and retrieval
- First-turn answer cache for eligible context-free `auto` requests, with
  token/semantic matching, chairman validation for borderline semantic matches,
  explicit `bypass_cache`, and process-local KPI reporting
- Per-run Stage 1 concurrency limiting and provider-path backoff for degraded provider periods
- Adaptive sparse routing for routine default-auto council runs, with full-pool expansion on failures, unavailable confidence, or low confidence
- Chairman/council model-family heterogeneity validation at config import and MCP startup
- MCP progress notifications and heartbeat liveness for long council runs
- Process-local council and answer-cache metrics exposed through the backend API
  and MCP tool surface
- Operator-facing LLM-as-a-judge evaluation helper for comparing a chairman
  synthesis against the best available Stage 1 baseline

**Out of Scope:**
- Hosted or multi-tenant deployment
- Custom LLM model hosting
- Durable cross-process async task queue
- Cross-request rate limiting or quota management
- Production-grade secrets management beyond environment-based local configuration

## Quality Goals

1. **Reliability**: Multi-model deliberation reduces single-point-of-failure risk and provides diverse perspectives
2. **Transparency**: Anonymous ranking ensures models evaluate responses objectively
3. **Extensibility**: Modular provider architecture allows adding new API providers
4. **Interoperability**: Standard MCP protocol enables integration with various MCP clients

## System Context

```
┌─────────────────────────────────────────────────────────────────┐
│                      MCP Client (Claude)                        │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ MCP Protocol
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                    llm-council MCP Server                       │
│  Tools: ask_council, start_council_async, poll_council_task,     │
│         list/get conversations, models, metrics                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ direct Python imports
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                 FastAPI backend / Council core                  │
│     mode routing, cache checks, council stages, storage, KPIs    │
└──────────────┬───────────────────┬────────────────────┬─────────┘
               │                   │                    │
               ▼                   ▼                    ▼
       ┌───────────────┐                          ┌───────────────┐
       │   OpenRouter  │                          │ SQLite data/  │
       │    Provider   │                          │ conversations │
       └───────────────┘                          └───────────────┘
               ▲
               │ HTTP API
┌──────────────┴──────────────────────────────────────────────────┐
│                    React/Vite local frontend                     │
└─────────────────────────────────────────────────────────────────┘
```

**External Dependencies:**
- OpenRouter API (openrouter.ai) - API provider
- MCP clients (Claude Desktop, Claude Code, OpenCode, Codex via local config)
- Local browser users of the optional FastAPI + React/Vite UI

## Core Structure

### Directory Layout (Inferred)

```
llm-council/
├── mcp_server/
│   ├── server.py           # FastMCP tools, sync and async council entrypoints
│   └── README.md           # MCP registration and tool documentation
├── backend/
│   ├── main.py             # FastAPI routes and streaming conversation flow
│   ├── config.py           # Council, provider, confidence, cache thresholds
│   ├── council.py          # Mode routing and deliberation stage orchestration
│   ├── agent_router.py     # Sparse/full council route decisions
│   ├── answer_cache.py     # First-turn answer-cache policy and validation
│   ├── metrics.py          # Process-local council/cache metrics
│   ├── observability.py    # Request-scoped structured log helpers
│   ├── openrouter.py       # Provider-agnostic query_model()/query_models_parallel(), dispatches via providers/registry.py
│   ├── providers/          # Provider Protocol + registry + per-provider implementations
│   │   ├── base.py         # Provider Protocol (build_request/parse_response/resolve_auth)
│   │   └── registry.py     # PROVIDER_REGISTRY name -> lazy loader, resolve_provider()
│   ├── eval/               # Judge, answer-check, and leakage-audit helpers
│   └── storage.py          # SQLite conversation storage
├── frontend/               # React 19 + Vite local UI
├── scripts/                # Replay and routing benchmark probes
├── tests/                  # Focused pytest suites for runtime policies
├── pyproject.toml          # Python package dependencies
└── README.md               # User-facing setup and tool contract
```

### Key Components

| Component | Responsibility |
|-----------|----------------|
| `mcp_server/server.py` | MCP protocol handler, tool registration, request routing, heartbeat, and in-process async task store |
| `backend/config.py` | Council model list, chairman model, API configuration, model-family inference, chairman heterogeneity validation |
| `backend/agent_router.py` | Deterministic sparse routing policy for routine auto-standard runs and expansion decisions |
| `backend/answer_cache.py` | First-turn cache eligibility, question similarity, candidate selection, chairman validation, cache-hit construction |
| `backend/eval/judge.py` | Strict JSON judge evaluation for offline/operator quality measurement |
| `backend/eval/answer_check.py` | Answer comparison helpers covered by focused tests |
| `backend/eval/leakage_audit.py` | Prompt/data leakage audit helper covered by focused tests |
| `backend/metrics.py` | Rolling process-local KPIs for council runs, degraded Stage 1 paths, answer-cache lookups, hit rates, validation outcomes, and lookup latency |
| `backend/openrouter.py` | Provider-agnostic `query_model()`/`query_models_parallel()`; dispatches to the resolved provider via `backend/providers/registry.py` instead of a hardcoded branch |
| `backend/providers/base.py` | `Provider` Protocol: structural contract (`build_request`, `parse_response`, `resolve_auth`) every provider implementation satisfies |
| `backend/providers/registry.py` | `PROVIDER_REGISTRY` name→lazy-loader map and `resolve_provider()`, the single point where a provider module is actually loaded |
| `backend/storage.py` | SQLite schema, WAL connection setup, conversation/message persistence, cache candidate queries |
| `frontend/` | Optional browser UI for the FastAPI backend |
| Council Logic | Stage orchestration: intent/reformulation → parallel query → ranking → critique/revision when needed → synthesis |

### API Provider Architecture

The system resolves its API provider through a small provider registry
(`backend/providers/registry.py`) rather than a hardcoded conditional. `backend/openrouter.py`'s
`query_model()` asks the registry for the query function that matches the configured
`API_PROVIDER`; the registry maps `"openrouter"` to a lazy loader callable, so
resolving a name only imports the module the request actually needs. A registered name whose
implementation module is missing from the current build raises an actionable `RuntimeError`
pointing at `API_PROVIDER=openrouter` as the fix, distinct from an unregistered/misspelled
provider name (`KeyError`).

**OpenRouter Provider:**
- The only shipped provider
- Single API key configuration
- Standard OpenAI-compatible API format
- Implemented directly in `backend/openrouter.py` (the registry's `"openrouter"` loader
  just returns that module)

**Adding a new provider**: implement `backend/providers/<name>.py` satisfying the `Provider`
Protocol in `backend/providers/base.py` (`build_request`, `parse_response`, `resolve_auth`),
then register a lazy loader for it in `PROVIDER_REGISTRY`. This is an extension point only —
no additional connectors are implemented by the current registry.

## Runtime Flows

### Stage 0: Mode, Intent, and Cache Gates
Public callers can request `quick`, `standard`, `deep`, or `auto`. Auto mode uses cheap heuristics plus a short model classifier budget to choose the cheapest safe path. First-turn context-free auto requests are eligible for answer-cache lookup before the system spends council calls. `clarify_when_unclear=true` can return a clarification result instead of running the full council.

`quick` mode bypasses the peer council and asks the chairman directly. Standard mode uses the three-stage council. Deep mode adds critique and revision stages before final synthesis. Low-confidence auto-standard runs can escalate into deep within the same run.

### Stage 1: Parallel Council Query
Before Stage 1, API and MCP request paths check the answer cache when the
request is a context-free first turn, `mode="auto"`, not `thorough`, not
clarification-gated, and not `bypass_cache`. Token hits and strong semantic hits
return the stored answer immediately; borderline semantic hits require chairman
validation before serving. Cache misses continue into the normal council flow.

Stage 1 fans out through `query_models_parallel()` with `COUNCIL_STAGE1_MAX_CONCURRENCY` limiting in-flight provider calls. If a provider path fails during that run, later calls for the same provider path reserve staggered starts using `COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS`; unrelated provider paths can still use available concurrency.

For routine `mode="auto"` requests that resolve to standard mode, adaptive routing can start Stage 1 and Stage 2 with a sparse subset of the configured council. High-risk or complex prompts use the full council immediately. Sparse runs expand to the full pool before synthesis when routed Stage 1 produces too few answers, Stage 2 produces too few rankings, confidence is unavailable, or confidence is low.

```
User Question
      │
      ▼
┌─────────┐  ┌─────────┐  ┌─────────┐
│ Model 1 │  │ Model 2 │  │ Model N │
└────┬────┘  └────┬────┘  └────┬────┘
     │            │            │
     └────────────┼────────────┘
                  ▼
         All Responses Collected
```

### Stage 2: Anonymous Ranking
```
All Responses
      │
      ▼
┌─────────┐  ┌─────────┐  ┌─────────┐
│ Model 1 │  │ Model 2 │  │ Model N │
│ Rankings│  │ Rankings│  │ Rankings│
└────┬────┘  └────┬────┘  └────┬────┘
     │            │            │
     └────────────┼────────────┘
                  ▼
         Rankings Aggregated
                  │
                  ▼
         Confidence Calculated
```

When an `auto` request initially selects standard mode and Stage 2 marks the
rankings as low-confidence, the orchestrator escalates that same run into the
deep critique/revision stages before chairman synthesis. Explicit `standard`
requests do not escalate, so callers who deliberately cap cost keep the old
3-stage boundary.

### Stage 2a/2b: Critique and Revision
Deep runs and low-confidence auto-standard escalations add critique and revision stages. The revision policy is evidence-gated, and the resulting `stage2a`/`stage2b` payloads can be persisted alongside the normal stage metadata.

### Stage 3: Chairman Synthesis
```
Responses + Rankings + Confidence Signal
         │
         ▼
    ┌─────────┐
    │Chairman │
    │  Model  │
    └────┬────┘
         │
         ▼
   Final Synthesized Answer
   (hedged when rankings split)
```

### MCP Async Start/Poll
For clients with short tool-call timeouts, `start_council_async` inserts a task record in an in-process dictionary and launches the same council execution through an `asyncio` background task. `poll_council_task` returns `pending`, `running`, `done`, or `error` and prunes the task store to the most recent 50 entries. This is a local convenience queue, not durable cross-process orchestration.

## Source of Truth

| Data Element | Source |
|--------------|--------|
| Council Models | `backend/config.py` (static configuration) |
| Low-confidence threshold | `backend/config.py` (`COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD`) |
| Confidence escalation | `backend/config.py` (`COUNCIL_CONFIDENCE_ESCALATION_ENABLED`) |
| Judge rubric and generation options | `backend/config.py` (`DEFAULT_JUDGE_RUBRIC`, `JUDGE_MODEL`, `JUDGE_TEMPERATURE`, `JUDGE_TOP_P`, `JUDGE_MAX_TOKENS`, `JUDGE_TIMEOUT_SECONDS`, `JUDGE_ENSEMBLE_*`) |
| Answer cache thresholds | `backend/config.py` (`ANSWER_CACHE_SIMILARITY_THRESHOLD`, `ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD`, `ANSWER_CACHE_VALIDATION_THRESHOLD`) |
| Answer cache metrics | `backend/metrics.py`, `GET /api/metrics/council`, MCP `get_council_metrics()` |
| API Keys | Environment variables (`.env` file) |
| Conversation History | SQLite database under `data/` via `backend/storage.py` |
| Available Tools | `mcp_server/server.py` (`ask_council`, async start/poll, conversation, model, metrics tools) |

## Cross-cutting Concepts

### Configuration Management
- Environment-based configuration via `python-dotenv`
- Supported environment variables:
  - `API_PROVIDER`: `openrouter` (the only registered provider)
  - `OPENROUTER_API_KEY`: OpenRouter API key
  - `COUNCIL_LOW_CONFIDENCE_TOP1_THRESHOLD`: top-1 vote share threshold for low-confidence ranking splits
  - `COUNCIL_CONFIDENCE_ESCALATION_ENABLED`: whether auto-standard low-confidence runs escalate into deep critique/revision before synthesis
  - `COUNCIL_ADAPTIVE_ROUTING_ENABLED`: whether routine auto-standard runs may start with a sparse council subset and expand on risk/confidence/failure triggers
  - `COUNCIL_STAGE1_MAX_CONCURRENCY`: max in-flight Stage 1 provider calls
  - `COUNCIL_STAGE1_PROVIDER_BACKOFF_SECONDS`: per-run provider-path backoff after failures
  - `JUDGE_MODEL`: active-provider judge model override
  - `JUDGE_MODEL_OPENROUTER`: provider-specific judge default
  - `JUDGE_TEMPERATURE`, `JUDGE_TOP_P`, `JUDGE_MAX_TOKENS`, `JUDGE_TIMEOUT_SECONDS`: single-pass judge-call controls
  - `JUDGE_ENSEMBLE_ENABLED`, `JUDGE_ENSEMBLE_SAMPLES`, `JUDGE_ENSEMBLE_TEMPERATURES`: optional operator-facing Ensemble Thermo-Judge controls
  - `ANSWER_CACHE_SIMILARITY_THRESHOLD`: token-overlap threshold for direct cache hits
  - `ANSWER_CACHE_SEMANTIC_HIT_THRESHOLD`: semantic-similarity threshold for direct cache hits
  - `ANSWER_CACHE_VALIDATION_THRESHOLD`: lower semantic threshold that requires chairman applicability validation
- `backend/config.py` rejects exact chairman/council overlap and same-family chairman overlap before the MCP server accepts traffic.

### Answer Cache Observability

- `GET /api/metrics/council` and MCP `get_council_metrics()` include an
  `answer_cache` section with lookup/hit/miss/bypass totals, validation
  attempts and outcomes, hit rate, validation approval rate, match-type counts,
  lookup/hit latency summaries, and rolling similarity summaries.
- `scripts/answer_cache_replay.py` replays stored first-turn conversations
  offline without model calls and reports which later questions would match
  earlier cache sources under the current policy. Borderline semantic matches
  are listed as validation candidates because production still requires a
  chairman check before serving them.

### Error Handling
- Timeout handling for API calls (configurable per request)
- Graceful fallback when individual models fail

### Async Architecture
- Async/await pattern for parallel model queries
- `httpx` for async HTTP requests
- `asyncio` for concurrent operation management
- `run_full_council()` accepts an optional best-effort progress callback. MCP wraps it with `Context.report_progress()` and a heartbeat so long stages keep the transport active.
- The MCP async task API is intentionally process-local: useful for Codex/OpenCode-style timeout limits, but not a durable job queue.

### Observability and Evaluation
- `backend/observability.py` binds request IDs through context variables and emits structured JSON log payloads.
- `backend/metrics.py` keeps rolling process-local council and answer-cache KPIs surfaced through FastAPI and MCP.
- `backend/eval/` contains operator-facing judge, answer-check, and leakage-audit helpers.
- Focused pytest suites cover deliberation mode, confidence, routing, backoff, cache behavior, progress callbacks, persistence metadata, judge output, and leakage audit behavior.

## Deployment/Operations

### Runtime Requirements
- Python 3.10+
- `uv` package manager (recommended)
- API keys for selected provider

### Deployment Configuration

**Environment Variables (`.env`):**
```bash
API_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
```

**MCP Server Launch:**
```bash
uv run mcp_server/server.py
```

**FastAPI + frontend launch:**
```bash
./start.sh
```
The README documents backend `:8001` and frontend `:5173` for local UI use.

### MCP Client Configuration

**Claude Desktop (`claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uv",
      "args": ["--directory", "/path/to/llm-council", "run", "mcp_server/server.py"],
      "env": { "OPENROUTER_API_KEY": "sk-or-v1-..." }
    }
  }
}
```

**Claude Code (`~/.claude/settings.json`):**
```json
{
  "mcpServers": {
    "llm-council": {
      "command": "uv",
      "args": ["--directory", "/path/to/llm-council", "run", "mcp_server/server.py"],
      "env": { "OPENROUTER_API_KEY": "sk-or-v1-..." }
    }
  }
}
```

## Known Risks/Gaps

1. **Process-local Runtime State**: Metrics and async task records reset on process restart; async start/poll is not a durable queue.

2. **Answer Cache Quality Risk**: Cache hit rate alone is not proof of quality. Thresholds need manual replay review and runtime validation monitoring, especially near semantic boundaries.

3. **Local Persistence Boundary**: Conversation storage is local SQLite under `data/`; it is not a multi-user durable service database.

4. **Frontend/API/MCP Drift Risk**: The MCP tool surface, FastAPI backend, and frontend all expose related council behavior; request-path cache, mode, async, and metrics semantics should stay aligned when any surface changes.

5. **No Security Review**: API key handling and network security are not documented as a dedicated threat model.

6. **Version 0.1.0**: Early-stage project; API and architecture may change significantly.

## ADR Links

Information not available in current evidence.

## Freshness

Last refreshed: 2026-06-26

Refresh reason: Daily architecture maintenance. The previous document was older than the freshness window and missed current async MCP start/poll behavior, SQLite persistence details, FastAPI/frontend boundaries, structured observability, and evaluation/security helper surfaces.

Evidence used:
- README.md
- pyproject.toml
- conftest.py
- main.py
- mcp_server/server.py
- mcp_server/README.md
- backend/main.py
- backend/council.py
- backend/storage.py
- backend/observability.py
- backend/answer_cache.py
- backend/metrics.py
- backend/agent_router.py
- backend/eval/
- frontend/package.json
- docs/answer-cache-metrics.md
- scripts/answer_cache_replay.py
- tests/

Current delta captured: The council flow remains a Python async MCP/FastAPI orchestration service with optional frontend code, while async MCP start/poll, SQLite-backed conversation persistence, structured observability, answer-cache metrics, adaptive sparse routing, confidence escalation, and evaluation/security helpers are part of the current runtime contract.
