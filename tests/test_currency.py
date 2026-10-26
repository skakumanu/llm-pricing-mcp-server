"""Unit tests for src/services/currency.py (v1.76.0).

Everything here is offline: the rate table is the vendored
data/exchange_rates.json or a temp file written by the test.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services import currency as cur  # noqa: E402

EUROZONE = ["AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE",
            "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES"]


@pytest.fixture
def table():
    cur.reset_rate_table_cache()
    t = cur.load_rate_table()
    assert t is not None
    return t


def _write_table(path: Path, as_of: date, rates=None, **extra) -> Path:
    rates = rates or {c: (1.0 if c == "USD" else 2.0) for c in cur.SUPPORTED_CURRENCIES}
    body = {"base": "USD", "as_of": as_of.isoformat(), "source": "test", "placeholder": True, "rates": rates}
    body.update(extra)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


class TestSupportedSet:
    def test_exactly_the_thirteen(self):
        assert cur.SUPPORTED_CURRENCIES == (
            "USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "BRL", "CNY", "SGD", "CHF", "KRW", "MXN",
        )

    def test_zero_decimal_set(self):
        assert cur.ZERO_DECIMAL_CURRENCIES == {"JPY", "KRW"}

    def test_every_mapped_currency_is_supported(self):
        assert set(cur.COUNTRY_TO_CURRENCY.values()) <= set(cur.SUPPORTED_CURRENCIES)


class TestCountryMap:
    @pytest.mark.parametrize("code", EUROZONE)
    def test_eurozone_maps_to_eur(self, code):
        assert cur.map_country_to_currency(code) == ("EUR", code)

    @pytest.mark.parametrize("code,expected", [
        ("GB", "GBP"), ("gb", "GBP"), ("IN", "INR"), ("JP", "JPY"), ("CA", "CAD"), ("AU", "AUD"),
        ("BR", "BRL"), ("CN", "CNY"), ("SG", "SGD"), ("CH", "CHF"), ("KR", "KRW"), ("MX", "MXN"),
        ("US", "USD"), ("PR", "USD"),
    ])
    def test_named_mappings(self, code, expected):
        assert cur.map_country_to_currency(code)[0] == expected

    def test_map_is_deterministic(self):
        for code in cur.COUNTRY_TO_CURRENCY:
            assert cur.map_country_to_currency(code) == cur.map_country_to_currency(code)

    @pytest.mark.parametrize("value", ["ZW", "Germany", "123", "", None, "D", "DEU", "PRIVATE", " "])
    def test_unmappable_values_never_raise(self, value):
        currency, _ = cur.map_country_to_currency(value)
        assert currency is None

    def test_us_is_mapped_not_fallback(self, table):
        ctx = cur.resolve_locale(country="US", table=table)
        assert ctx.currency == "USD" and ctx.fallback_to_usd is False and ctx.active is True


class TestResolvePrecedence:
    def test_no_input_is_inactive_usd(self, table):
        ctx = cur.resolve_locale(table=table)
        assert ctx.currency == "USD" and ctx.active is False and ctx.currency_source == "default"
        assert ctx.source == "default"
        assert cur.build_conversion(ctx) is None and cur.conversion_metadata(ctx) is None

    def test_explicit_usd_is_inactive_like_no_input(self, table):
        ctx = cur.resolve_locale(currency="usd", table=table)
        assert ctx.currency == "USD" and ctx.active is False

    def test_currency_is_case_insensitive_and_echoed_upper(self, table):
        ctx = cur.resolve_locale(currency="eur", table=table)
        assert ctx.currency == "EUR" and ctx.rate == table.rates["EUR"] and ctx.active

    def test_country_derives_currency(self, table):
        ctx = cur.resolve_locale(country="in", table=table)
        assert (ctx.currency, ctx.country, ctx.currency_source) == ("INR", "IN", "country")

    def test_currency_beats_country(self, table):
        ctx = cur.resolve_locale(currency="GBP", country="IN", table=table)
        assert ctx.currency == "GBP" and ctx.country == "IN" and ctx.currency_source == "query"

    def test_explicit_country_beats_detected(self, table):
        ctx = cur.resolve_locale(country="JP", detected_country="DE", table=table)
        assert ctx.currency == "JPY" and ctx.country == "JP" and ctx.detected is False

    def test_detected_country_used_when_nothing_explicit(self, table):
        ctx = cur.resolve_locale(detected_country="DE", table=table, detected_source="platform_header")
        assert ctx.currency == "EUR" and ctx.detected is True and ctx.country_source == "platform_header"
        assert ctx.source == "platform_header"

    def test_header_sources_are_recorded(self, table):
        ctx = cur.resolve_locale(currency="CAD", country="JP", table=table,
                                 currency_source="header", country_source="header")
        assert ctx.currency_source == "header" and ctx.country_source == "header" and ctx.source == "header"

    def test_private_sentinel_is_undetected(self, table):
        ctx = cur.resolve_locale(detected_country="PRIVATE", table=table)
        assert ctx.active is False and ctx.country is None

    def test_unmapped_country_falls_back_with_reason(self, table):
        ctx = cur.resolve_locale(country="ZW", table=table)
        assert ctx.currency == "USD" and ctx.fallback_to_usd and ctx.fallback_reason == "country_not_mapped"
        assert ctx.active is True and ctx.rate == 1.0

    def test_malformed_explicit_country_falls_back_not_raises(self, table):
        ctx = cur.resolve_locale(country="Germany", table=table)
        assert ctx.currency == "USD" and ctx.fallback_to_usd and ctx.fallback_reason == "country_not_mapped"
        # Stated, just unusable: the source says the caller supplied it (same as ZW),
        # rather than looking like a request that stated nothing.
        assert ctx.country is None and ctx.country_source == "query" and ctx.source == "query"
        ctx = cur.resolve_locale(country="123", table=table, country_source="header")
        assert ctx.fallback_to_usd and ctx.source == "header"

    def test_unusable_explicit_country_still_suppresses_detection(self, table):
        # Explicit beats detected even when the explicit value cannot be mapped: a
        # detected DE must not silently override what the caller asked for.
        ctx = cur.resolve_locale(country="Germany", detected_country="DE", table=table)
        assert ctx.currency == "USD" and ctx.fallback_to_usd and ctx.detected is False and ctx.country is None

    def test_unsupported_currency_raises_listing_all_thirteen(self, table):
        with pytest.raises(cur.UnsupportedCurrencyError) as exc:
            cur.resolve_locale(currency="XYZ", table=table)
        assert exc.value.code == "XYZ"
        assert exc.value.supported == list(cur.SUPPORTED_CURRENCIES)
        assert "XYZ" in str(exc.value) and "MXN" in str(exc.value)

    def test_unsupported_currency_raises_even_with_valid_country(self, table):
        with pytest.raises(cur.UnsupportedCurrencyError):
            cur.resolve_locale(currency="ZZZ", country="DE", table=table)

    def test_missing_table_serves_usd_with_rates_unavailable(self):
        ctx = cur.resolve_locale(currency="EUR", table=None)
        assert ctx.currency == "USD" and ctx.fallback_to_usd and ctx.fallback_reason == "rates_unavailable"

    def test_table_missing_the_code_serves_usd_with_rates_unavailable(self, tmp_path):
        rates = {c: 2.0 for c in cur.SUPPORTED_CURRENCIES if c != "EUR"}
        rates["USD"] = 1.0
        path = _write_table(tmp_path / "r.json", date.today(), rates)
        cur.reset_rate_table_cache()
        t = cur.load_rate_table(path)
        ctx = cur.resolve_locale(currency="EUR", table=t)
        assert ctx.currency == "USD" and ctx.fallback_reason == "rates_unavailable"


class TestStaleness:
    def test_seven_days_is_fresh_eight_is_stale(self, tmp_path):
        as_of = date(2026, 10, 1)
        t = cur.load_rate_table(_write_table(tmp_path / "r.json", as_of))
        assert t.is_stale(today=as_of + timedelta(days=7)) is False
        assert t.age_days(today=as_of + timedelta(days=7)) == 7
        assert t.is_stale(today=as_of + timedelta(days=8)) is True

    def test_resolved_context_carries_staleness(self, tmp_path):
        as_of = date(2026, 10, 1)
        t = cur.load_rate_table(_write_table(tmp_path / "r.json", as_of))
        stale = cur.resolve_locale(currency="EUR", table=t, today=as_of + timedelta(days=10))
        fresh = cur.resolve_locale(currency="EUR", table=t, today=as_of + timedelta(days=2))
        assert stale.rate_is_stale is True and stale.rate_age_days == 10 and stale.rate_as_of == "2026-10-01"
        assert fresh.rate_is_stale is False and fresh.rate_age_days == 2
        assert cur.build_conversion(stale).rate_is_stale is True

    def test_future_as_of_has_zero_age(self, tmp_path):
        as_of = date(2026, 10, 10)
        t = cur.load_rate_table(_write_table(tmp_path / "r.json", as_of))
        assert t.age_days(today=as_of - timedelta(days=3)) == 0


class TestRateTableLoading:
    def test_vendored_table_has_exactly_the_thirteen_codes(self):
        raw = json.loads((project_root / "data" / "exchange_rates.json").read_text(encoding="utf-8"))
        assert raw["base"] == "USD"
        assert set(raw["rates"]) == set(cur.SUPPORTED_CURRENCIES)
        assert raw["rates"]["USD"] == 1.0
        assert all(isinstance(v, (int, float)) and v > 0 for v in raw["rates"].values())
        date.fromisoformat(raw["as_of"])
        assert isinstance(raw["placeholder"], bool) and raw["source"]

    def test_placeholder_table_loads_and_is_flagged(self, tmp_path):
        t = cur.load_rate_table(_write_table(tmp_path / "r.json", date.today(), placeholder=True))
        assert t.placeholder is True and t.base == "USD"
        ctx = cur.resolve_locale(currency="EUR", table=t)
        assert cur.build_conversion(ctx).rate_placeholder is True

    def test_missing_file_returns_none_not_exception(self, tmp_path):
        cur.reset_rate_table_cache()
        assert cur.load_rate_table(tmp_path / "nope.json") is None

    def test_corrupt_file_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert cur.load_rate_table(p) is None

    def test_non_usd_base_is_rejected(self, tmp_path):
        p = _write_table(tmp_path / "eur.json", date.today())
        body = json.loads(p.read_text())
        body["base"] = "EUR"
        p.write_text(json.dumps(body))
        assert cur.load_rate_table(p) is None

    def test_usd_not_one_is_rejected(self, tmp_path):
        rates = {c: 2.0 for c in cur.SUPPORTED_CURRENCIES}
        assert cur.load_rate_table(_write_table(tmp_path / "r.json", date.today(), rates)) is None

    def test_loader_is_cached_per_path_and_resettable(self, tmp_path):
        p = _write_table(tmp_path / "r.json", date(2026, 1, 1))
        cur.reset_rate_table_cache()
        first = cur.load_rate_table(p)
        _write_table(p, date(2026, 2, 2))
        assert cur.load_rate_table(p) is first  # cached
        cur.reset_rate_table_cache()
        assert cur.load_rate_table(p).as_of == date(2026, 2, 2)

    def test_default_path_is_the_vendored_file(self):
        assert cur.DEFAULT_RATES_PATH == project_root / "data" / "exchange_rates.json"


class TestConversionAndRounding:
    def test_per_token_never_rounds_to_zero(self, table):
        ctx = cur.resolve_locale(currency="GBP", table=table)
        for usd in (1e-7, 1e-9, 1e-12, 3e-6):
            assert cur.convert(usd, ctx, per_token=True) > 0

    def test_tiny_amount_keeps_significant_digits(self, table):
        # Relative to whatever the vendored table holds: the weekly refresh job runs
        # this file against the freshly fetched rates, so no rate may be hard-coded.
        ctx = cur.resolve_locale(currency="EUR", table=table)
        assert cur.convert(1e-7, ctx) == pytest.approx(1e-7 * table.rates["EUR"])
        assert cur.convert(0.0, ctx) == 0.0

    def test_amounts_round_to_six_decimals(self, table):
        ctx = cur.resolve_locale(currency="EUR", table=table)
        assert cur.convert(1.23456789, ctx) == round(1.23456789 * table.rates["EUR"], 6)

    def test_usd_context_is_identity(self, table):
        ctx = cur.resolve_locale(table=table)
        assert cur.convert(0.123456789, ctx) == 0.123457
        assert cur.convert(2.5e-6, ctx, per_token=True) == 2.5e-6

    def test_jpy_local_prices_shape(self, table):
        model = PricingMetrics(model_name="m", provider="p", cost_per_input_token=3e-6, cost_per_output_token=1.5e-5)
        ctx = cur.resolve_locale(currency="JPY", table=table)
        lp = cur.local_prices_for(model, ctx)
        assert lp.currency == "JPY"
        assert lp.cost_per_input_token == pytest.approx(3e-6 * table.rates["JPY"])
        assert lp.cost_per_1m_input == pytest.approx(3.0 * table.rates["JPY"])
        assert lp.cost_at_1m_tokens.total_cost == pytest.approx((3.0 + 15.0) / 2 * table.rates["JPY"], abs=0.01)

    def test_localize_metrics_copies_never_mutates(self, table):
        model = PricingMetrics(model_name="m", provider="p", cost_per_input_token=3e-6, cost_per_output_token=1.5e-5)
        ctx = cur.resolve_locale(currency="EUR", table=table)
        local = cur.localize_metrics(model, ctx)
        assert local is not model and model.local_prices is None and local.local_prices.currency == "EUR"
        assert "local_prices" not in model.model_dump()
        assert local.model_dump()["local_prices"]["currency"] == "EUR"
        assert local.cost_per_input_token == model.cost_per_input_token  # USD untouched

    def test_localize_metrics_inactive_returns_same_object(self, table):
        model = PricingMetrics(model_name="m", provider="p", cost_per_input_token=3e-6, cost_per_output_token=1.5e-5)
        assert cur.localize_metrics(model, cur.resolve_locale(table=table)) is model

    def test_localize_cost_and_use_case(self, table):
        ctx = cur.resolve_locale(currency="INR", table=table)
        inr = table.rates["INR"]
        lc = cur.localize_cost(0.003, 0.0075, 0.0105, ctx, cost_per_1m_tokens=7.0)
        assert lc.currency == "INR" and lc.total_cost == pytest.approx(0.0105 * inr)
        assert lc.cost_per_1m_tokens == pytest.approx(7.0 * inr)
        assert cur.localize_cost(1, 1, 2, cur.resolve_locale(table=table)) is None
        model = PricingMetrics(model_name="m", provider="p", cost_per_input_token=3e-6, cost_per_output_token=1.5e-5)
        uc = cur.localize_use_case_cost(model, ctx)
        assert uc.avg_cost_per_1m_tokens_usd == 9.0 and uc.avg_cost_per_1m_tokens == pytest.approx(9.0 * inr)

    def test_conversion_block_contents(self, table):
        ctx = cur.resolve_locale(currency="EUR", country="DE", table=table)
        conv = cur.build_conversion(ctx)
        assert conv.currency == "EUR" and conv.base_currency == "USD" and conv.rate == table.rates["EUR"]
        assert conv.rate_as_of == table.as_of.isoformat() and conv.note == cur.CONVERSION_NOTE
        assert conv.country == "DE" and conv.currency_source == "query" and conv.fallback_to_usd is False
        assert set(cur.conversion_metadata(ctx)) >= {
            "currency", "rate", "rate_as_of", "rate_age_days", "rate_is_stale", "rate_source",
            "country", "country_source", "currency_source", "fallback_to_usd", "fallback_reason", "note",
        }
