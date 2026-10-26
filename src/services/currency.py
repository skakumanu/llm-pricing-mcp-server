"""Locale and currency presentation (v1.76.0).

One place owns everything about showing a USD price in another currency:

* the exact set of supported currencies (``SUPPORTED_CURRENCIES``),
* the deterministic country -> currency map (``COUNTRY_TO_CURRENCY``),
* loading the vendored rate table ``data/exchange_rates.json`` (never the
  network — the table is refreshed weekly by ``scripts/refresh_exchange_rates.py``
  through a human-merged PR, so serving a price never depends on reaching an
  external host),
* staleness (``RATE_STALE_AFTER_DAYS``), conversion and rounding,
* the single precedence function ``resolve_locale()``.

This is a presentation-layer concern: pricing data, the oracle, the aggregator
and the data-quality report are untouched. Conversion happens after the
catalogue is read, on per-request ``model_copy()``s, never on cached objects.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.models.pricing import (
    CurrencyConversion,
    LocalCost,
    LocalPrices,
    LocalUseCaseCost,
    LocalVolumePrice,
    PricingMetrics,
)

logger = logging.getLogger(__name__)

# OWNER DECISION (OQ2): exactly these thirteen. An explicitly requested code
# outside this tuple is rejected, never silently downgraded to USD.
SUPPORTED_CURRENCIES: Tuple[str, ...] = (
    "USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD", "BRL", "CNY", "SGD", "CHF", "KRW", "MXN",
)
ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW"})

# OWNER DECISION (OQ3): a rate may be a week old before it is flagged.
RATE_STALE_AFTER_DAYS = 7
CONVERSION_NOTE = "Converted amounts are indicative; providers bill in USD."

DEFAULT_RATES_PATH = Path(__file__).resolve().parents[2] / "data" / "exchange_rates.json"

# geolocation.py's sentinel for a private/loopback address: "no country".
UNDETECTED_SENTINELS = frozenset({"PRIVATE", "", "XX", "ZZ", "T1"})

_EUROZONE = (
    "AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES",
)

# Explicit and deterministic. Anything not listed falls back to USD with
# fallback_reason="country_not_mapped" — the response says so (AC4).
COUNTRY_TO_CURRENCY: Dict[str, str] = {
    **{c: "EUR" for c in _EUROZONE},
    **{c: "GBP" for c in ("GB", "GG", "JE", "IM")},
    "IN": "INR",
    "JP": "JPY",
    "CA": "CAD",
    **{c: "AUD" for c in ("AU", "CX", "CC", "NF")},
    "BR": "BRL",
    "CN": "CNY",
    "SG": "SGD",
    **{c: "CHF" for c in ("CH", "LI")},
    "KR": "KRW",
    "MX": "MXN",
    # Mapped to USD (not a fallback): the dollar is their currency.
    **{c: "USD" for c in ("US", "PR", "GU", "VI", "AS", "UM", "EC", "SV", "PA")},
}


class UnsupportedCurrencyError(ValueError):
    """Raised when an explicitly requested currency is outside SUPPORTED_CURRENCIES."""

    def __init__(self, code: str, supported: Tuple[str, ...] = SUPPORTED_CURRENCIES):
        self.code = code
        self.supported = list(supported)
        super().__init__(
            f"Unsupported currency '{code}'. Supported currencies: {', '.join(self.supported)}"
        )


# ---------------------------------------------------------------------------
# Rate table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExchangeRateTable:
    """The vendored USD-based rate table, as loaded from disk."""

    base: str
    as_of: date
    rates: Dict[str, float]
    source: str
    placeholder: bool = False

    def age_days(self, today: Optional[date] = None) -> int:
        today = today or datetime.now(UTC).date()
        return max(0, (today - self.as_of).days)

    def is_stale(self, today: Optional[date] = None) -> bool:
        return self.age_days(today) > RATE_STALE_AFTER_DAYS

    def rate_for(self, currency: str) -> Optional[float]:
        value = self.rates.get(currency)
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value <= 0 or value != value or value in (float("inf"), float("-inf")):
            return None
        return value


def _parse_rate_table(raw: dict) -> ExchangeRateTable:
    base = str(raw.get("base", "USD")).upper()
    if base != "USD":
        raise ValueError(f"rate table base must be USD, got {base!r}")
    as_of = date.fromisoformat(str(raw["as_of"]))
    rates = raw.get("rates")
    if not isinstance(rates, dict) or not rates:
        raise ValueError("rate table has no rates")
    cleaned: Dict[str, float] = {}
    for code, value in rates.items():
        cleaned[str(code).upper()] = float(value)
    if abs(cleaned.get("USD", 0.0) - 1.0) > 1e-9:
        raise ValueError("rate table must carry USD == 1.0")
    return ExchangeRateTable(
        base="USD",
        as_of=as_of,
        rates=cleaned,
        source=str(raw.get("source", "unknown")),
        placeholder=bool(raw.get("placeholder", False)),
    )


@lru_cache(maxsize=8)
def _load_rate_table_cached(path_str: str) -> Optional[ExchangeRateTable]:
    path = Path(path_str)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _parse_rate_table(raw)
    except FileNotFoundError:
        logger.warning("Exchange-rate table not found at %s; serving USD only", path)
    except Exception as exc:  # corrupt file: USD-only, never a 500
        logger.warning("Exchange-rate table at %s unusable (%s); serving USD only", path, exc)
    return None


def load_rate_table(path: Optional[str | Path] = None) -> Optional[ExchangeRateTable]:
    """Read the vendored table once per path (cached). Never performs network I/O.

    Returns None when the file is missing or corrupt; callers treat that as
    "conversion unavailable" and serve USD with ``fallback_reason='rates_unavailable'``.
    """
    return _load_rate_table_cached(str(path or DEFAULT_RATES_PATH))


def reset_rate_table_cache() -> None:
    """Forget loaded tables (tests; after the weekly refresh)."""
    _load_rate_table_cached.cache_clear()


# ---------------------------------------------------------------------------
# Locale resolution
# ---------------------------------------------------------------------------

def normalize_country(country: Optional[str]) -> Optional[str]:
    """Upper-case ISO-3166 alpha-2, or None for anything unusable (no error)."""
    if not country or not isinstance(country, str):
        return None
    code = country.strip().upper()
    if len(code) != 2 or not code.isalpha():
        return None
    if code in UNDETECTED_SENTINELS:
        return None
    return code


def map_country_to_currency(country: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(currency, normalized_country)``; currency is None when unmapped."""
    code = normalize_country(country)
    if code is None:
        return None, None
    return COUNTRY_TO_CURRENCY.get(code), code


@dataclass
class LocaleContext:
    """What the request resolved to. ``active`` decides whether local_* fields appear."""

    currency: str = "USD"
    country: Optional[str] = None
    country_source: Optional[str] = None          # 'query' | 'header' | 'platform_header' | 'ip' | None
    currency_source: str = "default"              # 'query' | 'header' | 'country' | 'default'
    fallback_to_usd: bool = False
    fallback_reason: Optional[str] = None         # 'country_not_mapped' | 'rates_unavailable'
    rate: Optional[float] = None
    rate_as_of: Optional[str] = None
    rate_age_days: Optional[int] = None
    rate_is_stale: bool = False
    rate_source: Optional[str] = None
    rate_placeholder: bool = False
    detected: bool = False                        # country came from IP / platform header

    @property
    def active(self) -> bool:
        """True iff the response should carry conversion metadata.

        A plain request (no currency, no country, nothing detected) is
        inactive and the payload is byte-for-byte today's. Any explicit or
        detected country makes the context active even when the result is USD
        (AC2/AC4: the caller asked about a place and gets told what happened).
        """
        return self.currency != "USD" or self.country is not None or self.fallback_to_usd

    @property
    def source(self) -> str:
        """Single 'how did we get here' label for GET /locale."""
        if self.currency_source in ("query", "header"):
            return self.currency_source
        if self.country_source:
            return self.country_source
        return "default"


def resolve_locale(
    currency: Optional[str] = None,
    country: Optional[str] = None,
    detected_country: Optional[str] = None,
    table: Optional[ExchangeRateTable] = None,
    *,
    currency_source: Optional[str] = None,
    country_source: Optional[str] = None,
    detected_source: str = "ip",
    today: Optional[date] = None,
) -> LocaleContext:
    """Pure precedence function: explicit currency > explicit country > detected country > USD.

    A stated currency beats a stated country at every level (the country then
    only labels the response). Case-insensitive input; canonical upper-case
    echo. Raises UnsupportedCurrencyError for an explicit code outside the
    supported set. Never raises for a country: unmapped or malformed -> USD
    with ``fallback_to_usd`` and ``fallback_reason='country_not_mapped'``.
    """
    ctx = LocaleContext()

    # Country: explicit first, else detected. Recorded even when a currency is
    # also stated (labels only). A stated-but-unusable value ("Germany", "123")
    # is not an error: it still counts as "the caller stated a country" — the
    # source is recorded and detection is not consulted — but it maps to nothing.
    stated_country = bool(country and str(country).strip())
    explicit_code = normalize_country(country)
    if explicit_code is not None:
        ctx.country = explicit_code
        ctx.country_source = country_source or "query"
    elif stated_country:
        ctx.country_source = country_source or "query"
    else:
        detected_code = normalize_country(detected_country)
        if detected_code is not None:
            ctx.country = detected_code
            ctx.country_source = detected_source
            ctx.detected = True

    # Currency: explicit wins; else derive from the resolved country.
    wanted: Optional[str] = None
    if currency is not None and str(currency).strip():
        code = str(currency).strip().upper()
        if code not in SUPPORTED_CURRENCIES:
            raise UnsupportedCurrencyError(code)
        wanted = code
        ctx.currency_source = currency_source or "query"
    elif ctx.country is not None or stated_country:
        # The one mapping rule, shared with callers/tests via map_country_to_currency.
        mapped, _ = map_country_to_currency(ctx.country)
        if mapped is None:
            ctx.fallback_to_usd = True
            ctx.fallback_reason = "country_not_mapped"
            wanted = "USD"
        else:
            wanted = mapped
        ctx.currency_source = "country"
    else:
        wanted = "USD"
        ctx.currency_source = "default"

    # Rate: USD never needs the table. Anything else does; a missing/corrupt
    # table or a missing code means USD with a stated reason, never a 500.
    if wanted == "USD":
        ctx.currency = "USD"
        ctx.rate = 1.0
        if table is not None:
            _attach_table_meta(ctx, table, today)
        return ctx

    rate = table.rate_for(wanted) if table is not None else None
    if rate is None:
        ctx.currency = "USD"
        ctx.rate = 1.0
        ctx.fallback_to_usd = True
        ctx.fallback_reason = "rates_unavailable"
        if table is not None:
            _attach_table_meta(ctx, table, today)
        return ctx

    ctx.currency = wanted
    ctx.rate = rate
    _attach_table_meta(ctx, table, today)
    return ctx


def _attach_table_meta(ctx: LocaleContext, table: ExchangeRateTable, today: Optional[date]) -> None:
    ctx.rate_as_of = table.as_of.isoformat()
    ctx.rate_age_days = table.age_days(today)
    ctx.rate_is_stale = table.is_stale(today)
    ctx.rate_source = table.source
    ctx.rate_placeholder = table.placeholder


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _significant(value: float, digits: int) -> float:
    if value == 0:
        return 0.0
    return float(f"{value:.{digits}g}")


def convert(usd: float, ctx: LocaleContext, *, per_token: bool = False) -> float:
    """USD -> ctx.currency.

    Per-token rates keep 10 significant digits so a tiny value is never
    collapsed to 0 (AC7). Amounts are rounded to 6 decimals, except that a
    non-zero amount which would round to 0 keeps its significant digits.
    """
    if usd is None:
        return usd
    rate = ctx.rate if ctx.rate is not None else 1.0
    value = float(usd) * rate
    if per_token or (value != 0 and abs(value) < 1e-4):
        return _significant(value, 10)
    return round(value, 6)


def build_conversion(ctx: LocaleContext) -> Optional[CurrencyConversion]:
    """The ``currency_conversion`` block, or None when the context is inactive."""
    if not ctx.active:
        return None
    return CurrencyConversion(
        currency=ctx.currency,
        base_currency="USD",
        rate=ctx.rate if ctx.rate is not None else 1.0,
        rate_as_of=ctx.rate_as_of,
        rate_age_days=ctx.rate_age_days,
        rate_is_stale=ctx.rate_is_stale,
        rate_source=ctx.rate_source,
        rate_placeholder=ctx.rate_placeholder,
        country=ctx.country,
        country_source=ctx.country_source,
        currency_source=ctx.currency_source,
        fallback_to_usd=ctx.fallback_to_usd,
        fallback_reason=ctx.fallback_reason,
        note=CONVERSION_NOTE,
    )


def conversion_metadata(ctx: LocaleContext) -> Optional[dict]:
    """``build_conversion`` as a plain dict (MCP tools, /pricing/public)."""
    conv = build_conversion(ctx)
    return conv.model_dump() if conv is not None else None


def local_prices_for(model: PricingMetrics, ctx: LocaleContext) -> LocalPrices:
    in_tok = convert(model.cost_per_input_token, ctx, per_token=True)
    out_tok = convert(model.cost_per_output_token, ctx, per_token=True)
    in_1m = convert(model.cost_per_input_token * 1_000_000, ctx)
    out_1m = convert(model.cost_per_output_token * 1_000_000, ctx)
    return LocalPrices(
        currency=ctx.currency,
        cost_per_input_token=in_tok,
        cost_per_output_token=out_tok,
        cost_per_1m_input=in_1m,
        cost_per_1m_output=out_1m,
        cost_at_1m_tokens=LocalVolumePrice(
            input_cost=round(in_1m, 2),
            output_cost=round(out_1m, 2),
            total_cost=round((in_1m + out_1m) / 2, 2),
        ),
    )


def localize_metrics(model: PricingMetrics, ctx: LocaleContext) -> PricingMetrics:
    """A per-request copy of ``model`` carrying ``local_prices``. The original is never mutated."""
    if not ctx.active:
        return model
    return model.model_copy(update={"local_prices": local_prices_for(model, ctx)})


def localize_cost(
    input_cost: float,
    output_cost: float,
    total_cost: float,
    ctx: LocaleContext,
    cost_per_1m_tokens: Optional[float] = None,
) -> Optional[LocalCost]:
    if not ctx.active:
        return None
    return LocalCost(
        currency=ctx.currency,
        input_cost=convert(input_cost, ctx),
        output_cost=convert(output_cost, ctx),
        total_cost=convert(total_cost, ctx),
        cost_per_1m_tokens=convert(cost_per_1m_tokens, ctx) if cost_per_1m_tokens is not None else None,
    )


def localize_use_case_cost(model: PricingMetrics, ctx: LocaleContext) -> Optional[LocalUseCaseCost]:
    if not ctx.active:
        return None
    in_1m_usd = model.cost_per_input_token * 1_000_000
    out_1m_usd = model.cost_per_output_token * 1_000_000
    avg_usd = (in_1m_usd + out_1m_usd) / 2
    return LocalUseCaseCost(
        currency=ctx.currency,
        avg_cost_per_1m_tokens=convert(avg_usd, ctx),
        input_per_1m=convert(in_1m_usd, ctx),
        output_per_1m=convert(out_1m_usd, ctx),
        avg_cost_per_1m_tokens_usd=round(avg_usd, 6),
    )
