"""Shared test fixtures: a FastMCP instance wired to a mocked DigitalOcean API."""

import json
from typing import Any, Callable

import httpx
from mcp.server.fastmcp import FastMCP

from digitalocean_databases_mcp.api_client import DOClient
from digitalocean_databases_mcp.server import INSTRUCTIONS
from digitalocean_databases_mcp.tools import clusters, databases, network, ops, users

Handler = Callable[[httpx.Request], httpx.Response]


def build_mcp(handler: Handler, token: str = "test-token") -> FastMCP:
    """A FastMCP with every tool registered against a client_factory whose
    HTTP transport is the given mock handler -- no network traffic."""

    def client_factory() -> DOClient:
        return DOClient(token, "https://api.digitalocean.com", transport=httpx.MockTransport(handler))

    mcp = FastMCP(name="digitalocean-databases-mcp-test", instructions=INSTRUCTIONS, stateless_http=True)
    clusters.register(mcp, client_factory)
    databases.register(mcp, client_factory)
    users.register(mcp, client_factory)
    network.register(mcp, client_factory)
    ops.register(mcp, client_factory)
    return mcp


async def call(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a registered tool and parse its JSON-string result."""
    content, _structured = await mcp.call_tool(name, arguments)
    text = content[0].text
    return json.loads(text)


def json_response(status_code: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
    if body is None:
        return httpx.Response(status_code, headers=headers)
    return httpx.Response(status_code, json=body, headers=headers)
