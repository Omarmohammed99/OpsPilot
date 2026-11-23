# Phase 4 local verification record

Date: 2026-11-23. This record covers local verification only. No GitHub push, hosted account, paid resource, or public deployment was used.

## Local synthetic record counts after live verification

The persistent local database contained 18 demo sessions, 25 incidents, 25 assets, 25 incident-asset links, 75 application logs, 24 work orders, and 8 knowledge documents. The run history contained 15 investigations, 3 proposed actions, 2 approval decisions, 1 action execution, and 55 audit events. Chroma contained 7 troubleshooting chunks and 1 error-reference chunk. These totals include synthetic test and troubleshooting sessions, not just the two reported live cases. Ingestion failures: 0. The one action execution corresponds to the approved live case; the rejected live case has no execution.

## Real provider workflow

Provider: configured Groq API, model `openai/gpt-oss-20b`. Application: local Docker Compose. Operational database and LangGraph checkpoint store: local PostgreSQL. Vector retrieval: local ChromaDB.

Two isolated sessions investigated the synthetic `db-pool-exhaustion` scenario through the FastAPI application:

| Path | Observed result |
| --- | --- |
| Approval | Adaptive graph used read-only asset/log/runbook tools; Chroma returned the pool-exhaustion runbook; report schema validated and citations resolved to evidence collected in the run; graph paused and resumed; approved action changed only the session asset from `degraded` to `healthy`; PostgreSQL recorded approval, execution, and audit events. |
| Rejection | Adaptive graph gathered PostgreSQL and Chroma evidence and paused/resumed through the checkpoint; rejection was audited; asset remained `degraded`; no execution row was created. |
| Duplicate approval | Repeating approval for the accepted action returned the existing execution as idempotent; only one execution row exists for that action. |

Accepted run ID: `e4fa1644-467c-4989-bc4f-0184eaf67c5c`. Rejected run ID: `afc0967b-af8e-4457-a647-d1541a832589`. Provider traces showed native function calls for investigation. One Groq `tool_use_failed` response exercised the bounded separate structured-output fallback for the final decision. Real API verification is distinct from the deterministic tests below.

## Deterministic checks

The regression suite last completed with 39 passing tests in the Linux Docker environment. Ruff checks and formatting passed, and `uv lock --check` passed. Re-run these commands after checkout:

```powershell
uv run --python 3.12 ruff check src tests
uv run --python 3.12 ruff format --check src tests
uv run --python 3.12 pytest -q
uv lock --check
```

The two live-provider runs were also scored against the offline `db-pool-exhaustion` case. This is a two-run sample, not a dataset-wide benchmark:

| Metric | Result |
| --- | ---: |
| Structured output valid | 1.00 |
| Evidence-code recall | 1.00 |
| Citation grounding precision / coverage | 1.00 / 1.00 |
| Required read-tool selection | 1.00 |
| Action proposal, approval compliance, execution correctness | 1.00 / 1.00 / 1.00 |
| Root-cause token recall | 0.4375 average |

The root-cause metric is token overlap against a longer reference sentence; its lower score indicates the reports were more concise than the reference and merits a richer semantic evaluation before making broad quality claims. Both reports passed schema validation and cited only observed evidence.

On Windows, PostgreSQL integration tests can run in a disposable Linux container on the Compose network; see the implementation session run record for the exact environment invocation. Never point tests at a production or personal database.

## Render Free resource check and deployment readiness

- Render lists Free web services at 0.1 CPU/512 MB, with 750 included instance-hours per workspace per month, 15-minute idle spin-down, and ephemeral disk. Idle services wake with a cold start. The `render.yaml` blueprint uses a Docker Web Service and `/api/health/ready` health check.
- Local simulation under the exact 0.1 CPU/512 MB limit reached ready status on the final image with `PORT=10000`. It applied startup migrations, created/used PostgreSQL checkpointer tables, rebuilt Chroma to 7 troubleshooting chunks and 1 error-reference chunk, and used 234 MiB maximum observed during startup (211.5 MiB after ready). Startup took roughly two minutes under this CPU cap. This does not measure peak request memory under a real Groq investigation or hosted traffic.
- Chroma used an empty container-local `/tmp/opspilot-chroma` path and was populated from the versioned document corpus at application startup. The local session therefore demonstrated reconstruction without a persistent vector volume. Render's ephemeral filesystem loss is compatible with that design.
- The Docker entrypoint now binds to Render's injected `PORT`, defaulting to 7860 for local Compose. Render health and real request behavior remain unverified.
- The current process-local request limiter keys on the peer IP. Render documents that apps normally see a proxy IP unless they read forwarded client IP headers. Until Render header behavior is confirmed, this is a conservative shared per-proxy limit of 90 requests per minute, not a per-visitor quota; a busy public demo may see unrelated visitors share that limit.
- Service snapshot checked 2026-11-23: Render Free is $0 compute, with the usage limits above. Neon Free lists 100 compute-hours/month and 1 GB per project, with scale-to-zero after five idle minutes. Staying within the Neon Free quota costs $0; usage beyond allowances may be billable. These published quotas may change.
- Neon is not provisioned. Its pooled endpoint uses PgBouncer transaction mode and does not support session-level advisory locks and some other session features. Start hosted compatibility checks with Neon’s direct endpoint; verify migrations, checkpoint pause/resume, approval writes, and SQLAlchemy connections against the actual project. The app currently shares a URL between SQLAlchemy and the psycopg checkpointer, so pooled endpoint compatibility remains unverified.
- No hosted Render health check, Neon migration/checkpoint test, account-specific quota check, hosted Groq run, or public URL exists yet.
- `render.yaml` requires `DATABASE_URL` and `GROQ_API_KEY` to be entered as private service secrets. Do not commit them or send them in chat.

## Authentication required to continue

At the first auth check, GitHub CLI, Render CLI, and Neon CLI were absent, no GitHub/Render/Neon MCP tools were connected, and no relevant API credential environment variables were present. Git Credential Manager is installed but access to enumerate saved credentials was denied. No external calls or writes were attempted. The user said they would connect the accounts; continue only after the secure sign-in state is available.

Once connected, the authorized workflow is to create and push the public GitHub repository, configure the Neon Free project and secret, deploy the Render Free service, then run a hosted real-provider investigation that verifies checkpointed approval, the approved database update, rejection without update, and duplicate-approval idempotency. No paid plan/resource is authorized.

Render cold-start latency, Groq charges/quota, Neon usage, and combined monthly spend depend on actual accounts and usage. Current documented free quotas do not guarantee future limits.
