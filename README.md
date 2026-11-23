# OpsPilot

OpsPilot is a portfolio demonstration of an AI operations investigation workflow. This phase implements only the data layer and ingestion pipeline; the LangGraph investigation agent, FastAPI investigation routes, and interface are intentionally deferred.

## Phase 2: data layer

- PostgreSQL via SQLAlchemy and Alembic stores incidents, assets, logs, work orders, knowledge sources, sessions, runs, findings, proposed actions, approvals, executions, resolutions, audits, and ingestion provenance.
- ChromaDB 1.5.9 stores rebuildable document chunks with stable IDs, source metadata, attribution, and retrieval filters.
- Eight deterministic synthetic scenarios are ingested into an inaccessible seed template. Visitors get independent cloned scenario rows and opaque, hashed session credentials.
- Evaluation labels live under `data/eval/ground_truth/`; they are not imported by the runtime, and the Docker image copies only public seed data.

## Local setup (PowerShell)

Requirements: Docker Desktop with its Linux engine running, Git, and `uv` 0.10.9 or later.

```powershell
uv python install 3.12
uv sync --python 3.12 --extra test --extra checkpoint --extra dev
docker compose --env-file .env.example up -d postgres chroma
```

The checked-in `.env.example` has local-only development values. Do not copy over an existing `.env`. For the commands below, set the local development connection explicitly in the current PowerShell process (the value shown is a local sample, not a production credential):

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://opspilot:opspilot_dev@localhost:5432/opspilot'
$env:CHROMA_MODE = 'http'
$env:CHROMA_HOST = 'localhost'
$env:CHROMA_PORT = '8001'
uv run --python 3.12 opspilot-data db-check
uv run --python 3.12 opspilot-data migrate
uv run --python 3.12 opspilot-data ingest
uv run --python 3.12 opspilot-data verify
uv run --python 3.12 opspilot-data counts
```

Stop local databases without deleting their persistent data:

```powershell
docker compose stop postgres chroma
```

To discard local state (destructive to local demo data):

```powershell
docker compose down -v
```

Run the test suite:

```powershell
uv run --python 3.12 pytest -q
```

## Database and Chroma configuration

See [docs/data-layer.md](docs/data-layer.md) for schema relationships, migrations, checkpoint setup, ingestion idempotency, Chroma modes, isolation, inspection commands, and Neon/Hugging Face persistence notes. See [docs/dataset-provenance.md](docs/dataset-provenance.md) for synthetic data mapping and ground-truth separation.

Key settings: `DATABASE_URL`, `OPSPILOT_DATA_DIR`, `CHROMA_MODE` (`http` for local Compose or `embedded` for a rebuildable single-container deployment), `CHROMA_HOST`, `CHROMA_PORT`, `CHROMA_PERSIST_DIRECTORY`, `CHROMA_COLLECTION_PREFIX`, `EMBEDDING_PROVIDER`, and `EMBEDDING_DIMENSIONS`. The default local embedding model is deterministic `hashing-tfidf-v1` and needs no external API key. An optional OpenAI-compatible embedding provider reads its credentials only from environment settings. `GROQ_API_KEY` and `OPENAI_API_KEY` are not needed by Phase 2 data commands.

The CLI is data tooling, not an investigation service. The Phase 2 Dockerfile is a reusable Python CLI image and currently shows CLI help by default; it is not yet a runnable Hugging Face web Space. Phase 3 will add the web process and Space launch command. No application deployment is configured here.

