"""HTTP seam for the live provider adapter (PHASE2B3 §3, D-2b3-1 = stdlib).

The adapter must be testable offline, with no network and no credentials, for every outcome the network can
produce. So the one place that opens a socket is isolated behind `Transport`, and the tests inject a fake.

Deliberately stdlib-only: adding an HTTP client would relax four architecture guards that currently forbid
`openai`, `httpx`, `requests` and `aiohttp` across the whole tree. `run_with_retry` already owns the retry
policy, so an SDK's backoff would be redundant anyway.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Protocol

USER_AGENT: Final[str] = "baaki-recovery/2b-3"


class TransportError(StrEnum):
    """Failures that happen before any HTTP status exists."""

    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class TransportOutcome:
    """One attempt's result: either an HTTP response, or a pre-HTTP failure. Never an exception."""

    status_code: int | None = None
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    error: TransportError | None = None
    error_class: str | None = None  # sanitized exception class name only; never a message body

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


class Transport(Protocol):
    def post_json(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float
    ) -> TransportOutcome: ...


class UrllibTransport:
    """The only component in `src/` that opens a socket to a model provider."""

    def post_json(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float
    ) -> TransportOutcome:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 — https URL is a module constant, never caller-supplied
            url, data=body, method="POST", headers={**headers, "User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                return TransportOutcome(
                    status_code=int(resp.status),
                    body=resp.read(),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as e:  # a real HTTP status, including 4xx/5xx
            return TransportOutcome(
                status_code=int(e.code),
                body=e.read(),
                headers={k.lower(): v for k, v in (e.headers or {}).items()},
                error_class=type(e).__name__,
            )
        except TimeoutError as e:
            return TransportOutcome(error=TransportError.TIMEOUT, error_class=type(e).__name__)
        except (urllib.error.URLError, OSError) as e:
            inner = getattr(e, "reason", None)
            if isinstance(inner, TimeoutError | socket.timeout):
                return TransportOutcome(error=TransportError.TIMEOUT, error_class=type(e).__name__)
            return TransportOutcome(error=TransportError.UNAVAILABLE, error_class=type(e).__name__)


__all__ = ["Transport", "TransportError", "TransportOutcome", "UrllibTransport", "USER_AGENT"]
