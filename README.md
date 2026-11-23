# OpsPilot

OpsPilot is a technically serious AI engineering portfolio demo for investigating synthetic IT incidents. It demonstrates a bounded adaptive LangGraph agent, read-only evidence tools, retrieval augmented generation, durable PostgreSQL checkpoints, human approval, and an auditable transactional action. It is a single FastAPI application backed by PostgreSQL and ChromaDB, not a production enterprise SaaS.

## Capabilities

- Eight deterministic synthetic incident scenarios. Each browser session receives isolated incident, asset, log, work order, and resolution records. Evaluation ground truth is kept separately under `data/eval/ground_truth` and excluded from the runtime image.
- Adaptive observe, choose, and read cycles. The model can select and refine allowlisted read-only tools based on earlier observations. Limits cover iterations, tool calls, tokens, wall time, estimated provider cost, and per-session investigation count.
- Chroma retrieval over versioned synthetic runbooks and incident notes. Local Docker Chroma is persistent; deployment can rebuild the small index from the versioned corpus.
- One action type: set an in-scope synthetic asset's operational state from degraded to healthy. Parameters are immutable and hashed. Approval is session-bound, expires, and is recorded; the update, execution record, and audit event commit in one transaction with idempotent execution.
- Minimal browser UI for scenario selection, investigation traces, report, approval/rejection, action result, audit history, and checkpoint resume.
- Offline evaluation CLI scores report validity, root-cause/evidence coverage, citations, tool coverage, action selection, approval compliance, and execution correctness.

## Local setup (PowerShell)

Requirements: Docker Desktop with the Linux engine, Git, and `uv` 0.10.9 or later.

```powershell
uv python install 3.12
uv sync --python 3.12 --extra test --extra checkpoint --extra dev
docker compose --env-file .env.example up -d --build
```

Open `http://localhost:7860`. The first startup applies migrations and ingests the public synthetic records idempotently. The LLM key is optional for startup and health checks; configure `GROQ_API_KEY` in the ignored local `.env` to run live investigations. The default is Groq `openai/gpt-oss-20b`; an OpenAI-compatible provider can be selected with `LLM_PROVIDER=openai_compatible`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL`. Never commit credentials. The app makes separate tool-selection and strict structured-report requests because Groq does not support tool use and structured outputs in the same request.

The compose app uses Docker-internal PostgreSQL/Chroma addresses. For host CLI commands, set local endpoints explicitly:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://opspilot:opspilot_dev@localhost:5432/opspilot'
$env:CHROMA_MODE = 'http'
$env:CHROMA_HOST = 'localhost'
$env:CHROMA_PORT = '8001'
uv run --python 3.12 opspilot-data db-check
uv run --python 3.12 opspilot-data counts
uv run --python 3.12 opspilot-eval score
```

Limits are configurable via environment settings (`MAX_ACTIVE_INVESTIGATION_SECONDS`, `MAX_TOOL_CALLS_PER_INVESTIGATION`, `MAX_INVESTIGATION_TURNS`, `MAX_TOKENS_PER_INVESTIGATION`, and `MAX_ESTIMATED_COST_USD`). Cost is a conservative estimate based on configured model rates and token limits; provider usage remains authoritative. API docs are available at `http://localhost:7860/docs` in development.

## Tests and development

```powershell
uv run --python 3.12 ruff check src tests
uv run --python 3.12 pytest -q
```

The tests include graph decision/approval/action behavior, API flows, migration/schema constraints, deterministic ingestion, retrieval, and checkpoint interrupt/resume. PostgreSQL integration tests use `OPSPILOT_TEST_DATABASE_URL`; point it only at a disposable test database. See [docs/data-layer.md](docs/data-layer.md) and [docs/dataset-provenance.md](docs/dataset-provenance.md).

## Deployment shape

The application is packaged as one Docker image for a Hugging Face Docker Space. Neon PostgreSQL is the preferred managed database, pending account-level limits and connection compatibility checks. LangGraph checkpoints and application data share PostgreSQL. The small Chroma index is rebuilt from versioned synthetic documents at startup rather than requiring paid persistent storage. The local Compose profile keeps PostgreSQL and Chroma data in named volumes. Deployment and remote Git publishing are intentionally separate, approval-gated steps.

## Local data cleanup

Stop containers while preserving the local database and Chroma volumes:

```powershell
docker compose stop
```

Remove local volumes and their demo data only when you intend to reset them:

```powershell
docker compose down -v
```
