"""Shared client handling for the MCP tools that accept ``client`` (v1.85.0).

Precedence: explicit ``client`` argument > legacy ``ide_context`` argument >
MCP handshake identity (``handshake_client`` ContextVar, set by the transport
from ``initialize`` clientInfo.name) > none. An inferred identity never
overrides an explicit argument. Must not import ``src.main``.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.services.client_models import (
    ClientEntry,
    recognised_client_ids,
    resolve_client,
    resolve_handshake_client,
    restriction_label,
)

# Per-call MCP client name from the handshake; set by the transport before
# execute_tool and reset in a finally (same pattern as support_chat).
handshake_client: ContextVar[Optional[str]] = ContextVar("mcp_handshake_client", default=None)

_NO_CLIENT_NOTE = (
    "No client given or inferred, so every priceable model was ranked; pass "
    "client (e.g. claude_code) to restrict to what your tool can run."
)


@dataclass
class ClientResolution:
    entry: Optional[ClientEntry]
    source: str  # "explicit" | "inferred" | "none"
    raw: Optional[str]
    note: Optional[str]


def _restricted_note(entry: ClientEntry, source: str, raw: Optional[str], runnable: Optional[int]) -> str:
    count = f"the {runnable} models" if runnable is not None else "the models"
    note = (
        f"Restricted to {count} {entry.display_name} can run "
        f"({restriction_label(entry)}); client table as of {entry.as_of}."
    )
    if entry.menu == "open":
        note += " " + entry.notes
    if source == "inferred":
        note += (
            f" Inferred from the MCP handshake (clientInfo.name={raw}); "
            "pass client explicitly to override."
        )
    return note


def resolve_tool_client(arguments: Dict[str, Any]) -> ClientResolution:
    explicit = arguments.get("client")
    if not (isinstance(explicit, str) and explicit.strip()):
        explicit = arguments.get("ide_context")
    if isinstance(explicit, str) and explicit.strip():
        entry = resolve_client(explicit)
        if entry is None:
            return ClientResolution(
                None, "explicit", explicit,
                f"Unknown client '{explicit}'; results are unfiltered. "
                f"Recognised clients: {', '.join(recognised_client_ids())}.",
            )
        return ClientResolution(entry, "explicit", explicit, _restricted_note(entry, "explicit", explicit, None))

    name = handshake_client.get()
    if name:
        entry = resolve_handshake_client(name)
        if entry is None:
            return ClientResolution(
                None, "none", name,
                f"MCP client '{name}' is not in the client table; results are unfiltered. "
                "Pass client=... to restrict.",
            )
        return ClientResolution(entry, "inferred", name, _restricted_note(entry, "inferred", name, None))

    return ClientResolution(None, "none", None, _NO_CLIENT_NOTE)


def with_runnable_count(res: ClientResolution, runnable: Optional[int]) -> ClientResolution:
    """Re-word the note once the runnable-model count is known."""
    if res.entry is None or runnable is None:
        return res
    return ClientResolution(res.entry, res.source, res.raw,
                            _restricted_note(res.entry, res.source, res.raw, runnable))


def client_response_fields(res: ClientResolution) -> Dict[str, Any]:
    return {
        "applied_client": res.entry.id if res.entry else None,
        "client_source": res.source,
        "client_as_of": res.entry.as_of if res.entry else None,
        "client_note": res.note,
    }


def no_runnable_model_error(res: ClientResolution, runnable: Optional[int] = None) -> Dict[str, Any]:
    entry = res.entry
    scope = restriction_label(entry) + (f", {runnable} runnable" if runnable is not None else "")
    out: Dict[str, Any] = {
        "success": False,
        "error_code": "no_runnable_model_for_client",
        "error": (
            f"No model matched within what {entry.display_name} can run "
            f"({scope}). Relax cost/quality/context "
            "filters, or omit client to search every model."
        ),
    }
    out.update(client_response_fields(res))
    return out
