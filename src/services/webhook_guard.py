"""Webhook URL guard (v1.80.1): keep price/budget alert webhooks off private destinations.

Price-change and budget-spend alerts POST to a caller-supplied URL. Without checks that
makes the server a blind SSRF proxy (loopback, RFC1918, 169.254.169.254 metadata, Fly 6PN
``fdaa::/16`` / ``*.internal``). This module applies one set of rules:

- at registration (``validate_webhook_url`` — first line of both services' ``register()``),
  so POST /pricing/alerts, POST /usage/alerts, the MCP register tools (HTTP and STDIO)
  and the PricingAgent all share it; and
- at every delivery (``check_url_static`` in ``_fire`` plus ``PinnedWebhookTransport``),
  which resolves the host, checks every address, and connects only to the checked IP
  (rebinding-safe) while TLS still verifies the original hostname via ``sni_hostname``.

Rules: https only; no IP literal (any spelling) or resolved address that is not publicly
routable; no reserved hostnames (localhost, *.localhost, *.internal, *.flycast); hosts that
do not resolve are refused. No port restriction. Rejection messages are fixed strings that
never contain resolved IPs, DNS error text, or the hostname.

``WEBHOOK_ALLOW_PRIVATE_TARGETS`` (settings.webhook_allow_private_targets) relaxes the
scheme and destination rules for local development only; it is never request-controllable.
"""
import asyncio
import ipaddress
import logging
import re
import socket
from typing import List, Optional, Tuple, Union
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

REASON_SCHEME = "scheme_not_allowed"
REASON_MALFORMED = "malformed_url"
REASON_PRIVATE = "private_destination"
REASON_UNRESOLVABLE = "unresolvable_host"

_MESSAGES = {
    REASON_SCHEME: "Webhook URL must use https.",
    REASON_MALFORMED: "Webhook URL is malformed or has no host.",
    REASON_PRIVATE: "Webhook URL points to a private, internal or reserved destination.",
    REASON_UNRESOLVABLE: "Could not resolve host.",
}

DNS_TIMEOUT_SECONDS = 2.0

_RESERVED_NAMES = ("localhost", "internal", "flycast")
_RESERVED_SUFFIXES = (".localhost", ".internal", ".flycast")

_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT
    ipaddress.ip_network("fc00::/7"),        # IPv6 ULA (covers Fly 6PN fdaa::/16)
    ipaddress.ip_network("fec0::/10"),       # deprecated IPv6 site-local
]
_NAT64 = [
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
]
_IPV4_LEGACY_CHARS = re.compile(r"^[0-9a-fA-FxX.]+$")

_PRIVATE_WARNING = (
    "WEBHOOK_ALLOW_PRIVATE_TARGETS is ON: webhook alerts may target http:// and "
    "private/loopback/internal hosts. Local development only; never enable in production."
)


class WebhookURLRejected(ValueError):
    """A webhook URL failed the destination rules. ``str(exc)`` is a fixed, non-revealing message."""

    def __init__(self, reason: str):
        self.reason = reason
        self.message = _MESSAGES[reason]
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def private_targets_allowed() -> bool:
    """Operator opt-out (env WEBHOOK_ALLOW_PRIVATE_TARGETS), read at call time."""
    from src.config.settings import settings
    return bool(getattr(settings, "webhook_allow_private_targets", False))


def warn_if_private_targets_allowed(log: Optional[logging.Logger] = None) -> bool:
    """Log a loud startup WARNING when the opt-out is on. Returns whether it is on."""
    if private_targets_allowed():
        (log or logger).warning(_PRIVATE_WARNING)
        return True
    return False


# ---------------------------------------------------------------------------
# Address and host classification
# ---------------------------------------------------------------------------

def is_blocked_address(ip: IPAddress) -> bool:
    """True when ``ip`` is not a publicly routable unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            ip = ipaddress.IPv6Address(str(ip).split("%", 1)[0])
        packed = int(ip)
        # IPv4-compatible ::/96 (deprecated) — includes :: and ::1.
        if packed >> 32 == 0:
            return True
        if ip.ipv4_mapped is not None:
            return is_blocked_address(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return is_blocked_address(ip.sixtofour)
        if ip.teredo is not None:
            server, client = ip.teredo
            return is_blocked_address(server) or is_blocked_address(client)
        for net in _NAT64:
            if ip in net:
                return is_blocked_address(ipaddress.IPv4Address(packed & 0xFFFFFFFF))
    if (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_unspecified
    ):
        return True
    return any(ip.version == net.version and ip in net for net in _EXTRA_BLOCKED)


def _parse_ip_literal(host: str) -> Optional[IPAddress]:
    """Parse ``host`` as an IP literal in any spelling, or return None for a hostname."""
    h = host.strip("[]").split("%", 1)[0]
    if not h:
        return None
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if _IPV4_LEGACY_CHARS.match(h):
        # Legacy IPv4 spellings the OS resolver accepts: 127.1, 2130706433,
        # 0x7f000001, 0x7f.0.0.1, 0177.0.0.1.
        try:
            return ipaddress.IPv4Address(socket.inet_aton(h))
        except OSError:
            return None
    return None


def _is_reserved_name(host: str) -> bool:
    h = host.lower().rstrip(".")
    return h in _RESERVED_NAMES or h.endswith(_RESERVED_SUFFIXES)


def _host_is_private(host: str) -> bool:
    if _is_reserved_name(host):
        return True
    literal = _parse_ip_literal(host)
    return literal is not None and is_blocked_address(literal)


# ---------------------------------------------------------------------------
# Static (no-DNS) URL check
# ---------------------------------------------------------------------------

def check_url_static(url: object) -> Tuple[str, int, Optional[IPAddress]]:
    """Validate scheme, host and literal/reserved destinations without DNS.

    Returns ``(host, port, literal_ip_or_None)``. Raises WebhookURLRejected.
    """
    if not isinstance(url, str) or not url.strip():
        raise WebhookURLRejected(REASON_MALFORMED)
    allow_private = private_targets_allowed()

    try:
        split = urlsplit(url)
    except ValueError:
        raise WebhookURLRejected(REASON_MALFORMED) from None
    scheme = split.scheme.lower()
    allowed_schemes = ("http", "https") if allow_private else ("https",)
    if scheme not in allowed_schemes:
        raise WebhookURLRejected(REASON_SCHEME)
    split_host = split.hostname or ""
    if not split_host:
        raise WebhookURLRejected(REASON_MALFORMED)

    try:
        parsed = httpx.URL(url)
        httpx_host = parsed.host
    except (httpx.InvalidURL, ValueError, TypeError):
        # httpx refuses e.g. 0177.0.0.1 that the OS resolver would accept.
        if not allow_private and _host_is_private(split_host):
            raise WebhookURLRejected(REASON_PRIVATE) from None
        raise WebhookURLRejected(REASON_MALFORMED) from None
    if not httpx_host:
        if not allow_private and _host_is_private(split_host):
            raise WebhookURLRejected(REASON_PRIVATE)
        raise WebhookURLRejected(REASON_MALFORMED)

    # Judge both parsers' view of the host (closes parser differentials).
    if not allow_private and (_host_is_private(split_host) or _host_is_private(httpx_host)):
        raise WebhookURLRejected(REASON_PRIVATE)

    try:
        port = split.port
    except ValueError:
        raise WebhookURLRejected(REASON_MALFORMED) from None
    if port is None:
        port = 443 if scheme == "https" else 80
    return httpx_host, port, _parse_ip_literal(httpx_host)


# ---------------------------------------------------------------------------
# DNS resolution
# ---------------------------------------------------------------------------

async def _getaddrinfo_resolve(host: str, port: int) -> List[str]:
    """Resolve ``host`` to a deduplicated, ordered list of address strings (2s timeout)."""
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
            DNS_TIMEOUT_SECONDS,
        )
    except (socket.gaierror, OSError, asyncio.TimeoutError, UnicodeError):
        raise WebhookURLRejected(REASON_UNRESOLVABLE) from None
    addrs: List[str] = []
    for info in infos or []:
        addr = str(info[4][0])
        if addr not in addrs:
            addrs.append(addr)
    if not addrs:
        raise WebhookURLRejected(REASON_UNRESOLVABLE)
    return addrs


# Patch seam: tests replace this with a stub (looked up via the module global at call time).
resolve_host = _getaddrinfo_resolve


async def resolve_and_check(host: str, port: int) -> List[str]:
    """Return the addresses to connect to; refuse if ANY resolved address is blocked."""
    literal = _parse_ip_literal(host)
    if literal is not None:
        if not private_targets_allowed() and is_blocked_address(literal):
            raise WebhookURLRejected(REASON_PRIVATE)
        return [str(literal)]
    addrs = await resolve_host(host, port)
    if not addrs:
        raise WebhookURLRejected(REASON_UNRESOLVABLE)
    if not private_targets_allowed():
        for addr in addrs:
            try:
                ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
            except ValueError:
                raise WebhookURLRejected(REASON_PRIVATE) from None
            if is_blocked_address(ip):
                raise WebhookURLRejected(REASON_PRIVATE)
    return list(addrs)


async def validate_webhook_url(url: object) -> None:
    """Registration-time check: static rules plus DNS (fails closed on NXDOMAIN/timeout)."""
    host, port, _literal = check_url_static(url)
    await resolve_and_check(host, port)


# ---------------------------------------------------------------------------
# Delivery transport
# ---------------------------------------------------------------------------

def _default_inner_transport() -> httpx.AsyncBaseTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False)


class PinnedWebhookTransport(httpx.AsyncBaseTransport):
    """Resolve + check the destination, then connect only to a checked IP.

    The request URL host is rewritten to the pinned address; the Host header (already
    built from the original URL) is unchanged and, for https, the ``sni_hostname``
    request extension keeps TLS SNI and certificate verification on the original name.

    Connection pools are keyed by the ORIGINAL hostname: httpcore pools by (scheme,
    rewritten IP, port) and ignores ``sni_hostname``, so a single shared pool would let
    a TLS connection verified for one hostname be reused for a different hostname that
    resolves to the same IP (e.g. two webhooks behind one CDN address). Each hostname
    therefore gets its own inner transport.

    Every checked address is tried in order until one connects (restoring the
    multi-address fallback httpcore performed when it was given the hostname).

    ``inner`` (tests only) forces one shared inner transport for every hostname;
    ``inner_factory`` builds the per-hostname transports (default: no retries, no env
    proxies).
    """

    def __init__(
        self,
        inner: Optional[httpx.AsyncBaseTransport] = None,
        *,
        inner_factory=None,
    ):
        self._shared_inner = inner
        self._inner_factory = inner_factory or _default_inner_transport
        self._inners = {}

    def _inner_for(self, host: str) -> httpx.AsyncBaseTransport:
        if self._shared_inner is not None:
            return self._shared_inner
        key = host.lower()
        transport = self._inners.get(key)
        if transport is None:
            transport = self._inner_factory()
            self._inners[key] = transport
        return transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, port, literal = check_url_static(str(request.url))
        addrs = await resolve_and_check(host, port)
        inner = self._inner_for(host)
        if literal is not None:
            return await inner.handle_async_request(request)
        original_url = request.url
        last_exc: Optional[Exception] = None
        for addr in addrs:
            request.url = original_url.copy_with(host=addr.split("%", 1)[0])
            if request.url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": host}
            try:
                return await inner.handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                continue
        if last_exc is None:  # addrs is never empty; defensive
            raise WebhookURLRejected(REASON_UNRESOLVABLE)
        raise last_exc

    async def aclose(self) -> None:
        if self._shared_inner is not None:
            await self._shared_inner.aclose()
        for transport in list(self._inners.values()):
            await transport.aclose()
        self._inners.clear()
