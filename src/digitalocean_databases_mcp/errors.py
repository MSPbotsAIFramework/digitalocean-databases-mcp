"""Structured error envelope (SOP SS4.2). Fixed, finite error-code vocabulary."""

import json
from typing import Literal

ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "not_found",
    "invalid_argument",
    "rate_limited",
    "upstream_error",
]

# DigitalOcean HTTP status -> our error code. Anything not listed falls back to
# upstream_error (5xx / unknown) via status_to_code().
_STATUS_MAP: dict[int, ErrorCode] = {
    401: "unauthorized",
    403: "unauthorized",
    404: "not_found",
    400: "invalid_argument",
    422: "invalid_argument",
    429: "rate_limited",
}

_RETRYABLE: set[ErrorCode] = {"rate_limited", "upstream_error"}


def status_to_code(status_code: int) -> ErrorCode:
    if status_code in _STATUS_MAP:
        return _STATUS_MAP[status_code]
    if status_code >= 500:
        return "upstream_error"
    return "upstream_error"


def envelope(code: ErrorCode, message: str, retryable: bool | None = None) -> str:
    """Build the JSON-string error envelope every tool returns on failure."""
    if retryable is None:
        retryable = code in _RETRYABLE
    return json.dumps(
        {"error": {"code": code, "message": message, "retryable": retryable}},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def not_configured(message: str = "DigitalOcean API credential is not configured") -> str:
    return envelope("not_configured", message, retryable=False)
