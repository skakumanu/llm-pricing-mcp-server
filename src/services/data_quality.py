"""Single source of truth for the public data-quality summary.

check_price_drift lists individual disputes; this is the aggregate a customer
can check in one call to gauge overall trust in the catalogue — how much of it
is confirmed, how much is stale, how many models are currently withheld for
price drift — without inspecting every model's provenance fields themselves.

Only per-token priced models are considered. Subscription-priced IDE tools
(Copilot, Cursor, ...) have no registry to drift against, so folding them in
would just dilute the signal with models that can never appear in any of
these counts.
"""
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class DataQualityReport:
    total_models: int
    confirmed_models: int
    confirmed_pct: float
    withheld_for_drift: int
    never_priced: int
    stale_models: int
    stale_pct: float
    registry_source: Optional[str]
    registry_models: int
    registry_fetched: Optional[str]
    summary: str
    # v1.74.0 — the three numbers the Oct 2026 reconfirmation pass showed were
    # missing: how much of the catalogue the registry can actually vouch for,
    # how many listed models the provider has already shut down, and how many
    # confirmed prices have no independent check at all.
    retired_listed: int = 0
    registry_coverage_pct: Optional[float] = None
    unmatched_confirmed: int = 0
    # v1.75.0 — the split of usable prices by where the number came from.
    # provider_confirmed + registry_sourced == confirmed_models, always. A
    # registry-sourced price is usable in every estimate and comparison, but it
    # was never checked against the provider's own rate card, and a customer
    # deciding how much to trust the catalogue should see that count.
    provider_confirmed: int = 0
    registry_sourced: int = 0

    def as_dict(self) -> dict:
        return {
            "total_models": self.total_models,
            "confirmed_models": self.confirmed_models,
            "provider_confirmed": self.provider_confirmed,
            "registry_sourced": self.registry_sourced,
            "confirmed_pct": round(self.confirmed_pct, 1),
            "withheld_for_drift": self.withheld_for_drift,
            "never_priced": self.never_priced,
            "retired_listed": self.retired_listed,
            "stale_models": self.stale_models,
            "stale_pct": round(self.stale_pct, 1),
            "registry_coverage_pct": (
                round(self.registry_coverage_pct, 1) if self.registry_coverage_pct is not None else None
            ),
            "unmatched_confirmed": self.unmatched_confirmed,
            "registry_source": self.registry_source,
            "registry_models": self.registry_models,
            "registry_fetched": self.registry_fetched,
            "summary": self.summary,
        }


def compute_data_quality_report(all_models: List[Any], oracle: Any) -> DataQualityReport:
    """Build the report from a model list that includes unconfirmed models.

    *all_models* must come from ``get_all_pricing_async(include_unconfirmed=True)``
    — the default (confirmed-only) output has already had withheld and
    never-priced models filtered out, which is exactly what this needs to count.
    """
    per_token = [m for m in all_models if m.pricing_model == "per_token"]
    total = len(per_token)

    confirmed = [m for m in per_token if m.price_confirmed]
    unconfirmed = [m for m in per_token if not m.price_confirmed]
    # A withheld (drift-demoted) model keeps its original nonzero price; a
    # never-priced one holds the 0.0 placeholder — see find_drift()/demote_drifted()
    # in price_oracle.py for why that's the right discriminator between the two.
    retired = [m for m in unconfirmed if getattr(m, "retired_on", None)]
    # A retired model is withheld too, but it is a different story from drift:
    # the provider shut it down. Count it once, under retirement.
    withheld = [m for m in unconfirmed if m.cost_per_input_token > 0 and m not in retired]
    never_priced = [m for m in unconfirmed if m.cost_per_input_token <= 0 and m not in retired]
    stale = [m for m in confirmed if getattr(m, "price_is_stale", False)]
    registry_sourced = [m for m in confirmed if getattr(m, "price_source_kind", "provider") == "registry"]
    provider_confirmed = len(confirmed) - len(registry_sourced)

    confirmed_pct = (len(confirmed) / total * 100) if total else 0.0
    stale_pct = (len(stale) / len(confirmed) * 100) if confirmed else 0.0

    # How much of the confirmed catalogue the registry can independently vouch
    # for. Before v1.73.11 this was ~60% without anyone knowing, because the
    # oracle's key matching silently skipped dotted and slashed model names.
    lookup = getattr(oracle, "lookup", None)
    coverage_pct: Optional[float] = None
    unmatched = 0
    if callable(lookup) and confirmed:
        matched = 0
        for m in confirmed:
            try:
                hit = lookup(m.model_name, m.provider, require_provider_match=True)
            except TypeError:
                hit = None
            if hit is not None:
                matched += 1
        unmatched = len(confirmed) - matched
        coverage_pct = matched / len(confirmed) * 100

    if not unconfirmed and not stale:
        summary = f"All {total} priced models have a confirmed, fresh price."
    else:
        parts = [f"{confirmed_pct:.1f}% of {total} priced models have a confirmed price."]
        if withheld:
            parts.append(
                f"{len(withheld)} withheld due to price drift — see check_price_drift."
            )
        if never_priced:
            parts.append(f"{len(never_priced)} newly discovered, not yet priced.")
        if retired:
            parts.append(f"{len(retired)} listed model(s) retired by their provider, withheld.")
        if stale:
            parts.append(f"{len(stale)} confirmed price(s) are over 90 days old.")
        summary = " ".join(parts)
    if coverage_pct is not None:
        summary += (
            f" Registry independently confirms {coverage_pct:.1f}% of confirmed prices"
            f" ({unmatched} have no first-party registry entry)."
        )
    if confirmed:
        summary += f" {provider_confirmed} provider-confirmed, {len(registry_sourced)} registry-sourced."

    return DataQualityReport(
        total_models=total,
        confirmed_models=len(confirmed),
        confirmed_pct=confirmed_pct,
        withheld_for_drift=len(withheld),
        never_priced=len(never_priced),
        stale_models=len(stale),
        stale_pct=stale_pct,
        registry_source=oracle.source,
        registry_models=oracle.model_count,
        registry_fetched=oracle.fetched_at,
        summary=summary,
        retired_listed=len(retired),
        registry_coverage_pct=coverage_pct,
        unmatched_confirmed=unmatched,
        provider_confirmed=provider_confirmed,
        registry_sourced=len(registry_sourced),
    )
