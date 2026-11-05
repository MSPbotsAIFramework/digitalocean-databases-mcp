"""Structured JSON logging for tool calls and outbound DigitalOcean requests.

SOP SS6/SS14: every tool call and every outbound DigitalOcean request gets one
structured JSON log line via the standard `logging` module -- no new
dependency. Never logged: the bearer token, any header value, or request/response
bodies (they can carry connection strings or a stored database password).

Every line also carries a `request_id`: one correlation id generated per MCP
tool invocation (`new_request_id()`, set by `run_tool`) and threaded through
the `DOClient` instance for that invocation, so the one `tool_call` line and
every `do_request` line it triggered -- including retries -- can be grouped
back into a single request (SOP SS6/SS14).
"""

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger("digitalocean_databases_mcp")

# Key substrings that mark a tool argument as credential-shaped -- dropped from
# the logged summary even though no current tool actually takes one (SOP SS6).
_REDACT_KEY_MARKERS = ("password", "token", "credential", "secret", "authorization", "api_key", "apikey")

# Cap on a single logged argument value's length so a bulk/list-shaped argument
# (e.g. do_update_firewall_rules' `rules`, do_create_cluster's `tags`) can't
# blow up a log line (tech design: "truncated arg summary", docs/tickets/PRD-21339.md).
_MAX_ARG_VALUE_CHARS = 200


def _emit(event: str, **fields: Any) -> None:
    record = {"event": event, **fields}
    logger.info(json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str))


def new_request_id() -> str:
    """Short correlation id for one MCP tool invocation (SOP SS6/SS14)."""
    return uuid.uuid4().hex[:12]


def _truncate_value(value: Any) -> Any:
    """Cap one argument value's logged size; short values pass through unchanged."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= _MAX_ARG_VALUE_CHARS:
        return value
    overflow = len(text) - _MAX_ARG_VALUE_CHARS
    return f"{text[:_MAX_ARG_VALUE_CHARS]}...<truncated {overflow} more chars>"


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Drop any credential-shaped key, then cap each remaining value's logged size (SOP SS6)."""
    kept = {k: v for k, v in args.items() if not any(marker in k.lower() for marker in _REDACT_KEY_MARKERS)}
    return {k: _truncate_value(v) for k, v in kept.items()}


def log_tool_call(tool_name: str, args: dict[str, Any], latency_ms: float, error_code: str | None, *, request_id: str) -> None:
    """One line per MCP tool invocation: request id, tool name, redacted+truncated arg summary, latency, error code."""
    _emit(
        "tool_call",
        request_id=request_id,
        tool=tool_name,
        args=redact_args(args),
        latency_ms=round(latency_ms, 1),
        outcome="error" if error_code else "ok",
        error_code=error_code,
    )


def log_do_request(
    method: str, path: str, status_code: int | None, latency_ms: float, error: str | None = None, *, request_id: str | None
) -> None:
    """One line per outbound DigitalOcean HTTP attempt: request id, path, status code, latency.

    `status_code` is None and `error` is set for a transport failure/timeout
    that never produced a response (retried or raised as DOApiError by the
    caller). `request_id` ties this line back to the `tool_call` line that
    triggered it; it is the id `run_tool` generated for the enclosing
    invocation, threaded in via `DOClient.request_id`.
    """
    _emit(
        "do_request",
        request_id=request_id,
        method=method,
        path=path,
        status_code=status_code,
        latency_ms=round(latency_ms, 1),
        error=error,
    )
