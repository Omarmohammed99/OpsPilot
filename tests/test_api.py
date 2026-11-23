from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from opspilot.agent.providers import DeterministicTestProvider
from opspilot.api.app import build_app
from opspilot.db.models import ProposedAction
from opspilot.ingestion.pipeline import run_ingestion
from opspilot.retrieval.chroma_store import KnowledgeIndex
from opspilot.retrieval.embeddings import LocalHashEmbeddingFunction


def test_http_flow_is_session_scoped_and_executes_approved_action(db_factory, chroma_path):
    index = KnowledgeIndex(
        path=chroma_path,
        mode="embedded",
        prefix="api-test",
        embedding_function=LocalHashEmbeddingFunction(),
    )
    with db_factory() as db:
        run_ingestion(db, index)
        db.commit()
    saver = MemorySaver()
    app = build_app(
        session_factory=db_factory,
        provider_factory=DeterministicTestProvider,
        checkpointer_context_factory=lambda: nullcontext(saver),
        initialize_on_startup=False,
    )
    app.state.checkpointer_override = saver
    app.state.knowledge_factory = lambda: KnowledgeIndex(
        path=chroma_path,
        mode="embedded",
        prefix="api-test",
        embedding_function=LocalHashEmbeddingFunction(),
    )
    with TestClient(app) as client:
        assert client.get("/api/health/live").json() == {"status": "ok"}
        assert client.get("/api/incidents").status_code == 401
        landing = client.get("/")
        assert landing.status_code == 200
        assert "Evidence-led investigation" in landing.text

        session_response = client.post(
            "/api/demo-sessions", json={"scenario_id": "db-pool-exhaustion"}
        )
        assert session_response.status_code == 201
        incident = client.get("/api/incidents").json()["incidents"][0]
        started = client.post(
            "/api/investigations",
            json={"incident_id": incident["id"], "request_text": "Trace the checkout failure."},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        run = client.get(f"/api/investigations/{run_id}").json()
        assert run["status"] == "waiting_approval"
        assert len(run["observations"]) >= 5
        assert run["report"]["evidence_refs"]
        proposal = run["proposed_action"]
        assert proposal["parameters"]["new_state"] == "healthy"

        approved = client.post(
            f"/api/actions/{proposal['id']}/approval",
            json={
                "decision": "approved",
                "expected_payload_hash": proposal["payload_hash"],
                "reason": "Reviewed evidence and approving the synthetic state change.",
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["investigation_status"] == "completed"
        assert approved.json()["execution"]["status"] == "succeeded"
        final = client.get(f"/api/investigations/{run_id}").json()
        assert final["status"] == "completed"
        assert final["execution"]["result"]["new_state"] == "healthy"
        assert client.get(f"/api/investigations/{run_id}/audit").json()["events"]
        assert (
            client.get(f"/api/actions/{proposal['id']}/execution").json()["status"] == "succeeded"
        )

        duplicate = client.post(
            f"/api/actions/{proposal['id']}/approval",
            json={
                "decision": "approved",
                "expected_payload_hash": proposal["payload_hash"],
                "reason": "Idempotent retry.",
            },
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["idempotent"] is True

        other = TestClient(app)
        second_session = other.post(
            "/api/demo-sessions", json={"scenario_id": "certificate-expiry"}
        )
        assert second_session.status_code == 201
        assert other.get(f"/api/incidents/{incident['id']}").status_code == 404
        cross_session = other.post(
            f"/api/actions/{proposal['id']}/approval",
            json={
                "decision": "approved",
                "expected_payload_hash": proposal["payload_hash"],
                "reason": "Attempt from another session.",
            },
        )
        assert cross_session.status_code == 404
        other.close()


def test_approval_api_rejects_bad_hash_and_input(db_factory, chroma_path):
    index = KnowledgeIndex(
        path=chroma_path,
        mode="embedded",
        prefix="api-validation",
        embedding_function=LocalHashEmbeddingFunction(),
    )
    with db_factory() as db:
        run_ingestion(db, index)
        db.commit()
    saver = MemorySaver()
    app = build_app(
        session_factory=db_factory,
        provider_factory=DeterministicTestProvider,
        checkpointer_context_factory=lambda: nullcontext(saver),
        initialize_on_startup=False,
    )
    app.state.checkpointer_override = saver
    app.state.knowledge_factory = lambda: KnowledgeIndex(
        path=chroma_path,
        mode="embedded",
        prefix="api-validation",
        embedding_function=LocalHashEmbeddingFunction(),
    )
    with TestClient(app) as client:
        client.post("/api/demo-sessions", json={"scenario_id": "db-pool-exhaustion"})
        incident = client.get("/api/incidents").json()["incidents"][0]
        run_response = client.post(
            "/api/investigations",
            json={"incident_id": incident["id"], "request_text": "Investigate this incident."},
        )
        run = client.get(f"/api/investigations/{run_response.json()['run_id']}").json()
        proposal = run["proposed_action"]
        invalid = client.post(
            f"/api/actions/{proposal['id']}/approval",
            json={"decision": "approved", "expected_payload_hash": "0" * 64},
        )
        assert invalid.status_code == 409
        malformed = client.post(
            f"/api/actions/{proposal['id']}/approval",
            json={"decision": "execute_sql", "expected_payload_hash": proposal["payload_hash"]},
        )
        assert malformed.status_code == 422
        with db_factory() as db:
            action = db.scalar(
                select(ProposedAction).where(ProposedAction.id == UUID(proposal["id"]))
            )
            assert action.status == "pending"
