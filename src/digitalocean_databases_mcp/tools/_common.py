"""Shared helpers used by every tools/<resource>.py module."""

import json
import time
from typing import Any, Awaitable, Callable

from digitalocean_databases_mcp.api_client import DOApiError, DOClient
from digitalocean_databases_mcp.errors import envelope, not_configured, status_to_code
from digitalocean_databases_mcp.observability import log_tool_call, new_request_id

MAX_RESULT_CHARS = 20_000

ClientFactory = Callable[[], DOClient | None]


def dumps(obj: object) -> str:
    """Compact, non-ASCII-escaped JSON (SOP SS2.5: no indent, ensure_ascii=False)."""
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    # Defensive cap for the rare oversized single object; list_* tools already
    # stay under this via their own limit/has_more (SOP SS2.5).
    return json.dumps(
        {
            "truncated": True,
            "note": f"Result exceeded {MAX_RESULT_CHARS} characters and was truncated.",
            "partial": text[: MAX_RESULT_CHARS - 200],
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


async def run_tool(
    client_factory: ClientFactory,
    fn: Callable[[DOClient], Awaitable[object]],
    tool_name: str,
    args: dict[str, Any],
) -> str:
    """Build a request-scoped client, run `fn`, map any DOApiError to the error envelope.

    Tools never raise -- every outcome, success or failure, comes back as a
    JSON string (SOP SS4.1). Always emits one structured `tool_call` log line
    (request id, tool name, redacted+truncated arg summary, latency, error
    code if any) regardless of how `fn` finishes (SOP SS6/SS14). The same
    request id is set on `client` so every `do_request` line the call makes
    (including retries) carries it too, tying the whole invocation together.
    """
    client = client_factory()
    request_id = new_request_id()
    start = time.perf_counter()
    if client is None:
        log_tool_call(tool_name, args, (time.perf_counter() - start) * 1000, "not_configured", request_id=request_id)
        return not_configured()
    client.request_id = request_id
    error_code: str | None = None
    try:
        async with client:
            result = await fn(client)
    except DOApiError as exc:
        error_code = status_to_code(exc.status_code) if exc.status_code else "upstream_error"
        return envelope(error_code, exc.message)
    finally:
        log_tool_call(tool_name, args, (time.perf_counter() - start) * 1000, error_code, request_id=request_id)
    return dumps(result)


def strip_passwords(obj: Any) -> Any:
    """Recursively drop any key named `password` (case-insensitive).

    DigitalOcean connection objects can carry a stored database password when
    the token has the database:view_credentials scope. Even though that is a
    separate credential from the MCP gateway token, putting a raw DB password
    into an agent's transcript is an avoidable exposure for a read-only tool
    (DEV01 decision, see docs/tickets/PRD-21339.md).
    """
    if isinstance(obj, dict):
        return {k: strip_passwords(v) for k, v in obj.items() if k.lower() != "password"}
    if isinstance(obj, list):
        return [strip_passwords(v) for v in obj]
    return obj
