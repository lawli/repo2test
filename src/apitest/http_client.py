"""Profile-bound HTTP client."""

from __future__ import annotations

import contextlib
import json as _json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from apitest.profile import Profile
from apitest.taint import register_secrets

# Retry only on transports/server errors that are typically transient.
# 4xx (client errors) and 2xx/3xx are never retried — those are deterministic
# outcomes of the request, not flake.
_RETRIABLE_STATUSES = {502, 503, 504}
_RETRIABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
)
# Errors raised before any bytes reached the server: safe to retry for any method.
_CONNECT_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# The service took the connection and ended it without a usable response.
NO_RESPONSE_EXCEPTIONS = (httpx.ReadError, httpx.RemoteProtocolError)
# A retried POST/PATCH can duplicate side effects, so those only retry when the
# connection never opened, and never on a status the server already returned.
_NON_IDEMPOTENT = {"POST", "PATCH"}


def merge_headers(*layers: Mapping[str, str] | None) -> dict[str, str]:
    """Merge header layers; later layers override earlier ones. Header names are
    case-insensitive, so `Authorization` and `authorization` are one header."""
    out = httpx.Headers()
    for layer in layers:
        if layer:
            out.update(layer)
    return dict(out.items())


class HttpClient:
    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._timeout = float(profile.http.get("default_timeout_s", 30))
        retries = profile.http.get("retries") or {}
        self._retry_count = int(retries.get("count", 0))
        self._retry_backoff_s = float(retries.get("backoff_s", 0.5))
        self._client = httpx.Client(timeout=self._timeout)
        self._clients: dict[tuple[str, str | None], httpx.Client] = {}
        self._log: list[dict[str, Any]] = []
        # Calls whose request may have reached the service on any attempt (a response,
        # or a request failure after connecting). Teardown uses it to decide whether
        # a resource could exist.
        self.delivered_count = 0
        # Final status of the latest call; None when it ended without a response.
        self.last_status: int | None = None

    def _service(self, service: str) -> dict[str, Any]:
        if service not in self._profile.services:
            raise KeyError(f"service {service!r} not in profile")
        svc: dict[str, Any] = self._profile.services[service]
        return svc

    def _base(self, service: str) -> str:
        return str(self._service(service)["base_url"]).rstrip("/")

    def request(
        self,
        method: str,
        service: str,
        path: str,
        *,
        role: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        url = self._base(service) + path
        # profile-level `services.<svc>.headers` sit under explicit headers
        svc = self._service(service)
        configured_headers = (
            svc.get("roles", {}).get(role, {}).get("headers", {}) if role else svc.get("headers")
        )
        merged_headers = merge_headers(configured_headers, headers)
        register_secrets(merged_headers)
        register_secrets(json)
        register_secrets(data)
        identity = (service, role)
        if identity not in self._clients:
            self._clients[identity] = (
                self._client if not self._clients else httpx.Client(timeout=self._timeout)
            )
        client = self._clients[identity]
        idempotent = method.upper() not in _NON_IDEMPOTENT
        retriable_exc = _RETRIABLE_EXCEPTIONS if idempotent else _CONNECT_EXCEPTIONS
        attempts = self._retry_count + 1
        r: httpx.Response | None = None
        retry_log: list[str] = []
        delivered = False
        self.last_status = None
        try:
            for i in range(attempts):
                try:
                    r = client.request(
                        method,
                        url,
                        headers=merged_headers,
                        params=params or None,
                        json=json,
                        data=data,
                        files=files,
                        content=content,
                    )
                    delivered = True
                    if idempotent and r.status_code in _RETRIABLE_STATUSES and i < attempts - 1:
                        retry_log.append(f"attempt {i + 1}: status {r.status_code}")
                        time.sleep(self._retry_backoff_s * (2**i))
                        continue
                    break
                except retriable_exc as e:
                    delivered |= not isinstance(e, _CONNECT_EXCEPTIONS)
                    retry_log.append(f"attempt {i + 1}: {type(e).__name__}: {e}")
                    if i < attempts - 1:
                        time.sleep(self._retry_backoff_s * (2**i))
                        continue
                    raise
        except httpx.RequestError as exc:  # also a response that failed to decode
            delivered |= not isinstance(exc, _CONNECT_EXCEPTIONS)
            raise
        finally:
            if delivered:
                self.delivered_count += 1
        assert r is not None  # if we got here either r is set or we raised
        self.last_status = r.status_code
        register_secrets(dict(r.headers))
        with contextlib.suppress(ValueError):
            register_secrets(r.json())
        req_body: Any
        if json is not None:
            req_body = json
        elif data is not None or files:
            req_body = {**(data or {}), **{k: f"<file {v[0]}>" for k, v in (files or {}).items()}}
        else:
            req_body = (content or b"").decode("utf-8", "replace")
        self._log.append(
            {
                "service": service,
                "role": role,
                "method": method,
                "url": url,
                "status": r.status_code,
                "request_headers": merged_headers,
                "response_headers": dict(r.headers),
                "request_body": req_body,
                "response_body": r.text,
                "retries": retry_log,
            }
        )
        return r

    def get(self, service: str, path: str, **kw: Any) -> httpx.Response:
        return self.request("GET", service, path, **kw)

    def post(self, service: str, path: str, **kw: Any) -> httpx.Response:
        return self.request("POST", service, path, **kw)

    def put(self, service: str, path: str, **kw: Any) -> httpx.Response:
        return self.request("PUT", service, path, **kw)

    def delete(self, service: str, path: str, **kw: Any) -> httpx.Response:
        return self.request("DELETE", service, path, **kw)

    def drain_log(self) -> list[dict[str, Any]]:
        out, self._log = self._log, []
        return out

    def close(self) -> None:
        for client in {self._client, *self._clients.values()}:
            client.close()


def _trunc(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def format_http_log(log: list[dict[str, Any]], *, max_body: int = 2000) -> str:
    """Render HttpClient log entries for a failure message."""
    lines: list[str] = []
    for i, e in enumerate(log, start=1):
        head = f"[{i}] {e['method']} {e['url']} -> {e['status']}"
        if e.get("retries"):
            head += f"  (retries: {'; '.join(e['retries'])})"
        lines.append(head)
        body = e.get("request_body")
        if body not in (None, ""):
            text = body if isinstance(body, str) else _json.dumps(body, default=str)
            lines.append(f"    request:  {_trunc(text, max_body)}")
        lines.append(f"    response: {_trunc(str(e.get('response_body', '')), max_body)}")
    return "\n".join(lines)
