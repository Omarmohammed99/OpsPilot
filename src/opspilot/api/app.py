"""FastAPI application with opaque session authorization and durable investigations."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opspilot.agent.graph import (
    build_investigation_graph,
    initial_investigation_state,
    resume_approval,
    thread_config,
)
from opspilot.agent.providers import InvestigatorProvider, OpenAICompatibleProvider
from opspilot.agent.schemas import ApprovalInput
from opspilot.config import Settings, get_settings
from opspilot.datasets.loaders import load_scenario_dataset
from opspilot.db.models import (
    ActionExecution,
    Approval,
    Asset,
    AuditEvent,
    DemoSession,
    Incident,
    IncidentAsset,
    InvestigationFinding,
    InvestigationRun,
    ProposedAction,
)
from opspilot.db.session import SessionLocal, engine
from opspilot.ingestion.pipeline import run_ingestion
from opspilot.retrieval.chroma_store import KnowledgeIndex
from opspilot.services.actions import ActionConflict, decide_action
from opspilot.services.demo_sessions import (
    InvalidDemoSession,
    create_demo_session,
    resolve_demo_session,
)

logger = logging.getLogger("opspilot.api")
MAX_BODY_BYTES = 16 * 1024
RATE_LIMIT_REQUESTS = 90
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_CONCURRENT_RUNS = 4


class DemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=2, max_length=80)


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: uuid.UUID
    request_text: str = Field(
        default="Investigate this incident and explain the evidence.", min_length=8, max_length=2000
    )


def _safe_provider_error(exc: Exception) -> str:
    return (
        "provider_not_configured"
        if "not configured" in str(exc).lower()
        else "provider_unavailable"
    )


@contextmanager
def postgres_checkpointer_context():
    from sqlalchemy.engine import make_url

    settings = get_settings()
    connection_string = (
        make_url(settings.safe_database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    with psycopg.connect(connection_string, autocommit=True) as connection:
        yield PostgresSaver(connection)


class UnavailableProvider:
    provider_name = "unconfigured"
    model_name = "unconfigured"

    def choose_tool(self, context: dict[str, Any]):
        raise RuntimeError("No configured model provider")

    def create_report(self, context: dict[str, Any], *, repair_note: str = ""):
        raise RuntimeError("No configured model provider")


def build_app(
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    provider_factory: Callable[[], InvestigatorProvider] | None = None,
    checkpointer_context_factory: Callable[[], Any] = postgres_checkpointer_context,
    settings: Settings | None = None,
    initialize_on_startup: bool = True,
) -> FastAPI:
    app_settings = settings or get_settings()
    provider_factory = provider_factory or OpenAICompatibleProvider.from_settings
    active_runs: set[uuid.UUID] = set()
    active_lock = threading.Lock()
    request_windows: dict[str, deque[float]] = defaultdict(deque)
    request_lock = threading.Lock()

    @contextmanager
    def checkpointer_context():
        if app.state.checkpointer_override is not None:
            yield app.state.checkpointer_override
        else:
            with checkpointer_context_factory() as checkpointer:
                yield checkpointer

    @contextmanager
    def graph_for(provider: InvestigatorProvider):
        with checkpointer_context() as checkpointer:
            yield build_investigation_graph(
                session_factory=session_factory,
                knowledge_factory=app.state.knowledge_factory,
                provider=provider,
                checkpointer=checkpointer,
            )

    def get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    def require_session(
        opspilot_session: str | None = Cookie(default=None), db: Session = Depends(get_db)
    ) -> DemoSession:
        if not opspilot_session:
            raise HTTPException(status_code=401, detail="Start a demo session first")
        try:
            return resolve_demo_session(db, opspilot_session)
        except InvalidDemoSession as exc:
            raise HTTPException(
                status_code=401, detail="Demo session is invalid or expired"
            ) from exc

    def scoped_run(db: Session, run_id: uuid.UUID, session_id: uuid.UUID) -> InvestigationRun:
        run = db.scalar(
            select(InvestigationRun).where(
                InvestigationRun.id == run_id,
                InvestigationRun.demo_session_id == session_id,
            )
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Investigation not found")
        return run

    def read_checkpoint(run: InvestigationRun) -> dict[str, Any]:
        try:
            provider = provider_factory()
        except Exception:
            provider = UnavailableProvider()
        with graph_for(provider) as graph:
            snapshot = graph.get_state(thread_config(run.thread_id))
            return dict(snapshot.values or {})

    def safe_run_view(db: Session, run: InvestigationRun, state: dict[str, Any] | None = None):
        action = db.scalar(
            select(ProposedAction)
            .where(ProposedAction.run_id == run.id)
            .order_by(ProposedAction.created_at.desc())
        )
        execution = (
            db.scalar(select(ActionExecution).where(ActionExecution.action_id == action.id))
            if action
            else None
        )
        findings = list(
            db.scalars(
                select(InvestigationFinding)
                .where(InvestigationFinding.run_id == run.id)
                .order_by(InvestigationFinding.created_at)
            )
        )
        report = state.get("report") if state else None
        if report is None and findings:
            report = {
                "incident_summary": next(
                    (item.claim for item in findings if item.category == "summary"), ""
                ),
                "probable_root_cause": next(
                    (item.claim for item in findings if item.category == "probable_root_cause"), ""
                ),
                "resolution_recommendation": next(
                    (item.claim for item in findings if item.category == "resolution"), ""
                ),
                "supporting_reasoning": next(
                    (
                        item.explanation
                        for item in findings
                        if item.category == "probable_root_cause"
                    ),
                    "",
                ),
                "uncertainty": next(
                    (item.explanation for item in findings if item.category == "resolution"), ""
                ),
                "evidence_refs": sorted(
                    {
                        ref["ref"]
                        for finding in findings
                        for ref in finding.evidence_refs
                        if "ref" in ref
                    }
                ),
                "investigation_steps": [],
                "should_propose_action": bool(action),
                "action_reason": action.parameters.get("reason", "") if action else "",
            }
        return {
            "id": str(run.id),
            "incident_id": str(run.incident_id),
            "status": run.status,
            "request_text": run.request_text,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error_code": run.error_code,
            "budget": (
                state.get("budget")
                or {
                    "loop_turns": state.get("loop_turns", 0),
                    "tool_calls": state.get("tool_calls", 0),
                    "total_tokens": state.get("total_tokens", 0),
                    "estimated_cost_usd": state.get("estimated_cost_usd", 0.0),
                }
                if state
                else run.budget
            ),
            "tool_trace": state.get("tool_trace", []) if state else [],
            "observations": state.get("observations", []) if state else [],
            "errors": state.get("errors", []) if state else [],
            "report": report,
            "proposed_action": (
                {
                    "id": str(action.id),
                    "run_id": str(action.run_id),
                    "action_type": action.action_type,
                    "parameters": action.parameters,
                    "payload_hash": action.payload_hash,
                    "status": action.status,
                    "expires_at": action.expires_at.isoformat(),
                }
                if action
                else None
            ),
            "approval": (
                {"decision": approval.decision, "decided_at": approval.decided_at.isoformat()}
                if action
                and (approval := db.scalar(select(Approval).where(Approval.action_id == action.id)))
                else None
            ),
            "execution": (
                {"id": str(execution.id), "status": execution.status, "result": execution.result}
                if execution
                else None
            ),
        }

    def run_task(run_id: uuid.UUID, session_id: uuid.UUID, request_text: str) -> None:
        overloaded = False
        with active_lock:
            if run_id in active_runs:
                return
            if len(active_runs) >= MAX_CONCURRENT_RUNS:
                overloaded = True
            else:
                active_runs.add(run_id)
        if overloaded:
            with session_factory() as db, db.begin():
                run = db.scalar(select(InvestigationRun).where(InvestigationRun.id == run_id))
                if run:
                    run.status = "interrupted"
                    run.error_code = "server_concurrency_limit"
                    db.add(
                        AuditEvent(
                            demo_session_id=session_id,
                            incident_id=run.incident_id,
                            run_id=run.id,
                            actor_id="system",
                            event_type="investigation_deferred",
                            details={"reason": "In-process investigation limit reached."},
                        )
                    )
            return
        try:
            with session_factory() as db, db.begin():
                run = db.scalar(
                    select(InvestigationRun).where(
                        InvestigationRun.id == run_id,
                        InvestigationRun.demo_session_id == session_id,
                    )
                )
                if run is None:
                    return
                run.status = "running"
                incident_id, thread_id = run.incident_id, run.thread_id
            provider = provider_factory()
            initial = initial_investigation_state(
                run_id=run_id,
                thread_id=thread_id,
                demo_session_id=session_id,
                incident_id=incident_id,
                request=request_text,
            )
            with graph_for(provider) as graph:
                final_state = graph.invoke(initial, thread_config(thread_id))
            logger.info(
                "investigation_finished run_id=%s status=%s tool_calls=%s total_tokens=%s",
                run_id,
                final_state.get("status"),
                final_state.get("tool_calls", 0),
                final_state.get("total_tokens", 0),
            )
        except Exception as exc:
            logger.warning(
                "investigation_task_failed run_id=%s error_type=%s", run_id, type(exc).__name__
            )
            with session_factory() as db, db.begin():
                run = db.scalar(select(InvestigationRun).where(InvestigationRun.id == run_id))
                if run and run.status not in {"waiting_approval", "completed"}:
                    run.status = "failed"
                    run.error_code = _safe_provider_error(exc)
                    run.finished_at = datetime.now(UTC)
        finally:
            with active_lock:
                active_runs.discard(run_id)

    def resume_task(run_id: uuid.UUID, session_id: uuid.UUID) -> None:
        with active_lock:
            if run_id in active_runs:
                return
            active_runs.add(run_id)
        try:
            with session_factory() as db:
                run = scoped_run(db, run_id, session_id)
                thread_id, incident_id, request_text = (
                    run.thread_id,
                    run.incident_id,
                    run.request_text,
                )
            try:
                provider = provider_factory()
            except Exception:
                provider = UnavailableProvider()
            with graph_for(provider) as graph:
                snapshot = graph.get_state(thread_config(thread_id))
                if snapshot.values and snapshot.next:
                    graph.invoke(None, thread_config(thread_id))
                else:
                    graph.invoke(
                        initial_investigation_state(
                            run_id=run_id,
                            thread_id=thread_id,
                            demo_session_id=session_id,
                            incident_id=incident_id,
                            request=request_text,
                        ),
                        thread_config(thread_id),
                    )
        except Exception as exc:
            logger.warning(
                "investigation_resume_failed run_id=%s error_type=%s", run_id, type(exc).__name__
            )
            with session_factory() as db, db.begin():
                run = db.scalar(select(InvestigationRun).where(InvestigationRun.id == run_id))
                if run and run.status not in {"waiting_approval", "completed"}:
                    run.status = "interrupted"
                    run.error_code = "resume_unavailable"
        finally:
            with active_lock:
                active_runs.discard(run_id)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app.state.ready = False
        if initialize_on_startup:
            from alembic import command

            from opspilot.cli import _alembic_config
            from opspilot.db.checkpoint import setup_checkpoint_tables

            command.upgrade(_alembic_config(), "head")
            setup_checkpoint_tables()
            deadline = time.monotonic() + 60
            while True:
                try:
                    with session_factory() as db:
                        run_ingestion(db, app.state.knowledge_factory())
                    break
                except Exception as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Database or Chroma startup initialization failed"
                        ) from exc
                    time.sleep(2)
            with session_factory() as db, db.begin():
                interrupted = list(
                    db.scalars(
                        select(InvestigationRun).where(
                            InvestigationRun.status.in_(["queued", "running"])
                        )
                    )
                )
                for run in interrupted:
                    run.status = "interrupted"
                    db.add(
                        AuditEvent(
                            demo_session_id=run.demo_session_id,
                            incident_id=run.incident_id,
                            run_id=run.id,
                            actor_id="system",
                            event_type="application_restart_recovery",
                            details={"message": "Resume is available from the latest checkpoint."},
                        )
                    )
            app.state.ready = True
        yield

    app = FastAPI(
        title="OpsPilot Incident Investigator",
        version="0.3.0",
        description="Bounded, evidence-grounded synthetic operations investigation demo.",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.provider_factory = provider_factory
    app.state.checkpointer_override = None
    app.state.knowledge_factory = KnowledgeIndex
    app.state.ready = not initialize_on_startup
    app.state.set_checkpointer = lambda value: setattr(app.state, "checkpointer_override", value)

    @app.middleware("http")
    async def request_guards(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
            return Response(status_code=413, content="Request body too large")
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with request_lock:
            window = request_windows[address]
            while window and now - window[0] > RATE_LIMIT_WINDOW_SECONDS:
                window.popleft()
            if len(window) >= RATE_LIMIT_REQUESTS:
                return Response(status_code=429, content="Request rate limit exceeded")
            window.append(now)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    def index_page():
        return FileResponse(Path(__file__).resolve().parents[1] / "web" / "index.html")

    @app.get("/api/health/live")
    def health_live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    def health_ready():
        try:
            with engine.connect() as connection:
                connection.execute(select(1))
            chroma = app.state.knowledge_factory().health()
        except Exception:
            return Response(
                status_code=503,
                content='{"status":"not_ready"}',
                media_type="application/json",
            )
        try:
            provider_factory()
            model_configured = True
        except Exception:
            model_configured = False
        return {
            "status": "ready" if app.state.ready else "starting",
            "postgres": "ok",
            "chroma": "ok",
            "llm_configured": model_configured,
            "collections": chroma.get("collections", {}),
        }

    @app.get("/api/scenarios")
    def scenarios():
        dataset = load_scenario_dataset()
        return {
            "scenarios": [
                {
                    "scenario_id": item.scenario_id,
                    "title": item.incident.title,
                    "severity": item.incident.severity,
                    "service_key": item.incident.service_key,
                }
                for item in dataset.scenarios
            ]
        }

    @app.post("/api/demo-sessions", status_code=201)
    def start_demo(body: DemoSessionRequest, response: Response, db: Session = Depends(get_db)):
        try:
            with db.begin():
                demo_session, token = create_demo_session(
                    db, body.scenario_id, ttl_hours=app_settings.session_ttl_hours
                )
                session_id = demo_session.id
                expires_at = demo_session.expires_at
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown synthetic scenario") from exc
        response.set_cookie(
            "opspilot_session",
            token,
            max_age=app_settings.session_ttl_hours * 3600,
            httponly=True,
            secure=app_settings.opspilot_env == "production",
            samesite="lax",
            path="/",
        )
        return {"session_id": str(session_id), "expires_at": expires_at.isoformat()}

    @app.delete("/api/demo-sessions/current", status_code=204)
    def end_demo(response: Response, demo: DemoSession = Depends(require_session)):
        with session_factory() as db, db.begin():
            current = db.get(DemoSession, demo.id)
            if current:
                current.status = "revoked"
        result = Response(status_code=204)
        result.delete_cookie("opspilot_session", path="/")
        return result

    @app.get("/api/incidents")
    def list_incidents(demo: DemoSession = Depends(require_session), db: Session = Depends(get_db)):
        incidents = list(
            db.scalars(
                select(Incident)
                .where(Incident.demo_session_id == demo.id)
                .order_by(Incident.opened_at.desc())
            )
        )
        return {
            "incidents": [
                {
                    "id": str(item.id),
                    "external_key": item.external_key,
                    "title": item.title,
                    "description": item.description,
                    "severity": item.severity,
                    "status": item.status,
                    "service_key": item.service_key,
                    "opened_at": item.opened_at.isoformat(),
                }
                for item in incidents
            ]
        }

    @app.get("/api/incidents/{incident_id}")
    def incident_details(
        incident_id: uuid.UUID,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        incident = db.scalar(
            select(Incident).where(Incident.id == incident_id, Incident.demo_session_id == demo.id)
        )
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        assets = list(
            db.scalars(
                select(Asset)
                .join(IncidentAsset)
                .where(
                    IncidentAsset.incident_id == incident.id,
                    Asset.demo_session_id == demo.id,
                )
            )
        )
        return {
            "id": str(incident.id),
            "external_key": incident.external_key,
            "title": incident.title,
            "description": incident.description,
            "severity": incident.severity,
            "status": incident.status,
            "service_key": incident.service_key,
            "opened_at": incident.opened_at.isoformat(),
            "assets": [
                {
                    "id": str(asset.id),
                    "asset_key": asset.asset_key,
                    "operational_state": asset.operational_state,
                    "asset_type": asset.asset_type,
                }
                for asset in assets
            ],
        }

    @app.post("/api/investigations", status_code=202)
    def start_investigation(
        body: InvestigationRequest,
        background_tasks: BackgroundTasks,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        try:
            provider_factory()
        except Exception as exc:
            code = _safe_provider_error(exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Configure GROQ_API_KEY or the OpenAI-compatible provider in the local "
                    "environment before starting an investigation."
                    if code == "provider_not_configured"
                    else "Configured language model is unavailable"
                ),
            ) from exc
        incident = db.scalar(
            select(Incident).where(
                Incident.id == body.incident_id,
                Incident.demo_session_id == demo.id,
            )
        )
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found in this session")
        db.scalar(select(DemoSession).where(DemoSession.id == demo.id).with_for_update())
        count = (
            db.scalar(
                select(func.count())
                .select_from(InvestigationRun)
                .where(InvestigationRun.demo_session_id == demo.id)
            )
            or 0
        )
        if count >= app_settings.max_investigations_per_session:
            raise HTTPException(status_code=429, detail="Session investigation quota reached")
        run = InvestigationRun(
            demo_session_id=demo.id,
            incident_id=incident.id,
            thread_id=str(uuid.uuid4()),
            status="queued",
            request_text=body.request_text.strip(),
            actor_id=f"visitor:{demo.id}",
            prompt_version="opspilot-investigator-v1",
            model_provider=app_settings.llm_provider,
            model_id=(
                app_settings.groq_model
                if app_settings.llm_provider == "groq"
                else app_settings.openai_model
            ),
            config_version="phase3-v1",
            budget={
                "max_tool_calls": app_settings.max_tool_calls_per_investigation,
                "max_turns": app_settings.max_investigation_turns,
                "max_tokens": app_settings.max_tokens_per_investigation,
                "max_active_seconds": app_settings.max_active_investigation_seconds,
                "estimated_cost_ceiling_usd": app_settings.max_estimated_cost_usd,
            },
        )
        db.add(run)
        db.flush()
        db.add(
            AuditEvent(
                demo_session_id=demo.id,
                incident_id=incident.id,
                run_id=run.id,
                actor_id=f"visitor:{demo.id}",
                event_type="investigation_started",
                details={"request_length": len(body.request_text)},
            )
        )
        db.commit()
        background_tasks.add_task(run_task, run.id, demo.id, body.request_text.strip())
        return {"run_id": str(run.id), "status": "queued"}

    @app.get("/api/investigations")
    def list_investigations(
        demo: DemoSession = Depends(require_session), db: Session = Depends(get_db)
    ):
        runs = list(
            db.scalars(
                select(InvestigationRun)
                .where(InvestigationRun.demo_session_id == demo.id)
                .order_by(InvestigationRun.started_at.desc())
                .limit(50)
            )
        )
        return {
            "investigations": [
                {
                    "id": str(run.id),
                    "incident_id": str(run.incident_id),
                    "status": run.status,
                    "request_text": run.request_text,
                    "started_at": run.started_at.isoformat(),
                    "error_code": run.error_code,
                }
                for run in runs
            ]
        }

    @app.get("/api/investigations/{run_id}")
    def get_investigation(
        run_id: uuid.UUID,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        run = scoped_run(db, run_id, demo.id)
        try:
            state = read_checkpoint(run)
        except Exception:
            state = {}
        return safe_run_view(db, run, state)

    @app.get("/api/investigations/{run_id}/report")
    def get_report(
        run_id: uuid.UUID,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        run = scoped_run(db, run_id, demo.id)
        try:
            state = read_checkpoint(run)
        except Exception:
            state = {}
        view = safe_run_view(db, run, state)
        if not view["report"]:
            raise HTTPException(status_code=404, detail="Report is not available yet")
        return {"run_id": str(run.id), "report": view["report"]}

    @app.get("/api/approvals")
    def pending_approvals(
        demo: DemoSession = Depends(require_session), db: Session = Depends(get_db)
    ):
        actions = list(
            db.scalars(
                select(ProposedAction)
                .where(
                    ProposedAction.demo_session_id == demo.id,
                    ProposedAction.status == "pending",
                )
                .order_by(ProposedAction.created_at.desc())
            )
        )
        return {
            "approvals": [
                {
                    "action_id": str(action.id),
                    "run_id": str(action.run_id),
                    "incident_id": str(action.incident_id),
                    "action_type": action.action_type,
                    "parameters": action.parameters,
                    "payload_hash": action.payload_hash,
                    "expires_at": action.expires_at.isoformat(),
                }
                for action in actions
            ]
        }

    @app.post("/api/actions/{action_id}/approval")
    def approve_action(
        action_id: uuid.UUID,
        body: ApprovalInput,
        background_tasks: BackgroundTasks,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        action = db.scalar(
            select(ProposedAction).where(
                ProposedAction.id == action_id,
                ProposedAction.demo_session_id == demo.id,
            )
        )
        if action is None:
            raise HTTPException(status_code=404, detail="Action not found")
        if body.expected_payload_hash != action.payload_hash:
            raise HTTPException(
                status_code=409, detail="Action payload changed; refresh before deciding"
            )
        try:
            result = decide_action(
                session_factory,
                action_id=action_id,
                demo_session_id=demo.id,
                expected_payload_hash=body.expected_payload_hash,
                decision=body.decision,
                reason=body.reason,
            )
        except ActionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result.get("expired"):
            raise HTTPException(status_code=409, detail="Approval window expired")
        run = db.get(InvestigationRun, action.run_id)
        if run and run.status == "waiting_approval":
            try:
                provider = provider_factory()
            except Exception:
                provider = UnavailableProvider()
            with graph_for(provider) as graph:
                resumed_state = resume_approval(
                    graph,
                    thread_id=run.thread_id,
                    action_id=action.id,
                    decision=body.decision,
                    payload_hash=body.expected_payload_hash,
                )
                result["investigation_status"] = resumed_state.get("status", "completed")
                db.refresh(action)
                result["action_status"] = action.status
                if resumed_state.get("execution_result"):
                    result["execution"] = resumed_state["execution_result"]
        return result

    @app.post("/api/investigations/{run_id}/resume", status_code=202)
    def resume_investigation(
        run_id: uuid.UUID,
        background_tasks: BackgroundTasks,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        run = scoped_run(db, run_id, demo.id)
        if run.status == "waiting_approval":
            raise HTTPException(
                status_code=409, detail="Use the action approval endpoint to resume"
            )
        if run.status not in {"interrupted", "failed", "queued", "running"}:
            raise HTTPException(
                status_code=409, detail=f"Investigation cannot resume from {run.status}"
            )
        run.status = "queued"
        run.error_code = None
        db.commit()
        background_tasks.add_task(resume_task, run.id, demo.id)
        return {"run_id": str(run.id), "status": "queued"}

    @app.get("/api/investigations/{run_id}/audit")
    def audit_history(
        run_id: uuid.UUID,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        scoped_run(db, run_id, demo.id)
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.demo_session_id == demo.id,
                    AuditEvent.run_id == run_id,
                )
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        )
        return {
            "events": [
                {
                    "id": item.id,
                    "event_type": item.event_type,
                    "actor_id": item.actor_id,
                    "details": item.details,
                    "occurred_at": item.occurred_at.isoformat(),
                }
                for item in events
            ]
        }

    @app.get("/api/actions/{action_id}/execution")
    def action_execution(
        action_id: uuid.UUID,
        demo: DemoSession = Depends(require_session),
        db: Session = Depends(get_db),
    ):
        action = db.scalar(
            select(ProposedAction).where(
                ProposedAction.id == action_id, ProposedAction.demo_session_id == demo.id
            )
        )
        if action is None:
            raise HTTPException(status_code=404, detail="Action not found")
        result = db.scalar(select(ActionExecution).where(ActionExecution.action_id == action.id))
        if result is None:
            raise HTTPException(status_code=404, detail="Action has not executed")
        return {
            "execution_id": str(result.id),
            "status": result.status,
            "result": result.result,
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        }

    return app
