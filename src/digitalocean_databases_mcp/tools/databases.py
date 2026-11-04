"""Logical databases inside a cluster: list, add, remove."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from digitalocean_databases_mcp.tools._common import ClientFactory, run_tool


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_databases(cluster_id: str) -> str:
        """List the logical databases inside one cluster.

        Args: cluster_id is the cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/dbs")
            return {"databases": (body or {}).get("dbs", [])}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    async def do_add_database(cluster_id: str, db_name: str) -> str:
        """Create a new logical database inside a cluster.

        Args: cluster_id is the target cluster. db_name is the new database's name.
        """

        async def call(client):
            body = await client.call("POST", f"/v2/databases/{cluster_id}/dbs", json={"name": db_name})
            return (body or {}).get("db", {"name": db_name})

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
    async def do_remove_database(cluster_id: str, db_name: str) -> str:
        """Permanently delete one logical database and its data. Cannot be undone.

        Args: cluster_id is the cluster. db_name is the single, explicit
        database to delete -- there is no bulk or pattern-based form.
        """

        async def call(client):
            await client.call("DELETE", f"/v2/databases/{cluster_id}/dbs/{db_name}")
            return {"cluster_id": cluster_id, "db_name": db_name, "deleted": True}

        return await run_tool(client_factory, call)
