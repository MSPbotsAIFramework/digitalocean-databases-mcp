"""Unit tests: tools/list snapshot, error-code mapping, and per-tool behavior.

The DigitalOcean API itself is mocked (httpx.MockTransport) -- these never hit
the network (SOP SS10).
"""

import httpx
import pytest

from conftest import build_mcp, call, json_response

EXPECTED_TOOLS = {
    "do_list_clusters", "do_get_cluster", "do_create_cluster", "do_resize_cluster",
    "do_destroy_cluster", "do_list_cluster_options",
    "do_list_databases", "do_add_database", "do_remove_database",
    "do_list_users", "do_add_user", "do_remove_user",
    "do_list_connection_pools", "do_list_firewall_rules", "do_update_firewall_rules",
    "do_list_backups", "do_get_cluster_config", "do_list_replicas",
}


@pytest.fixture
def no_op_handler():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected outbound call: {request.method} {request.url}")

    return handler


async def test_tools_list_snapshot(no_op_handler):
    """18 tools, with the exact names the tech design promises (SOP SS2.1 cap)."""
    mcp = build_mcp(no_op_handler)
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert len(tools) == 18


async def test_destroy_cluster_schema_is_single_explicit_id_only(no_op_handler):
    """AC7/TC7: no tag/filter/bulk parameter, exactly one required string id."""
    mcp = build_mcp(no_op_handler)
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["do_destroy_cluster"].inputSchema
    assert set(schema.get("properties", {}).keys()) == {"cluster_id"}
    assert schema.get("required") == ["cluster_id"]
    assert schema["properties"]["cluster_id"]["type"] == "string"


async def test_remove_database_and_remove_user_are_also_single_id_only(no_op_handler):
    mcp = build_mcp(no_op_handler)
    tools = {t.name: t for t in await mcp.list_tools()}
    db_schema = tools["do_remove_database"].inputSchema
    assert set(db_schema["required"]) == {"cluster_id", "db_name"}
    user_schema = tools["do_remove_user"].inputSchema
    assert set(user_schema["required"]) == {"cluster_id", "username"}


async def test_update_firewall_rules_description_states_full_replace(no_op_handler):
    """AC8/TC8 (description half): the description says this replaces, not merges."""
    mcp = build_mcp(no_op_handler)
    tools = {t.name: t for t in await mcp.list_tools()}
    description = (tools["do_update_firewall_rules"].description or "").lower()
    assert "replaces" in description
    assert "merge" in description


async def test_descriptions_within_token_budget(no_op_handler):
    """SOP SS2.2: description <=500 chars, first line <=100 chars."""
    mcp = build_mcp(no_op_handler)
    for tool in await mcp.list_tools():
        description = tool.description or ""
        assert len(description) <= 500, f"{tool.name} description too long: {len(description)}"
        first_line = description.strip().splitlines()[0]
        assert len(first_line) <= 100, f"{tool.name} first line too long: {len(first_line)}"


# ---- error-code mapping (SOP SS4.2) --------------------------------------------------


async def test_unknown_cluster_id_maps_to_not_found():
    """AC5/TC5: a 404 from DigitalOcean becomes {"error":{"code":"not_found"}}, never a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(404, {"id": "not_found", "message": "cluster not found"})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_get_cluster", {"cluster_id": "does-not-exist"})
    assert result == {"error": {"code": "not_found", "message": "cluster not found", "retryable": False}}


async def test_get_cluster_isolated_per_token():
    """AC3/TC3: a cluster id that does not belong to this token comes back not_found,
    never another tenant's data -- DigitalOcean itself returns 404 for cross-token ids,
    and this tool must surface that as the structured not_found envelope."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer wrong-token"
        return json_response(404, {"id": "not_found", "message": "cluster not found"})

    mcp = build_mcp(handler, token="wrong-token")
    result = await call(mcp, "do_get_cluster", {"cluster_id": "belongs-to-another-token"})
    assert result["error"]["code"] == "not_found"


async def test_429_maps_to_rate_limited_and_backs_off():
    """AC4/TC4: still-429 after the retry budget -> rate_limited, retryable=true;
    the client must have actually retried (more than one outbound call)."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response(429, {"message": "too many requests"}, headers={"retry-after": "0"})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_list_clusters", {})
    assert result == {"error": {"code": "rate_limited", "message": "too many requests", "retryable": True}}
    assert len(calls) > 1, "expected the client to retry at least once before giving up"


@pytest.mark.parametrize(
    "status,expected_code",
    [(401, "unauthorized"), (403, "unauthorized"), (400, "invalid_argument"),
     (422, "invalid_argument"), (500, "upstream_error"), (503, "upstream_error")],
)
async def test_status_code_mapping_table(status, expected_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(status, {"message": "boom"})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_get_cluster", {"cluster_id": "x"})
    assert result["error"]["code"] == expected_code


# ---- cluster-specific behavior --------------------------------------------------------


async def test_list_clusters_shape_has_no_password_field():
    """AC2/TC2: id/name/engine/status/region present, no password-shaped field anywhere."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {
            "databases": [{
                "id": "c1", "name": "prod-pg", "engine": "pg", "version": "17", "status": "online",
                "region": "nyc3", "size": "db-s-2vcpu-4gb", "num_nodes": 2, "tags": ["prod"],
                "connection": {"host": "h", "port": 25060, "user": "doadmin", "database": "defaultdb",
                               "password": "super-secret", "ssl": True},
            }],
            "links": {},
        })

    mcp = build_mcp(handler)
    result = await call(mcp, "do_list_clusters", {})
    assert result["clusters"], "expected at least one cluster"
    for cluster in result["clusters"]:
        for key in ("id", "name", "engine", "status", "region"):
            assert key in cluster
        assert "password" not in (cluster.get("connection") or {})


async def test_get_cluster_strips_password_from_all_connection_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"database": {
            "id": "c1", "name": "prod-pg", "status": "online",
            "connection": {"host": "h", "password": "a"},
            "private_connection": {"host": "h2", "password": "b"},
            "standby_connection": {"host": "h3", "password": "c"},
            "maintenance_window": {"day": "sunday", "hour": "02:00"},
        }})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_get_cluster", {"cluster_id": "c1"})
    for key in ("connection", "private_connection", "standby_connection"):
        assert "password" not in result[key]
    assert result["maintenance_window"]["day"] == "sunday"


async def test_list_users_strips_password_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"users": [{"name": "doadmin", "role": "primary", "password": "secret"}]})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_list_users", {"cluster_id": "c1"})
    assert "password" not in result["users"][0]
    assert result["users"][0]["name"] == "doadmin"


async def test_create_cluster_returns_creating_status():
    """AC6/TC6."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return json_response(201, {"database": {
            "id": "new-cluster", "name": "demo", "engine": "pg", "status": "creating",
        }})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_create_cluster",
                         {"name": "demo", "engine": "pg", "region": "nyc3", "size": "db-s-1vcpu-1gb"})
    assert result["status"] == "creating"


async def test_list_clusters_default_limit_is_50_with_has_more():
    """AC10/TC10 (default side): 75 mocked items -> capped at 50, has_more=true."""

    def handler(request: httpx.Request) -> httpx.Response:
        items = [{"id": f"c{i}", "name": f"c{i}", "engine": "pg", "status": "online", "region": "nyc3"}
                  for i in range(75)]
        return json_response(200, {"databases": items, "links": {"pages": {"next": "page2"}}})

    mcp = build_mcp(handler)
    result = await call(mcp, "do_list_clusters", {})
    assert len(result["clusters"]) <= 50
    assert result["has_more"] is True


async def test_list_clusters_limit_is_hard_capped_at_200():
    """AC10/TC10 (hard-cap side): limit=500 upstream per_page is clamped to 200."""
    seen_per_page = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_per_page["value"] = request.url.params.get("per_page")
        return json_response(200, {"databases": [], "links": {}})

    mcp = build_mcp(handler)
    await call(mcp, "do_list_clusters", {"limit": 500})
    assert seen_per_page["value"] == "200"


async def test_update_firewall_rules_is_full_replace_not_merge():
    """AC8/TC8 (behavior half): exactly one outbound call, body is exactly the
    given list -- no preceding GET-and-merge."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "PUT"
        return httpx.Response(204)

    mcp = build_mcp(handler)
    rules = [{"type": "ip_addr", "value": "9.9.9.9"}]
    result = await call(mcp, "do_update_firewall_rules", {"cluster_id": "c1", "rules": rules})
    assert len(calls) == 1, "expected no preceding GET -- this tool must not merge"
    import json as _json

    assert _json.loads(calls[0].content) == {"rules": rules}
    assert result["rules"] == rules
