"""Tier copy is one truth (v1.84.0): docs/FAQ.md, /billing, the landing page and the agent all
derive from src/services/tiers.py and the Settings defaults (AC1, AC16, AC20).

- every $price and requests/minute literal in the FAQ, /billing and the landing matches tiers.py
  and Settings defaults (the HTML keeps the literals; tests/test_currency_ui.py pins "$29");
- every tier-card <li> equals an ENTITLEMENTS string and vice versa;
- the removed entitlement phrases appear nowhere customer-facing (static/whats-new and
  docs/archive are historical and excluded);
- static/faq/index.html is in step with docs/FAQ.md (scripts/sync_faq.py --check).
"""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from scripts import sync_faq  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.main import app  # noqa: E402
from src.services import tiers  # noqa: E402

DEFAULTS = Settings(_env_file=None)
FAQ = (project_root / "docs" / "FAQ.md").read_text(encoding="utf-8")
BILLING = (project_root / "static" / "billing" / "index.html").read_text(encoding="utf-8")
LANDING = (project_root / "static" / "landing" / "index.html").read_text(encoding="utf-8")
client = TestClient(app)


def _tier_cards(html: str, card_class: str):
    """{tier name: [li texts]} for each <div class="card_class..."> block."""
    out = {}
    for block in re.findall(rf'<div class="{card_class}[^"]*">(.*?)</ul>', html, re.S):
        name = re.search(r"<h3>([^<]+)</h3>", block).group(1).strip().lower()
        out[name] = [re.sub(r"\s+", " ", li).strip() for li in re.findall(r"<li>(.*?)</li>", block, re.S)]
    return out


def _price_literal(html: str, tier_name: str) -> int:
    block = re.search(rf"<h3>{tier_name}</h3>.*?<div class=\"price\">\$(\d+) <span>/mo</span>", html, re.S)
    assert block, f"{tier_name} price block missing"
    return int(block.group(1))


class TestTiersModule:
    def test_prices_and_priorities(self):
        assert tiers.TIER_IDS == ("free", "pro", "enterprise")
        assert tiers.PRICE_USD_MONTH == {"free": 0, "pro": 29, "enterprise": 299}
        assert tiers.ESCALATION_PRIORITY == {"free": "standard", "pro": "pro", "enterprise": "enterprise"}

    def test_entitlements_derive_from_settings_not_literals(self):
        class S:
            rate_limit_free, rate_limit_pro, rate_limit_enterprise, rate_limit_per_minute = 7, 70, 700, 60
            support_daily_cap_anonymous_usd, support_daily_cap_free_usd = 0.01, 0.2
            support_daily_cap_pro_usd, support_daily_cap_enterprise_usd = 2.0, 20.0
            stripe_secret_key = None
            stripe_price_id_pro = stripe_price_id_enterprise = None
        assert tiers.entitlements("pro", S)[0] == "70 requests / minute"
        assert "$2.00/day" in tiers.entitlements("pro", S)[2]
        assert tiers.entitlements("free", S)[0] == "7 requests / minute"
        with pytest.raises(KeyError):
            tiers.entitlements("platinum", S)
        cat = tiers.tier_catalog(S)
        assert [t["rate_limit_per_minute"] for t in cat] == [7, 70, 700]
        assert all(t["checkout_available"] is False for t in cat)

    def test_no_forbidden_phrase_in_entitlements(self):
        for tier in tiers.TIER_IDS:
            for bullet in tiers.entitlements(tier, DEFAULTS):
                for phrase in tiers.FORBIDDEN_ENTITLEMENT_PHRASES:
                    assert phrase.lower() not in bullet.lower()


class TestFaqMatchesTiers:
    @pytest.mark.parametrize("tier", tiers.TIER_IDS)
    def test_price_rate_limit_and_allowance_row(self, tier):
        row = next(line for line in FAQ.splitlines() if line.startswith(f"| {tier.capitalize()} |"))
        assert f"${tiers.PRICE_USD_MONTH[tier]} / month" in row
        assert f"{tiers.rate_limit_for(tier, DEFAULTS)} requests / minute" in row
        assert f"${tiers.support_cap_for(tier, DEFAULTS):.2f} / day" in row
        assert tiers.escalation_priority_for(tier) in row

    def test_anonymous_allowance_and_grace_period(self):
        assert f"${DEFAULTS.support_daily_cap_anonymous_usd:.2f} / day" in FAQ
        assert f"{DEFAULTS.billing_past_due_grace_days}-day grace period" in FAQ

    def test_rate_limit_sentence(self):
        assert (f"Free {DEFAULTS.rate_limit_free}, Pro {DEFAULTS.rate_limit_pro}, Enterprise "
                f"{DEFAULTS.rate_limit_enterprise} requests per minute") in FAQ

    def test_faq_promises_only_what_exists(self):
        for phrase in tiers.FORBIDDEN_ENTITLEMENT_PHRASES:
            assert phrase.lower() not in FAQ.lower(), phrase
        for needle in ("best effort", "never changes your plan", "shown exactly once", "no self-serve recovery",
                       "Manage Billing", "/billing/key/rotate", "talk to a human"):
            assert needle in FAQ, needle
        assert not re.search(r"^\*\*Version\*\*", FAQ, re.M)


class TestHtmlTierCards:
    @pytest.mark.parametrize("html,card_class,label",
                             [(BILLING, "tier-card", "billing"), (LANDING, "tier", "landing")])
    def test_every_li_is_an_entitlement_and_vice_versa(self, html, card_class, label):
        cards = _tier_cards(html, card_class)
        assert set(cards) == set(tiers.TIER_IDS), label
        for tier in tiers.TIER_IDS:
            assert cards[tier] == tiers.entitlements(tier, DEFAULTS), (label, tier)

    @pytest.mark.parametrize("html,label", [(BILLING, "billing"), (LANDING, "landing")])
    def test_price_literals_match(self, html, label):
        for tier in tiers.TIER_IDS:
            assert _price_literal(html, tier.capitalize()) == tiers.PRICE_USD_MONTH[tier], (label, tier)

    def test_billing_page_has_no_coming_soon_and_loads_config(self):
        assert "Coming soon" not in BILLING and "Coming Soon" not in BILLING and "beta" not in BILLING.lower()
        assert "/billing/config" in BILLING and "Checkout unavailable on this server" in BILLING
        assert "Rotate key" in BILLING and "/billing/key/rotate" in BILLING
        assert 'href="/faq"' in BILLING and "/chat?mode=support" in BILLING

    def test_landing_has_no_contact_sales(self):
        assert "Contact sales" not in LANDING
        assert 'href="/faq"' in LANDING


class TestForbiddenPhrasesAbsent:
    SCAN = ["static", "docs", "agent", "README.md"]
    SKIP = ("static/whats-new", "docs/archive")

    def _files(self):
        for rel in self.SCAN:
            p = project_root / rel
            paths = [p] if p.is_file() else [x for x in p.rglob("*") if x.suffix in (".html", ".md", ".py", ".js")]
            for path in paths:
                r = str(path.relative_to(project_root)).replace("\\", "/")
                if r.startswith(self.SKIP) or r == "src/services/tiers.py":
                    continue
                yield r, path.read_text(encoding="utf-8", errors="ignore")

    @pytest.mark.parametrize("phrase", tiers.FORBIDDEN_ENTITLEMENT_PHRASES)
    def test_phrase_absent(self, phrase):
        offenders = [rel for rel, text in self._files()
                     if phrase in text and "FORBIDDEN_ENTITLEMENT_PHRASES" not in text]
        assert offenders == [], f"{phrase!r} still advertised in {offenders}"


class TestGeneratedPage:
    def test_faq_page_in_step_with_markdown(self):
        assert sync_faq.main(["--check"]) == 0

    def test_served_page_has_sections_and_help_links(self):
        resp = client.get("/faq/")
        assert resp.status_code == 200
        for heading in ("Plans and prices", "I lost my key or think it leaked", "Refunds, double charges and disputes",
                        "How do I reach a human?"):
            assert heading in resp.text, heading
        assert "/chat?mode=support" in resp.text and 'href="/billing"' in resp.text
        assert resp.text.count('<nav class="main-nav">') == 1

    def test_markdown_renderer_escapes_html(self):
        html = sync_faq.render_markdown("## T\n\n- <script>alert(1)</script> and `code` and [x](javascript:alert(1))")
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "<code>code</code>" in html and 'href="javascript' not in html

    def test_billing_config_entitlements_are_the_same_strings(self):
        body = client.get("/billing/config").json()
        for t in body["tiers"]:
            assert t["entitlements"] == tiers.entitlements(t["id"], DEFAULTS)
