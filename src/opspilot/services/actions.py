"""Transactional proposal, approval, and exactly-once allowlisted execution services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from opspilot.db.models import (
    ActionExecution,
    Approval,
    Asset,
    AuditEvent,
    IncidentAsset,
    InvestigationRun,
    ProposedAction,
)
from opspilot.schemas.actions import SetAssetOperationalState, action_payload_hash


class ActionConflict(ValueError):
    """The action is stale, out of scope, or already has a conflicting decision."""


def create_action_proposal(
    db: Session,
    *,
    run: InvestigationRun,
    asset_id: uuid.UUID,
    reason: str,
    ttl_minutes: int,
) -> ProposedAction:
    action = SetAssetOperationalState(asset_id=asset_id, reason=reason)
    payload = action.model_dump(mode="json")
    payload_hash = action_payload_hash(action)
    existing = db.scalar(
        select(ProposedAction).where(
            ProposedAction.run_id == run.id,
            ProposedAction.payload_hash == payload_hash,
        )
    )
    if existing:
        return existing
    now = datetime.now(UTC)
    proposal = ProposedAction(
        run_id=run.id,
        incident_id=run.incident_id,
        demo_session_id=run.demo_session_id,
        action_type=action.action_type,
        parameters=payload,
        payload_hash=payload_hash,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.add(proposal)
    db.flush()
    return proposal


def decide_action(
    session_factory: sessionmaker[Session],
    *,
    action_id: uuid.UUID,
    demo_session_id: uuid.UUID,
    expected_payload_hash: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ActionConflict("Decision is not supported")
    with session_factory() as db, db.begin():
        action = db.scalar(
            select(ProposedAction)
            .where(
                ProposedAction.id == action_id,
                ProposedAction.demo_session_id == demo_session_id,
            )
            .with_for_update()
        )
        if action is None:
            raise ActionConflict("Action not found in this demo session")
        if action.payload_hash != expected_payload_hash:
            raise ActionConflict("Action parameters changed; approval hash does not match")
        parsed = SetAssetOperationalState.model_validate(action.parameters)
        if action_payload_hash(parsed) != action.payload_hash:
            raise ActionConflict("Stored action payload failed integrity validation")
        existing = db.scalar(select(Approval).where(Approval.action_id == action.id))
        if existing:
            if (
                existing.actor_session_id == demo_session_id
                and existing.payload_hash == expected_payload_hash
                and existing.decision == decision
            ):
                return {
                    "action_id": str(action.id),
                    "decision": existing.decision,
                    "idempotent": True,
                    "payload_hash": action.payload_hash,
                    "status": action.status,
                }
            raise ActionConflict("This action already has a different human decision")
        if action.status != "pending":
            raise ActionConflict(f"Action is no longer pending ({action.status})")
        expires_at = action.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            action.status = "expired"
            db.add(
                AuditEvent(
                    demo_session_id=demo_session_id,
                    incident_id=action.incident_id,
                    run_id=action.run_id,
                    action_id=action.id,
                    actor_id=f"visitor:{demo_session_id}",
                    event_type="approval_expired",
                    details={"payload_hash": action.payload_hash},
                )
            )
            return {
                "action_id": str(action.id),
                "decision": None,
                "expired": True,
                "payload_hash": action.payload_hash,
                "status": action.status,
            }
        approval = Approval(
            action_id=action.id,
            decision=decision,
            actor_session_id=demo_session_id,
            payload_hash=action.payload_hash,
            reason=reason.strip()[:1000] or None,
        )
        action.status = decision
        db.add(approval)
        db.add(
            AuditEvent(
                demo_session_id=demo_session_id,
                incident_id=action.incident_id,
                run_id=action.run_id,
                action_id=action.id,
                actor_id=f"visitor:{demo_session_id}",
                event_type=f"action_{decision}",
                details={"payload_hash": action.payload_hash, "reason": approval.reason},
            )
        )
        db.flush()
        return {
            "action_id": str(action.id),
            "decision": decision,
            "idempotent": False,
            "payload_hash": action.payload_hash,
            "status": action.status,
        }


def execute_approved_action(
    session_factory: sessionmaker[Session],
    *,
    action_id: uuid.UUID,
    demo_session_id: uuid.UUID,
) -> dict[str, Any]:
    with session_factory() as db, db.begin():
        action = db.scalar(
            select(ProposedAction)
            .where(
                ProposedAction.id == action_id,
                ProposedAction.demo_session_id == demo_session_id,
            )
            .with_for_update()
        )
        if action is None:
            raise ActionConflict("Action not found in this demo session")
        prior = db.scalar(select(ActionExecution).where(ActionExecution.action_id == action.id))
        if prior:
            return {
                "execution_id": str(prior.id),
                "status": prior.status,
                "result": prior.result,
                "idempotent": True,
            }
        if action.status != "approved":
            raise ActionConflict("Action does not have an approved human decision")
        approval = db.scalar(select(Approval).where(Approval.action_id == action.id))
        if (
            approval is None
            or approval.decision != "approved"
            or approval.actor_session_id != demo_session_id
            or approval.payload_hash != action.payload_hash
        ):
            raise ActionConflict("No matching session-bound approval exists")
        parsed = SetAssetOperationalState.model_validate(action.parameters)
        if action_payload_hash(parsed) != action.payload_hash:
            raise ActionConflict("Stored action payload failed integrity validation")

        def record_stale_failure(reason_code: str) -> dict[str, Any]:
            now = datetime.now(UTC)
            result = {"error_code": reason_code, "message": "The approved asset state was stale."}
            execution = ActionExecution(
                action_id=action.id,
                payload_hash=action.payload_hash,
                idempotency_key=f"approved-action:{action.id}:{action.payload_hash}",
                status="failed",
                result=result,
                completed_at=now,
            )
            action.status = "failed"
            db.add(execution)
            db.add(
                AuditEvent(
                    demo_session_id=demo_session_id,
                    incident_id=action.incident_id,
                    run_id=action.run_id,
                    action_id=action.id,
                    actor_id=f"visitor:{demo_session_id}",
                    event_type="action_execution_failed",
                    details={"payload_hash": action.payload_hash, "error_code": reason_code},
                )
            )
            db.flush()
            return {
                "execution_id": str(execution.id),
                "status": execution.status,
                "result": execution.result,
                "idempotent": False,
            }

        target = db.scalar(
            select(Asset)
            .join(IncidentAsset, IncidentAsset.asset_id == Asset.id)
            .where(
                Asset.id == parsed.asset_id,
                Asset.demo_session_id == demo_session_id,
                IncidentAsset.incident_id == action.incident_id,
            )
            .with_for_update()
        )
        if target is None or target.operational_state != parsed.expected_state:
            return record_stale_failure("stale_or_out_of_scope_asset")
        changed = db.execute(
            update(Asset)
            .where(
                Asset.id == target.id,
                Asset.demo_session_id == demo_session_id,
                Asset.operational_state == parsed.expected_state,
            )
            .values(operational_state=parsed.new_state)
        )
        if changed.rowcount != 1:
            return record_stale_failure("concurrent_asset_state_change")
        result = {
            "asset_id": str(target.id),
            "asset_key": target.asset_key,
            "previous_state": parsed.expected_state,
            "new_state": parsed.new_state,
        }
        execution = ActionExecution(
            action_id=action.id,
            payload_hash=action.payload_hash,
            idempotency_key=f"approved-action:{action.id}:{action.payload_hash}",
            status="succeeded",
            result=result,
            completed_at=datetime.now(UTC),
        )
        db.add(execution)
        action.status = "executed"
        db.add(
            AuditEvent(
                demo_session_id=demo_session_id,
                incident_id=action.incident_id,
                run_id=action.run_id,
                action_id=action.id,
                actor_id=f"visitor:{demo_session_id}",
                event_type="action_executed",
                details={"payload_hash": action.payload_hash, "result": result},
            )
        )
        db.flush()
        return {
            "execution_id": str(execution.id),
            "status": execution.status,
            "result": execution.result,
            "idempotent": False,
        }
