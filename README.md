---
title: OpsPilot
emoji: 🧭
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
short_description: Adaptive incident investigation with evidence and human approval
---

# OpsPilot

OpsPilot is an AI engineering portfolio demo for investigating synthetic IT incidents. It demonstrates an adaptive, bounded LangGraph agent that gathers evidence from PostgreSQL and ChromaDB, produces a cited structured report, pauses for a human decision, and can execute one allowlisted change against isolated demo data.

**Status:** Phase 4 prepared locally; no public deployment or GitHub push has been made. The live provider workflow has been verified against Groq on the local Docker stack. There is no live demo URL yet.

## Architecture

```mermaid
flowchart LR
  UI[Browser UI] --> API[FastAPI single app]
  API --> G[Bounded adaptive LangGraph]
  G --> R[Read only PostgreSQL tools]
  G --> V[Chroma runbook retrieval]
  G --> P[Groq configurable provider]
  G --> CP[(PostgreSQL checkpoints)]
  API --> S[(PostgreSQL app data)]
  API --> H[Session bound approval]
  H -->|approved only| TX[Allowlisted transaction]
  TX --> S
  TX --> A[Immutable action and audit records]
```

## How the agent works

For each incident, the graph loads session-scoped incident context and presents the model with an allowlist of read-only tools. The model can choose additional tools and refine filters using prior observations. The graph ends investigation when evidence is sufficient, a structured report is ready, or a configured budget is exhausted. Iteration, tool-call, token, wall-clock, estimated-cost, concurrency, and per-session limits bound work. It is an agent because the next read operation is selected from evolving evidence rather than fixed in advance.

The report uses a strict schema and citations must resolve to evidence references gathered in the current run. Groq `openai/gpt-oss-20b` is the default. Native tool calls are used for investigation; if Groq returns its `tool_use_failed` response for a particular tool decision, one separate strict JSON-schema fallback is attempted within the same budget. Report generation is a separate structured-output request. An OpenAI-compatible provider can be configured as an alternative. Keys are read from environment variables and never stored in source.

## Data, isolation, and retrieval

The operational dataset is synthetic: eight reproducible incident scenarios and associated assets, logs, work orders, and resolutions. Each opaque browser session receives its own cloned records. The offline evaluation labels live under `data/eval/ground_truth` and are excluded from the application image and agent-facing loaders. Versioned synthetic runbooks are indexed in Chroma. The current local embedding implementation is deterministic hashing; it is lightweight and reproducible, with semantic embedding upgrades left as future work.

PostgreSQL stores operational records, investigation/action/audit data, approval decisions, and LangGraph checkpoints. Chroma is persistent in local Compose. For a small hosted demo, rebuild its index from the versioned documents on startup rather than paying for a persistent vector store. Session reset is available by starting a fresh browser session, which creates a pristine isolated copy. Revoking a session does not erase its audit trail.

## Human approval and write safety

There is exactly one permitted write: change an in-scope synthetic asset state from `degraded` to `healthy`. The model cannot execute SQL or authorize itself. Action type, parameters, and payload hash are persisted before approval and treated as immutable. Approval is tied to the opaque demo session and expires. The approval record, asset update, execution result, and audit event are written transactionally. Unique action and idempotency constraints prevent duplicate execution. Rejection records the decision without modifying the asset. This authentication mechanism is suitable for a controlled portfolio demo, not a general identity system.

## Verification

The actual Groq verification used the local Docker application, PostgreSQL, PostgreSQL-backed LangGraph checkpoints, and real API calls. On the database-pool-exhaustion scenario, the agent selected read-only PostgreSQL tools, retrieved the relevant Chroma runbook, and produced a structured report citing observed evidence. The graph paused for approval and resumed from its PostgreSQL checkpoint. Approval changed the isolated asset from degraded to healthy, recorded the action and audit trail, and a repeated approval returned the original execution. A separate rejection run resumed successfully and left the asset degraded with no execution record. These are end-to-end local provider checks, not hosted-service tests or a benchmark over all scenarios.

The deterministic suite covers graph behavior, API flows, database constraints, ingestion, retrieval, checkpoints, provider fallback, and safety invariants. For reproducible commands, see [Phase 4 verification notes](docs/phase4-verification.md). Evaluation labels are only loaded by `opspilot-eval`; a score is meaningful only for the saved runs and scenario IDs it covers.

## Local setup (PowerShell)

Requirements: Docker Desktop with Linux containers, Git, and `uv` 0.10.9 or later.

```powershell
uv python install 3.12
uv sync --python 3.12 --extra test --extra checkpoint --extra dev
Copy-Item .env.example .env
# Add a Groq key to the ignored .env file for live investigation.
docker compose --env-file .env up -d --build
```

Open `http://localhost:7860`. Startup applies migrations and runs idempotent ingestion. `GROQ_API_KEY` is optional for startup and health checks. Never commit `.env` or paste credentials into chat. The default model is `openai/gpt-oss-20b`. Alternative provider settings are `LLM_PROVIDER=openai_compatible`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`.

The Compose app uses Docker network hostnames. For host-side CLI access, point settings to localhost:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://opspilot:opspilot_dev@localhost:5432/opspilot'
$env:CHROMA_MODE = 'http'
$env:CHROMA_HOST = 'localhost'
$env:CHROMA_PORT = '8001'
uv run --python 3.12 opspilot-data db-check
uv run --python 3.12 opspilot-data counts
uv run --python 3.12 opspilot-eval score path\to\saved-runs.json
```

## Tests and evaluation

```powershell
uv run --python 3.12 ruff check src tests
uv run --python 3.12 pytest -q
uv run --python 3.12 opspilot-eval score path\to\saved-runs.json
```

PostgreSQL integration tests require `OPSPILOT_TEST_DATABASE_URL` to point to a disposable database. Ground truth is never sent to the model. See [data layer](docs/data-layer.md) and [dataset provenance](docs/dataset-provenance.md).

## Deployment preparation

The current target is one Render Free Docker Web Service and Neon PostgreSQL. [`render.yaml`](render.yaml) configures the service health check, production cookie mode, embedded Chroma rebuild, Groq provider, and secret placeholders. The Docker entrypoint honors Render's `PORT` setting. Neon holds operational data and LangGraph checkpoints; set the Neon connection URL and Groq key in Render's secret environment settings, never in source. Start with Neon’s direct endpoint and verify checkpoint resume against the actual Neon project before launch; Neon’s pooler is transaction-mode and has session-feature limits.

Render Free currently provides 0.1 CPU and 512 MB RAM, 750 free instance hours per workspace per month, and sleeps after 15 minutes idle; it has no persistent filesystem. In a local container limited to 0.1 CPU/512 MB and listening on Render's `PORT=10000`, OpsPilot completed migrations, rebuilt the embedded Chroma index, and reached readiness. Peak observed startup memory was about 234 MiB; ready memory was about 212 MiB. Startup took about two minutes under the CPU cap. This measured startup and readiness only, not peak request memory under a live provider investigation or hosted traffic. Chroma is rebuilt from versioned documents at startup, so loss of Render's ephemeral filesystem is expected. See [Render Free limitations](https://render.com/docs/free), [Docker deployment](https://render.com/docs/docker), and [Render compute plans](https://render.com/docs/compute-plans).

Neon Free currently includes 100 compute-hours/month and 1 GB per project with scale-to-zero after five idle minutes. No paid resource is configured. These current published quotas can change; hosted usage must be monitored. See [Neon pricing](https://neon.com/pricing) and [connection pooling documentation](https://neon.com/docs/connect/connection-pooling). No GitHub push, Neon provisioning, or Render deployment has been performed yet.

## Limitations and trade-offs

- The data, scenarios, and actions are synthetic and demonstrate an engineering pattern rather than production operations.
- The browser session token is a lightweight authorization boundary, not user identity or multi-factor authentication.
- Rate limits and concurrency guards are process-local and reset with the app container; a multi-replica public deployment would need shared coordination. On Render, the rate limiter currently sees the proxy peer IP, so the 90-request/minute limit can be shared by multiple visitors.
- Fresh sessions provide clean demo state, but records and audit history accumulate in PostgreSQL; a hosted retention policy should be chosen before public launch.
- Chroma uses deterministic local hashing embeddings, which favors reproducibility over semantic retrieval quality.
- Groq free/account quotas may throttle requests; the client honors bounded retry timing, so investigation latency can vary.
- No hosted deployment, public URL, hosted database compatibility test, or account-specific cost estimate is claimed.

## Future improvements

Evaluate a semantic embedding provider, add quality benchmarks across every scenario, measure hosted cold-start and investigation latency, define session/audit retention, and verify Neon connection-pool behavior under the Render configuration. Any future actions should remain separately allowlisted and approval gated.
