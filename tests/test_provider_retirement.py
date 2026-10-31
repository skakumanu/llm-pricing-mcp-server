"""v1.81.2 — guards for a retired provider (Anyscale).

Anyscale's public pricing page and hosted endpoints are gone. Its service was
removed from the aggregator, its data sources and price-oracle alias were
dropped, and its settings key was removed (with ``extra="ignore"`` so a
deployment still carrying ANYSCALE_API_KEY starts). History rows and existing
alerts are deliberately kept.

The retired name is spelled once, in RETIRED_PROVIDER below; every other
reference in this file derives from it. No test here makes a live network call.
"""
import asyncio
import importlib
import logging
import subprocess  # nosec B404 — fixed argv, no shell, used for git ls-files
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402

from src.config.settings import Settings  # noqa: E402
from src.main import app  # noqa: E402
from src.services import pricing_aggregator as pa  # noqa: E402
from src.services.base_provider import BasePricingProvider  # noqa: E402
from src.services.data_sources import PERFORMANCE_SOURCES, PRICING_SOURCES  # noqa: E402
from src.services.dispatch_executors import (  # noqa: E402
    EXECUTABLE_PROVIDERS, executor_spec_for, is_executable_provider,
)
from src.services.price_oracle import PROVIDER_ALIASES, PriceOracle  # noqa: E402
from src.services.pricing_alerts import PricingAlertService  # noqa: E402

RETIRED_PROVIDER = "Anyscale"
RETIRED_LOWER = RETIRED_PROVIDER.lower()
RETIRED_ENV_VAR = f"{RETIRED_PROVIDER.upper()}_API_KEY"
RETIRED_SETTING = f"{RETIRED_LOWER}_api_key"
RETIRED_HOST = f"{RETIRED_LOWER}.com"

# AC2: the only git-tracked files allowed to mention the retired name.
# - the vendored registry snapshot (never edited by hand — non-goal)
# - What's New (past entries are a historical record, plus the retirement entry)
# - README's single durable "Retired providers" line
# - this guard file
NAME_ALLOWLIST = {
    "data/model_prices_snapshot.json",
    "static/whats-new/index.html",
    "README.md",
    "tests/test_provider_retirement.py",
}
README_RETIRED_LINE = f"- **Retired providers**: {RETIRED_PROVIDER} (2026-10)"

client = TestClient(app)


def _fake_key() -> str:
    # Built at runtime so no key-shaped literal sits in the source.
    return "-".join(["retired", "provider", "placeholder", "value"])


# ---------------------------------------------------------------------------
# AC1 / AC3 / AC4 — gone from every provider registry
# ---------------------------------------------------------------------------

class TestRemovedFromRegistries:
    def test_not_in_provider_services(self):
        attrs = [attr for attr, _ in pa.PROVIDER_SERVICES]
        classes = [cls.__name__.lower() for _, cls in pa.PROVIDER_SERVICES]
        assert f"{RETIRED_LOWER}_service" not in attrs
        assert not any(RETIRED_LOWER in c for c in classes)

    def test_not_in_filter_aliases(self):
        assert RETIRED_LOWER not in pa.PROVIDER_FILTER_ALIASES

    def test_every_filter_alias_still_points_at_a_live_service(self):
        attrs = {attr for attr, _ in pa.PROVIDER_SERVICES}
        assert set(pa.PROVIDER_FILTER_ALIASES.values()) <= attrs

    def test_not_in_pricing_or_performance_sources(self):
        for sources in (PRICING_SOURCES, PERFORMANCE_SOURCES):
            assert not any(k.lower() == RETIRED_LOWER for k in sources)
            for src in sources.values():
                urls = [getattr(src, f, None) for f in (
                    "api_endpoint", "pricing_url", "public_health_check",
                    "health_check_endpoint", "public_status_page",
                )]
                assert not any(u and RETIRED_HOST in u.lower() for u in urls), src.provider

    def test_not_in_price_oracle_aliases(self):
        assert RETIRED_LOWER not in PROVIDER_ALIASES
        assert not any(RETIRED_LOWER in vals for vals in PROVIDER_ALIASES.values())

    def test_service_module_is_gone(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"src.services.{RETIRED_LOWER}_pricing")
        assert not (project_root / "src" / "services" / f"{RETIRED_LOWER}_pricing.py").exists()

    def test_aggregator_has_no_service_attribute(self):
        agg = pa.PricingAggregatorService()
        assert not hasattr(agg, f"{RETIRED_LOWER}_service")


# ---------------------------------------------------------------------------
# AC3 — a full catalogue fetch never contacts the retired host or logs it
# ---------------------------------------------------------------------------

class _RecordingOfflineTransport(httpx.AsyncBaseTransport):
    def __init__(self, seen):
        self._seen = seen

    async def handle_async_request(self, request):
        self._seen.append(str(request.url))
        raise httpx.ConnectError("offline in tests", request=request)


def test_full_catalogue_fetch_makes_no_request_to_retired_host(caplog):
    seen = []
    orig_init = httpx.AsyncClient.__init__

    def _init(self, *args, **kwargs):
        kwargs["transport"] = _RecordingOfflineTransport(seen)
        orig_init(self, *args, **kwargs)

    oracle = PriceOracle()
    with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=None)), \
         patch.object(BasePricingProvider, "_fetch_live_model_ids", new=AsyncMock(return_value=None)), \
         patch.object(httpx.AsyncClient, "__init__", _init), \
         patch.object(pa, "get_price_oracle", return_value=oracle), \
         caplog.at_level(logging.DEBUG):
        asyncio.run(oracle.load())
        models, statuses = asyncio.run(
            pa.PricingAggregatorService().get_all_pricing_async(include_unconfirmed=True)
        )

    assert models, "catalogue should still be served from static/snapshot data offline"
    assert not [u for u in seen if RETIRED_LOWER in u.lower()]
    assert RETIRED_LOWER not in caplog.text.lower()
    assert not [m for m in models if m.provider.lower() == RETIRED_LOWER]
    assert len(statuses) == 30
    assert not [s for s in statuses if s.provider_name.lower() == RETIRED_LOWER]


# ---------------------------------------------------------------------------
# AC4 — the filter behaves like any unknown provider
# ---------------------------------------------------------------------------

class TestProviderFilter:
    @pytest.mark.parametrize("spelling", [RETIRED_LOWER, RETIRED_PROVIDER, f"  {RETIRED_PROVIDER.upper()}  "])
    def test_aggregator_filter_returns_empty(self, spelling):
        agg = pa.PricingAggregatorService()
        assert asyncio.run(agg.get_pricing_by_provider_async(spelling)) == ([], [])

    def test_rest_pricing_filter_returns_200_and_empty(self):
        resp = client.get("/pricing", params={"provider": RETIRED_LOWER})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["models"] == [] and body["total_models"] == 0
        assert RETIRED_LOWER not in resp.text.lower()

    def test_rest_models_filter_returns_200_and_empty(self):
        resp = client.get("/models", params={"provider": RETIRED_LOWER})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_models"] == 0
        assert body["all_models"] == [] and body["providers"] == []

    def test_unknown_provider_behaves_identically(self):
        a = client.get("/pricing", params={"provider": RETIRED_LOWER}).json()
        b = client.get("/pricing", params={"provider": "no-such-provider"}).json()
        assert a["total_models"] == b["total_models"] == 0
        assert a["provider_status"] == b["provider_status"] == []


# ---------------------------------------------------------------------------
# AC9 — settings: key dropped, stale env var / .env entry tolerated
# ---------------------------------------------------------------------------

class TestSettings:
    def test_setting_is_removed(self):
        assert RETIRED_SETTING not in Settings.model_fields
        assert not hasattr(Settings(_env_file=None), RETIRED_SETTING)

    def test_builds_with_env_var_set(self, monkeypatch):
        monkeypatch.setenv(RETIRED_ENV_VAR, _fake_key())
        s = Settings(_env_file=None)
        assert not hasattr(s, RETIRED_SETTING)

    def test_builds_with_dotenv_entry(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"{RETIRED_ENV_VAR}={_fake_key()}\n", encoding="utf-8")
        s = Settings(_env_file=str(env_file))
        assert not hasattr(s, RETIRED_SETTING)

    def test_dotenv_entry_does_not_disturb_other_settings(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SERVER_PORT", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"{RETIRED_ENV_VAR}={_fake_key()}\nSERVER_PORT=8123\n", encoding="utf-8"
        )
        assert Settings(_env_file=str(env_file)).server_port == 8123


# ---------------------------------------------------------------------------
# AC10 — history kept, endpoints still answer
# ---------------------------------------------------------------------------

def _history_service(snapshots, trends):
    svc = MagicMock()
    svc.get_history = AsyncMock(return_value={"snapshots": snapshots, "total": len(snapshots)})
    svc.get_trends = AsyncMock(return_value=trends)
    return patch("src.main.get_pricing_history_service", return_value=svc)


class TestHistoryKept:
    ROW = {
        "model_name": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "provider": RETIRED_PROVIDER,
        "cost_per_input_token": 0.0000005,
        "cost_per_output_token": 0.0000005,
        "captured_at": 1_780_000_000.0,
    }

    def test_history_endpoint_returns_retired_rows(self):
        with _history_service([self.ROW], []):
            resp = client.get("/pricing/history", params={"provider": RETIRED_PROVIDER})
        assert resp.status_code == 200
        assert resp.json()["snapshots"][0]["provider"] == RETIRED_PROVIDER

    def test_trends_endpoint_returns_200(self):
        with _history_service([], []):
            resp = client.get("/pricing/trends")
        assert resp.status_code == 200

    def test_history_rows_survive_in_real_store(self, tmp_path):
        from src.services.pricing_history import PricingHistoryService

        async def run():
            svc = PricingHistoryService(str(tmp_path / "hist.db"))
            await svc.initialize()
            m = MagicMock()
            m.model_name, m.provider = self.ROW["model_name"], RETIRED_PROVIDER
            m.cost_per_input_token = m.cost_per_output_token = 0.0000005
            m.subscription_monthly_usd = None
            await svc.record_snapshot([m])
            return await svc.get_history(provider=RETIRED_PROVIDER, days=30, limit=10)

        result = asyncio.run(run())
        assert result["total"] == 1


# ---------------------------------------------------------------------------
# AC11 — existing alerts stay and go quiet without errors
# ---------------------------------------------------------------------------

class TestAlertsGoQuiet:
    @pytest.fixture
    def alert_svc(self, tmp_path):
        svc = PricingAlertService(str(tmp_path / "alerts.db"))
        asyncio.run(svc.initialize())
        asyncio.run(svc.register("https://example.com/hook", threshold_pct=1.0, provider=RETIRED_PROVIDER))
        return svc

    def test_listing_keeps_the_alert(self, alert_svc):
        alerts = asyncio.run(alert_svc.list_alerts())
        assert [a["provider"] for a in alerts] == [RETIRED_PROVIDER]

    def test_check_and_fire_on_live_trends_fires_nothing(self, alert_svc):
        trends = [{"model_name": "gpt-4o", "provider": "OpenAI", "input_change_pct": 90.0,
                   "output_change_pct": 90.0, "direction": "increased",
                   "first_seen": 0.0, "last_seen": 1.0}]
        with patch("src.services.pricing_alerts.httpx.AsyncClient") as cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock()
            cls.return_value = mock_client
            assert asyncio.run(alert_svc.check_and_fire(trends)) == 0
        mock_client.post.assert_not_called()

    def test_check_and_fire_with_no_trends(self, alert_svc):
        assert asyncio.run(alert_svc.check_and_fire([])) == 0

    def test_operator_listing_endpoint(self, alert_svc):
        op_key = "-".join(["op", "retired", "test", "key"])
        with patch("src.main.settings.mcp_api_key", op_key), \
             patch("src.main.get_pricing_alert_service", return_value=alert_svc):
            resp = client.get("/pricing/alerts", headers={"x-api-key": op_key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["alerts"][0]["provider"] == RETIRED_PROVIDER


# ---------------------------------------------------------------------------
# AC12 — never dispatchable
# ---------------------------------------------------------------------------

def test_not_an_executable_provider():
    assert not any(name.lower() == RETIRED_LOWER for name in EXECUTABLE_PROVIDERS)
    assert executor_spec_for(RETIRED_PROVIDER) is None
    assert not is_executable_provider(RETIRED_LOWER)
    assert not any(RETIRED_HOST in s.base_url.lower() for s in EXECUTABLE_PROVIDERS.values())


# ---------------------------------------------------------------------------
# AC13 — What's New retirement entry
# ---------------------------------------------------------------------------

def test_whats_new_has_the_retirement_entry_with_before_after_numbers():
    text = (project_root / "static/whats-new/index.html").read_text(encoding="utf-8")
    start = text.index("<!-- v1.81.2 -->")
    end = text.index("<!-- v1.81.1 -->")
    entry = text[start:end]
    assert f"{RETIRED_PROVIDER} retired" in entry
    for chip in ("31 → 30", "608 → 602", "98.4% → 98.5%", "2 → 1", "66 → 63"):
        assert chip in entry, chip
    # The previous top entry only lost its "latest" marker.
    assert text[end:].startswith('<!-- v1.81.1 -->\n  <div class="release">\n')


# ---------------------------------------------------------------------------
# AC2 — the name survives only where it is allowed to
# ---------------------------------------------------------------------------

def _git_tracked_files():
    try:
        out = subprocess.run(  # nosec B603 B607 — fixed argv, no shell
            ["git", "ls-files", "-z"], cwd=str(project_root),
            capture_output=True, check=True, timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available")
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_retired_name_only_in_allowlisted_files():
    needle = RETIRED_LOWER.encode()
    offenders = []
    for rel in _git_tracked_files():
        path = project_root / rel
        if not path.is_file():  # deleted in the working tree, not yet committed
            continue
        if needle in path.read_bytes().lower() and rel not in NAME_ALLOWLIST:
            offenders.append(rel)
    assert not offenders, offenders


def test_readme_mentions_it_only_on_the_retired_line():
    lines = (project_root / "README.md").read_text(encoding="utf-8").splitlines()
    hits = [ln for ln in lines if RETIRED_LOWER in ln.lower()]
    assert hits == [README_RETIRED_LINE]
