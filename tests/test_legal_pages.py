"""Legal pages, live gate and checkout coupling (v1.86.0, AC10-AC17)."""
import logging
import re
import shutil
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from scripts import sync_legal  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.main import app  # noqa: E402
from src.services import legal_pages, tiers  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402

DEFAULTS = Settings(_env_file=None)
client = TestClient(app)
LEGAL_DIR = project_root / "docs" / "legal"
STATIC = project_root / "static"
ROUTES = {
    "/terms": "terms", "/privacy": "privacy", "/refunds": "refunds",
    "/dpa": "dpa", "/subprocessors": "subprocessors", "/security": "security",
}
GATED = ["/terms", "/privacy", "/refunds", "/dpa", "/subprocessors"]
SK = "sk_" + "test_placeholder" + "Q" * 10
PRICE_PRO = "price_" + "pro" + "X" * 11


def _read(name):
    return (LEGAL_DIR / name).read_text(encoding="utf-8")


def _legal_text():
    return {p.name: p.read_text(encoding="utf-8") for p in LEGAL_DIR.glob("*.md")}


@pytest.fixture
def clean_gate(tmp_path, monkeypatch):
    """A placeholder-free temp copy of the legal sources, wired in as the docs dir."""
    copy = tmp_path / "legal"
    shutil.copytree(LEGAL_DIR, copy)
    for path in copy.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for ph in legal_pages.LEGAL_PLACEHOLDERS:
            text = text.replace(ph, "Example Co")
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(legal_pages, "LEGAL_DOCS_DIR", copy)
    legal_pages.refresh()
    yield copy
    monkeypatch.undo()
    legal_pages._cache = None


@pytest.fixture(autouse=True)
def _reset_cache():
    legal_pages._cache = None
    yield
    legal_pages._cache = None


@pytest.fixture
def stripe_ready():
    with patch.object(main_module.settings, "stripe_secret_key", SK), \
            patch.object(main_module.settings, "stripe_price_id_pro", PRICE_PRO), \
            patch.object(main_module.settings, "stripe_price_id_enterprise", None):
        yield


def _customer():
    return CustomerRecord(
        id="cust-1", email="payer@example.org", stripe_customer_id="cus_" + "L" * 14,
        stripe_subscription_id=None, api_key="k-legal-test", tier="free", org_id="org-legal-1",
        created_at=time.time(), updated_at=time.time())


# ---------------------------------------------------------------- AC10
class TestPagesServed:
    @pytest.mark.parametrize("path", sorted(ROUTES))
    def test_route_returns_html(self, path):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")

    def test_sync_check_exits_zero(self):
        assert sync_legal.main(["--check"]) == 0

    @pytest.mark.parametrize("path", GATED)
    def test_served_page_equals_rendered_file_with_notice_substituted(self, path):
        folder = ROUTES[path]
        rendered = (STATIC / folder / "index.html").read_text(encoding="utf-8")
        assert legal_pages.NOTICE_MARKER in rendered
        served = client.get(path).text
        assert served == rendered.replace(legal_pages.NOTICE_MARKER, legal_pages.DRAFT_NOTICE_HTML)

    def test_security_page_is_never_a_draft(self):
        served = client.get("/security").text
        rendered = (STATIC / "security" / "index.html").read_text(encoding="utf-8")
        assert served == rendered.replace(legal_pages.NOTICE_MARKER, "")
        assert "Draft, not yet in force" not in served

    @pytest.mark.parametrize("path", sorted(ROUTES))
    def test_each_page_shows_a_last_updated_date(self, path):
        assert re.search(r"Last updated \d{4}-\d{2}-\d{2}", client.get(path).text)

    def test_every_source_has_the_date_line_and_a_title(self):
        for name, text in _legal_text().items():
            assert text.startswith("# "), name
            assert re.search(r"^\*\*Last updated:\*\* \d{4}-\d{2}-\d{2}$", text, re.M), name

    def test_footer_billing_and_faq_link_to_every_policy(self):
        landing = (STATIC / "landing" / "index.html").read_text(encoding="utf-8")
        footer = landing[landing.index("<footer>"):]
        billing = (STATIC / "billing" / "index.html").read_text(encoding="utf-8")
        faq = (project_root / "docs" / "FAQ.md").read_text(encoding="utf-8")
        for path in ROUTES:
            assert f'href="{path}"' in footer, path
            assert f'href="{path}"' in billing, path
            assert f"]({path})" in faq, path

    def test_pages_cross_link_each_other(self):
        html = client.get("/terms").text
        for path in ROUTES:
            assert f'href="{path}"' in html

    def test_no_auth_needed_even_with_a_bad_key(self):
        assert client.get("/terms", headers={"x-api-key": "wrong"}).status_code == 200


# ---------------------------------------------------------------- AC11
class TestRepositoryGate:
    def test_placeholders_remain_in_the_templates(self):
        remaining = legal_pages.scan_placeholders(LEGAL_DIR)
        assert remaining, "templates are expected to carry placeholders until the owner fills them"
        assert set(sum(remaining.values(), [])) <= set(legal_pages.LEGAL_PLACEHOLDERS)

    def test_flag_is_off_everywhere_while_placeholders_remain(self):
        if not legal_pages.scan_placeholders(LEGAL_DIR):
            pytest.skip("no placeholders remain: the repository may turn the flag on")
        assert DEFAULTS.legal_pages_live is False
        fly_env = tomllib.loads((project_root / "fly.toml").read_text(encoding="utf-8")).get("env", {})
        assert str(fly_env.get("LEGAL_PAGES_LIVE", "false")).lower() not in {"1", "true", "yes", "on"}
        for line in (project_root / ".env.example").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("LEGAL_PAGES_LIVE"):
                assert line.split("=", 1)[1].strip().lower() in {"", "false", "0", "no", "off"}

    def test_only_the_four_documented_placeholders_are_used(self):
        for name, text in _legal_text().items():
            tokens = set(re.findall(r"\[[A-Z][A-Z ]{2,}\](?!\()", text))
            assert tokens <= set(legal_pages.LEGAL_PLACEHOLDERS), (name, tokens)

    def test_missing_sources_fail_closed(self, tmp_path):
        assert legal_pages.scan_placeholders(tmp_path)

    def test_placeholders_flag_script(self):
        assert sync_legal.main(["--placeholders"]) == 1


# ---------------------------------------------------------------- AC12
class TestRuntimeGate:
    def test_live_flag_with_placeholders_stays_draft(self, caplog, stripe_ready):
        with patch.object(main_module.settings, "legal_pages_live", True):
            with caplog.at_level(logging.WARNING, logger="src.services.legal_pages"):
                legal_pages.refresh(main_module.settings)
            assert legal_pages.is_live(main_module.settings) is False
            assert "Draft, not yet in force" in client.get("/terms").text
            cfg = client.get("/billing/config").json()
        assert cfg["checkout_enabled"] is False and cfg["legal_pages_live"] is False
        assert cfg["checkout_disabled_reason"] == "legal_pages_not_live"
        assert any(r.levelno == logging.WARNING and "LEGAL_PAGES_LIVE" in r.getMessage() for r in caplog.records)

    def test_no_warning_when_flag_off(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.services.legal_pages"):
            legal_pages.refresh(main_module.settings)
        assert not [r for r in caplog.records if "LEGAL_PAGES_LIVE" in r.getMessage()]


# ---------------------------------------------------------------- AC13
class TestCheckoutCoupling:
    def test_stripe_ready_but_legal_off(self, stripe_ready):
        cfg = client.get("/billing/config").json()
        assert cfg["checkout_enabled"] is False
        assert cfg["checkout_disabled_reason"] == "legal_pages_not_live"
        assert cfg["portal_enabled"] is True
        assert all(t["checkout_available"] is False for t in cfg["tiers"])

    def test_checkout_refused_with_the_same_503(self, stripe_ready):
        svc = MagicMock()
        svc.get_customer_by_api_key = AsyncMock(return_value=_customer())
        with patch("src.main.get_billing_service", return_value=svc):
            resp = client.post("/billing/checkout", json={"tier": "pro"}, headers={"x-api-key": "k-legal-test"})
        assert resp.status_code == 503
        assert "not enabled on this server" in resp.json()["detail"]

    def test_stripe_absent_reason(self):
        with patch.object(main_module.settings, "stripe_secret_key", None):
            cfg = client.get("/billing/config").json()
        assert cfg["checkout_disabled_reason"] == "stripe_not_configured"

    def test_live_with_clean_sources_enables_checkout(self, stripe_ready, clean_gate):
        import stripe
        svc = MagicMock()
        svc.get_customer_by_api_key = AsyncMock(return_value=_customer())
        session = SimpleNamespace(url="https://checkout.example/session")
        with patch.object(main_module.settings, "legal_pages_live", True), \
                patch("src.main.get_billing_service", return_value=svc), \
                patch.object(stripe.checkout.Session, "create", return_value=session) as create:
            cfg = client.get("/billing/config").json()
            assert cfg["checkout_enabled"] is True and cfg["legal_pages_live"] is True
            assert cfg["checkout_disabled_reason"] is None
            assert next(t for t in cfg["tiers"] if t["id"] == "pro")["checkout_available"] is True
            resp = client.post("/billing/checkout", json={"tier": "pro"}, headers={"x-api-key": "k-legal-test"})
            page = client.get("/terms").text
        assert resp.status_code == 200 and resp.json()["checkout_url"] == session.url
        assert create.call_count == 1
        assert "Draft, not yet in force" not in page

    @pytest.mark.parametrize("live", [False, True])
    def test_portal_works_either_way(self, stripe_ready, clean_gate, live):
        import stripe
        svc = MagicMock()
        svc.get_customer_by_api_key = AsyncMock(return_value=_customer())
        portal = SimpleNamespace(url="https://portal.example/p")
        with patch.object(main_module.settings, "legal_pages_live", live), \
                patch("src.main.get_billing_service", return_value=svc), \
                patch.object(stripe.billing_portal.Session, "create", return_value=portal):
            resp = client.get("/billing/portal", headers={"x-api-key": "k-legal-test"})
        assert resp.status_code == 200 and resp.json()["portal_url"] == portal.url

    def test_tiers_helpers_are_no_longer_aliases(self):
        assert tiers.checkout_enabled is not tiers.portal_enabled
        s = SimpleNamespace(stripe_secret_key=SK, legal_pages_live=False)
        assert tiers.portal_enabled(s) is True and tiers.checkout_enabled(s) is False
        assert tiers.checkout_disabled_reason(s) == "legal_pages_not_live"
        assert tiers.checkout_disabled_reason(SimpleNamespace(stripe_secret_key=None)) == "stripe_not_configured"

    def test_billing_page_picks_the_sentence_from_the_reason(self):
        billing = (STATIC / "billing" / "index.html").read_text(encoding="utf-8")
        assert "Checkout unavailable on this server" in billing
        assert "legal_pages_not_live" in billing


# ---------------------------------------------------------------- AC14
class TestPinnedFacts:
    def test_retention_and_windows_match_settings(self):
        privacy, terms, refunds = _read("PRIVACY.md"), _read("TERMS.md"), _read("REFUNDS.md")
        assert f"| {DEFAULTS.dispatch_receipt_retention_days} days |" in privacy
        assert f"{DEFAULTS.orchestrator_run_retention_days} days" in privacy
        assert f"{DEFAULTS.orchestrator_io_retention_days} days" in privacy
        assert f"grace period of {DEFAULTS.billing_past_due_grace_days} days" in terms
        assert f"within {DEFAULTS.billing_refund_window_days} days" in refunds
        assert f"After {DEFAULTS.billing_refund_window_days} days" in refunds

    def test_refund_window_default(self):
        assert DEFAULTS.billing_refund_window_days == 14

    def test_prices_match_the_tier_catalogue(self):
        terms = _read("TERMS.md")
        assert f"${tiers.PRICE_USD_MONTH['pro']} per month" in terms
        assert f"${tiers.PRICE_USD_MONTH['enterprise']} per month" in terms
        for text in (_read("PRIVACY.md"), _read("REFUNDS.md"), _read("DPA.md")):
            for price in re.findall(r"\$(\d+)", text):
                assert int(price) in set(tiers.PRICE_USD_MONTH.values()) | {100}

    def test_best_effort_wording(self):
        assert "best effort" in _read("TERMS.md")
        assert "best effort" in _read("SECURITY.md")
        assert "no sla" in _read("TERMS.md").lower()

    def test_faq_quotes_the_same_window(self):
        faq = (project_root / "docs" / "FAQ.md").read_text(encoding="utf-8")
        assert f"within {DEFAULTS.billing_refund_window_days} days" in faq


# ---------------------------------------------------------------- AC15
class TestRefundPolicy:
    def test_required_statements(self):
        text = _read("REFUNDS.md")
        assert "full refund" in text and "within 14 days of your first payment" in text
        assert "pro-rated" in text
        assert "The owner reviews every request" in text
        assert "support assistant" in text and "reference" in text
        assert "disputes" in text.lower()

    def test_faq_links_refunds_and_no_forbidden_phrasing(self):
        faq = (project_root / "docs" / "FAQ.md").read_text(encoding="utf-8")
        section = faq[faq.index("## Refunds, double charges and disputes"):faq.index("## Legal, privacy")]
        assert "](/refunds)" in section
        for name, text in {**_legal_text(), "FAQ.md": faq}.items():
            low = text.lower()
            assert "automatic refund" not in low, name
            assert "no refunds" not in low, name


# ---------------------------------------------------------------- AC16
class TestSubprocessors:
    def test_named_with_roles(self):
        text = _read("SUBPROCESSORS.md")
        rows = {r.split("|")[1].strip(): r for r in text.splitlines() if r.startswith("| ") and "---" not in r}
        for name in ("Fly.io", "Stripe", "LiteLLM pricing registry"):
            assert name in rows and rows[name].split("|")[2].strip()
        assert any(k.startswith("Anthropic") and "OpenAI" in k for k in rows)
        assert "hosting" in rows["Fly.io"].lower() and "payments" in rows["Stripe"].lower()

    def test_backup_storage_is_disclosed(self):
        assert "Backup storage" in _read("SUBPROCESSORS.md")


# ---------------------------------------------------------------- AC17
FORBIDDEN = ("SOC 2 compliant", "SOC 2 certified", "ISO certified", "enterprise-grade security",
             "bank-level security")
NEGATED_ONLY = ("SOC 2", "ISO 27001", "penetration test")


def _honesty_files():
    files = list(LEGAL_DIR.glob("*.md")) + [project_root / "docs" / "FAQ.md", project_root / "SECURITY.md"]
    files += [STATIC / "landing" / "index.html", STATIC / "billing" / "index.html"]
    files += [STATIC / folder / "index.html" for folder in ROUTES.values()]
    files += [STATIC / "faq" / "index.html"]
    return files


class TestSecurityHonesty:
    @pytest.mark.parametrize("path", _honesty_files(), ids=lambda p: p.name if p.suffix == ".md" else p.parent.name)
    def test_no_forbidden_claims(self, path):
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            assert phrase.lower() not in text.lower(), (path, phrase)

    @pytest.mark.parametrize("path", _honesty_files(), ids=lambda p: p.name if p.suffix == ".md" else p.parent.name)
    def test_certification_names_only_in_denials(self, path):
        text = re.sub(r"<[^>]+>", " ", path.read_text(encoding="utf-8"))
        for term in NEGATED_ONLY:
            for m in re.finditer(re.escape(term), text, re.I):
                start = max(text.rfind("\n", 0, m.start()), text.rfind(". ", 0, m.start()))
                end_candidates = [i for i in (text.find("\n", m.end()), text.find(". ", m.end())) if i != -1]
                sentence = text[start + 1:min(end_candidates) if end_candidates else len(text)].lower()
                assert re.search(r"\b(no|not|without|does not|do not|has not)\b", sentence), (path.name, sentence)

    def test_security_statement_lists_what_it_lacks(self):
        text = _read("SECURITY.md")
        for item in ("SOC 2", "ISO 27001", "penetration test", "HIPAA", "PCI"):
            assert item in text

    def test_security_statement_facts_match_the_repo(self):
        text = _read("SECURITY.md")
        assert "AES-256-GCM" in text
        fly = (project_root / "fly.toml").read_text(encoding="utf-8")
        assert "force_https = true" in fly
        assert "USER appuser" in (project_root / "Dockerfile").read_text(encoding="utf-8")
        assert (project_root / ".github" / "workflows" / "codeql.yml").exists()
        assert (project_root / ".github" / "dependabot.yml").exists()

    def test_root_security_md_points_at_the_page(self):
        root = (project_root / "SECURITY.md").read_text(encoding="utf-8")
        assert "/security" in root and "support assistant" in root


def test_unreadable_sources_fail_closed(monkeypatch):
    from types import SimpleNamespace
    from src.services import legal_pages

    def boom(*a, **k):
        raise OSError("unreadable")

    monkeypatch.setattr(legal_pages, "scan_placeholders", boom)
    monkeypatch.setattr(legal_pages, "_cache", None)
    assert legal_pages.is_live(SimpleNamespace(legal_pages_live=True)) is False
    assert legal_pages.is_live(SimpleNamespace(legal_pages_live=True)) is False
