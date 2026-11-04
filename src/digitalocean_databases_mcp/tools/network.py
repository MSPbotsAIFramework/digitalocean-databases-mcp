"""Connection pools and the firewall trusted-sources list."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from digitalocean_databases_mcp.tools._common import ClientFactory, run_tool


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_connection_pools(cluster_id: str) -> str:
        """List PgBouncer connection pools for a cluster. Empty on non-PostgreSQL engines.

        Args: cluster_id is the cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/pools")
            return {"pools": (body or {}).get("pools", [])}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_firewall_rules(cluster_id: str) -> str:
        """List a cluster's firewall trusted-sources rules.

        Args: cluster_id is the cluster to inspect.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}/firewall")
            return {"rules": (body or {}).get("rules", [])}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
    async def do_update_firewall_rules(cluster_id: str, rules: list[dict[str, str]]) -> str:
        """Replace a cluster's entire firewall trusted-sources list.

        This REPLACES the whole list -- it does not merge with existing rules.
        To add one rule, first call do_list_firewall_rules and include its
        current rules plus the new one. Args: cluster_id is the cluster.
        rules is the full desired list, each item like {"type": "ip_addr",
        "value": "1.2.3.4"} (type is one of ip_addr/droplet/k8s/tag/app).
        """

        async def call(client):
            await client.call("PUT", f"/v2/databases/{cluster_id}/firewall", json={"rules": rules})
            return {"cluster_id": cluster_id, "rules": rules}

        return await run_tool(client_factory, call)
