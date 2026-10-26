"""Shared locale handling for the MCP tools that accept ``currency`` / ``country`` (v1.76.0).

MCP never detects anything: there is no visitor IP on STDIO and the HTTP
transport deliberately does not infer one either. A tool sees only the
arguments the caller passed. Omitted arguments resolve to an inactive context,
so every tool's output is exactly its pre-v1.76.0 dict.
"""
from typing import Any, Dict, Union

from src.config.settings import settings
from src.services.currency import LocaleContext, UnsupportedCurrencyError, load_rate_table, resolve_locale


def resolve_tool_locale(arguments: Dict[str, Any]) -> Union[LocaleContext, Dict[str, Any]]:
    """Resolve the tool's optional ``currency``/``country`` arguments.

    Returns a LocaleContext, or the tool's standard ``success: false`` error
    dict when the currency is outside the supported set.
    """
    currency = arguments.get("currency")
    country = arguments.get("country")
    try:
        return resolve_locale(
            currency=currency if isinstance(currency, str) else None,
            country=country if isinstance(country, str) else None,
            detected_country=None,
            table=load_rate_table(settings.exchange_rates_path),
            currency_source="query" if currency else None,
            country_source="query" if country else None,
        )
    except UnsupportedCurrencyError as exc:
        return {
            "success": False,
            "error": f"Unsupported currency '{exc.code}'",
            "error_type": "unsupported_currency",
            "supported_currencies": exc.supported,
        }
