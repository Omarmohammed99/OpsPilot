from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import event, select

from opspilot.agent.graph import (
    build_investigation_graph,
    initial_investigation_state,
    resume_approval,
    thread_config,
)
from opspilot.agent.providers import DeterministicTestProvider
from opspilot.db.models import (
    ActionExecution,
    Asset,
    DemoSession,
    Incident,
    InvestigationRun,
    ProposedAction,
)
from opspilot.ingestion.pipeline import run_ingestion
from opspilot.retrieval.chroma_store import KnowledgeIndex
from opspilot.retrieval.embeddings import LocalHashEmbeddingFunction
from opspilot.services.actions import ActionConflict, decide_action, execute_approved_action
from opspilot.services.demo_sessions import create_demo_session


def make_graph_session(db_factory, chroma_path, scenario_id="db-pool-exhaustion"):
    index = KnowledgeIndex(
        path=chroma_path,
        mode="embedded",
        prefix="agent-test",
        embedding_function=LocalHashEmbeddingFunction(),
    )
    with db_factory() as db:
        run_ingestion(db, index)
        demo, _token = create_demo_session(db, scenario_id)
        db.flush()
        incident = db.scalar(select(Incident).where(Incident.demo_session_id == demo.id))
        run = InvestigationRun(
            demo_session_id=demo.id,
            incident_id=incident.id,
            thread_id=str(uuid4()),
            status="running",
            request_text="Find the probable cause and report evidence.",
        )
        db.add(run)
        db.commit()
        run_id, session_id, incident_id = run.id, demo.id, incident.id
        thread_id = run.thread_id

    saver = MemorySaver()

    def knowledge_factory():
        return KnowledgeIndex(
            path=chroma_path,
            mode="embedded",
            prefix="agent-test",
            embedding_function=LocalHashEmbeddingFunction(),
        )

    graph = build_investigation_graph(
        session_factory=db_factory,
        knowledge_factory=knowledge_factory,
        provider=DeterministicTestProvider(),
        checkpointer=saver,
    )
    state = initial_investigation_state(
        run_id=run_id,
        thread_id=thread_id,
        demo_session_id=session_id,
        incident_id=incident_id,
        request="Find the probable cause and report evidence.",
    )
    result = graph.invoke(state, thread_config(thread_id))
    return graph, run_id, session_id, incident_id, thread_id, result


def test_adaptive_graph_proposes_then_executes_approved_action(db_factory, chroma_path):
    graph, run_id, session_id, _incident_id, thread_id, result = make_graph_session(
        db_factory, chroma_path
    )
    assert result["status"] == "waiting_approval"
    tool_names = [item["tool_name"] for item in result["observations"]]
    assert tool_names == [
        "get_incident",
        "search_logs",
        "list_related_assets",
        "list_work_orders",
        "search_runbooks",
        "search_incident_history",
    ]
    assert result["report"]["evidence_refs"]
    assert result["proposed_action"]["parameters"]["expected_state"] == "degraded"
    action_id = __import__("uuid").UUID(result["proposed_action"]["action_id"])
    payload_hash = result["proposed_action"]["payload_hash"]

    decided = decide_action(
        db_factory,
        action_id=action_id,
        demo_session_id=session_id,
        expected_payload_hash=payload_hash,
        decision="approved",
        reason="Evidence reviewed in test.",
    )
    assert decided["decision"] == "approved"
    resumed = resume_approval(
        graph,
        thread_id=thread_id,
        action_id=action_id,
        decision="approved",
        payload_hash=payload_hash,
    )
    assert resumed["status"] == "completed"
    executed = execute_approved_action(db_factory, action_id=action_id, demo_session_id=session_id)
    assert executed["idempotent"] is True
    with db_factory() as db:
        action = db.get(ProposedAction, action_id)
        assert action.status == "executed"
        assert db.scalar(select(ActionExecution).where(ActionExecution.action_id == action_id))
        target = db.get(Asset, UUID(action.parameters["asset_id"]))
        assert target.operational_state == "healthy"
        run = db.get(InvestigationRun, run_id)
        assert run.status == "completed"


def test_rejection_and_cross_session_approval_never_write(db_factory, chroma_path):
    graph, _run_id, session_id, _incident_id, thread_id, result = make_graph_session(
        db_factory, chroma_path, "certificate-expiry"
    )
    action_id = __import__("uuid").UUID(result["proposed_action"]["action_id"])
    payload_hash = result["proposed_action"]["payload_hash"]
    with db_factory() as db:
        other_session = db.scalar(
            select(DemoSession).where(
                DemoSession.id != session_id, DemoSession.is_template.is_(False)
            )
        )
        if other_session is None:
            other_session = DemoSession(
                token_hash="other-session-token-hash",
                is_template=False,
                seed_version="test",
                status="active",
            )
            db.add(other_session)
            db.commit()
        other_session_id = other_session.id
    try:
        decide_action(
            db_factory,
            action_id=action_id,
            demo_session_id=other_session_id,
            expected_payload_hash=payload_hash,
            decision="approved",
            reason="Wrong visitor",
        )
    except ActionConflict:
        pass
    else:
        raise AssertionError("Cross-session action approval should be rejected")

    decide_action(
        db_factory,
        action_id=action_id,
        demo_session_id=session_id,
        expected_payload_hash=payload_hash,
        decision="rejected",
        reason="Review rejected.",
    )
    resumed = resume_approval(
        graph,
        thread_id=thread_id,
        action_id=action_id,
        decision="rejected",
        payload_hash=payload_hash,
    )
    assert resumed["status"] == "completed"
    with db_factory() as db:
        action = db.get(ProposedAction, action_id)
        assert action.status == "rejected"
        assert (
            db.scalar(select(ActionExecution).where(ActionExecution.action_id == action_id)) is None
        )
        target = db.get(Asset, UUID(action.parameters["asset_id"]))
        assert target.operational_state == "degraded"


def test_ambiguous_evidence_abstains_without_action(db_factory, chroma_path):
    _graph, _run_id, _session_id, _incident_id, _thread_id, result = make_graph_session(
        db_factory, chroma_path, "ambiguous-network-symptom"
    )
    assert result["status"] == "completed"
    assert not result.get("proposed_action")
    assert "does not establish" in result["report"]["probable_root_cause"]
    with db_factory() as db:
        assert db.scalar(select(ProposedAction)) is None


def test_stale_target_is_recorded_without_write(db_factory, chroma_path):
    _graph, _run_id, session_id, _incident_id, _thread_id, result = make_graph_session(
        db_factory, chroma_path
    )
    proposal = result["proposed_action"]
    action_id = UUID(proposal["action_id"])
    asset_id = UUID(proposal["parameters"]["asset_id"])
    decide_action(
        db_factory,
        action_id=action_id,
        demo_session_id=session_id,
        expected_payload_hash=proposal["payload_hash"],
        decision="approved",
        reason="Approval before a concurrent asset-state change.",
    )
    with db_factory() as db:
        db.get(Asset, asset_id).operational_state = "maintenance"
        db.commit()
    result = execute_approved_action(db_factory, action_id=action_id, demo_session_id=session_id)
    assert result["status"] == "failed"
    with db_factory() as db:
        assert db.get(Asset, asset_id).operational_state == "maintenance"
        execution = db.scalar(select(ActionExecution).where(ActionExecution.action_id == action_id))
        assert execution.status == "failed"
        assert db.get(ProposedAction, action_id).status == "failed"


def test_execution_transaction_rolls_back_asset_update_on_database_error(db_factory, chroma_path):
    _graph, _run_id, session_id, _incident_id, _thread_id, result = make_graph_session(
        db_factory, chroma_path
    )
    proposal = result["proposed_action"]
    action_id = UUID(proposal["action_id"])
    asset_id = UUID(proposal["parameters"]["asset_id"])
    decide_action(
        db_factory,
        action_id=action_id,
        demo_session_id=session_id,
        expected_payload_hash=proposal["payload_hash"],
        decision="approved",
        reason="Rollback test.",
    )
    engine = db_factory.kw["bind"]

    def fail_execution_insert(_conn, _cursor, statement, _params, _context, _many):
        if "INSERT INTO ACTION_EXECUTIONS" in statement.upper():
            raise RuntimeError("Injected database insert failure")

    event.listen(engine, "before_cursor_execute", fail_execution_insert)
    try:
        try:
            execute_approved_action(db_factory, action_id=action_id, demo_session_id=session_id)
        except RuntimeError as exc:
            assert "Injected database insert failure" in str(exc)
        else:
            raise AssertionError("Injected execution insert failure should propagate")
    finally:
        event.remove(engine, "before_cursor_execute", fail_execution_insert)
    with db_factory() as db:
        assert db.get(Asset, asset_id).operational_state == "degraded"
        assert db.get(ProposedAction, action_id).status == "approved"
        assert (
            db.scalar(select(ActionExecution).where(ActionExecution.action_id == action_id)) is None
        )


def test_expired_approval_is_recorded_without_approval_row(db_factory, chroma_path):
    _graph, _run_id, session_id, _incident_id, _thread_id, result = make_graph_session(
        db_factory, chroma_path
    )
    proposal = result["proposed_action"]
    action_id = UUID(proposal["action_id"])
    with db_factory() as db:
        action = db.get(ProposedAction, action_id)
        action.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    response = decide_action(
        db_factory,
        action_id=action_id,
        demo_session_id=session_id,
        expected_payload_hash=proposal["payload_hash"],
        decision="approved",
        reason="Too late.",
    )
    assert response["expired"] is True
    with db_factory() as db:
        assert db.get(ProposedAction, action_id).status == "expired"
        assert (
            db.scalar(select(ActionExecution).where(ActionExecution.action_id == action_id)) is None
        )
