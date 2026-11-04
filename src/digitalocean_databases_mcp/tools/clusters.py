"""Cluster lifecycle: list, inspect, create, resize, destroy, list options."""

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from digitalocean_databases_mcp.tools._common import ClientFactory, run_tool, strip_passwords


def _trim_summary(cluster: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": cluster.get("id"),
        "name": cluster.get("name"),
        "engine": cluster.get("engine"),
        "version": cluster.get("version"),
        "status": cluster.get("status"),
        "region": cluster.get("region"),
        "size": cluster.get("size"),
        "num_nodes": cluster.get("num_nodes"),
        "tags": cluster.get("tags", []),
        "connection": strip_passwords(cluster.get("connection")) if cluster.get("connection") else None,
    }


def _trim_detail(cluster: dict[str, Any]) -> dict[str, Any]:
    detail = dict(cluster)
    for key in ("connection", "private_connection", "standby_connection"):
        if detail.get(key):
            detail[key] = strip_passwords(detail[key])
    detail.pop("password", None)
    return detail


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_list_clusters(tag_name: str | None = None, limit: int = 50, page: int = 1) -> str:
        """List database clusters visible to the current DigitalOcean token.

        Args: tag_name filters by tag. limit caps the page size (default 50,
        hard max 200). page is 1-based.
        """

        async def call(client):
            items, has_more = await client.paginate("/v2/databases", "databases", tag_name=tag_name,
                                                      limit=limit, page=page)
            return {"clusters": [_trim_summary(c) for c in items], "has_more": has_more}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def do_get_cluster(cluster_id: str) -> str:
        """Get full detail for one database cluster: connections, maintenance window.

        Args: cluster_id is the cluster's DigitalOcean id.
        """

        async def call(client):
            body = await client.call("GET", f"/v2/databases/{cluster_id}")
            return _trim_detail((body or {}).get("database", {}))

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    async def do_create_cluster(name: str, engine: str, region: str, size: str,
                                 num_nodes: int = 1, version: str | None = None,
                                 tags: list[str] | None = None) -> str:
        """Create a new database cluster. Returns the new cluster with status "creating".

        Args: name is the cluster name. engine is one of pg/mysql/mongodb/redis/
        kafka/opensearch/valkey (see do_list_cluster_options for valid values per
        region). region is a DigitalOcean region slug. size is a DigitalOcean
        database droplet size slug. num_nodes is the node count. version and
        tags are optional.
        """

        async def call(client):
            body: dict[str, Any] = {"name": name, "engine": engine, "region": region,
                                     "size": size, "num_nodes": num_nodes}
            if version:
                body["version"] = version
            if tags:
                body["tags"] = tags
            resp = await client.call("POST", "/v2/databases", json=body)
            return _trim_detail((resp or {}).get("database", {}))

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
    async def do_resize_cluster(cluster_id: str, size: str, num_nodes: int) -> str:
        """Resize a cluster's node size and/or node count.

        Args: cluster_id is the cluster to resize. size is the new size slug.
        num_nodes is the new node count.
        """

        async def call(client):
            await client.call("PUT", f"/v2/databases/{cluster_id}/resize",
                               json={"size": size, "num_nodes": num_nodes})
            return {"cluster_id": cluster_id, "resize_accepted": True}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
    async def do_destroy_cluster(cluster_id: str) -> str:
        """Permanently delete a database cluster and all its data. Cannot be undone.

        Args: cluster_id is the single, explicit cluster to delete -- there is
        no tag-based, pattern-based, or bulk form of this tool.
        """

        async def call(client):
            await client.call("DELETE", f"/v2/databases/{cluster_id}")
            return {"cluster_id": cluster_id, "deleted": True}

        return await run_tool(client_factory, call)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def do_list_cluster_options() -> str:
        """List valid engines, regions, sizes, and versions for creating a cluster."""

        async def call(client):
            body = await client.call("GET", "/v2/databases/options")
            return (body or {}).get("options", body)

        return await run_tool(client_factory, call)
