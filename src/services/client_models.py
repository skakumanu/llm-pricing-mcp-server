"""Curated table of AI coding clients and the models each one can run (v1.85.0).

The recommendation tools used to rank every model the service can price, even
ones the caller's tool cannot select (a Claude Code session was told to use
codestral). This table lets them restrict the candidate set to what the client
can run BEFORE ranking.

Hand-maintained, like ``ide_pricing.py``: every entry cites a public source and
an ``as_of`` date. Nothing here is scraped or auto-updated. The service cannot
see the user's plan tier or configured API keys, so a "fixed" menu means "the
providers this tool's model picker exposes", not "what this user has enabled".

Menus:
- ``fixed``: eligibility by whole catalogue provider (``providers``) plus any
  explicit extra ``models``. Subscription plans are eligible only when they
  belong to the client itself (provider matches ``native_providers``).
- ``open``: the tool accepts any API key / endpoint, so every per-token model
  is eligible; other tools' subscription plans are not.
"""
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

CLIENT_TABLE_AS_OF = "2026-10-31"


@dataclass(frozen=True)
class ClientEntry:
    id: str
    display_name: str
    aliases: Tuple[str, ...]
    handshake_names: Tuple[str, ...]
    menu: str  # "fixed" | "open"
    providers: Tuple[str, ...]
    models: Tuple[str, ...]
    # Providers native to this client: subscription plans from these providers
    # are runnable, and they get the router's existing 5x / 1.15x preference.
    native_providers: Tuple[str, ...]
    source: str
    as_of: str
    notes: str = ""


_OPEN_NOTE = (
    "Open model menu: this tool accepts any API key or endpoint, so every "
    "per-token API model was considered (other tools' subscription plans excluded)."
)

CLIENTS: Tuple[ClientEntry, ...] = (
    ClientEntry(
        id="claude_code",
        display_name="Claude Code",
        aliases=(),
        handshake_names=("claude-code",),
        menu="fixed",
        providers=("Anthropic",),
        models=(),
        native_providers=("anthropic",),
        source="https://docs.claude.com/en/docs/claude-code/model-config",
        as_of="2026-10-31",
        notes="Claude models only (Haiku, Sonnet, Opus, Fable families).",
    ),
    ClientEntry(
        id="claude_desktop",
        display_name="Claude Desktop / claude.ai",
        aliases=("claude_ai", "claude.ai", "claude-desktop", "claude_desktop_app"),
        handshake_names=("claude-ai", "claude-desktop", "Claude Desktop"),
        menu="fixed",
        providers=("Anthropic",),
        models=(),
        native_providers=(),
        source="https://support.claude.com/en/articles/11940350-claude-models",
        as_of="2026-10-31",
        notes="Claude models only; not a coding tool.",
    ),
    ClientEntry(
        id="cursor",
        display_name="Cursor",
        aliases=(),
        handshake_names=("cursor", "cursor-vscode"),
        menu="fixed",
        providers=("Cursor", "Anthropic", "OpenAI", "Google", "xAI"),
        models=(),
        native_providers=("cursor",),
        source="https://docs.cursor.com/models",
        as_of="2026-10-31",
        notes="Cursor plans plus the frontier vendors its model picker exposes.",
    ),
    ClientEntry(
        id="github_copilot",
        display_name="GitHub Copilot",
        aliases=("copilot",),
        handshake_names=(),
        menu="fixed",
        providers=("GitHub Copilot", "OpenAI", "Anthropic", "Google"),
        models=(),
        native_providers=("github copilot",),
        source="https://docs.github.com/en/copilot/reference/ai-models/supported-models",
        as_of="2026-10-31",
        notes="Copilot plans plus the vendors its model picker exposes.",
    ),
    ClientEntry(
        id="windsurf",
        display_name="Windsurf",
        aliases=("codeium",),
        handshake_names=("windsurf",),
        menu="fixed",
        providers=("Windsurf", "Anthropic", "OpenAI", "Google", "xAI"),
        models=(),
        native_providers=("windsurf", "codeium"),
        source="https://docs.windsurf.com/windsurf/models",
        as_of="2026-10-31",
        notes="Windsurf plans plus the frontier vendors its model picker exposes.",
    ),
    ClientEntry(
        id="cline",
        display_name="Cline",
        aliases=(),
        handshake_names=("cline",),
        menu="open",
        providers=(),
        models=(),
        native_providers=(),
        source="https://docs.cline.bot/provider-config/openai-compatible",
        as_of="2026-10-31",
        notes=_OPEN_NOTE,
    ),
    ClientEntry(
        id="continue",
        display_name="Continue",
        aliases=("continue_dev",),
        handshake_names=("continue",),
        menu="open",
        providers=(),
        models=(),
        native_providers=(),
        source="https://docs.continue.dev/customize/model-providers/overview",
        as_of="2026-10-31",
        notes=_OPEN_NOTE,
    ),
    ClientEntry(
        id="zed",
        display_name="Zed",
        aliases=("zed_editor",),
        handshake_names=("zed",),
        menu="open",
        providers=(),
        models=(),
        native_providers=(),
        source="https://zed.dev/docs/ai/llm-providers",
        as_of="2026-10-31",
        notes=_OPEN_NOTE,
    ),
    ClientEntry(
        id="jetbrains_ai",
        display_name="JetBrains AI",
        aliases=("jetbrains", "jetbrains_ai_assistant"),
        handshake_names=(),
        menu="fixed",
        providers=("JetBrains AI", "OpenAI", "Anthropic", "Google"),
        models=(),
        native_providers=("jetbrains ai",),
        source="https://www.jetbrains.com/help/ai-assistant/switching-to-different-models.html",
        as_of="2026-10-31",
        notes="JetBrains AI plans plus the vendors its model picker exposes.",
    ),
    ClientEntry(
        id="gemini_cli",
        display_name="Gemini CLI",
        aliases=("gemini",),
        handshake_names=("gemini-cli",),
        menu="fixed",
        providers=("Google",),
        models=(),
        native_providers=(),
        source="https://github.com/google-gemini/gemini-cli",
        as_of="2026-10-31",
        notes="Google Gemini models only.",
    ),
    ClientEntry(
        id="codex_cli",
        display_name="Codex CLI",
        aliases=("codex",),
        handshake_names=("codex", "codex-cli"),
        menu="fixed",
        providers=("OpenAI",),
        models=(),
        native_providers=(),
        source="https://github.com/openai/codex",
        as_of="2026-10-31",
        notes="OpenAI models only.",
    ),
    ClientEntry(
        id="aider",
        display_name="Aider",
        aliases=(),
        handshake_names=("aider",),
        menu="open",
        providers=(),
        models=(),
        native_providers=(),
        source="https://aider.chat/docs/llms.html",
        as_of="2026-10-31",
        notes=_OPEN_NOTE,
    ),
    ClientEntry(
        id="amazon_q",
        display_name="Amazon Q Developer",
        aliases=("amazon_q_developer", "q_developer"),
        handshake_names=(),
        menu="fixed",
        providers=("Amazon Q Developer",),
        models=(),
        native_providers=("amazon q developer",),
        source="https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/what-is.html",
        as_of="2026-10-31",
        notes="Amazon Q Developer plans only.",
    ),
    ClientEntry(
        id="chatgpt_desktop",
        display_name="ChatGPT desktop",
        aliases=("chatgpt", "chatgpt_desktop_app"),
        handshake_names=("chatgpt", "ChatGPT Desktop"),
        menu="fixed",
        providers=("OpenAI",),
        models=(),
        native_providers=(),
        source="https://help.openai.com/en/articles/9624314-model-release-notes",
        as_of="2026-10-31",
        notes="OpenAI models only; not a coding tool.",
    ),
)


def normalize_client_name(raw: str) -> str:
    """Lowercase and strip non-alphanumerics ('Claude-Code' == 'claude code')."""
    return re.sub(r"[^a-z0-9]", "", str(raw).lower())


def _index(include_handshake: bool) -> dict:
    index = {}
    for entry in CLIENTS:
        names = [entry.id, entry.display_name, *entry.aliases]
        if include_handshake:
            names.extend(entry.handshake_names)
        for n in names:
            index.setdefault(normalize_client_name(n), entry)
    return index


_BY_NAME = _index(include_handshake=False)
_BY_HANDSHAKE = _index(include_handshake=True)


def resolve_client(raw: Optional[str]) -> Optional[ClientEntry]:
    """Resolve a caller-supplied client name (id, display name or alias)."""
    if not raw or not isinstance(raw, str):
        return None
    key = normalize_client_name(raw)
    return _BY_NAME.get(key) if key else None


def resolve_handshake_client(client_info_name: Optional[str]) -> Optional[ClientEntry]:
    """Resolve an MCP ``clientInfo.name``: exact normalised match only.

    No substring matching, so generic hosts such as 'Visual Studio Code'
    (which could be Copilot, Cline or Continue) resolve to None, not a guess.
    """
    if not client_info_name or not isinstance(client_info_name, str):
        return None
    key = normalize_client_name(client_info_name)
    return _BY_HANDSHAKE.get(key) if key else None


def recognised_client_ids() -> List[str]:
    return [e.id for e in CLIENTS]


def is_runnable(entry: ClientEntry, model) -> bool:
    """True when ``model`` is on ``entry``'s model menu."""
    pricing_model = getattr(model, "pricing_model", "per_token") or "per_token"
    provider = (model.provider or "").lower()
    if pricing_model == "subscription":
        # A subscription plan is runnable only inside the tool it belongs to.
        return any(np in provider for np in entry.native_providers)
    if entry.menu == "open":
        return pricing_model == "per_token"
    if provider in {p.lower() for p in entry.providers}:
        return True
    return model.model_name.lower() in {m.lower() for m in entry.models}


def eligible_models(entry: ClientEntry, models: Iterable) -> list:
    return [m for m in models if is_runnable(entry, m)]


def restriction_label(entry: ClientEntry) -> str:
    if entry.menu == "open":
        return "per-token API models (no subscription plans)"
    return " + ".join(entry.providers) + " models"
