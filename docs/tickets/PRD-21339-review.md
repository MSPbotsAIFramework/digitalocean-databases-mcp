# PRD-21339 · Code review

## Round 1 · 2026-11-05 · reviewed dev/PRD-21339 b48f699 (base main @ 1b998d0)

Verdict: changes requested

### Findings

1. [must-fix] `src/digitalocean_databases_mcp/tools/_common.py:32` (`run_tool`), `src/digitalocean_databases_mcp/api_client.py:59` (`DOClient._request`) — no structured observability logging is implemented anywhere in the service, even though the tech design's "Non-functional > Observability" section (`docs/tickets/PRD-21339.md:186`) commits to *"structured JSON logs per request -- tool name, truncated arg summary (cluster id ok to log, token/credential never), DigitalOcean path + status code, latency, error `code` if any (SOP §6)"*, and the SOP's mandatory §6 ("日志必须谨慎...用结构化日志...记录: 工具名、入参摘要、你的 API 路径与状态码、耗时、错误 code") plus the §14 delivery checklist both require it ("结构化日志带 request-id; 不打印凭据、Header 中的 token、以及返回的敏感业务数据"). `grep -rn 'log\|Logger\|logging' src/` returns zero hits outside unrelated docstring/comment text ("logical databases"). The only logging the running service produces is uvicorn's default access log (method/path/status on `/mcp` itself), which carries none of: tool name, the DigitalOcean path/status code it called, latency, or the mapped error code -- so there is currently no way to trace what a given tenant's agent actually did against DigitalOcean, or to diagnose a production incident (which tool ran, which DO endpoint it hit, why it failed).

   The Self-test section's "SOP §14 checklist" paragraph (`docs/tickets/PRD-21339.md:344-351`) states the "Container and sensitive-info items are satisfied per steps 3-4 above" and otherwise claims the checklist is covered, but step 3/4 are only the secret scan and the docker build -- neither addresses the "日志与可观测性" (§6) half of that checklist bucket, which is silently unmet rather than called out as a gap.

   Fix: add structured per-tool-call logging (e.g. wrapped around `run_tool` in `tools/_common.py` and/or `DOClient._request` in `api_client.py`) emitting at minimum: tool name, a redacted argument summary, the DigitalOcean request path and status code, latency, and the mapped error code on failure -- with the bearer token/credential never included, consistent with how the rest of this codebase already treats the credential.

### Fixes

1. [must-fix #1] Added `src/digitalocean_databases_mcp/observability.py`: stdlib `logging` (no new dependency), emitting one structured JSON line per outbound DigitalOcean request (`do_request`: method, path, status code, latency_ms -- including retried attempts, with `status_code: null, error: "timeout"|"transport_error"` for a failed attempt) and one per MCP tool invocation (`tool_call`: tool name, redacted arg summary, latency_ms, outcome, error_code). Wired into `DOClient._request` (`api_client.py`) for the per-request line and `run_tool` (`tools/_common.py`) for the per-tool-call line -- the exact two locations this finding named. All 18 tool registration call sites now pass their own tool name and argument dict into `run_tool`. `redact_args()` drops any argument key containing `password`/`token`/`credential`/`secret`/`authorization`/`api_key`/`apikey` (case-insensitive) before logging; no current tool argument is credential-shaped, this is defense in depth. The bearer token is never a tool argument (it lives only in the request header / `DOClient`), so it was never at risk of being logged by this change, and the `redact_args` test and a live-server smoke test both assert the token string never appears in the log.

   Verification: `uv run pytest` 34/34 passed (27 pre-existing + 7 new in `tests/test_observability.py`, covering: successful call logs both lines with the expected fields; a 404 logs `error_code: not_found`; the bearer token is never present in any log record; `log_tool_call` emits the exact expected JSON shape; a transport timeout logs 4 `do_request` lines -- 1 initial + 3 retries -- each with `status_code: null`). Also ran a live smoke test: started the server (`uv run python -m digitalocean_databases_mcp`), confirmed `/health` 200, missing-header 401, `initialize` -> `tools/list` returns the same 18 tools, then called `do_list_clusters` with a placeholder token and confirmed the server log contains both a `do_request` line (`path: /v2/databases, status_code: 401`) and a `tool_call` line (`tool: do_list_clusters, error_code: unauthorized`), with the placeholder token absent from the log (`grep -c <token> server.log` = 0).

   Commit: `87d45d3` on `dev/PRD-21339` (branch head at review round 1 was `e451624`).

## Round 2 · 2026-11-05 · reviewed dev/PRD-21339 3ba1173 (base main @ 1b998d0)

Verdict: changes requested

### Round 1 fixes re-checked

Fix #1 (structured logging) re-verified against `b48f699...3ba1173`: `src/digitalocean_databases_mcp/observability.py` exists, is wired into `DOClient._request` (`api_client.py:77,84,90`) and `run_tool` (`tools/_common.py:50,60`), and all 18 tool registrations in `tools/{clusters,databases,network,ops,users}.py` now pass `tool_name`/`args` into `run_tool`. Re-ran `uv run pytest -q`: 34/34 passed, matching the claim in the `[DEV]` stub. `tests/test_observability.py` covers the success path, a mapped error (`404` -> `not_found`), token-never-logged, the exact JSON shape, and retried transport failures (4 `do_request` lines, `status_code: null`) -- each assertion checked against the actual test code, not just the stub's summary. This part of round 1's must-fix is resolved: a `tool_call` and matching `do_request` line are now emitted for every call.

Re-reading the same design commitment and SOP bucket that round 1's finding quoted (`docs/tickets/PRD-21339.md:186`; SOP §6/§14) against the actual fix turned up two parts of that same commitment the fix does not satisfy — see Findings below. Neither is a new area of the code; both are in the module round 1 already named.

### Findings

1. [must-fix] `src/digitalocean_databases_mcp/observability.py:30-39` (`log_tool_call`), `:42-56` (`log_do_request`) — neither log line carries a request-id / correlation id, so SOP §6's requirement is only half met: §6 says structured logs must have "每条带 request-id / 关联 id，便于串联一次请求" (`reference/vendor-mcp-development-sop.md:282`), and the §14 delivery checklist item is literally "结构化日志带 request-id" (`reference/vendor-mcp-development-sop.md:484`) — the same checklist bucket round 1's finding quoted as unmet. With retries, one tool call now produces 1 `tool_call` line plus up to 4 `do_request` lines (verified by `test_transport_failure_logs_do_request_without_status_code`), and under concurrent requests (this is a shared service process per `api_client.py`'s own docstring on request-scoped clients) there is nothing in any log line that ties a given `do_request` line back to the `tool_call` that produced it, or distinguishes interleaved calls from different tenants. Fix: generate one id per tool invocation (e.g. `uuid4().hex[:12]` in `run_tool`) and pass it into both `log_tool_call` and every `log_do_request` call made while handling that invocation (threading it through the `call()` closure/`DOClient`, or a `contextvars.ContextVar` set for the duration of `run_tool`).

2. [must-fix] `src/digitalocean_databases_mcp/observability.py:25-27` (`redact_args`), `:35` (`args=redact_args(args)`) — the arg summary logged with every `tool_call` is not truncated. The tech design's "Non-functional > Observability" section (`docs/tickets/PRD-21339.md:186`) specifically commits to a *"truncated arg summary"*, not just a redacted one; `redact_args` only drops credential-shaped keys and returns the rest of the dict verbatim, with no size cap. Tools that take list/dict arguments log them in full: `do_update_firewall_rules(cluster_id, rules: list[dict[str, str]])` (`tools/network.py`) and `do_create_cluster(..., tags: list[str] | None)` (`tools/clusters.py`) both pass the raw list straight into `log_tool_call`'s `args`, so a caller passing a large rule set or tag list produces an unbounded log line — exactly what "truncated" was meant to bound against, and the kind of thing that causes production log-pipeline problems (oversized lines dropped or truncated mid-JSON by the log shipper) during an incident, which is when this logging is supposed to help. Fix: cap the serialized args (e.g. JSON-dump then truncate to a fixed length with a marker, mirroring the existing `MAX_RESULT_CHARS` pattern in `tools/_common.py:10`) before passing them to `log_tool_call`.

### Fixes

1. [must-fix #1] `src/digitalocean_databases_mcp/observability.py`: added `new_request_id()` (`uuid4().hex[:12]`); `log_tool_call` and `log_do_request` now take a required `request_id` kwarg emitted as the first field after `event`. `tools/_common.py`'s `run_tool` generates one `request_id` per invocation and sets `client.request_id` on the freshly-constructed `DOClient` before `async with client`, covering both the `not_configured` early return and the normal/error `finally` path. `api_client.py`'s `DOClient.__init__` now carries `self.request_id: str | None = None`; all three `log_do_request` call sites in `_request` (timeout, transport error, response) pass `request_id=self.request_id`, so a retry storm's lines all share the id of the `tool_call` that triggered them. `DOClient` is still built fresh per tool invocation by `server.py`'s `client_factory()` (`return DOClient(token, settings.do_api_base_url)`), so the instance attribute never leaks across tenants or concurrent calls.

   Verification: `tests/test_observability.py` asserts `do_requests[i]["request_id"] == tool_calls[0]["request_id"]` on the success path, the mapped-error path, and the 4-attempt retry path (`test_transport_failure_logs_do_request_without_status_code`, asserts exactly one distinct `request_id` across all 4 lines). Live re-check: a real `do_list_clusters` call against a placeholder token produced matching `request_id":"78516b97242c"` on both the `do_request` and `tool_call` lines.

2. [must-fix #2] `observability.py`: `redact_args` now caps each remaining argument value's logged length (`_MAX_ARG_VALUE_CHARS = 200`) after dropping credential-shaped keys — non-string values are JSON-dumped first, then truncated with an `...<truncated N more chars>` marker, mirroring the existing `MAX_RESULT_CHARS` pattern in `tools/_common.py`. This bounds `do_update_firewall_rules`' `rules` and `do_create_cluster`'s `tags`, the two unbounded list arguments round 2 named.

   Verification: `test_redact_args_truncates_oversized_values` — a 50-item `rules` list collapses to a capped string strictly shorter than the untruncated JSON, with a `"truncated"` marker; `cluster_id` passes through unchanged.

   Tests: `uv run pytest -q` 35/35 (34 pre-existing + 1 new: `test_redact_args_truncates_oversized_values`; the three correlation tests above were updated in place, not added). Commit: `4a08267` on `dev/PRD-21339` (branch head at review round 2 was `784183e`).

## Round 3 · 2026-11-05 · reviewed dev/PRD-21339 7911349 (base main @ 1b998d0)

Status: frozen @ 7911349

Verdict: approved

### Round 2 fixes re-checked

Independently re-read `observability.py`, `api_client.py`, and `tools/_common.py` against both round 2 findings (not just the `[DEV]` stub's summary):

- Must-fix 1 (request-id correlation): confirmed `new_request_id()` is called once per `run_tool` invocation and the same id is set on `client.request_id` before any `DOClient._request` call, then read back by all three `log_do_request` call sites (timeout, transport error, response) in `api_client.py:81,88-90,96`. `server.py:104`'s `client_factory()` builds a fresh `DOClient` per call, so the instance attribute cannot bleed across tenants/concurrent requests. Resolved.
- Must-fix 2 (truncated arg summary): confirmed `redact_args` now calls `_truncate_value` on every remaining value after credential-key dropping, capping each at 200 chars with an overflow marker; both named call sites (`do_update_firewall_rules`'s `rules`, `do_create_cluster`'s `tags`) go through `redact_args` before `log_tool_call`. Resolved.

Independent verification run by the reviewer (not just re-stating the `[DEV]` stub): `uv sync && uv run pytest -q` on `HEAD` (`7911349`) → `35 passed`, matching the stub's claim. Chinese-character scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) over the full tree, excluding `docs/tickets/PRD-21339-review.md` (which quotes the Chinese-language SOP verbatim for citation and is the review leg's own record) → no hits. README's `## MSPbots Connector` … `## Authentication headers` section diffed byte-for-byte against `reference/readme-connector-section.md` → identical (unchanged this round). `gitleaks` not installed on this host, skipped per `gates.md` ("有就跑").

### Findings

None — no must-fix or suggestion. Both round 2 must-fix items are correctly and completely resolved, with test coverage, and nothing else changed.
