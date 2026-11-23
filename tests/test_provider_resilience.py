import httpx
import pytest
from openai import BadRequestError, RateLimitError

from opspilot.agent import providers
from opspilot.agent.providers import (
    TOOL_SCHEMA,
    StructuredPlannerDecision,
    _call_planner_with_fallback,
    _call_with_rate_limit_retry,
    _parse_wait_seconds,
)


def _tool_generation_error() -> BadRequestError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Tool generation failed", "code": "tool_use_failed"}},
    )
    return BadRequestError("Tool generation failed", response=response, body=response.json())


def test_planner_uses_strict_output_fallback_for_tool_generation_failure():
    calls = {"native": 0, "structured": 0}

    def native_request():
        calls["native"] += 1
        raise _tool_generation_error()

    def structured_request():
        calls["structured"] += 1
        return "valid-structured-decision"

    response, reserved_tokens, used_fallback = _call_planner_with_fallback(
        native_request,
        structured_request,
        remaining_seconds=30,
        retry_token_reserve=1200,
    )
    assert response == "valid-structured-decision"
    assert reserved_tokens == 1200
    assert used_fallback is True
    assert calls == {"native": 1, "structured": 1}


def test_tool_generation_failure_is_not_retried_without_budget():
    calls = {"native": 0, "structured": 0}

    def native_request():
        calls["native"] += 1
        raise _tool_generation_error()

    def structured_request():
        calls["structured"] += 1
        return "unused"

    with pytest.raises(BadRequestError):
        _call_planner_with_fallback(
            native_request,
            structured_request,
            remaining_seconds=30,
            retry_token_reserve=0,
        )
    assert calls == {"native": 1, "structured": 0}


def test_planner_tool_schema_exposes_supported_argument_fields():
    properties = TOOL_SCHEMA["function"]["parameters"]["properties"]
    argument_fields = properties["arguments"]["properties"]
    assert "start_at" in argument_fields
    assert "end_at" in argument_fields
    assert "start_time" not in argument_fields
    assert "end_time" not in argument_fields


def test_structured_fallback_schema_is_strict_and_fully_required():
    schema = StructuredPlannerDecision.model_json_schema()
    arguments = schema["$defs"]["StructuredPlannerArguments"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["name", "arguments", "rationale"]
    assert arguments["additionalProperties"] is False
    assert set(arguments["required"]) == set(arguments["properties"])


def test_rate_limit_retry_uses_provider_wait_header(monkeypatch):
    request_obj = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response_obj = httpx.Response(
        429,
        request=request_obj,
        headers={"retry-after": "0.01", "x-ratelimit-reset-tokens": "0.02s"},
    )
    error = RateLimitError("Rate limited", response=response_obj, body={})
    calls = 0
    waits = []

    def request():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return "retried"

    monkeypatch.setattr(providers.time, "sleep", waits.append)
    response, retry_tokens = _call_with_rate_limit_retry(request, remaining_seconds=30)
    assert response == "retried"
    assert retry_tokens == 0
    assert waits == [1.52]
    assert calls == 2


def test_rate_limit_wait_parser_handles_groq_duration_headers():
    assert _parse_wait_seconds("1m2.5s") == 62.5
    assert _parse_wait_seconds("2.25") == 2.25
    assert _parse_wait_seconds(None) == 0
