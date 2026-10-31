"""CLAUDE.md checklist item 10: documentation ships with the code it describes.

These checks are mechanical by design. They catch the misses that drift silently
for months (an endpoint that never reached /api-docs, a tool missing from the
setup page, a setting nobody documented); they do not judge prose accuracy.
"""
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.routing import APIRoute  # noqa: E402

from src.main import app  # noqa: E402

ARCHITECTURE = project_root / "docs" / "ARCHITECTURE.md"
API_DOCS = project_root / "static" / "api-docs" / "index.html"
QUICK_START = project_root / "docs" / "MCP_QUICK_START.md"
MCP_SETUP = project_root / "static" / "mcp-setup" / "index.html"
README = project_root / "README.md"
DEPLOYMENT = project_root / "docs" / "DEPLOYMENT.md"
ENV_EXAMPLE = project_root / ".env.example"

# FastAPI's own documentation routes, plus HTML pages served by route handlers rather
# than static mounts (they are UI pages, documented in the README's UI table, not API).
UNDOCUMENTED_ROUTES = {
    "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/", "/admin",
    # v1.86.0 legal pages: UI pages served by handlers (README / ARCHITECTURE UI lists)
    "/terms", "/privacy", "/refunds", "/dpa", "/subprocessors", "/security",
}
# Settings that are constants derived in code, not environment configuration.
INTERNAL_SETTINGS = {"app_name", "app_version", "app_description"}

_PARAM = re.compile(r"\{[^}]+\}")


def _normalize(path: str) -> str:
    """`/usage/alerts/{alert_id}` -> `/usage/alerts/{id}` so docs may name params freely."""
    return _PARAM.sub("{id}", path)


def _documented_paths(text: str) -> set:
    """Paths a doc mentions, expanding the `/x[/{id}]` shorthand the endpoint map uses."""
    text = _normalize(text)
    found = set(re.findall(r"(/[A-Za-z0-9_./{}-]+)", text))
    for base in re.findall(r"(/[A-Za-z0-9_./{}-]+)\[/\{id\}\]", text):
        found.add(base)
        found.add(base + "/{id}")
    return {p.rstrip(".,)") for p in found}


def api_routes():
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in UNDOCUMENTED_ROUTES:
            continue
        if not (set(route.methods) - {"HEAD", "OPTIONS"}):
            continue
        yield route.path


class TestEndpointsAreDocumented:
    def test_every_route_is_in_the_architecture_endpoint_map(self):
        documented = _documented_paths(ARCHITECTURE.read_text(encoding="utf-8"))
        missing = sorted({p for p in api_routes() if _normalize(p) not in documented})
        assert not missing, f"routes missing from docs/ARCHITECTURE.md endpoint map: {missing}"

    def test_every_route_is_on_the_api_docs_page(self):
        documented = _documented_paths(API_DOCS.read_text(encoding="utf-8"))
        missing = sorted({p for p in api_routes() if _normalize(p) not in documented})
        assert not missing, f"routes missing from static/api-docs/index.html: {missing}"


class TestToolsAreDocumented:
    def _tools(self):
        from mcp.tools.tool_manager import ToolManager
        return sorted(ToolManager().tools)

    def test_every_tool_is_in_the_quick_start_table(self):
        text = QUICK_START.read_text(encoding="utf-8")
        missing = [t for t in self._tools() if t not in text]
        assert not missing, f"tools missing from docs/MCP_QUICK_START.md: {missing}"

    def test_every_tool_is_on_the_setup_page(self):
        text = MCP_SETUP.read_text(encoding="utf-8")
        missing = [t for t in self._tools() if t not in text]
        assert not missing, f"tools missing from static/mcp-setup/index.html: {missing}"


class TestSettingsAreDocumented:
    def test_every_setting_is_documented_somewhere(self):
        from src.config.settings import Settings
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in (README, DEPLOYMENT, ARCHITECTURE, ENV_EXAMPLE))
        missing = [f.upper() for f in Settings.model_fields
                   if f not in INTERNAL_SETTINGS and f.upper() not in corpus]
        assert not missing, (
            "settings with no documentation (add a commented line to .env.example or a README row): "
            f"{missing}"
        )


class TestReleaseNotesExist:
    def test_current_version_renders_release_notes(self):
        """The deploy job builds the GitHub Release from the What's New entry; no entry, no release."""
        import subprocess
        from src import __version__
        proc = subprocess.run(
            [sys.executable, str(project_root / "scripts" / "release_notes_from_whats_new.py"), __version__],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("## ")
