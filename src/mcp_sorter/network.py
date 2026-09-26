from urllib.parse import urlsplit

import httpx

from mcp_sorter.settings import Settings


class NetworkDenied(ValueError):
    """An operation would cross the configured network boundary."""


def authorize_url(url: str, settings: Settings) -> None:
    if settings.mode != "live":
        raise NetworkDenied("External connections are disabled in demo mode")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise NetworkDenied("An HTTPS URL without credentials or fragments is required")
    if parsed.hostname not in settings.allowed_hosts:
        raise NetworkDenied("The destination is not in the operator's allowlist")
    if parsed.port not in (None, 443):
        raise NetworkDenied("Only HTTPS on port 443 is permitted")


class GuardedTransport(httpx.BaseTransport):
    def __init__(self, settings: Settings, inner: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.inner = inner or httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        authorize_url(str(request.url), self.settings)
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


def client(settings: Settings, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        transport=GuardedTransport(settings, transport),
        timeout=settings.request_timeout,
        follow_redirects=False,
        trust_env=False,
    )
