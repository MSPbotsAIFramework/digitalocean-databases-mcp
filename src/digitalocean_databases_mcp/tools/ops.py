"""Backups, engine config, and read-only replicas."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from digitalocean_databases_mcp.tools._common import ClientFactory, run_tool


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_backups(cluster_id: str) -> str:
        """List available backups for a cluster. Empty on engines without backups (e.g. MongoDB).

        Args: cluster_id is the cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/backups")
            return {"backups": (body or {}).get("backups", [])}

        return await run_tool(client_factory, call, "do_list_backups", {"cluster_id": cluster_id})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_get_cluster_config(cluster_id: str) -> str:
        """Get a cluster's engine-specific configuration (e.g. PostgreSQL settings).

        Args: cluster_id is the cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/config")
            return (body or {}).get("config", {})

        return await run_tool(client_factory, call, "do_get_cluster_config", {"cluster_id": cluster_id})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_replicas(cluster_id: str) -> str:
        """List read-only replicas of a cluster.

        Args: cluster_id is the primary cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/replicas")
            return {"replicas": (body or {}).get("replicas", [])}

        return await run_tool(client_factory, call, "do_list_replicas", {"cluster_id": cluster_id})
