"""Shared SecretStr unwrap helpers (v1.89.4): one definition, one None behaviour."""
from typing import Any


def secret_value(value: Any) -> str:
    """Return the plain string for a SecretStr or str; '' for None."""
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return "" if raw is None else str(raw)


def secret_present(value: Any) -> bool:
    """True when the value holds a non-blank secret."""
    return secret_value(value).strip() != ""
