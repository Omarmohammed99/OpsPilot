"""Canonical, typed payload for the one allowlisted database action."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SetAssetOperationalState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    action_type: Literal["set_asset_operational_state"] = "set_asset_operational_state"
    asset_id: UUID
    expected_state: Literal["degraded"] = "degraded"
    new_state: Literal["healthy"] = "healthy"
    reason: str = Field(min_length=8, max_length=500)


def canonical_action_json(action: SetAssetOperationalState) -> str:
    return json.dumps(
        action.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def action_payload_hash(action: SetAssetOperationalState) -> str:
    return hashlib.sha256(canonical_action_json(action).encode("utf-8")).hexdigest()
