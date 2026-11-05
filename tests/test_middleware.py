"""Unit tests for CredentialMiddleware: missing-header 401, and per-request
isolation of the credential ContextVar under real concurrency (SOP SS3.3, SS10).
"""

import asyncio
import json

import httpx
import pytest

from digitalocean_databases_mcp.server import CREDENTIAL_HEADER, CredentialMiddleware, current_credential


def _ok_echo_app():
    """A downstream ASGI app that echoes back whatever credential it sees."""

    async def app(scope, receive, send):
        await asyncio.sleep(0.01)  # force a real interleave point under concurrency
        token = current_credential()
        body = json.dumps({"seen_token": token}).encode()
        await send({"type": "http.response.start", "status": 200,
                     "headers": [[b"content-type", b"application/json"]]})
        await send({"type": "http.response.body", "body": body})

    return app


async def test_missing_header_returns_401_and_never_calls_downstream():
    called = False

    async def downstream(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"should not run"})

    app = CredentialMiddleware(downstream, "/mcp")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert resp.status_code == 401
    assert CREDENTIAL_HEADER in resp.text
    assert called is False


async def test_health_path_is_exempt_from_the_credential_check():
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = CredentialMiddleware(downstream, "/mcp")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200


async def test_header_present_credential_reaches_context_and_resets_after():
    app = CredentialMiddleware(_ok_echo_app(), "/mcp")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/mcp", headers={CREDENTIAL_HEADER: "tok-A"}, json={})

    assert resp.status_code == 200
    assert resp.json()["seen_token"] == "tok-A"
    # Outside any request, the ContextVar must be back to its default: `finally: reset()` ran.
    assert current_credential() is None


@pytest.mark.parametrize("_iteration", range(3))
async def test_concurrent_requests_do_not_leak_credentials_or_data(_iteration):
    """AC9/TC9: two concurrent requests, two different tokens, each sees only its own.

    A shared-ContextVar-without-reset bug would make this flaky under
    pytest-asyncio concurrent execution -- run it a few times (parametrize) to
    make that failure mode reliably visible rather than a one-in-N flake.
    """
    app = CredentialMiddleware(_ok_echo_app(), "/mcp")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

        async def one(token: str) -> str:
            resp = await client.post("/mcp", headers={CREDENTIAL_HEADER: token}, json={})
            return resp.json()["seen_token"]

        seen_a, seen_b = await asyncio.gather(one("tenant-A-token"), one("tenant-B-token"))

    assert seen_a == "tenant-A-token"
    assert seen_b == "tenant-B-token"
