"""Structured logging coverage for PRD-21339 review round 1 must-fix:
tool_call and do_request log lines must exist, carry the right fields, and
never leak a credential-shaped argument (SOP SS6/SS14).
"""

import json
import logging

import httpx
import pytest

from conftest import build_mcp, call, json_response
from digitalocean_databases_mcp.observability import log_tool_call, redact_args

LOGGER_NAME = "digitalocean_databases_mcp"


async def _no_sleep(*_args, **_kwargs) -> None:
    return None


def _records(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == LOGGER_NAME]


def test_redact_args_drops_credential_shaped_keys():
    args = {"cluster_id": "abc-123", "token": "secret", "api_key": "x", "password": "y", "limit": 50}
    assert redact_args(args) == {"cluster_id": "abc-123", "limit": 50}


async def test_successful_tool_call_logs_tool_call_and_do_request(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"database": {"id": "c1", "name": "db1"}})

    mcp = build_mcp(handler)
    await call(mcp, "do_get_cluster", {"cluster_id": "c1"})

    records = _records(caplog)
    tool_calls = [r for r in records if r["event"] == "tool_call"]
    do_requests = [r for r in records if r["event"] == "do_request"]

    assert len(tool_calls) == 1
    assert tool_calls[0]["tool"] == "do_get_cluster"
    assert tool_calls[0]["args"] == {"cluster_id": "c1"}
    assert tool_calls[0]["outcome"] == "ok"
    assert tool_calls[0]["error_code"] is None
    assert isinstance(tool_calls[0]["latency_ms"], (int, float))

    assert len(do_requests) == 1
    assert do_requests[0]["method"] == "GET"
    assert do_requests[0]["path"] == "/v2/databases/c1"
    assert do_requests[0]["status_code"] == 200
    assert isinstance(do_requests[0]["latency_ms"], (int, float))


async def test_failed_tool_call_logs_mapped_error_code(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(404, {"message": "cluster not found"})

    mcp = build_mcp(handler)
    await call(mcp, "do_get_cluster", {"cluster_id": "missing"})

    tool_calls = [r for r in _records(caplog) if r["event"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["outcome"] == "error"
    assert tool_calls[0]["error_code"] == "not_found"

    do_requests = [r for r in _records(caplog) if r["event"] == "do_request"]
    assert do_requests[0]["status_code"] == 404


async def test_logged_record_never_contains_the_bearer_token(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"database": {"id": "c1"}})

    mcp = build_mcp(handler, token="super-secret-token")
    await call(mcp, "do_get_cluster", {"cluster_id": "c1"})

    for record in caplog.records:
        assert "super-secret-token" not in record.message


def test_log_tool_call_emits_single_json_line(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    log_tool_call("do_list_clusters", {"limit": 50, "token": "nope"}, 12.345, None)

    records = _records(caplog)
    assert len(records) == 1
    assert records[0] == {
        "event": "tool_call",
        "tool": "do_list_clusters",
        "args": {"limit": 50},
        "latency_ms": 12.3,
        "outcome": "ok",
        "error_code": None,
    }


@pytest.mark.parametrize("exc_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_transport_failure_logs_do_request_without_status_code(caplog, monkeypatch, exc_type):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    # Skip the real retry backoff (up to ~7s) -- only the logging behavior is under test.
    monkeypatch.setattr("digitalocean_databases_mcp.api_client.asyncio.sleep", _no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("boom", request=request)

    mcp = build_mcp(handler)
    await call(mcp, "do_get_cluster", {"cluster_id": "c1"})

    do_requests = [r for r in _records(caplog) if r["event"] == "do_request"]
    # 1 initial attempt + MAX_RETRIES retries, each logged.
    assert len(do_requests) == 4
    assert all(r["status_code"] is None for r in do_requests)
    assert all(r["error"] in ("timeout", "transport_error") for r in do_requests)
