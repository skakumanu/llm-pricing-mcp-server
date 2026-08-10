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

    def as_dict(self) -> dict:
        return {
            "total_models": self.total_models,
            "confirmed_models": self.confirmed_models,
            "confirmed_pct": round(self.confirmed_pct, 1),
            "withheld_for_drift": self.withheld_for_drift,
            "never_priced": self.never_priced,
            "stale_models": self.stale_models,
            "stale_pct": round(self.stale_pct, 1),
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
    withheld = [m for m in unconfirmed if m.cost_per_input_token > 0]
    never_priced = [m for m in unconfirmed if m.cost_per_input_token <= 0]
    stale = [m for m in confirmed if getattr(m, "price_is_stale", False)]

    confirmed_pct = (len(confirmed) / total * 100) if total else 0.0
    stale_pct = (len(stale) / len(confirmed) * 100) if confirmed else 0.0

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
        if stale:
            parts.append(f"{len(stale)} confirmed price(s) are over 90 days old.")
        summary = " ".join(parts)

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
    )
