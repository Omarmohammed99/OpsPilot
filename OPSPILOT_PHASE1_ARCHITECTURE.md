# OpsPilot — Phase 1 Architecture Proposal

**Status:** Revised to incorporate user direction; awaiting final Phase 2 approval  
**Prepared:** 23 November 2026  
**Decision gate:** User approval required before implementation, dataset downloads, or paid provisioning

## 1. Executive summary

Build OpsPilot as a compact FastAPI application with a single responsive web UI, PostgreSQL as the source of truth, ChromaDB for versioned troubleshooting knowledge, and a LangGraph `StateGraph` for adaptive investigation and approval. Use synthetic, internally consistent incident records as the primary demonstration dataset. Keep the architecture technically genuine while avoiding microservices and distributed infrastructure beyond what the workflow needs.

The agent receives typed read-only application tools and can adapt its investigation over multiple steps: select a tool, inspect observations, refine searches, or pivot to another tool based on evidence. Strict call, iteration, token, and estimated-cost budgets bound this autonomy. It cannot submit SQL or write to the database. It produces a validated report and, when appropriate, one typed action proposal. Human approval is bound to immutable action parameters and a hash. A narrowly scoped application service revalidates and commits the single allowlisted PostgreSQL update, execution record, and audit event in one transaction, protected by a unique action/idempotency key. Rejection has no write side effect. The graph checkpoints to PostgreSQL and interrupts/resumes around approval.

Use Docker Compose locally. For a public demo, prefer a Hugging Face Docker Space for the web/API and Neon for managed PostgreSQL. The Space filesystem is ephemeral and free CPU Spaces can sleep. Rebuild the small Chroma index from versioned synthetic documents at startup; keep graph checkpoints and workflow state in Neon. Groq is the preferred configurable LLM provider, subject to model/API mode capability checks for both tool use and structured reports. No API keys are hardcoded. Visitors should receive isolated synthetic records per session if practical; a bounded shared-demo alternative is documented below.

## 2. Repository inspection and implications

Inspection of the intended workspace found one `.env` file, no visible source files or project manifests, and no Git repository (`git status` reports that the directory is not a repository). The `.env` contents were deliberately not read. The user has confirmed this is the intended greenfield folder. Initialize Git locally during implementation, protect `.env` through `.gitignore`, and do not configure a remote or push without explicit approval. This document is the only project artifact so far.

## 3. Dataset research and data strategy

### Recommended baseline

Generate the core demonstration corpus synthetically and commit its versioned seed source (JSONL/YAML) after approval. There is no verified public dataset found in this research that supplies all linked entities OpsPilot needs—incident narrative, service/asset inventory, timestamp-correlated logs, work orders, troubleshooting material, and known resolution—under one clearly reusable license. Combining unrelated corpora would create misleading correlations. Synthetic records can be consistent, deterministic, safely public, and have explicit ground truth.

### Public log candidate: LogHub

The upstream [LogHub repository](https://github.com/logpai/loghub) describes research/academic availability and requires users to refer to the repository and cite the LogHub paper. It lists Linux system logs (25,567 lines, 2.25 MiB), Apache error logs (56,481 lines, 4.90 MiB), and ZooKeeper logs (74,380 lines, 9.95 MiB), among others. HDFS v1 is labeled but 1.47 GiB, so it is a poor Phase 1 download choice. The [LogHub-2.0 license](https://github.com/logpai/loghub-2.0/blob/main/LICENSE) likewise states research/academic use and attribution conditions; do not treat this as an OSI permissive license or assume commercial redistribution rights. Upstream warns that logs may not be sanitized. Verify the exact chosen dataset's terms and content before any download; do not redistribute raw logs in a public app image. The small Linux/Apache corpora are candidate parser/retrieval benchmarks only, not incident-grounded scenario data.

The initial research did not verify a Hugging Face dataset card that adds coherent operational incident entities or a clearer license for the intended end-to-end workflow. Do not choose a Hugging Face mirror merely because it exists; inspect its repository identifier, revision, files/features, declared license, upstream provenance, and size before use. Synthetic-first means the implementation can proceed without downloading any dataset.

### Synthetic scenarios and leakage boundary

Create 8–12 deterministic base scenarios spanning: expired TLS certificate, exhausted database connection pool, disk pressure from runaway logs, failed deployment due to missing environment configuration, and stale worker queue. Each includes consistent service/asset IDs, event times, logs, work-order history, troubleshooting guides, expected cause, evidence IDs, and bounded expected fix. Add ambiguous/no-fix-needed cases. At session creation, clone one small scenario and its related synthetic records into rows tied to that opaque session. Reads and the approved change are always scoped to those rows. This is a lightweight demo isolation boundary, not a claim of production multi-tenant isolation.

Split files and access paths:

- `data/seed/public/`: incidents, assets, operational logs, work orders, and troubleshooting documents visible to the application/agent.
- `data/eval/ground_truth/`: scenario labels, expected cause/evidence/resolution and scoring annotations. Load only in the offline evaluator/test process; never include in the runtime image, API, Chroma collection, prompt context, or agent database role.
- Stable scenario IDs tie the two sets together only inside evaluation code. Runtime records have no answer-key columns.

## 4. Data placement and schema proposal

PostgreSQL holds transactional, relational, time-filtered, and auditable records. ChromaDB holds derived document chunks and embedding vectors; PostgreSQL retains the canonical source documents and ingestion/version metadata. Logs are rows in PostgreSQL for the modest demo volume; for larger corpora, store immutable files in object storage and retain indexed excerpts/locations in PostgreSQL.

| Table | Main columns and constraints |
|---|---|
| `demo_sessions` | UUID PK; hash of random session secret (never store raw token), created/expiry times, status and seed version; unique secret hash. |
| `incidents` | UUID PK; demo session FK (nullable only for offline evaluation); external key unique within session; title, description, severity/status enums, service key, opened/updated timestamps; session/status/severity and session/service/time indexes. |
| `assets` | UUID PK; demo session FK; asset key unique within session; service/environment/type, hostname label, operational state, metadata JSONB; index session/service/environment. |
| `incident_assets` | incident and asset FKs, composite PK, relationship role. |
| `application_logs` | UUID PK; demo session FK; asset FK nullable; service key, timestamp, level, source, message, event code, safe structured attributes JSONB; indexes `(session_id, service_key, occurred_at DESC)`, `(asset_id, occurred_at DESC)`, event code. No secrets in seed logs. |
| `work_orders` | UUID PK; demo session FK; incident FK nullable, asset FK, status/type, summary, opened/closed timestamps; indexes by session/asset/status and incident. |
| `knowledge_documents` | UUID PK; stable `source_key` unique, document type/title/body, version, license/source attribution, checksum, active flag. |
| `investigation_runs` | UUID PK; incident and demo-session FKs, unique graph `thread_id`, state/status, request/actor/session IDs, prompt/model/config version, started/finished timestamps, budget counters and error code; indexes incident/time and status/time. |
| `investigation_findings` | UUID PK; run FK, category/claim, confidence, explanation, ordered supporting evidence refs (typed refs validated against DB), created time. |
| `proposed_actions` | UUID PK; run FK, incident and demo-session FKs, enum `action_type`, validated JSONB parameters, canonical payload SHA-256, status, created/expiry timestamps; immutable after proposal, unique `(id, payload_hash)`. |
| `approvals` | UUID PK; action FK, decision, actor/session, payload hash, decision time, optional reason; unique `(action_id, actor/session)` policy as appropriate; decision must match the action hash. |
| `action_executions` | UUID PK; action FK unique, payload hash, status, result JSONB, started/completed timestamps, idempotency key unique; prevents duplicate execution. |
| `resolution_records` | UUID PK; incident/run/action FKs, summary, outcome, recorded time. |
| `audit_events` | monotonic identity PK plus event UUID unique; actor/session, run/incident/action refs, event type, timestamp, request correlation ID, redacted details JSONB. Append-only at application level. |

Relationships: a demo session owns its cloned incidents, assets, logs, work orders, and runs; all API repository reads/mutations scope through that session. An incident has assets via `incident_assets`; an incident has many runs; a run has findings/actions; each action has at most one effective approval and at most one execution record; resolutions point back to the run and optional action. Apply FK `ON DELETE RESTRICT` for audit/workflow history. Expiry cleanup removes disposable session records while retaining minimal, non-secret audit history for a documented period. Seed records are reset by a controlled routine, not cascading deletion of audit history.

Use SQLAlchemy 2.x typed models and Alembic revisions. Add DB checks for enums/ranges, nonempty keys, valid timestamps, and action status transitions where practical. Use UUIDs, UTC timestamps, JSONB only for bounded flexible attributes, and database-enforced uniqueness for external keys and idempotency. `READ COMMITTED` transactions suffice for ordinary reads; lock the action row (`SELECT … FOR UPDATE`) during approval/execution. The execution transaction rechecks action status/hash, session scope, approval, expiry, allowlisted action type, and target ownership; updates exactly one asset row using an expected-before-state predicate; inserts execution/resolution/audit rows; then commits atomically. LLM/graph calls occur outside DB transactions. Exactly-once behavior applies to this database-only change through atomic commit and uniqueness; retries return the stored execution result.

## 5. ChromaDB design

Collections (same embedding dimensions/provider per collection):

- `opspilot_troubleshooting_v1`: synthetic runbooks and explicitly approved public procedures.
- `opspilot_error_refs_v1`: error-code and symptom references.
- Optional `opspilot_log_patterns_v1`: curated templates/examples only, not raw bulk logs.

Chunk at heading/paragraph boundaries around 400–700 tokens with 60–100 token overlap; preserve title, section path and source. Use stable IDs derived from `source_key + version + chunk_index + content_hash` so re-ingestion is an upsert. Metadata: `source_key`, `doc_version`, `doc_type`, `service_key`, `environment`, `error_codes`, `tags`, `source_url`, `license`, `content_hash`, `active`, and `ingested_at`. Filter by active version and service/error code when available; retrieve a small candidate set (e.g. 8), then rerank or filter by actual evidence, returning chunk IDs/scores and text snippets to the graph. Keep authoritative document text and version in PostgreSQL; vectors are rebuildable derived state.

Start with one configurable embedding provider/model, pinned by identifier and dimensions in configuration. Do not silently mix embedding models in one collection; version the collection and re-embed during a controlled index rebuild. Cache index build results; verify dimension and model metadata at startup. For deployment, rebuild from small synthetic documents or persist Chroma data on a supported durable volume. Chroma is not the checkpoint or audit store.

### LLM provider configuration

Prefer Groq through its OpenAI-compatible API configuration (`base_url`, model ID, timeout and token ceilings from environment/config); never hardcode or log `GROQ_API_KEY`. Current [Groq tool-use docs](https://console.groq.com/docs/tool-use/overview) say tool use is supported on hosted models, while [Structured Outputs docs](https://console.groq.com/docs/structured-outputs) list strict JSON Schema support only on a subset (currently including `openai/gpt-oss-20b` and `openai/gpt-oss-120b`). They state tool use and Structured Outputs are not supported together in the same request. Therefore use separate calls: tool-call mode for each adaptive investigator step, then a non-tool strict-schema call for the final report (where supported). Validate with Pydantic regardless; schema conformance does not establish factual correctness. Pin a selected model ID and run a small compatibility smoke check when configured; do not silently choose a model whose current capabilities differ. Keep an OpenAI-compatible alternative provider adapter configurable for fallback, selected explicitly by configuration, not automatic key switching. Provider limits/pricing and available model IDs must be checked again at implementation/deployment time.

## 6. LangGraph workflow

Use LangGraph `StateGraph` with typed `InvestigationState`: `run_id`, `thread_id`, `demo_session_id`, `incident_id`, `request`, `observations`, `tool_trace`, `evidence_refs`, `retrieved_chunks`, `findings`, `report`, `proposed_action`, `approval_decision`, `execution_result`, `budget`, `errors`, and `status`. Persist necessary state and evidence references; avoid duplicating large logs in checkpoints. Use the PostgreSQL-backed LangGraph checkpointer keyed by stable thread ID in Neon. Store per-run `max_tool_calls`, `max_steps`, `max_tokens`, `deadline`, and optional cost ceiling counters in state/config, with a hard-coded server-side ceiling so callers cannot increase limits.

Adaptive graph loop:

1. `load_incident`: scoped read-only fetch; missing or out-of-session incident ends safely.
2. `choose_next_step`: LLM sees the incident plus accumulated observation summary and chooses one declared read-only tool call with validated arguments, or chooses `analyze/finish` when evidence suffices. It may refine time bounds, narrow a service/error filter, inspect another related entity, or retrieve runbooks in response to prior results. This is a genuine observe → choose → act → observe loop, not a fixed list of tool nodes.
3. `execute_read_tool`: deterministic dispatcher invokes only an allowlisted typed function. Validate scope, argument bounds, result limits and timeout; add provenance-bearing observations/evidence IDs to state. Tool errors become typed observations and may cause a bounded alternative query.
4. Conditional route: return to `choose_next_step` if budget remains; route to analysis/report when the model finishes or any hard budget is exhausted. Never expose a write-capable function to the model.
5. `retrieve_knowledge`: this is one selectable tool in the same loop, using Chroma filters and returning citation IDs/scores. It can be called again with a refined query if budget remains.
6. `analyze_and_report`: correlate timestamped evidence, distinguish fact/inference/unknown, cite only observed evidence IDs, allow abstention, and produce a Pydantic-validated structured report. Invalid schema receives at most one bounded repair attempt, then a safe error/uncertainty report.
7. `propose_action`: deterministically create the one allowlisted action proposal only if evidence and policy justify it; otherwise finalize without action.
8. `approval_gate`: persist proposal and LangGraph `interrupt` with proposal ID and immutable details. Approval API records decision; resume using `Command(resume=...)` only after revalidating saved decision/hash/session. Expiry never auto-approves.
9. `execute_action`: invoke application action service, never an LLM tool. Store result and checkpoint.
10. `finalize`: persist report/findings/status and audit event.

Budget defaults proposed for the MVP: at most 10 model-selected read-tool calls, 8 investigation loop turns, 2 report-generation/repair calls, 20,000 provider-reported input+output tokens per run (or the closest enforceable provider-side limit), and a 90-second active investigation deadline. Enforce counters in graph state and provider request limits; once reached, stop tool selection and report based on available evidence with explicit budget-limited uncertainty. Add configurable lower limits for public traffic and a per-session run quota. If cost accounting is unavailable, token ceilings and request quotas still apply. These are initial engineering defaults, adjustable during implementation but with a non-user-overridable hard cap.

One FastAPI app/container with an in-process task runner is sufficient for portfolio scope. Keep checkpoints durable in PostgreSQL; if a Space restarts mid-investigation, expose the latest checkpoint and allow a safe user-triggered resume/retry, rather than adding a broker/worker fleet. Human approval waits are durable graph interrupts. Never blindly replay an action: the transactional idempotency record controls it. Transient read/LLM failures use bounded retries with jitter; writes are not automatically retried outside their idempotent DB operation.

## 7. Tool interfaces and permissions

Read-only tools exposed to the investigator: `get_incident(incident_id)`, `list_related_assets(incident_id)`, `search_logs(service_key, asset_ids, start, end, levels, event_codes, limit)`, `list_work_orders(incident_id, asset_ids, statuses, limit)`, and `search_runbooks(query, service_key, error_codes, limit)`. Enforce incident-scoped IDs, bounded time windows, maximum result counts, parameter validation, timeouts, and redacted output. Tool functions use repository/service methods; no general SQL tool, shell, filesystem, network fetch, or database credential is available to the LLM.

The proposal step is application logic, not a write tool: `build_action_proposal(typed_parameters)` validates the one MVP operation `set_asset_operational_state(asset_id, expected_state, new_state, reason)`. It is limited to a session-owned seeded asset and one meaningful state transition, such as `degraded → healthy`, with expected-before-state concurrency check. The model can suggest a proposal, but cannot change its parameters after approval or execute it. Do not expose arbitrary SQL, shell, URLs, free-form table/column names, or arbitrary JSON patches. A human approval does not widen this allowlist.

## 8. Approval, authorization, and audit

On first visit, issue a cryptographically random opaque session token in a Secure, HttpOnly, SameSite cookie over HTTPS; store only its hash in `demo_sessions`. Use the token as a lightweight real authorization credential, rotate it on renewal, enforce expiry, and protect cookie-authenticated state-changing requests with CSRF token plus Origin checks. This avoids accounts/OAuth while providing a real bearer-session boundary appropriate to a public demo. Clone one selected seed scenario and related records for that session. Every incident/run/action endpoint derives session scope from the cookie, never from a caller-supplied session ID. Do not expose the session secret to browser JavaScript or logs.

The approval card shows incident, action type, exact target and before/after values, reason, supporting evidence, expiry, and stable payload hash. Persist immutable parameters and hash when proposing. Approval API accepts only action ID, expected hash, decision and optional reason; it does not accept new execution parameters. Enforce same session scope, CSRF/origin protections, rate limits, and explicit approval permission. On submission, compare stored hash, status, expiry and session under row lock; record decision plus audit event; only then resume.

Execution revalidates independently, requires an approved matching hash and session, and inserts the unique execution/idempotency row in the same DB transaction as the single allowlisted asset-state update and audit record. Concurrent approvals/retries return the existing decision/result or conflict; they never execute twice. Rejection records an audit entry and advances to finalization without calling the write service. Session isolation is practical because the corpus is small: cloning a handful of rows is straightforward, but requires session propagation in every query, cleanup/retention rules, and connection-pool-safe tests. If implementation reveals that complexity is disproportionate, fallback is one shared synthetic demo dataset with this same single bounded mutation, public rate limits, visible reset behavior, and serializable/concurrency-safe state transitions; trade-off is visitors can observe one another's demo changes. Do not silently fall back without documenting it in the UI/README.

## 9. FastAPI API plan

Prefix `/api/v1`; Pydantic request/response models; OpenAPI docs disabled or protected on public deployment if desired.

- `POST /demo/session` (or auto-provisioned on first page load): create/refresh opaque session and return only non-secret session metadata; create isolated seed rows.
- `GET /incidents?status=&severity=&limit=&cursor=` and `GET /incidents/{incident_id}`: list/details, related assets, timeline summary.
- `POST /investigations` with incident ID/request and client idempotency key: create persistent run/thread, return `202` and run ID.
- `GET /investigations/{run_id}` and `/report`: status, report, findings, evidence references/citations, pending action.
- `GET /approvals?status=pending`: scoped pending cards.
- `POST /actions/{action_id}/approval`: approve/reject with expected payload hash and reason; returns state/status.
- `GET /investigations/{run_id}/audit`: ordered audit timeline and execution result.
- `GET /health/live`, `/health/ready`: liveness separate from DB/Chroma/model readiness.
- Optional `POST /demo/reset`: disabled publicly by default or protected with operator-only auth and rate limiting.

Return persistent run IDs and use polling or server-sent events for progress. For portfolio scope, use one app process with a lightweight in-process task runner, PostgreSQL checkpoints and interrupt/resume. A restart during active inference can interrupt that attempt; expose a safe resume/retry from the stored checkpoint and audit it. Do not add Redis, a broker, or a separate worker service unless actual failure testing demonstrates the simple runner cannot meet the demo requirement.

## 10. Interface proposal

One responsive interface with incident list/search, incident timeline and details, “Investigate” request form, live step/status panel, evidence drawer showing source/time/snippet and retrieval citation, structured findings/report, and an explicit action approval card with approve/reject controls. Clearly label the two phases “Investigation (read-only)” and “Approval (database change).” Show pending/expired/rejected/executed state and an audit timeline. Provide uncertainty and missing evidence prominently. Use a simple React/TypeScript client or server-rendered HTML/HTMX; recommendation is React/Vite for clear portfolio polish while keeping the API independent. No separate admin product; the same UI can include a gated demo-reset control.

## 11. Evaluation and testing

Keep evaluation labels out of runtime as described above. Add a deterministic 8–12 case evaluation set with expected cause, evidence IDs, acceptable resolution/action, and abstention expectation. Freeze seed version and model/provider/config versions per run.

Test layers: Alembic upgrade from empty and downgrade policy; FK/check/unique constraints; seed/import idempotency; Chroma rebuild and metadata filters; retrieval precision@k/recall@k and citation validity; tool query scoping/limits; report Pydantic schema and evidence-ID validation; workflow transitions/checkpoint restart/resume; no-action/reject paths; unauthorized, expired, mismatched-hash, malformed and out-of-scope approval rejection; concurrent and repeated execution exactly once; transaction rollback; API auth/rate limiting; full scenario runs with deterministic model fakes for control-flow plus separately tagged provider-backed evaluations.

Metrics: root-cause top-1 and acceptable-set accuracy; evidence precision/recall and citation validity; resolution correctness; action appropriateness and unsafe-action rate (target zero); abstention calibration/coverage; structured output validity; retrieval recall@k; approval-to-execution integrity; duplicate-write count (target zero); latency and token/cost per run; recovery rate after injected dependency failures. A successful completion string alone is not correctness evidence. Record evaluation results and prompts/model versions without logging secrets or unredacted sensitive inputs.

## 12. Observability

Use LangGraph node/tool metadata plus structured JSON logs keyed by run, thread, incident, and request IDs. Record model/provider/model-version, token usage/cost if exposed, duration, retries, retrieval IDs/scores, transitions, validation failures, approval and execution IDs. Redact credentials and sensitive strings; avoid copying whole logs or prompts into third-party tracing by default. Start with local structured logs and optional trace export; do not require MLflow, a separate tracing service, or another paid account for the MVP. Keep durable business audit events in PostgreSQL rather than treating telemetry as audit.

## 13. Deployment and costs

### Suggested topology

- Hugging Face Docker Space hosts FastAPI plus built frontend on the configured app port.
- Neon hosts product records, audit, and LangGraph PostgreSQL checkpoints. The MVP does not require a separate queue/outbox service.
- Chroma index is rebuilt at startup from small, versioned synthetic documents in the Space image; no paid persistent vector storage required.
- Groq is preferred for inference, configured by secret and model settings; alternative OpenAI-compatible provider is supported through the same adapter.
- Docker Compose locally includes one app, PostgreSQL and Chroma with named volumes; an external provider can replace local model inference.

Hugging Face [Docker Spaces docs](https://huggingface.co/docs/hub/main/en/spaces-sdks-docker) verify custom-container support and secret settings. Its default disk is ephemeral; current [Spaces overview](https://huggingface.co/docs/hub/spaces-overview) lists CPU Basic at 2 vCPU/16 GB with no hourly hardware charge and says free hardware sleeps when unused. The same documentation also says creating a compute Space requires a paid plan. This account/plan wording is not fully clear from public docs, so verify eligibility and checkout/billing terms before deployment. The design avoids persistent Space storage because synthetic Chroma can be rebuilt. Neon’s official [Free plan limits](https://github.com/neondatabase/website/blob/main/content/faqs/free-plan-limits-and-quotas.md), checked 23 November 2026, list $0/month, 100 projects, 10 branches/project, 100 CU-hours/project/month, 1 GB Postgres storage/project (up to 20 GB total), and 5 GB network transfer/project/month; compute scales to zero after 5 minutes and up to 2 CU. A project suspends compute when its free compute or transfer quota is exhausted. Use Neon’s pooled connection URL and conservative SQLAlchemy pool/overflow settings; use SSL and test LangGraph PostgresSaver compatibility, migrations, pooling mode, and checkpoint schema on a disposable project before deployment. Neon is PostgreSQL-compatible, but integration compatibility still requires an implementation smoke test.

Groq prices and rate limits depend on the chosen model and current account tier; no usage/cost promise is made. Hard token and per-session run ceilings limit exposure. Space compute and external model calls may have charges under account-specific terms. Proposed base demo infrastructure cost is $0 only if free-tier eligibility remains available and usage stays within limits; otherwise compute, model usage or paid DB/storage may incur charges. Never create paid resources without explicit approval. Secrets belong in deployment secrets/settings, not Git or image layers. Pin container/dependency versions; provide migration-on-release, health checks, database backup/export instructions, session expiry cleanup, seed/reset command, and index rebuild instructions. Do not rely on local Docker volumes in deployment.

### Public safety

Use only synthetic records and a narrowly scoped DB role. Allow only the single state transition against a session-owned seeded asset; prohibit writes to unrelated entities. Add rate limits, request-size limits, per-session investigation quotas, session expiry cleanup and a clear reset. Never expose provider keys, SQL diagnostics, stack traces, or internal secrets to client responses.

## 14. Proposed repository structure

```text
.
├── README.md
├── pyproject.toml
├── Dockerfile
├── compose.yaml
├── alembic.ini
├── alembic/versions/
├── src/opspilot/
│   ├── api/            # FastAPI routes, dependencies, schemas
│   ├── config.py
│   ├── db/             # SQLAlchemy models, session, repositories
│   ├── services/       # sessions, approvals, actions, incidents, audit
│   ├── graph/          # StateGraph state, nodes, routing, checkpointer
│   ├── tools/          # scoped read-only tools and retrieval
│   ├── ingestion/      # deterministic seed and Chroma indexing
│   ├── evaluation/     # offline harness; answer key excluded from runtime
│   └── main.py
├── frontend/           # single responsive interface
├── data/seed/public/   # synthetic agent-visible seed corpus
├── data/eval/          # ground truth, excluded from image/runtime
├── tests/              # unit, integration, graph, API, safety
└── docs/               # architecture, data provenance, operations
```

## 15. Implementation plan after final approval

1. Initialize local Git in this intended folder, add `.env` and secret/artifact exclusions before staging, and do not add a remote or push. Configure settings without revealing existing `.env` contents.
2. Create the compact app skeleton, validated settings, redacted logs, Docker Compose and pinned dependencies.
3. Implement SQLAlchemy schema/Alembic and deterministic public seed plus evaluator-only labels. Add session provisioning and clone/scoping; verify isolation with tests.
4. Build Chroma ingestion and startup index rebuild from versioned synthetic docs. Do not download LogHub unless later selected and terms are confirmed.
5. Implement typed read-only tools and the adaptive LangGraph tool loop with strict call/step/token/time/cost ceilings; add evidence provenance, structured report validation, abstention, and provider capability checks.
6. Add PostgreSQL LangGraph checkpoints and interrupt/resume. Keep one application container/process and a lightweight task runner; add restart recovery without broker/microservice architecture.
7. Implement session-authorized approval and the one immutable asset-state operation, including payload hash, transaction, audit, and unique idempotency enforcement.
8. Build the single responsive interface and connect incident selection, progress, evidence, report, approval, execution and audit views.
9. Implement the offline ground-truth evaluator, tests, deterministic provider-independent model fakes, and separate optional live-provider smoke/evaluation. Debug and rerun relevant checks.
10. Add lightweight tracing, rate limits, session cleanup, reset and reproducibility docs; prepare Git commits locally for review, but do not push.
11. Verify current HF/Neon/Groq limits and compatibility against actual accounts. The user supplies/connects credentials through secure provider settings; never ask them to paste keys into chat or commit them.
12. Prepare deployment configuration and a reviewable deployment candidate. Publish only after final deployment approval and any required account/paid-resource approval.

## 16. Risks, assumptions, and remaining decisions

- **Dataset:** synthetic-first is selected. LogHub is optional and can be omitted from the portfolio runtime; no decision is needed unless a real-log benchmark is desired later.
- **LLM:** Groq is preferred, with model capability checked at setup. Strict structured report output and tool calls must be separate requests on Groq according to current docs. If the chosen model cannot pass output/tool smoke checks, configure the compatible alternative provider. Credentials are needed for live LLM runs; deterministic tests can proceed without them.
- **Session isolation:** implement per-visitor cloned records as the default; use the documented shared-demo fallback only if implementation demonstrates material complexity, and document it before use.
- **Providers:** Hugging Face Spaces and Neon are preferred. Free-tier limits and connection behavior are verified from current public documentation, but account-specific eligibility/limits and runtime compatibility need smoke tests. Account connections may be needed for deployment. No paid resource will be provisioned without approval.
- **Frontend/action:** React/Vite and the single allowlisted `degraded → healthy` asset-state transition are architecture defaults; no further design decision is needed before Phase 2.
- **Git:** initialize locally; no remote push without approval.
- **Approval gate:** final Phase 2 implementation approval has not been granted. Public deployment and any paid resources will also require explicit approval when concrete deployment details are ready.

No implementation, dataset download, Git initialization, account connection, paid provisioning, or deployment has begun. This revision is the review artifact for the final Phase 2 approval decision.
