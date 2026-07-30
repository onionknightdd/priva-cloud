"""HTTP client policy for calls made by the runner itself.

Tenant workloads receive ``HTTP_PROXY``/``HTTPS_PROXY`` from the operator.  For
platform-owned external calls we consume only the proxy that matches the target
scheme and pass it to HTTPX explicitly.  Keeping ``trust_env=False`` is
intentional: it prevents a broad ``NO_PROXY`` value from silently turning a
proxied request into a direct connection.

When the operator has not injected a proxy (for example during local
development), the client remains direct.  In-cluster enforcement is provided by
the runner's NetworkPolicy, so a missing proxy cannot become an egress bypass in
the deployed workload.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx


def _platform_proxy_for(url: str) -> str | None:
    """Return the operator-injected proxy for an HTTP(S) target."""
    scheme = urlsplit(url).scheme.lower()
    if scheme == "https":
        name = "HTTPS_PROXY"
    elif scheme == "http":
        name = "HTTP_PROXY"
    else:
        return None

    value = os.environ.get(name, "").strip()
    if not value:
        return None

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an http(s) proxy URL")
    return value


def external_async_client(url: str, **kwargs: Any) -> httpx.AsyncClient:
    """Build an external client that cannot be diverted by ``NO_PROXY``."""
    return httpx.AsyncClient(
        proxy=_platform_proxy_for(url),
        trust_env=False,
        **kwargs,
    )


__all__ = ["external_async_client"]
