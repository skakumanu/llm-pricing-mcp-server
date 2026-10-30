"""v1.80.1 — unit tests for src/services/webhook_guard.py (webhook SSRF guard).

Covers the URL rules (AC2-AC5, AC9, AC15, AC16), address classification, the real
getaddrinfo resolver (patched loop), PinnedWebhookTransport (pinning, Host/SNI, AC6
rebinding at delivery), and real local deliveries over plain HTTP (AC8 redirects not
followed, AC15 opt-out delivery) and TLS (hostname verification still on the original
name). DNS is stubbed by the autouse ``webhook_dns`` fixture in tests/conftest.py.
"""
import asyncio
import datetime
import http.server
import inspect
import ipaddress
import json
import logging
import re
import socket
import ssl
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config.settings import Settings, settings  # noqa: E402
from src.services import webhook_guard  # noqa: E402
from src.services.webhook_guard import (  # noqa: E402
    REASON_MALFORMED, REASON_PRIVATE, REASON_SCHEME, REASON_UNRESOLVABLE,
    PinnedWebhookTransport, WebhookURLRejected, check_url_static, is_blocked_address,
    validate_webhook_url,
)
from src.services.pricing_alerts import PricingAlertService, verify_webhook_signature  # noqa: E402

IP_PATTERN = re.compile(r"\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F]*:[0-9a-fA-F:]+")

# AC2: refused on every surface (registration). http://localhost is a scheme refusal.
AC2_URLS = [
    ("http://localhost/x", REASON_SCHEME),
    ("https://LOCALHOST./x", REASON_PRIVATE),
    ("https://foo.localhost/x", REASON_PRIVATE),
    ("https://127.0.0.1/x", REASON_PRIVATE),
    ("https://127.1/x", REASON_PRIVATE),
    ("https://2130706433/x", REASON_PRIVATE),
    ("https://0177.0.0.1/x", REASON_PRIVATE),
    ("https://0x7f000001/x", REASON_PRIVATE),
    ("https://0x7f.0.0.1/x", REASON_PRIVATE),
    ("https://0.0.0.0/x", REASON_PRIVATE),
    ("https://[::1]/x", REASON_PRIVATE),
    ("https://[::ffff:127.0.0.1]/x", REASON_PRIVATE),
    ("https://10.0.0.5/x", REASON_PRIVATE),
    ("https://172.16.0.1/x", REASON_PRIVATE),
    ("https://172.31.255.255/x", REASON_PRIVATE),
    ("https://192.168.1.1/x", REASON_PRIVATE),
    ("https://169.254.169.254/latest/meta-data/", REASON_PRIVATE),
    ("https://[fe80::1]/x", REASON_PRIVATE),
    ("https://[fe80::1%25eth0]/x", REASON_PRIVATE),
    ("https://[fdaa:0:1::2]/x", REASON_PRIVATE),
    ("https://100.64.0.1/x", REASON_PRIVATE),
    ("https://my-app.internal/x", REASON_PRIVATE),
    ("https://MY-APP.INTERNAL./x", REASON_PRIVATE),
    ("https://my-app.flycast/x", REASON_PRIVATE),
    ("https://224.0.0.1/x", REASON_PRIVATE),
    ("https://255.255.255.255/x", REASON_PRIVATE),
]

# AC3: refused (scheme / malformed), nothing stored.
AC3_URLS = [
    ("file:///etc/passwd", REASON_SCHEME),
    ("ftp://example.com/x", REASON_SCHEME),
    ("gopher://example.com/x", REASON_SCHEME),
    ("data:text/plain,hi", REASON_SCHEME),
    ("javascript:alert(1)", REASON_SCHEME),
    ("wss://example.com/x", REASON_SCHEME),
    ("example.com/hook", REASON_SCHEME),
    ("https:///nohost", REASON_MALFORMED),
    ("", REASON_MALFORMED),
    ("   ", REASON_MALFORMED),
    ("http://example.com/x", REASON_SCHEME),
    ("https://example.com:99999/x", REASON_MALFORMED),
]


@pytest.fixture
def allow_private(monkeypatch):
    monkeypatch.setattr(settings, "webhook_allow_private_targets", True)


@pytest.fixture(autouse=True)
def _default_opt_out_off(monkeypatch):
    monkeypatch.setattr(settings, "webhook_allow_private_targets", False)


async def _reason(url):
    with pytest.raises(WebhookURLRejected) as info:
        await validate_webhook_url(url)
    return info.value


# ---------------------------------------------------------------------------
# URL rules
# ---------------------------------------------------------------------------

class TestURLRules:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,reason", AC2_URLS)
    async def test_ac2_private_and_reserved_refused(self, url, reason):
        exc = await _reason(url)
        assert exc.reason == reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,reason", AC3_URLS)
    async def test_ac3_bad_scheme_or_malformed_refused(self, url, reason):
        exc = await _reason(url)
        assert exc.reason == reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [None, 123, b"https://example.com/x"])
    async def test_non_string_is_malformed(self, value):
        assert (await _reason(value)).reason == REASON_MALFORMED

    @pytest.mark.asyncio
    async def test_ac4_userinfo_judged_by_real_host(self, webhook_dns):
        assert (await _reason("https://example.com@127.0.0.1/x")).reason == REASON_PRIVATE
        # A public host with userinfo is fine; the resolver sees the real host only.
        await validate_webhook_url("https://user:pw@hooks.example.com/x")
        assert webhook_dns.calls[-1][0] == "hooks.example.com"
        # ...and userinfo naming a private host does not matter either way.
        await validate_webhook_url("https://127.0.0.1:pw@hooks.example.com/x")

    @pytest.mark.asyncio
    async def test_ac5_rebind_name_resolving_private_refused(self, webhook_dns):
        webhook_dns["rebind.test"] = ["127.0.0.1"]
        assert (await _reason("https://rebind.test/x")).reason == REASON_PRIVATE

    @pytest.mark.asyncio
    async def test_ac5_any_blocked_address_refused(self, webhook_dns):
        webhook_dns["multi.test"] = ["93.184.216.34", "10.0.0.1"]
        assert (await _reason("https://multi.test/x")).reason == REASON_PRIVATE

    @pytest.mark.asyncio
    async def test_unparseable_resolved_address_counts_as_blocked(self, webhook_dns):
        webhook_dns["weird.test"] = ["not-an-ip"]
        assert (await _reason("https://weird.test/x")).reason == REASON_PRIVATE

    @pytest.mark.asyncio
    async def test_resolved_ipv6_ula_refused(self, webhook_dns):
        webhook_dns["sixpn.test"] = ["fdaa:0:1:a7b::2"]
        assert (await _reason("https://sixpn.test/x")).reason == REASON_PRIVATE

    @pytest.mark.asyncio
    async def test_ac9_and_ac16_messages_reveal_nothing(self, webhook_dns):
        webhook_dns["rebind.test"] = ["169.254.169.254"]
        webhook_dns.unresolvable.add("nx.test")
        private = await _reason("https://rebind.test/x")
        unresolvable = await _reason("https://nx.test/x")
        assert unresolvable.reason == REASON_UNRESOLVABLE
        assert unresolvable.message == "Could not resolve host."
        for exc in (private, unresolvable):
            assert not IP_PATTERN.search(exc.message)
            assert "rebind" not in exc.message and "nx.test" not in exc.message
            assert str(exc) == exc.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", [
        "https://hooks.slack.com/services/T000/B000/XXXX",
        "https://hooks.example.org:8443/inbound/alerts/abc/?a=1&b=two",
        "https://api.example.com:1/x",
        "https://93.184.216.34/x",
        "https://[2606:2800:220:1:248:1893:25c8:1946]/x",
        "HTTPS://Example.COM/x",
    ])
    async def test_public_https_accepted_any_port_path_query(self, url):
        await validate_webhook_url(url)

    def test_check_url_static_returns_host_port_literal(self):
        assert check_url_static("https://Example.com/x") == ("example.com", 443, None)
        assert check_url_static("https://example.com:8443/x")[1] == 8443
        host, port, literal = check_url_static("https://93.184.216.34/x")
        assert literal == ipaddress.ip_address("93.184.216.34")


# ---------------------------------------------------------------------------
# Address classification
# ---------------------------------------------------------------------------

class TestIsBlockedAddress:
    @pytest.mark.parametrize("addr", [
        "127.0.0.1", "127.255.255.254", "::1", "0.0.0.0", "0.1.2.3", "::",
        "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
        "169.254.169.254", "fe80::1", "100.64.0.1", "100.127.255.255",
        "fc00::1", "fdaa::1", "fdaa:0:1::2", "fec0::1",
        "224.0.0.1", "239.1.1.1", "ff02::1", "ff0e::1",
        "240.0.0.1", "255.255.255.255", "192.0.2.1", "198.18.0.1",
        "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::127.0.0.1", "::8.8.8.8",
        "2002:7f00:1::", "2002:a9fe:a9fe::",
        "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "64:ff9b:1::a00:1",
    ])
    def test_blocked(self, addr):
        assert is_blocked_address(ipaddress.ip_address(addr)) is True

    @pytest.mark.parametrize("addr", [
        "93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111",
        "2a00:1450:4001:80b::200e", "::ffff:8.8.8.8", "2002:808:808::", "64:ff9b::808:808",
    ])
    def test_allowed(self, addr):
        assert is_blocked_address(ipaddress.ip_address(addr)) is False

    def test_scoped_ipv6_is_judged_without_scope(self):
        assert is_blocked_address(ipaddress.ip_address("fe80::1%eth0")) is True


# ---------------------------------------------------------------------------
# Real resolver (loop.getaddrinfo patched — never real DNS)
# ---------------------------------------------------------------------------

def _info(addr, family=socket.AF_INET):
    sockaddr = (addr, 443) if family == socket.AF_INET else (addr, 443, 0, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


class TestGetaddrinfoResolve:
    @pytest.mark.asyncio
    async def test_dedupes_in_order(self, monkeypatch):
        loop = asyncio.get_running_loop()

        async def fake(host, port, **kw):
            return [_info("93.184.216.34"), _info("93.184.216.34"),
                    _info("2606:2800:220:1::1", socket.AF_INET6)]
        monkeypatch.setattr(loop, "getaddrinfo", fake)
        assert await webhook_guard._getaddrinfo_resolve("example.com", 443) == [
            "93.184.216.34", "2606:2800:220:1::1",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [socket.gaierror(-2, "Name or service not known"), OSError("boom")])
    async def test_errors_become_unresolvable(self, monkeypatch, error):
        loop = asyncio.get_running_loop()

        async def fake(host, port, **kw):
            raise error
        monkeypatch.setattr(loop, "getaddrinfo", fake)
        with pytest.raises(WebhookURLRejected) as info:
            await webhook_guard._getaddrinfo_resolve("nx.example", 443)
        assert info.value.reason == REASON_UNRESOLVABLE
        assert "Name or service" not in info.value.message
        assert info.value.__cause__ is None and info.value.__suppress_context__

    @pytest.mark.asyncio
    async def test_timeout_becomes_unresolvable(self, monkeypatch):
        loop = asyncio.get_running_loop()

        async def slow(host, port, **kw):
            await asyncio.sleep(5)
        monkeypatch.setattr(loop, "getaddrinfo", slow)
        monkeypatch.setattr(webhook_guard, "DNS_TIMEOUT_SECONDS", 0.05)
        with pytest.raises(WebhookURLRejected) as info:
            await webhook_guard._getaddrinfo_resolve("slow.example", 443)
        assert info.value.reason == REASON_UNRESOLVABLE

    @pytest.mark.asyncio
    async def test_empty_result_is_unresolvable(self, monkeypatch):
        loop = asyncio.get_running_loop()

        async def empty(host, port, **kw):
            return []
        monkeypatch.setattr(loop, "getaddrinfo", empty)
        with pytest.raises(WebhookURLRejected):
            await webhook_guard._getaddrinfo_resolve("empty.example", 443)

    @pytest.mark.asyncio
    async def test_mixed_result_through_validate_is_refused(self, monkeypatch):
        loop = asyncio.get_running_loop()

        async def mixed(host, port, **kw):
            return [_info("93.184.216.34"), _info("10.0.0.1")]
        monkeypatch.setattr(loop, "getaddrinfo", mixed)
        monkeypatch.setattr(webhook_guard, "resolve_host", webhook_guard._getaddrinfo_resolve)
        with pytest.raises(WebhookURLRejected) as info:
            await validate_webhook_url("https://mixed.example/x")
        assert info.value.reason == REASON_PRIVATE


# ---------------------------------------------------------------------------
# Opt-out (AC15)
# ---------------------------------------------------------------------------

class TestOptOut:
    def test_default_off_and_env_controlled(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_TARGETS", raising=False)
        assert Settings(_env_file=None).webhook_allow_private_targets is False
        monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_TARGETS", "true")
        assert Settings(_env_file=None).webhook_allow_private_targets is True

    @pytest.mark.asyncio
    async def test_off_refuses_local_http(self):
        assert (await _reason("http://127.0.0.1:8080/x")).reason == REASON_SCHEME
        assert (await _reason("https://127.0.0.1:8080/x")).reason == REASON_PRIVATE

    @pytest.mark.asyncio
    async def test_on_allows_local_http_and_private(self, allow_private, webhook_dns):
        webhook_dns["rebind.test"] = ["127.0.0.1"]
        for url in ("http://127.0.0.1:8080/x", "https://localhost/x", "https://my-app.internal/x",
                    "https://rebind.test/x", "http://[::1]:9000/x", "https://169.254.169.254/x"):
            await validate_webhook_url(url)

    @pytest.mark.asyncio
    async def test_on_still_refuses_malformed_scheme_and_unresolvable(self, allow_private, webhook_dns):
        webhook_dns.unresolvable.add("nx.test")
        assert (await _reason("ftp://127.0.0.1/x")).reason == REASON_SCHEME
        assert (await _reason("https:///nohost")).reason == REASON_MALFORMED
        assert (await _reason("https://nx.test/x")).reason == REASON_UNRESOLVABLE

    def test_startup_warning_only_when_on(self, monkeypatch, caplog):
        log = logging.getLogger("test.webhook_guard.startup")
        with caplog.at_level(logging.WARNING, logger=log.name):
            assert webhook_guard.warn_if_private_targets_allowed(log) is False
        assert not caplog.records
        monkeypatch.setattr(settings, "webhook_allow_private_targets", True)
        with caplog.at_level(logging.WARNING, logger=log.name):
            assert webhook_guard.warn_if_private_targets_allowed(log) is True
        assert any(
            r.levelno == logging.WARNING and "WEBHOOK_ALLOW_PRIVATE_TARGETS is ON" in r.getMessage()
            for r in caplog.records
        )

    def test_startup_hooks_call_the_warning(self):
        import src.main as main_module
        import mcp.server as mcp_server
        assert "warn_if_private_targets_allowed" in inspect.getsource(main_module.startup_pricing_history)
        assert "warn_if_private_targets_allowed" in inspect.getsource(mcp_server.main)


# ---------------------------------------------------------------------------
# PinnedWebhookTransport
# ---------------------------------------------------------------------------

class CapturingTransport(httpx.AsyncBaseTransport):
    def __init__(self, status=200, headers=None):
        self.requests = []
        self.status = status
        self.headers = headers or {}
        self.closed = False

    async def handle_async_request(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, headers=self.headers, request=request)

    async def aclose(self):
        self.closed = True


class TestPinnedTransport:
    @pytest.mark.asyncio
    async def test_https_pins_ip_keeps_host_and_sni(self, webhook_dns):
        webhook_dns["pinned.test"] = ["93.184.216.34", "93.184.216.35"]
        inner = CapturingTransport()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            resp = await client.post("https://pinned.test:8443/hook/a?x=1&y=2", content=b"{}")
        assert resp.status_code == 200
        (req,) = inner.requests
        assert req.url.host == "93.184.216.34"
        assert req.url.port == 8443
        assert req.url.raw_path == b"/hook/a?x=1&y=2"
        assert req.headers["Host"] == "pinned.test:8443"
        assert req.extensions["sni_hostname"] == "pinned.test"
        assert inner.closed is True

    @pytest.mark.asyncio
    async def test_ipv6_pin(self, webhook_dns):
        webhook_dns["v6.test"] = ["2606:2800:220:1::1"]
        inner = CapturingTransport()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            await client.post("https://v6.test/x")
        assert inner.requests[0].url.host == "2606:2800:220:1::1"
        assert inner.requests[0].extensions["sni_hostname"] == "v6.test"

    @pytest.mark.asyncio
    async def test_public_literal_untouched(self):
        inner = CapturingTransport()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            await client.post("https://93.184.216.34/x")
        assert str(inner.requests[0].url) == "https://93.184.216.34/x"
        assert "sni_hostname" not in inner.requests[0].extensions

    @pytest.mark.asyncio
    async def test_http_under_opt_out_has_no_sni(self, allow_private, webhook_dns):
        webhook_dns["pinned.test"] = ["127.0.0.1"]
        inner = CapturingTransport()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            await client.post("http://pinned.test:8080/x")
        req = inner.requests[0]
        assert req.url.host == "127.0.0.1" and req.headers["Host"] == "pinned.test:8080"
        assert "sni_hostname" not in req.extensions

    @pytest.mark.asyncio
    async def test_ac6_rebinding_at_delivery_never_reaches_inner(self, webhook_dns):
        answers = iter([["93.184.216.34"], ["169.254.169.254"]])
        webhook_dns["rebind.test"] = lambda: next(answers)
        await validate_webhook_url("https://rebind.test/hook")  # public at registration
        inner = CapturingTransport()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            with pytest.raises(WebhookURLRejected) as info:
                await client.post("https://rebind.test/hook")
        assert info.value.reason == REASON_PRIVATE
        assert inner.requests == []

    @pytest.mark.asyncio
    async def test_ac6_via_service_not_fired_and_logged_without_ip(self, tmp_path, webhook_dns, caplog):
        answers = iter([["93.184.216.34"], ["169.254.169.254"]])
        webhook_dns["rebind.test"] = lambda: next(answers)
        svc = PricingAlertService(str(tmp_path / "a.db"))
        await svc.initialize()
        await svc.register("https://rebind.test/hook", threshold_pct=1.0)
        inner = CapturingTransport()
        with patch("src.services.pricing_alerts.PinnedWebhookTransport",
                   lambda: PinnedWebhookTransport(inner=inner)), \
                caplog.at_level(logging.WARNING, logger="src.services.pricing_alerts"):
            fired = await svc.check_and_fire([_trend()])
        assert fired == 0
        assert inner.requests == []
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "delivery blocked (private_destination)" in text
        assert "169.254" not in text

    @pytest.mark.asyncio
    async def test_unresolvable_at_delivery_never_reaches_inner(self, webhook_dns):
        webhook_dns.unresolvable.add("gone.test")
        inner = CapturingTransport()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            with pytest.raises(WebhookURLRejected) as info:
                await client.post("https://gone.test/x")
        assert info.value.reason == REASON_UNRESOLVABLE
        assert inner.requests == []

    @pytest.mark.asyncio
    async def test_env_proxy_is_not_used(self, monkeypatch):
        """trust_env=False: with HTTPS_PROXY set, the client mounts no proxy and the
        request still goes straight to the pinned transport."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
        inner = CapturingTransport()
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, trust_env=False,
            transport=PinnedWebhookTransport(inner=inner),
        ) as client:
            await client.post("https://hooks.example.com/x")
        assert len(inner.requests) == 1
        assert inner.requests[0].url.host == "93.184.216.34"
        default = PinnedWebhookTransport()
        inner_default = default._inner_for("hooks.example.com")
        assert isinstance(inner_default, httpx.AsyncHTTPTransport)
        assert type(inner_default._pool).__name__ == "AsyncConnectionPool"  # not a proxy pool

    @pytest.mark.asyncio
    async def test_each_hostname_gets_its_own_pool(self, webhook_dns):
        """Review fix: httpcore pools by rewritten IP, so two hostnames on one IP must
        not share an inner transport (a TLS session verified for one name)."""
        webhook_dns["a.test"] = ["93.184.216.34"]
        webhook_dns["b.test"] = ["93.184.216.34"]
        built = []

        def factory():
            t = CapturingTransport()
            built.append(t)
            return t

        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner_factory=factory)) as client:
            await client.post("https://a.test/x")
            await client.post("https://b.test/x")
            await client.post("https://A.test/y")
        assert len(built) == 2
        assert [r.extensions["sni_hostname"] for r in built[0].requests] == ["a.test", "a.test"]
        assert [r.extensions["sni_hostname"] for r in built[1].requests] == ["b.test"]
        assert all(t.closed for t in built)

    @pytest.mark.asyncio
    async def test_falls_back_to_next_checked_address(self, webhook_dns):
        """Review fix: a connect failure on the first address tries the next one."""
        webhook_dns["multi-ok.test"] = ["2606:2800:220:1::1", "93.184.216.34"]

        class FailFirst(CapturingTransport):
            async def handle_async_request(self, request):
                self.requests.append(request.url.host)
                if request.url.host == "2606:2800:220:1::1":
                    raise httpx.ConnectError("unreachable", request=request)
                return httpx.Response(200, request=request)

        inner = FailFirst()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            resp = await client.post("https://multi-ok.test/x")
        assert resp.status_code == 200
        assert inner.requests == ["2606:2800:220:1::1", "93.184.216.34"]

    @pytest.mark.asyncio
    async def test_all_addresses_fail_raises_connect_error(self, webhook_dns):
        webhook_dns["dead.test"] = ["93.184.216.34", "93.184.216.35"]

        class AlwaysFail(CapturingTransport):
            async def handle_async_request(self, request):
                self.requests.append(request.url.host)
                raise httpx.ConnectError("unreachable", request=request)

        inner = AlwaysFail()
        async with httpx.AsyncClient(transport=PinnedWebhookTransport(inner=inner)) as client:
            with pytest.raises(httpx.ConnectError):
                await client.post("https://dead.test/x")
        assert inner.requests == ["93.184.216.34", "93.184.216.35"]


def _trend(model="gpt-4o"):
    return {
        "model_name": model, "provider": "openai", "input_change_pct": 50.0, "output_change_pct": 0.0,
        "direction": "up", "first_seen": "2026-10-30T00:00:00", "last_seen": "2026-10-30T06:00:00",
    }


# ---------------------------------------------------------------------------
# Real local deliveries (opt-out ON so 127.0.0.1 is reachable)
# ---------------------------------------------------------------------------

class _Recorder(http.server.BaseHTTPRequestHandler):
    hits = []        # (method, path, headers, body)
    redirect_to = None

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        type(self).hits.append(("POST", self.path, dict(self.headers), body))
        if self.path == "/hook" and type(self).redirect_to:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST  # noqa: N815

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    handler = type("Handler", (_Recorder,), {"hits": [], "redirect_to": None})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, handler
    finally:
        server.shutdown()
        server.server_close()


class TestLocalDelivery:
    @pytest.mark.asyncio
    async def test_ac15_opt_out_registers_and_delivers_to_loopback(self, tmp_path, allow_private, local_server):
        server, handler = local_server
        port = server.server_address[1]
        svc = PricingAlertService(str(tmp_path / "a.db"))
        await svc.initialize()
        await svc.register(f"http://127.0.0.1:{port}/hook", threshold_pct=1.0)
        fired = await svc.check_and_fire([_trend()])
        assert fired == 1
        assert [h[1] for h in handler.hits] == ["/hook"]

    @pytest.mark.asyncio
    async def test_pinned_delivery_keeps_host_header_and_signature(
        self, tmp_path, allow_private, local_server, webhook_dns,
    ):
        server, handler = local_server
        port = server.server_address[1]
        webhook_dns["pinned.test"] = ["127.0.0.1"]
        svc = PricingAlertService(str(tmp_path / "a.db"))
        await svc.initialize()
        await svc.register(f"http://pinned.test:{port}/hook", threshold_pct=1.0)
        secret = "-".join(["whk", "test", "local"])
        fired = await svc.check_and_fire([_trend()], secret=secret)
        assert fired == 1
        (_m, path, headers, body), = handler.hits
        assert path == "/hook"
        assert headers["Host"] == f"pinned.test:{port}"
        assert json.loads(body)["model_name"] == "gpt-4o"
        assert verify_webhook_signature(secret, body, headers["X-LLM-Pricing-Signature"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target", ["/second", "http://169.254.169.254/latest/meta-data/", "gopher://x/"])
    async def test_ac8_redirect_never_followed(self, tmp_path, allow_private, local_server, target):
        server, handler = local_server
        port = server.server_address[1]
        handler.redirect_to = target
        svc = PricingAlertService(str(tmp_path / "a.db"))
        await svc.initialize()
        await svc.register(f"http://127.0.0.1:{port}/hook", threshold_pct=1.0)
        fired = await svc.check_and_fire([_trend()])
        assert fired == 1  # a 3xx counts as delivered
        assert [h[1] for h in handler.hits] == ["/hook"]

    @pytest.mark.asyncio
    async def test_ac8_budget_redirect_never_followed(self, tmp_path, allow_private, local_server):
        from unittest.mock import AsyncMock, MagicMock
        from src.services.budget_alerts import BudgetAlertService
        server, handler = local_server
        port = server.server_address[1]
        handler.redirect_to = "/second"
        svc = BudgetAlertService(str(tmp_path / "b.db"))
        await svc.initialize()
        await svc.register(f"http://127.0.0.1:{port}/hook", threshold_usd=1.0)
        tracker = MagicMock()
        tracker.get_summary = AsyncMock(return_value={"total_cost_usd": 5.0, "total_requests": 3})
        assert await svc.check_and_fire(tracker) == 1
        assert [h[1] for h in handler.hits] == ["/hook"]


# ---------------------------------------------------------------------------
# Local TLS: certificate is still verified against the ORIGINAL hostname
# ---------------------------------------------------------------------------

def _make_ca_and_leaf(tmp_path, leaf_name):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "webhook-guard-test-ca")])
    ca_cert = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, leaf_name)]))
        .issuer_name(ca_name).public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(leaf_name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_pem = tmp_path / "leaf.pem"
    cert_pem.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_pem = tmp_path / "leaf.key"
    key_pem.write_bytes(leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_pem, cert_pem, key_pem


@pytest.fixture
def tls_server_factory(tmp_path):
    servers = []

    def _start(leaf_name):
        ca_pem, cert_pem, key_pem = _make_ca_and_leaf(tmp_path, leaf_name)
        handler = type("Handler", (_Recorder,), {"hits": [], "redirect_to": None})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(str(cert_pem), str(key_pem))
        server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        client_ctx = ssl.create_default_context(cafile=str(ca_pem))
        return server, handler, client_ctx

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


class TestLocalTLS:
    @pytest.mark.asyncio
    async def test_cert_for_original_hostname_verifies(self, allow_private, webhook_dns, tls_server_factory):
        server, handler, ctx = tls_server_factory("pinned.test")
        port = server.server_address[1]
        webhook_dns["pinned.test"] = ["127.0.0.1"]
        transport = PinnedWebhookTransport(inner=httpx.AsyncHTTPTransport(verify=ctx, retries=0, trust_env=False))
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            resp = await client.post(f"https://pinned.test:{port}/hook", content=b"{}")
        assert resp.status_code == 200
        assert handler.hits[0][2]["Host"] == f"pinned.test:{port}"

    @pytest.mark.asyncio
    async def test_cert_for_other_hostname_fails(self, allow_private, webhook_dns, tls_server_factory):
        server, handler, ctx = tls_server_factory("someone-else.test")
        port = server.server_address[1]
        webhook_dns["pinned.test"] = ["127.0.0.1"]
        transport = PinnedWebhookTransport(inner=httpx.AsyncHTTPTransport(verify=ctx, retries=0, trust_env=False))
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            with pytest.raises(httpx.ConnectError) as info:
                await client.post(f"https://pinned.test:{port}/hook", content=b"{}")
        assert "certificate" in str(info.value).lower() or "hostname" in str(info.value).lower()
        assert handler.hits == []

    @pytest.mark.asyncio
    async def test_tls_connection_not_reused_across_hostnames_on_same_ip(
        self, allow_private, webhook_dns, tls_server_factory,
    ):
        """Review fix: a keep-alive TLS connection verified for good.test must not
        carry a request for other.test that resolves to the same IP."""
        server, handler, ctx = tls_server_factory("good.test")
        handler.protocol_version = "HTTP/1.1"  # keep-alive, so reuse would actually happen
        port = server.server_address[1]
        webhook_dns["good.test"] = ["127.0.0.1"]
        webhook_dns["other.test"] = ["127.0.0.1"]
        transport = PinnedWebhookTransport(
            inner_factory=lambda: httpx.AsyncHTTPTransport(verify=ctx, retries=0, trust_env=False))
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            resp = await client.post(f"https://good.test:{port}/hook", content=b"{}")
            assert resp.status_code == 200
            with pytest.raises(httpx.ConnectError):
                await client.post(f"https://other.test:{port}/hook", content=b"{}")
        assert [h[2]["Host"] for h in handler.hits] == [f"good.test:{port}"]
