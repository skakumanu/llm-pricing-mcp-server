"""REST locale resolution (v1.76.0): the six in-scope endpoints, /locale, /currency/rates.

Offline by construction: the only network-touching path (IP geolocation) is
monkeypatched, and a request from the test client carries no public IP so no
lookup is attempted unless a test supplies a forwarded address.
"""
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.services import currency as cur  # noqa: E402
from src.services.geolocation import GeolocationService  # noqa: E402

SUPPORTED = list(cur.SUPPORTED_CURRENCIES)
# Every header that can change a locale-aware body; present on every response of
# the six endpoints (active or not) so a shared cache never serves the USD body
# to a header-driven request.
VARY = main_module._LOCALE_VARY_HEADERS
# A genuinely global address: 203.0.113.0/24 (TEST-NET-3) is *reserved*, so
# ipaddress.is_global is False for it and the resolver rightly never looks it up.
PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "8.8.8.8"
RESERVED_IP = "203.0.113.9"
ESTIMATE = {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
BATCH = {"model_names": ["gpt-4o", "gpt-4o-mini", "no-such-model"], "input_tokens": 1000, "output_tokens": 500}
ROUTE = {"max_cost_per_1m_tokens": 5}

# (method, path, json body) for every in-scope endpoint
ENDPOINTS = [
    ("GET", "/pricing", None),
    ("POST", "/cost-estimate", ESTIMATE),
    ("POST", "/cost-estimate/batch", BATCH),
    ("GET", "/use-cases", None),
    ("POST", "/router/recommend", ROUTE),
    ("GET", "/pricing/public", None),
]


@pytest.fixture(scope="module")
def client():
    # Lifespan is needed: /router/recommend uses the router initialised at startup.
    with TestClient(app) as c:
        yield c


def _call(client, method, path, body=None, **kw):
    if method == "GET":
        return client.get(path, **kw)
    return client.post(path, json=body, **kw)


def _strip(payload):
    """Drop the per-call stamps: response timestamp, per-model last_updated, updated_at,
    routing_id and the router's cache-hit flag (a repeat within 5 minutes is served cached)."""
    text = json.dumps(payload)
    data = json.loads(text)
    for key in ("timestamp", "updated_at", "routing_id", "cached"):
        data.pop(key, None)
    for m in data.get("models", []) or []:
        if isinstance(m, dict):
            m.pop("last_updated", None)
    for key in ("recommended",):
        if isinstance(data.get(key), dict):
            data[key].pop("last_updated", None)
    for m in data.get("alternatives", []) or []:
        m.pop("last_updated", None)
    return data


def _walk(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _has_local_fields(payload) -> bool:
    for node in _walk(payload):
        if isinstance(node, dict) and any(k.startswith("local_") or k == "currency_conversion" for k in node):
            return True
    return False


@pytest.fixture
def geo(monkeypatch):
    """Replace the IP lookup with a deterministic, network-free stub.

    Mirrors the real service's contract: a private address gets the PRIVATE
    sentinel, anything that is not the public test address resolves to None
    (the telemetry middleware looks up the raw socket peer, "testclient"),
    and only PUBLIC_IP yields ``result`` (or raises ``raise_exc``).
    """
    def install(result=None, raise_exc=None, raise_times=None):
        """``raise_times=N`` raises only for the first N PUBLIC_IP lookups, then returns ``result``."""
        async def fake(ip_address, timeout=5.0):
            fake.calls.append((ip_address, timeout))
            if GeolocationService._is_private_ip(ip_address):
                return {"country": "Private Network", "country_code": "PRIVATE"}
            if ip_address != PUBLIC_IP:
                return None
            if raise_exc and (raise_times is None or fake.raised < raise_times):
                fake.raised += 1
                raise raise_exc
            return result
        fake.calls = []
        fake.raised = 0
        monkeypatch.setattr(GeolocationService, "get_geolocation", staticmethod(fake))
        return fake
    return install


@pytest.fixture
def rates_file(monkeypatch, tmp_path):
    """Point the app at a temp rate table; restores the vendored one afterwards."""
    def install(as_of: date, **extra):
        body = {"base": "USD", "as_of": as_of.isoformat(), "source": "test table", "placeholder": False,
                "rates": {c: (1.0 if c == "USD" else 2.0) for c in SUPPORTED}}
        body.update(extra)
        p = tmp_path / "rates.json"
        p.write_text(json.dumps(body), encoding="utf-8")
        monkeypatch.setattr(main_module.settings, "exchange_rates_path", str(p))
        cur.reset_rate_table_cache()
        return p
    yield install
    cur.reset_rate_table_cache()


# ---------------------------------------------------------------------------
# AC1 — listing
# ---------------------------------------------------------------------------

class TestPricingListing:
    def test_eur_adds_local_prices_and_metadata_to_every_model(self, client):
        r = client.get("/pricing?currency=EUR")
        assert r.status_code == 200
        data = r.json()
        conv = data["currency_conversion"]
        assert conv["currency"] == "EUR" and conv["base_currency"] == "USD" and conv["rate"] > 0
        assert conv["rate_as_of"] and "indicative" in conv["note"] and "bill in USD" in conv["note"]
        assert conv["currency_source"] == "query" and conv["fallback_to_usd"] is False
        assert data["models"], "catalogue empty"
        for m in data["models"]:
            assert m["currency"] == "USD"                       # canonical field untouched
            assert "cost_per_input_token" in m and "cost_per_output_token" in m
            lp = m["local_prices"]
            assert lp["currency"] == "EUR"
            assert lp["cost_per_input_token"] == pytest.approx(m["cost_per_input_token"] * conv["rate"], rel=1e-6)
            assert lp["cost_per_1m_input"] == pytest.approx(m["cost_per_input_token"] * 1e6 * conv["rate"], abs=1e-5)
            assert set(lp["cost_at_1m_tokens"]) == {"input_cost", "output_cost", "total_cost"}

    def test_usd_is_identical_to_no_currency(self, client):
        plain = client.get("/pricing").json()
        usd = client.get("/pricing?currency=USD").json()
        assert "currency_conversion" not in plain and "currency_conversion" not in usd
        assert _strip(plain) == _strip(usd)
        assert not _has_local_fields(plain)

    def test_plain_request_keeps_todays_shape(self, client):
        r = client.get("/pricing")
        assert r.headers["cache-control"] == "public, max-age=300"
        # The USD variant varies on the same headers as the converted ones: a shared
        # cache must not hand it to the next caller who sends X-Currency: EUR.
        assert r.headers["vary"] == VARY
        assert not _has_local_fields(r.json())

    def test_filters_still_work_with_currency(self, client):
        r = client.get("/pricing?provider=openai&currency=GBP")
        assert r.status_code == 200
        assert all(m["provider"] == "OpenAI" and m["local_prices"]["currency"] == "GBP" for m in r.json()["models"])

    def test_header_currency_works(self, client):
        r = client.get("/pricing", headers={"X-Currency": "inr"})
        assert r.json()["currency_conversion"]["currency"] == "INR"
        assert r.json()["currency_conversion"]["currency_source"] == "header"

    def test_jpy_per_token_values_are_not_zero(self, client):
        r = client.get("/pricing?currency=JPY").json()
        for m in r["models"]:
            if m["cost_per_input_token"] > 0:
                assert m["local_prices"]["cost_per_input_token"] > 0

    def test_explicit_currency_keeps_public_cache_and_sets_vary(self, client):
        r = client.get("/pricing?currency=EUR")
        assert r.headers["cache-control"] == "public, max-age=300"
        assert r.headers["vary"] == VARY
        for name in ("X-Currency", "X-Country", "CF-IPCountry", "X-Vercel-IP-Country",
                     "CloudFront-Viewer-Country", "X-Country-Code"):
            assert name in r.headers["vary"], name


# ---------------------------------------------------------------------------
# AC2 / AC4 — country
# ---------------------------------------------------------------------------

class TestCountry:
    def test_country_in_gives_inr(self, client):
        r = client.post("/cost-estimate?country=IN", json=ESTIMATE)
        data = r.json()
        assert r.status_code == 200 and data["currency"] == "USD"
        assert data["local_cost"]["currency"] == "INR"
        rate = data["currency_conversion"]["rate"]
        assert data["local_cost"]["total_cost"] == pytest.approx(data["total_cost"] * rate, rel=1e-6)
        assert data["currency_conversion"]["country"] == "IN"
        assert data["currency_conversion"]["currency_source"] == "country"

    def test_stated_currency_beats_stated_country(self, client):
        r = client.post("/cost-estimate?country=IN&currency=GBP", json=ESTIMATE).json()
        assert r["local_cost"]["currency"] == "GBP" and r["currency_conversion"]["country"] == "IN"

    def test_header_country(self, client):
        r = client.post("/cost-estimate", json=ESTIMATE, headers={"X-Country": "jp"}).json()
        assert r["local_cost"]["currency"] == "JPY" and r["currency_conversion"]["country_source"] == "header"

    @pytest.mark.parametrize("country", ["ZW", "Germany", "123", "XX"])
    def test_unsupported_or_unknown_country_falls_back_to_usd_explicitly(self, client, country):
        r = client.post(f"/cost-estimate?country={country}", json=ESTIMATE)
        assert r.status_code == 200
        data = r.json()
        assert data["local_cost"]["currency"] == "USD"
        assert data["local_cost"]["total_cost"] == pytest.approx(data["total_cost"])
        assert data["currency_conversion"]["fallback_to_usd"] is True
        assert data["currency_conversion"]["fallback_reason"] == "country_not_mapped"
        # Whether the value was unmapped (ZW) or unusable (Germany, 123), the caller
        # *stated* it: the source says so instead of looking like a plain request.
        assert data["currency_conversion"]["country_source"] == "query"

    def test_unusable_stated_country_is_still_reported_as_stated(self, client):
        for value in ("Germany", "123"):
            data = client.get(f"/locale?country={value}").json()
            assert data["source"] == "query", value
            assert data["country"] is None and data["fallback_to_usd"] is True
            assert data["fallback_reason"] == "country_not_mapped"

    def test_us_is_mapped_not_a_fallback(self, client):
        r = client.get("/pricing?country=US").json()
        assert r["currency_conversion"]["currency"] == "USD" and r["currency_conversion"]["fallback_to_usd"] is False


# ---------------------------------------------------------------------------
# AC3 — rejection
# ---------------------------------------------------------------------------

class TestUnsupportedCurrency:
    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_every_endpoint_rejects_with_400_listing_codes(self, client, method, path, body):
        sep = "&" if "?" in path else "?"
        r = _call(client, method, f"{path}{sep}currency=XYZ", body)
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error"] == "unsupported_currency" and detail["currency"] == "XYZ"
        assert detail["supported_currencies"] == SUPPORTED
        assert not _has_local_fields(r.json()) and "input_cost" not in r.text

    def test_header_rejection_too(self, client):
        r = client.get("/pricing", headers={"X-Currency": "BTC"})
        assert r.status_code == 400 and r.json()["detail"]["supported_currencies"] == SUPPORTED

    def test_locale_endpoint_rejects_too(self, client):
        assert client.get("/locale?currency=XYZ").status_code == 400


# ---------------------------------------------------------------------------
# AC5 / AC6 — detection, fallback, privacy
# ---------------------------------------------------------------------------

class TestDetection:
    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_lookup_failure_is_todays_response(self, client, geo, method, path, body):
        geo(raise_exc=RuntimeError("no network"))
        plain = _call(client, method, path, body)
        detected = _call(client, method, path, body, headers={"X-Forwarded-For": f"{PUBLIC_IP}, 10.0.0.1"})
        assert detected.status_code == 200
        assert _strip(plain.json()) == _strip(detected.json())
        assert not _has_local_fields(detected.json())
        assert PUBLIC_IP not in detected.text

    def test_lookup_returning_none_is_usd(self, client, geo):
        geo(result=None)
        r = client.post("/cost-estimate", json=ESTIMATE, headers={"Fly-Client-IP": PUBLIC_IP})
        assert r.json()["currency"] == "USD" and "currency_conversion" not in r.json()

    def test_lookup_timeout_is_the_configured_one(self, client, geo, monkeypatch):
        """The telemetry middleware looks up first (default timeout); when that fails the
        request-path retry uses the short configured cap and still resolves the currency."""
        fake = geo(result={"country": "Germany", "country_code": "DE"},
                   raise_exc=RuntimeError("flaky upstream"), raise_times=1)
        monkeypatch.setattr(main_module.settings, "geo_lookup_timeout_seconds", 0.25)
        r = client.get("/locale", headers={"Fly-Client-IP": PUBLIC_IP})
        assert r.json()["currency"] == "EUR"
        assert fake.calls == [(PUBLIC_IP, 5.0), (PUBLIC_IP, 0.25)]

    def test_fly_client_ip_beats_a_client_prepended_forwarded_hop(self, client, geo):
        """Only the trusted address is ever geolocated: a spoofed X-Forwarded-For first hop
        must not steer the country that /locale echoes back (and must not spend a lookup)."""
        fake = geo(result={"country": "Germany", "country_code": "DE"})
        r = client.get("/locale", headers={"X-Forwarded-For": f"{OTHER_PUBLIC_IP}, 10.0.0.1",
                                           "Fly-Client-IP": PUBLIC_IP})
        assert r.json()["country"] == "DE" and r.json()["source"] == "ip"
        assert fake.calls == [(PUBLIC_IP, 5.0)]
        assert all(ip != OTHER_PUBLIC_IP for ip, _ in fake.calls)

    def test_detected_country_sets_private_cache_and_echoes_only_the_code(self, client, geo, caplog):
        geo(result={"country": "Germany", "country_code": "DE", "city": "Berlin"})
        with caplog.at_level(logging.DEBUG):
            r = client.get("/pricing", headers={"Fly-Client-IP": PUBLIC_IP})
        assert r.status_code == 200
        conv = r.json()["currency_conversion"]
        assert conv["currency"] == "EUR" and conv["country"] == "DE" and conv["country_source"] == "ip"
        assert r.headers["cache-control"] == "private, max-age=300"
        assert r.headers["vary"] == VARY
        assert PUBLIC_IP not in r.text and "Berlin" not in r.text
        assert PUBLIC_IP not in caplog.text

    @pytest.mark.parametrize("method,path,body", [e for e in ENDPOINTS if e[1] != "/pricing"])
    def test_detected_locale_is_private_on_every_endpoint(self, client, geo, method, path, body):
        """A visitor-specific (detected) body must never be shareable from a cache — on every
        endpoint, not only the one that carries a public TTL; a stated locale is not marked private."""
        geo(result={"country": "Germany", "country_code": "DE"})
        detected = _call(client, method, path, body, headers={"Fly-Client-IP": PUBLIC_IP})
        assert detected.status_code == 200 and detected.json()["currency_conversion"]["country"] == "DE"
        assert detected.headers["cache-control"] == "private" and detected.headers["vary"] == VARY
        platform = _call(client, method, path, body, headers={"CF-IPCountry": "DE"})
        assert platform.headers["cache-control"] == "private"
        stated = _call(client, method, path, body, headers={"X-Country": "DE"})
        assert stated.status_code == 200 and "cache-control" not in {k.lower() for k in stated.headers}

    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_no_endpoint_echoes_the_forwarded_ip(self, client, geo, caplog, method, path, body):
        geo(result={"country": "India", "country_code": "IN"})
        with caplog.at_level(logging.DEBUG):
            for header in ("X-Forwarded-For", "Fly-Client-IP"):
                r = _call(client, method, path, body, headers={header: PUBLIC_IP})
                assert r.status_code == 200
                assert PUBLIC_IP not in r.text
        assert PUBLIC_IP not in caplog.text

    def test_private_and_invalid_addresses_are_not_looked_up(self, client, geo):
        fake = geo(result={"country": "Germany", "country_code": "DE"})
        for addr in ("10.1.2.3", "192.168.0.9", "127.0.0.1", "unknown", "testclient", "::1", RESERVED_IP):
            r = client.get("/locale", headers={"Fly-Client-IP": addr, "X-Forwarded-For": addr})
            assert r.json()["currency"] == "USD", addr
        # the telemetry middleware may still call the stub with the raw header (default
        # timeout); the request-path resolver never passed such an address its short cap
        assert all(timeout == 5.0 for _, timeout in fake.calls)

    def test_detection_can_be_disabled(self, client, geo, monkeypatch):
        geo(result={"country": "Germany", "country_code": "DE"})
        monkeypatch.setattr(main_module.settings, "currency_detection_enabled", False)
        r = client.get("/pricing", headers={"Fly-Client-IP": PUBLIC_IP})
        assert "currency_conversion" not in r.json() and r.headers["cache-control"] == "public, max-age=300"

    def test_middleware_stash_is_reused(self, client, geo):
        """The telemetry middleware already resolved the country; the resolver does not look up twice."""
        fake = geo(result={"country": "Japan", "country_code": "JP"})
        r = client.get("/locale", headers={"X-Forwarded-For": PUBLIC_IP})
        assert r.json()["currency"] == "JPY" and r.json()["source"] == "ip"
        assert fake.calls == [(PUBLIC_IP, 5.0)]


class TestPrecedence:
    def test_platform_header_counts_as_detected(self, client):
        r = client.get("/pricing", headers={"CF-IPCountry": "DE"})
        conv = r.json()["currency_conversion"]
        assert conv["currency"] == "EUR" and conv["country_source"] == "platform_header"
        assert r.headers["cache-control"] == "private, max-age=300"

    @pytest.mark.parametrize("header", ["X-Vercel-IP-Country", "CloudFront-Viewer-Country", "X-Country-Code"])
    def test_other_platform_headers(self, client, header):
        r = client.get("/locale", headers={header: "GB"}).json()
        assert r["currency"] == "GBP" and r["source"] == "platform_header"

    def test_x_country_beats_platform_header(self, client):
        r = client.get("/locale", headers={"X-Country": "JP", "CF-IPCountry": "DE"}).json()
        assert r["currency"] == "JPY" and r["country"] == "JP" and r["source"] == "header"

    def test_country_query_beats_x_country(self, client):
        r = client.get("/locale?country=IN", headers={"X-Country": "JP"}).json()
        assert r["currency"] == "INR" and r["source"] == "query"

    def test_x_currency_beats_any_country(self, client):
        r = client.get("/locale?country=IN", headers={"X-Currency": "CAD", "CF-IPCountry": "DE"}).json()
        assert r["currency"] == "CAD" and r["country"] == "IN" and r["source"] == "header"

    def test_currency_query_beats_x_currency(self, client):
        r = client.get("/locale?currency=AUD", headers={"X-Currency": "CAD"}).json()
        assert r["currency"] == "AUD" and r["source"] == "query"

    def test_platform_header_beats_ip(self, client, geo):
        geo(result={"country": "India", "country_code": "IN"})
        r = client.get("/locale", headers={"CF-IPCountry": "DE", "Fly-Client-IP": PUBLIC_IP}).json()
        assert r["currency"] == "EUR" and r["source"] == "platform_header"


# ---------------------------------------------------------------------------
# Batch, use-cases, router, public
# ---------------------------------------------------------------------------

class TestBatch:
    def test_local_cost_only_on_available_models_and_usd_ranking(self, client):
        r = client.post("/cost-estimate/batch?currency=JPY", json=BATCH)
        data = r.json()
        plain = client.post("/cost-estimate/batch", json=BATCH).json()
        assert data["cheapest_model"] == plain["cheapest_model"]
        assert data["most_expensive_model"] == plain["most_expensive_model"]
        assert data["cost_range"] == plain["cost_range"]
        for m in data["models"]:
            if m["is_available"]:
                assert m["local_cost"]["currency"] == "JPY"
                assert m["local_cost"]["cost_per_1m_tokens"] == pytest.approx(
                    m["cost_per_1m_tokens"] * data["currency_conversion"]["rate"], rel=1e-6)
            else:
                assert "local_cost" not in m
        lr = data["local_cost_range"]
        assert lr["currency"] == "JPY" and lr["min"] <= lr["max"]
        assert lr["min"] == pytest.approx(plain["cost_range"]["min"] * data["currency_conversion"]["rate"], rel=1e-6)
        assert "local_cost_range" not in plain and not _has_local_fields(plain)

    def test_all_unknown_models_has_no_range(self, client):
        body = {"model_names": ["nope"], "input_tokens": 1, "output_tokens": 1}
        r = client.post("/cost-estimate/batch?currency=EUR", json=body)
        assert r.status_code == 200 and "local_cost_range" not in r.json() and "currency_conversion" in r.json()


class TestUseCasesAndRouter:
    def test_use_cases_same_models_same_order_same_tiers(self, client):
        plain = client.get("/use-cases").json()
        eur = client.get("/use-cases?currency=EUR").json()
        assert [m["model_name"] for m in eur["models"]] == [m["model_name"] for m in plain["models"]]
        assert [m["cost_tier"] for m in eur["models"]] == [m["cost_tier"] for m in plain["models"]]
        assert eur["providers"] == plain["providers"]
        assert not _has_local_fields(plain)
        for m in eur["models"]:
            lc = m["local_cost"]
            assert lc["currency"] == "EUR"
            assert lc["avg_cost_per_1m_tokens"] == pytest.approx(
                lc["avg_cost_per_1m_tokens_usd"] * eur["currency_conversion"]["rate"], rel=1e-5)
            assert lc["input_per_1m"] >= 0 and lc["output_per_1m"] >= 0
        assert eur["currency_conversion"]["country"] is None

    def test_use_cases_country_labelled(self, client):
        r = client.get("/use-cases?country=DE").json()
        assert r["currency_conversion"]["country"] == "DE" and r["currency_conversion"]["currency"] == "EUR"

    def test_router_same_recommendation_with_local_prices(self, client):
        plain = client.post("/router/recommend", json=ROUTE).json()
        eur = client.post("/router/recommend?currency=EUR", json=ROUTE).json()
        assert eur["recommended"]["model_name"] == plain["recommended"]["model_name"]
        assert [a["model_name"] for a in eur["alternatives"]] == [a["model_name"] for a in plain["alternatives"]]
        assert eur["recommended"]["local_prices"]["currency"] == "EUR"
        assert all(a["local_prices"]["currency"] == "EUR" for a in eur["alternatives"])
        assert eur["recommended"]["currency"] == "USD"
        assert "currency_conversion" in eur and not _has_local_fields(plain)

    def test_router_cached_result_is_not_mutated(self, client):
        """A localized call must not leave local_prices on the cached RouterResult."""
        client.post("/router/recommend?currency=EUR", json=ROUTE)
        again = client.post("/router/recommend", json=ROUTE).json()
        assert "local_prices" not in again["recommended"] and "currency_conversion" not in again


class TestPublicPricing:
    def test_additive_keys_and_unchanged_sort(self, client):
        plain = client.get("/pricing/public").json()
        eur = client.get("/pricing/public?currency=EUR").json()
        assert [m["model_name"] for m in eur["models"]] == [m["model_name"] for m in plain["models"]]
        assert set(plain["models"][0]) == {
            "model_name", "provider", "input_per_1m_usd", "output_per_1m_usd", "context_window",
        }
        assert "currency_conversion" not in plain
        rate = eur["currency_conversion"]["rate"]
        for p, e in zip(plain["models"], eur["models"]):
            assert e["input_per_1m_usd"] == p["input_per_1m_usd"]
            assert e["local_currency"] == "EUR"
            assert e["input_per_1m_local"] == pytest.approx(p["input_per_1m_usd"] * rate, abs=1e-5)
            assert e["output_per_1m_local"] == pytest.approx(p["output_per_1m_usd"] * rate, abs=1e-5)

    def test_public_endpoint_stays_public(self, client):
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            assert client.get("/pricing/public?currency=EUR").status_code == 200
        finally:
            main_module.settings.mcp_api_key = original


# ---------------------------------------------------------------------------
# /locale and /currency/rates
# ---------------------------------------------------------------------------

class TestLocaleEndpoint:
    def test_default_shape(self, client):
        r = client.get("/locale")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        data = r.json()
        assert data == {"country": None, "currency": "USD", "source": "default", "fallback_to_usd": False,
                        "fallback_reason": None, "supported_currencies": SUPPORTED}

    def test_fallback_is_reported(self, client):
        data = client.get("/locale?country=ZW").json()
        assert data["country"] == "ZW" and data["currency"] == "USD" and data["fallback_to_usd"] is True
        assert data["fallback_reason"] == "country_not_mapped" and data["source"] == "query"

    def test_public_without_key(self, client):
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            assert client.get("/locale").status_code == 200
            assert client.get("/currency/rates").status_code == 200
        finally:
            main_module.settings.mcp_api_key = original

    def test_in_openapi(self, client):
        spec = client.get("/openapi.json").json()
        assert "/locale" in spec["paths"] and "/currency/rates" in spec["paths"]
        params = {p["name"] for p in spec["paths"]["/pricing"]["get"]["parameters"]}
        assert {"currency", "country"} <= params
        desc = spec["paths"]["/pricing"]["get"]["description"]
        for needle in ("X-Currency", "X-Country", "7 days", "never stored", "MXN", "precedence".title()):
            assert needle in desc, needle


class TestRatesEndpoint:
    def test_shape(self, client):
        r = client.get("/currency/rates")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"
        data = r.json()
        assert data["base"] == "USD" and set(data["rates"]) == set(SUPPORTED) and data["rates"]["USD"] == 1.0
        assert data["stale_after_days"] == 7 and isinstance(data["stale"], bool)
        assert data["zero_decimal"] == ["JPY", "KRW"] and "bill in USD" in data["note"]
        date.fromisoformat(data["as_of"])
        assert isinstance(data["placeholder"], bool) and data["source"]

    def test_missing_table_is_503_not_500(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(main_module.settings, "exchange_rates_path", str(tmp_path / "missing.json"))
        cur.reset_rate_table_cache()
        try:
            assert client.get("/currency/rates").status_code == 503
            # ...and pricing still serves, in USD, saying why
            r = client.get("/pricing?currency=EUR")
            assert r.status_code == 200
            conv = r.json()["currency_conversion"]
            assert conv["currency"] == "USD" and conv["fallback_reason"] == "rates_unavailable"
            assert client.get("/pricing").status_code == 200
        finally:
            cur.reset_rate_table_cache()


# ---------------------------------------------------------------------------
# AC8 — staleness
# ---------------------------------------------------------------------------

class TestStaleRates:
    def test_old_table_still_converts_and_is_flagged(self, client, rates_file):
        old = date.today() - timedelta(days=10)
        rates_file(old)
        r = client.get("/pricing?currency=EUR").json()
        conv = r["currency_conversion"]
        assert conv["rate"] == 2.0 and conv["rate_as_of"] == old.isoformat()
        assert conv["rate_is_stale"] is True and conv["rate_age_days"] == 10
        assert r["models"][0]["local_prices"]["currency"] == "EUR"
        rates = client.get("/currency/rates").json()
        assert rates["stale"] is True and rates["as_of"] == old.isoformat()

    def test_fresh_table_is_not_flagged(self, client, rates_file):
        rates_file(date.today() - timedelta(days=3))
        assert client.get("/pricing?currency=EUR").json()["currency_conversion"]["rate_is_stale"] is False
        assert client.get("/currency/rates").json()["stale"] is False

    def test_exactly_seven_days_is_fresh(self, client, rates_file):
        rates_file(date.today() - timedelta(days=7))
        assert client.get("/currency/rates").json()["stale"] is False

    def test_placeholder_flag_surfaces(self, client, rates_file):
        rates_file(date.today(), placeholder=True)
        assert client.get("/currency/rates").json()["placeholder"] is True
        assert client.get("/pricing?currency=EUR").json()["currency_conversion"]["rate_placeholder"] is True


# ---------------------------------------------------------------------------
# FR12 — existing contract
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_plain_calls_have_no_new_fields(self, client, method, path, body):
        r = _call(client, method, path, body)
        assert r.status_code == 200
        assert not _has_local_fields(r.json())
        # Vary is the one header the plain variant gains (cache safety); nothing is
        # marked private for a request that stated and detected nothing.
        assert r.headers["vary"] == VARY
        assert r.headers.get("cache-control", "") != "private"

    def test_existing_currency_fields_still_say_usd(self, client):
        assert client.post("/cost-estimate", json=ESTIMATE).json()["currency"] == "USD"
        assert client.post("/cost-estimate/batch", json=BATCH).json()["currency"] == "USD"
        assert all(m["currency"] == "USD" for m in client.get("/pricing?currency=EUR").json()["models"])
