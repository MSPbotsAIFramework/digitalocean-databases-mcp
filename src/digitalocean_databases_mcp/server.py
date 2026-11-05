"""FastMCP factory and the request-level credential middleware.

Every request carries its own tenant's DigitalOcean token in a header (SOP
SS3.1). The middleware reads it into a ContextVar for the duration of that
request only -- `finally: reset()` -- so concurrent requests from different
tenants never see each other's credential (SOP SS3.3). There is no
environment-variable fallback: a request without the header is rejected with
401 before it ever reaches a tool (SOP SS3.2).
"""

from contextvars import ContextVar, Token

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from digitalocean_databases_mcp.api_client import DOClient
from digitalocean_databases_mcp.config import settings

CREDENTIAL_HEADER = "X-DigitalOcean-Api-Key"
_CREDENTIAL_HEADER_BYTES = CREDENTIAL_HEADER.lower().encode("ascii")

_credential_var: ContextVar[str | None] = ContextVar("do_api_token", default=None)

INSTRUCTIONS = """\
This server manages DigitalOcean Managed Databases for one DigitalOcean \
account per request (the account that owns the API token presented in the \
X-DigitalOcean-Api-Key header).

Core concepts: a "cluster" is a managed database instance (PostgreSQL, \
MySQL, MongoDB, etc). A cluster contains logical "databases" and "users", \
exposes "connection pools" (PostgreSQL only) and a "firewall" trusted-source \
list, and may have read-only "replicas" and automatic "backups".

Typical flows:
- Discover: do_list_clusters -> do_get_cluster for detail.
- Provision access: do_add_database and/or do_add_user on an existing cluster.
- Lock down network access: do_list_firewall_rules, then do_update_firewall_rules \
with the full replacement list (this call replaces the whole list, it does not merge).
- Lifecycle: do_create_cluster to provision, do_resize_cluster to scale, \
do_destroy_cluster to tear down (destructive, takes only an explicit cluster_id).

Destructive tools (do_destroy_cluster, do_remove_database, do_remove_user) take \
only an explicit resource id -- there is no bulk or pattern-based delete.
"""


class CredentialMiddleware:
    """Pure-ASGI middleware: reject /mcp requests without the credential header."""

    def __init__(self, app: ASGIApp, mcp_path: str):
        self.app = app
        self.mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.mcp_path:
            await self.app(scope, receive, send)
            return

        token = _read_header(scope, _CREDENTIAL_HEADER_BYTES)
        if not token:
            response = JSONResponse(
                {
                    "error": {
                        "code": "unauthorized",
                        "message": f"Missing required header: {CREDENTIAL_HEADER}",
                    }
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        reset_token: Token[str | None] = _credential_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _credential_var.reset(reset_token)


def _read_header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def current_credential() -> str | None:
    """The current request's DigitalOcean token, or None outside a request."""
    return _credential_var.get()


def client_factory() -> DOClient | None:
    """Build a DOClient scoped to the current request's credential.

    Returns None when called outside a credentialed request (defensive --
    CredentialMiddleware already rejects those before any tool runs).
    """
    token = current_credential()
    if not token:
        return None
    return DOClient(token, settings.do_api_base_url)


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        name="digitalocean-databases-mcp",
        instructions=INSTRUCTIONS,
        host=settings.mcp_http_host,
        port=settings.mcp_http_port,
        stateless_http=True,
        # The service sits behind an internal proxy/gateway, never exposed
        # directly to the public internet, so DNS-rebinding protection (which
        # rejects proxied Host headers with 421) is safe to disable (SOP SS7).
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    from digitalocean_databases_mcp.tools import clusters, databases, network, ops, users

    clusters.register(mcp, client_factory)
    databases.register(mcp, client_factory)
    users.register(mcp, client_factory)
    network.register(mcp, client_factory)
    ops.register(mcp, client_factory)

    return mcp


async def health(_request: Request) -> JSONResponse:
    """GET /health -- a pure local probe, never calls the DigitalOcean API (SOP SS1.1)."""
    return JSONResponse({"status": "ok"})
