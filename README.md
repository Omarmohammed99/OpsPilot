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

The intended host is a single Docker Space listening on port 7860, with Neon PostgreSQL for app state and checkpoints. The Chroma index is rebuilt from the versioned corpus. Configure `OPSPILOT_ENV=production`, database credentials, and provider credentials as hosted secrets; do not bake secrets into the image. Apply migrations at startup. Neon provides both pooled and direct connection URLs. Its pooler uses transaction mode and has session-feature limits; begin hosted verification with the direct connection URL, then test the checkpointer and SQLAlchemy pool before considering the pooled endpoint.

This repository does not provision accounts, create paid resources, push to GitHub, or deploy publicly. Hugging Face currently lists CPU Basic at 2 vCPU/16 GB RAM with 50 GB of non-persistent disk and no hourly hardware charge, but personal Docker Spaces require PRO ($9/month); free personal accounts cannot create Docker Spaces. Neon Free currently includes 100 compute-hours/month and 1 GB per project, with scale-to-zero after five idle minutes. Review current [Hugging Face Spaces overview](https://huggingface.co/docs/hub/en/spaces-overview), [Hugging Face pricing](https://huggingface.co/pricing), [Neon pricing](https://neon.com/pricing), and [Neon connection pooling documentation](https://neon.com/docs/connect/connection-pooling) before selecting resources. A basic Space filesystem is not durable across restarts; this design avoids relying on it for Chroma persistence. See the dated verification record for the plan snapshot and limitations.

## Limitations and trade-offs

- The data, scenarios, and actions are synthetic and demonstrate an engineering pattern rather than production operations.
- The browser session token is a lightweight authorization boundary, not user identity or multi-factor authentication.
- Rate limits and concurrency guards are process-local and reset with the app container; a multi-replica public deployment would need shared coordination.
- Fresh sessions provide clean demo state, but records and audit history accumulate in PostgreSQL; a hosted retention policy should be chosen before public launch.
- Chroma uses deterministic local hashing embeddings, which favors reproducibility over semantic retrieval quality.
- Groq free/account quotas may throttle requests; the client honors bounded retry timing, so investigation latency can vary.
- No hosted deployment, public URL, hosted database compatibility test, or account-specific cost estimate is claimed.

## Future improvements

Evaluate a semantic embedding provider, add quality benchmarks across every scenario, measure hosted cold-start and investigation latency, define session/audit retention, and verify Neon connection-pool behavior under the intended Space configuration. Any future actions should remain separately allowlisted and approval gated.
