"""Thin async wrapper over DigitalOcean's public Databases REST API.

One instance is built per incoming MCP request from the credential read off the
request's ContextVar (see server.py) -- never a shared module-level client that
would mix one tenant's token into another tenant's request (SOP SS3.3/SS3.4).
"""

import asyncio
import time
from typing import Any

import httpx

from digitalocean_databases_mcp.observability import log_do_request

MAX_RETRIES = 3
BACKOFF_SECONDS = 1.0
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class DOApiError(Exception):
    """Raised for any DigitalOcean API response >= 400, or a transport failure."""

    def __init__(self, status_code: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


def clamp_limit(limit: int) -> int:
    if limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


class DOClient:
    """One instance per MCP request. Holds the caller's own bearer token."""

    def __init__(self, token: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        # `transport` has no default beyond httpx's own: it exists so tests can
        # substitute httpx.MockTransport instead of hitting the network.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=READ_TIMEOUT, pool=READ_TIMEOUT),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DOClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One outbound call with bounded retry+backoff on 429/5xx (SOP SS5).

        Every attempt -- including retries -- gets its own structured
        `do_request` log line (path, status code, latency; never headers or
        body) so a retry storm or a slow upstream is visible per-request,
        not just on the final outcome (SOP SS6/SS14).
        """
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                last_exc = exc
                log_do_request(method, path, None, (time.perf_counter() - start) * 1000, error="timeout")
                if attempt == MAX_RETRIES:
                    raise DOApiError(0, f"Timed out calling DigitalOcean API: {exc}") from exc
                await asyncio.sleep(BACKOFF_SECONDS * (2**attempt))
                continue
            except httpx.TransportError as exc:
                last_exc = exc
                log_do_request(method, path, None, (time.perf_counter() - start) * 1000, error="transport_error")
                if attempt == MAX_RETRIES:
                    raise DOApiError(0, f"Network error calling DigitalOcean API: {exc}") from exc
                await asyncio.sleep(BACKOFF_SECONDS * (2**attempt))
                continue

            log_do_request(method, path, resp.status_code, (time.perf_counter() - start) * 1000)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRIES:
                    return resp
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                await asyncio.sleep(retry_after if retry_after is not None else BACKOFF_SECONDS * (2**attempt))
                continue
            return resp
        if last_exc is not None:
            raise DOApiError(0, str(last_exc)) from last_exc
        raise DOApiError(0, "Request failed with no response")  # pragma: no cover - defensive

    async def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any] | None:
        """Call an endpoint; return the parsed JSON body or None for 204 No Content.

        Raises DOApiError for any status >= 400 after retries are exhausted.
        """
        resp = await self._request(method, path, **kwargs)
        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            message = _extract_message(resp)
            if resp.status_code == 429:
                raise DOApiError(429, message, retry_after=_parse_retry_after(resp.headers.get("retry-after")))
            raise DOApiError(resp.status_code, message)
        if not resp.content:
            return None
        return resp.json()

    async def paginate(self, path: str, item_key: str, *, tag_name: str | None = None,
                        limit: int = DEFAULT_LIMIT, page: int = 1) -> tuple[list[dict[str, Any]], bool]:
        """GET a paginated list endpoint. Returns (items capped at `limit`, has_more)."""
        capped = clamp_limit(limit)
        params: dict[str, Any] = {"page": max(page, 1), "per_page": capped}
        if tag_name:
            params["tag_name"] = tag_name
        body = await self.call("GET", path, params=params)
        items = (body or {}).get(item_key, []) if body else []
        links = (body or {}).get("links", {}) if body else {}
        has_more = bool(links.get("pages", {}).get("next"))
        return items[:capped], has_more


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


def _extract_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") if isinstance(body, dict) else None
        if msg:
            return str(msg)
    except Exception:
        pass
    return f"DigitalOcean API returned HTTP {resp.status_code}"
