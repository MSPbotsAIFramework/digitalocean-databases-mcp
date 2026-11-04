# digitalocean-databases-mcp

An MCP (Model Context Protocol) server that lets an AI agent inspect and
manage a tenant's [DigitalOcean Managed Databases](https://docs.digitalocean.com/products/databases/)
clusters: clusters, logical databases, users, connection pools, firewall
rules, backups, engine config, and read-only replicas.

Transport: MCP Streamable HTTP (`POST /mcp`), stateless, one credential per
request. Stack: Python 3.12, [FastMCP](https://github.com/modelcontextprotocol/python-sdk),
`httpx`. License: Apache-2.0.

---

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Type | Meaning | Where to get it |
|---|---|---|---|---|
| `X-DigitalOcean-Api-Key` | yes | password | DigitalOcean personal access token, sent as `Authorization: Bearer <token>` to `api.digitalocean.com` | DigitalOcean Control Panel -> Account -> API -> Tokens. Any account holder can self-generate one; no partner/paid gate. |

The credential is read fresh from this header on every request. It is never
cached, logged, written to disk, or shared between requests (see "Operating
model" below).

## Tools

18 tools, prefix `do_`, mapped onto DigitalOcean's public Databases REST API
(`https://api.digitalocean.com/v2/databases/...`).

| Tool | Semantics | What it does |
|---|---|---|
| `do_list_clusters(tag_name?, limit=50, page=1)` | read-only | List database clusters |
| `do_get_cluster(cluster_id)` | read-only | Full detail for one cluster |
| `do_create_cluster(name, engine, region, size, num_nodes=1, version?, tags?)` | write | Create a cluster |
| `do_resize_cluster(cluster_id, size, num_nodes)` | write | Resize a cluster |
| `do_destroy_cluster(cluster_id)` | **destructive** | Delete a cluster (explicit id only) |
| `do_list_cluster_options()` | read-only | Valid engines/regions/sizes/versions |
| `do_list_databases(cluster_id)` | read-only | List logical databases |
| `do_add_database(cluster_id, db_name)` | write | Create a logical database |
| `do_remove_database(cluster_id, db_name)` | **destructive** | Delete a logical database |
| `do_list_users(cluster_id)` | read-only | List database users (no passwords) |
| `do_add_user(cluster_id, username, mysql_auth_plugin?)` | write | Create a database user |
| `do_remove_user(cluster_id, username)` | **destructive** | Delete a database user |
| `do_list_connection_pools(cluster_id)` | read-only | List PgBouncer connection pools (PostgreSQL only) |
| `do_list_firewall_rules(cluster_id)` | read-only | List firewall trusted-sources |
| `do_update_firewall_rules(cluster_id, rules)` | **destructive** (full replace) | Replace the entire firewall trusted-sources list |
| `do_list_backups(cluster_id)` | read-only | List backups (PostgreSQL/MySQL only) |
| `do_get_cluster_config(cluster_id)` | read-only | Engine-specific config |
| `do_list_replicas(cluster_id)` | read-only | List read-only replicas |

**v1 scope**: clusters, logical databases, users, connection pools, firewall,
backups, config, replicas across PostgreSQL/MySQL/MongoDB-shaped operations.
Engine-specific surfaces (Kafka topics, OpenSearch indexes, Valkey/MySQL
engine settings), autoscaling, online migration, and a handful of other
lifecycle actions are out of scope for v1 -- see
`docs/tickets/PRD-21339.md` for the full list and why each was deferred.

## Operating model

```
Agent -> Gateway (injects X-DigitalOcean-Api-Key from the tenant's stored credential)
      -> POST /mcp  (this service)
          -> https://api.digitalocean.com/v2/databases/...
      <- tool result (JSON string; structured error envelope on failure)
```

- Stateless: no session stickiness, no local persistent state. `stateless_http=True`.
- One credential per request, read from the header into a request-scoped
  context variable and discarded when the request completes. Never an
  environment-variable fallback.
- Errors are a structured JSON envelope `{"error":{"code","message","retryable"}}`
  with a fixed code vocabulary (`not_configured` / `unauthorized` / `not_found`
  / `invalid_argument` / `rate_limited` / `upstream_error`) -- tools never
  raise.

## Local development

```bash
uv sync
uv run pytest

# Run the server
uv run python -m digitalocean_databases_mcp
# or: docker compose up --build

# Exercise it with your own DigitalOcean token
curl -X POST http://localhost:8080/mcp \
  -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -H 'X-DigitalOcean-Api-Key: <your token>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"do_list_clusters","arguments":{}}}'
```

```bash
docker build --platform linux/amd64 -t digitalocean-databases-mcp:dev .
```
