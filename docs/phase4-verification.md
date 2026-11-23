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

## Deployment readiness and unverified items

- The Docker image and Compose stack are intended for a Hugging Face Docker Space. The image listens on port 7860. Public deployment remains unverified.
- Service snapshot checked 2026-11-23: Hugging Face CPU Basic is listed as 2 vCPU, 16 GB RAM, 50 GB non-persistent disk, and no hourly hardware fee. However, creating a Docker Space from a personal account requires HF PRO at $9/month (organizations require Team or Enterprise); free personal accounts cannot create a Docker Space. Neon Free lists 100 compute-hours/month and 1 GB per project, with scale-to-zero after five idle minutes. Staying inside those Neon allowances costs $0; usage beyond them may be billable. These are current published plan terms, not a guarantee of account eligibility or future pricing.
- Neon is not provisioned. Its pooled endpoint uses PgBouncer transaction mode and does not support session-level advisory locks and some other session features. Start compatibility verification with Neon’s direct endpoint; test migrations, LangGraph checkpoint create/pause/resume, approval writes, and SQLAlchemy pooling on an actual project before launch. The current app shares a URL for SQLAlchemy and the psycopg checkpointer, so switching to a pooled endpoint is not verified.
- Chroma can be reconstructed from versioned synthetic documents; hosted persistent disk is not required by this deployment shape.
- Hosted startup, migrations, health checks, quotas, cold starts, resource use, and account-specific costs are unverified.
- The deployed app must set `OPSPILOT_ENV=production` and use hosted secret storage for database/provider credentials.
- There is no public URL. GitHub remote is not configured and no push has been made.

## Required account and approval steps

1. Connect/authorize GitHub and explicitly approve creation of the intended public repository and push of the two local Phase 4 commits.
2. Authorize Hugging Face Space creation and public deployment. For an individual Docker Space, confirm that HF PRO is already active or explicitly approve its currently listed $9/month subscription. CPU Basic itself has no hourly compute charge.
3. Authorize Neon account/project use (Free plan is sufficient for initial low-volume validation if quotas fit), and add the direct PostgreSQL URL to the Hugging Face Space secrets/settings. Do not send the database URL or API keys in chat.
4. Explicitly approve public launch after the Neon hosted checkpoint and write paths pass the verification checklist.

Groq provider usage is account/quota dependent and was not priced here. Hosted cold-start latency, actual Groq spend, Neon consumption, and combined monthly spend cannot be measured before hosted testing.
