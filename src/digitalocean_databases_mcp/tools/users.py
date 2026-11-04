"""Database users inside a cluster: list, add, remove."""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from digitalocean_databases_mcp.tools._common import ClientFactory, run_tool, strip_passwords


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_users(cluster_id: str) -> str:
        """List database users for one cluster. Passwords are never included.

        Args: cluster_id is the cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/users")
            return {"users": strip_passwords((body or {}).get("users", []))}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    async def do_add_user(cluster_id: str, username: str, mysql_auth_plugin: str | None = None) -> str:
        """Create a new database user on a cluster.

        Args: cluster_id is the target cluster. username is the new user's
        name. mysql_auth_plugin only applies to MySQL clusters (e.g.
        "mysql_native_password" or "caching_sha2_password").
        """

        async def call(client):
            payload: dict[str, Any] = {"name": username}
            if mysql_auth_plugin:
                payload["mysql_settings"] = {"auth_plugin": mysql_auth_plugin}
            body = await client.call("POST", f"/v2/databases/{cluster_id}/users", json=payload)
            return strip_passwords((body or {}).get("user", {"name": username}))

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
    async def do_remove_user(cluster_id: str, username: str) -> str:
        """Permanently delete one database user. Cannot be undone.

        Args: cluster_id is the cluster. username is the single, explicit user
        to delete -- there is no bulk or pattern-based form.
        """

        async def call(client):
            await client.call("DELETE", f"/v2/databases/{cluster_id}/users/{username}")
            return {"cluster_id": cluster_id, "username": username, "deleted": True}

        return await run_tool(client_factory, call)
