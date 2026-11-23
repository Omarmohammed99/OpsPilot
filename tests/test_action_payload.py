from uuid import uuid4

import pytest
from pydantic import ValidationError

from opspilot.schemas.actions import SetAssetOperationalState, action_payload_hash


def test_action_payload_is_typed_canonical_and_immutable():
    payload = SetAssetOperationalState(
        asset_id=uuid4(),
        expected_state="degraded",
        new_state="healthy",
        reason="Evidence-backed recovery was verified.",
    )
    equivalent = SetAssetOperationalState.model_validate(payload.model_dump())
    assert action_payload_hash(payload) == action_payload_hash(equivalent)
    assert len(action_payload_hash(payload)) == 64
    with pytest.raises(ValidationError):
        payload.new_state = "offline"


def test_action_schema_rejects_other_transitions_and_extra_commands():
    with pytest.raises(ValidationError):
        SetAssetOperationalState(
            asset_id=uuid4(),
            expected_state="healthy",
            new_state="offline",
            reason="Not the approved transition.",
        )
    with pytest.raises(ValidationError):
        SetAssetOperationalState(
            asset_id=uuid4(),
            expected_state="degraded",
            new_state="healthy",
            reason="Valid transition.",
            sql="UPDATE assets SET operational_state='healthy'",
        )
