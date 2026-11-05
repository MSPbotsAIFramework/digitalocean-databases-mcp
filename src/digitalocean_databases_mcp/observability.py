"""Structured JSON logging for tool calls and outbound DigitalOcean requests.

SOP SS6/SS14: every tool call and every outbound DigitalOcean request gets one
structured JSON log line via the standard `logging` module -- no new
dependency. Never logged: the bearer token, any header value, or request/response
bodies (they can carry connection strings or a stored database password).
"""

import json
import logging
from typing import Any

logger = logging.getLogger("digitalocean_databases_mcp")

# Key substrings that mark a tool argument as credential-shaped -- dropped from
# the logged summary even though no current tool actually takes one (SOP SS6).
_REDACT_KEY_MARKERS = ("password", "token", "credential", "secret", "authorization", "api_key", "apikey")


def _emit(event: str, **fields: Any) -> None:
    record = {"event": event, **fields}
    logger.info(json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str))


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Drop any argument whose key looks like a credential; keep the rest (SOP SS6)."""
    return {k: v for k, v in args.items() if not any(marker in k.lower() for marker in _REDACT_KEY_MARKERS)}


def log_tool_call(tool_name: str, args: dict[str, Any], latency_ms: float, error_code: str | None) -> None:
    """One line per MCP tool invocation: tool name, redacted arg summary, latency, error code."""
    _emit(
        "tool_call",
        tool=tool_name,
        args=redact_args(args),
        latency_ms=round(latency_ms, 1),
        outcome="error" if error_code else "ok",
        error_code=error_code,
    )


def log_do_request(method: str, path: str, status_code: int | None, latency_ms: float, error: str | None = None) -> None:
    """One line per outbound DigitalOcean HTTP attempt: path, status code, latency.

    `status_code` is None and `error` is set for a transport failure/timeout
    that never produced a response (retried or raised as DOApiError by the
    caller).
    """
    _emit(
        "do_request",
        method=method,
        path=path,
        status_code=status_code,
        latency_ms=round(latency_ms, 1),
        error=error,
    )
