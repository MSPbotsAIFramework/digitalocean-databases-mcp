"""Shared helpers used by every tools/<resource>.py module."""

import json
from typing import Any, Awaitable, Callable

from digitalocean_databases_mcp.api_client import DOApiError, DOClient
from digitalocean_databases_mcp.errors import envelope, not_configured, status_to_code

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


async def run_tool(client_factory: ClientFactory, fn: Callable[[DOClient], Awaitable[object]]) -> str:
    """Build a request-scoped client, run `fn`, map any DOApiError to the error envelope.

    Tools never raise -- every outcome, success or failure, comes back as a
    JSON string (SOP SS4.1).
    """
    client = client_factory()
    if client is None:
        return not_configured()
    try:
        async with client:
            result = await fn(client)
    except DOApiError as exc:
        code = status_to_code(exc.status_code) if exc.status_code else "upstream_error"
        return envelope(code, exc.message)
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
