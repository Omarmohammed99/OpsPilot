# OpsPilot Data Layer

## Components

`src/opspilot/db/models.py` defines the application relational models; `ingestion_models.py` records each seed run and failures. SQLAlchemy uses PostgreSQL UUID and JSONB types on Neon/Postgres and portable types for isolated unit tests. Alembic is the production schema authority. Local Compose runs PostgreSQL 16 with named volume `postgres_data` and Chroma 1 with named volume `chroma_data`.

`KnowledgeIndex` creates `opspilot_troubleshooting_v1` and `opspilot_error_refs_v1`. Its IDs are SHA-256 of source key, version, chunk index, and chunk content hash. Chunking cleans HTML/NUL/whitespace, splits on word boundaries (550 words with 80 overlap), and records source, version, content hash, license, document type, title, service, error codes, tags, and chunk index. Retrieval scopes by active flag, service (including wildcard documents), and indexed error-code flags; it returns IDs, text, metadata, and cosine distance for later citations.

The default `local_hash` embedding provider is a deterministic 1,024-dimension word and bigram `HashingVectorizer` (`hashing-tfidf-v1`). It makes offline ingestion reproducible without model downloads. It is lexical and is not a substitute for a neural semantic embedding model. An optional OpenAI-compatible provider is configurable through `EMBEDDING_PROVIDER=openai`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_EMBEDDING_MODEL`. Keep embedding provider/model/dimension stable within a collection; changing them requires a collection rebuild.

For local Docker development, set `CHROMA_MODE=http` and connect to Compose's Chroma server. `embedded` mode uses Chroma's persistent client under `CHROMA_PERSIST_DIRECTORY`; the intended single-container hosted configuration rebuilds this small synthetic index at startup on ephemeral disk. PostgreSQL, including LangGraph checkpoints, remains external/persistent in hosted deployment.

## PostgreSQL tables and relationships

- `demo_sessions` stores only a SHA-256 hash of a random opaque token. The fixed `is_template=true` session contains seed rows and is never returned by session authorization.
- `incidents`, `assets`, `incident_assets`, `application_logs`, and `work_orders` are the operational demo corpus. Every row has a session scope; the runtime data layer must always query by the cookie-derived session ID.
- `knowledge_documents` keeps canonical source text and provenance; Chroma is rebuildable derived data.
- `investigation_runs` is prepared for stable LangGraph `thread_id`, state/status, model/prompt/config versions and budget. `investigation_findings` stores validated claims and evidence references.
- `proposed_actions` stores a typed action, immutable JSON parameters and canonical payload hash. Database constraints allow only `set_asset_operational_state`.
- `approvals`, `action_executions`, `resolution_records`, and append-only-by-policy `audit_events` preserve decisions and results. Unique action and idempotency constraints support exactly-once database changes.
- `ingestion_batches` and `ingestion_failures` preserve seed version, counts, status and diagnostic information for retry.

No evaluation answer-key table or production loader exists. The ground truth is an offline test/evaluation file excluded from the runtime image. LangGraph's checkpointer manages its own PostgreSQL tables through `PostgresSaver.setup()`; it is separate from business records and is created only by the explicit setup command.

## Migrations and setup

The Alembic environment reads `DATABASE_URL` from process environment / `.env` and never prints it.

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://opspilot:opspilot_dev@localhost:5432/opspilot'
uv run opspilot-data migrate
uv run alembic current
uv run alembic history
```

Use a Neon pooled connection URL for application traffic, set SSL as required by the provider, and keep SQLAlchemy's pool small for serverless Postgres. The Neon direct connection may be more suitable for migrations and `PostgresSaver.setup`; test against a disposable branch/project before using production credentials. Never add Neon credentials to source control.

Optional LangGraph checkpoint setup (extra is installed by the documented `uv sync --extra checkpoint`):

```powershell
uv run opspilot-data checkpoint-setup
```

This creates the library-managed checkpoint schema only. It does not create an agent or investigation workflow.

## Ingestion and verification

```powershell
uv run opspilot-data ingest
uv run opspilot-data verify
uv run opspilot-data counts
```

Scenario IDs, incident/asset/log/work-order IDs, document IDs, and chunk IDs are deterministic. The pipeline upserts stable records and Chroma chunks; reruns do not duplicate them. PostgreSQL and Chroma do not share a transaction manager, so the ingestion batch/failure record captures errors and rerunning reconciles Chroma with canonical documents. A failure never gets reported as a completed batch.

Use `create_demo_session(db, scenario_id)` after seed ingestion to clone one scenario's incident, assets, logs and work orders into isolated session rows. It returns the raw opaque token once; only the token hash is stored. `resolve_demo_session` validates active status and expiry. Session expiry marks a session inaccessible without deleting audit history. Do not take a session ID from a request body as authorization.

Data inspection without printing secrets:

```powershell
uv run opspilot-data counts
uv run python -c "from opspilot.datasets.loaders import load_scenario_dataset; print([(x.scenario_id, x.incident.external_key, len(x.logs)) for x in load_scenario_dataset().scenarios])"
```

## Hosted persistence

Hugging Face Spaces container disk is ephemeral. The future single-container Space must rebuild Chroma from `data/seed/public/runbooks.json` at startup, and connect to Neon for all relational state and LangGraph checkpoints. Do not put SQLite or a local Postgres/Chroma volume in the hosted runtime and assume it persists. Configure provider keys as runtime Space secrets, never Docker build arguments or source files. Check current account free-tier eligibility and quotas immediately before provisioning; this phase does not provision or deploy anything.

