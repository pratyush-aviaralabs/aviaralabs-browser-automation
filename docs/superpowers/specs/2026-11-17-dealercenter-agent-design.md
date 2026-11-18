# DealerCenter Automation Agent — Design

**Status:** Approved by user 2026-11-17. This is the spec the build plans implement.

## Intent

Replace the ad-hoc Gumloop-driven DealerCenter cost-add automation with a
system that runs the deterministic parts (login, search, data entry, file
upload, verification) as plain tool calls — no LLM — and only invokes an
LLM when a step fails and a judgment call is needed about how to recover.
Must scale to ~1000 invoices/day now, with a clear path to ~10k/5min later
without a rewrite.

## Goals

- Deterministic tool layer with zero LLM cost on the happy path.
- LLM/LangGraph involvement confined to failure classification, self-heal
  attempts, and human escalation.
- Pluggable at three seams: browser engine, LLM provider, DB dialect.
- DB schema multi-tenant-ready; API/deployment stays single-tenant for now.
- Queue sized for 1000/day without introducing Kafka-class operational cost.
- Cost/time per tool call and per LLM call recorded per invoice.
- Session/MFA minimized via persisted, reusable browser auth state.

## Non-goals (for now)

- Multi-tenant API surface (auth, tenant switching) — schema only.
- Front-end dashboard — schema/log shape should make one easy later, not
  built now.
- Horizontal worker autoscaling — design must not block it, not implement it.

## Approaches considered

1. **Single LangGraph agent drives every step via tool-calling.** Rejected:
   puts an LLM call (cost + latency + nondeterminism) in the hot path of
   every one of 1000 daily invoices for logic that has exactly one correct
   answer.
2. **Pure deterministic script, no LLM at all** (closest to the existing
   Gumloop skill). Rejected as the end state: when DealerCenter's DOM
   shifts (as we hit repeatedly in the POC — duplicate hidden buttons, async
   modals), a human has to fix the script every time. No self-repair path.
3. **Deterministic tool layer + LangGraph recovery layer, LLM only on
   failure** (chosen). Keeps cost at zero for ~99% of runs, adds a bounded,
   observable self-heal path for the rest, escalates cleanly when self-heal
   can't resolve it.

## Architecture

```
Webhook/API -> enqueue -> Redis+RQ -> worker process
                                        |
                              [Tool Layer: Login -> Search -> CostAdd -> Verify]
                                        | (raises StepFailure)
                                        v
                              [LangGraph recovery: classify -> repair/retry -> escalate]
                                        |
                                   Postgres (invoices, tool_runs, recovery_attempts,
                                             escalations, auth_sessions, tenants)
```

## Pluggability seams

- **Browser engine:** `BrowserDriver` ABC; `PlaywrightDriver` is the only
  implementation today. Tools depend on the ABC, never on `playwright.*`
  directly.
- **LLM provider:** `config.py` → `LLMConfig(provider, model,
  system_prompt_path, api_key)`; an `LLMClientFactory` returns a
  LangChain-compatible chat model. Switching OpenAI/Anthropic/Gemini is a
  config change.
- **DB dialect:** SQLAlchemy models + Alembic migrations, dialect-agnostic
  types only (no Postgres-only JSONB, etc.). `DBConfig` reads `DATABASE_URL`
  directly from `.env` (already provided, Supabase connection string in
  dev); `DB_BACKEND` is a label for dialect-specific behavior if ever
  needed, not something a URL gets rebuilt from. Prod points `DATABASE_URL`
  at Azure Database for PostgreSQL or MySQL — same models, same migrations.
- **PDF storage:** `StorageConfig` reads `STORAGE_BACKEND` /
  `STORAGE_LOCAL_PATH` from `.env`. Local disk today; swappable to blob
  storage later without touching `Invoice.local_pdf_path`, which just
  stores whatever path/key the active backend returned.

## Workflow as data, not code

Tool *sequence* is itself a row set (`workflows` / `workflow_steps`), and
each invoice's trip through a sequence is a queryable, resumable record
(`workflow_runs`, pointing at `current_step_order`). This is what lets a
future UI retry a single failed step instead of the whole pipeline, and
lets the sequence be reordered/edited as data instead of a code change.
The runner that walks `workflow_steps` and writes `workflow_runs` is
Phase 2 (queue/worker) scope — Phase 1 only lays the schema and ships
the `Tool` implementations the runner will eventually call.

## Data model (see Task 3 of Phase 1 plan for exact columns)

`invoices.vin` stores the **full VIN** (not pre-truncated) — the ingestion
layer receives the full VIN; truncation to last-6 for DealerCenter's search
box is a `SearchInvoiceTool`-time concern, not a storage decision.

`tenants`, `invoices`, `tool_runs` (one row per tool call: duration_ms,
llm_cost_usd — 0 for deterministic tools), `recovery_attempts` (LLM calls
only: model, tokens, cost_usd, classification, outcome), `escalations`,
`auth_sessions` (tenant-scoped, holds serialized browser storage state).

## Queue

Redis + RQ. Rejected Kafka (operational cost disproportionate to 1000
jobs/day) and BullMQ (Node-only; would add a second runtime alongside an
all-Python stack for no semantic gain RQ doesn't already provide).

## Observability

- Every tool call → one `tool_runs` row (status, duration, cost=0).
- Every LLM call inside the recovery graph → one `recovery_attempts` row
  (tokens, cost, classification, outcome) **and** a LangSmith trace (free
  tier). `recovery_attempts.langsmith_run_id` is the join key a future FE
  would use to deep-link into LangSmith.
- Escalations are a terminal, logged state — never silent.

## Future scale path (not built now, must not be blocked)

- One worker process = one browser instance = one invoice at a time today.
  Going to 10k/5min means N worker processes pulled from the same RQ queue,
  each with its own `AuthSession` checkout — the queue and DB schema
  already support this; it's a deployment/process-count change, not a
  redesign.
- A read-only FE later reads `tool_runs`/`recovery_attempts`/`escalations`
  directly and links out to LangSmith via `langsmith_run_id` — no new
  write path needed.
