"""Entrypoint: a Starlette app serving GET /health and POST /mcp over uvicorn."""

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from digitalocean_databases_mcp.config import settings
from digitalocean_databases_mcp.server import CredentialMiddleware, create_mcp_server, health


def build_app() -> ASGIApp:
    mcp = create_mcp_server()
    mcp_app = mcp.streamable_http_app()

    # The MCP app's own lifespan (its session manager) must be wired to the
    # outer app's lifespan explicitly -- Mount() does not run a mounted app's
    # lifespan on its own, which otherwise surfaces as "Task group is not
    # initialized" on every /mcp call while /health still returns 200 (SOP SS7).
    app = Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.router.lifespan_context,
    )
    return CredentialMiddleware(app, mcp.settings.streamable_http_path)


def main() -> None:
    uvicorn.run(build_app(), host=settings.mcp_http_host, port=settings.mcp_http_port)


if __name__ == "__main__":
    main()
