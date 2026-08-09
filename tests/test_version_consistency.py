"""Guards against version drift.

src/__init__.py is the single source of truth. Before this guard existed the repo
carried three independent version numbers: the app version (1.52.0), a hardcoded
MCP server version (1.1.0, which is what MCP clients actually saw in serverInfo),
and hand-typed values frozen into doc headers (1.0.0 / 1.6.0 / 1.38.0 / 1.39.1).
"""
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src import __version__ as APP_VERSION  # noqa: E402

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class TestCanonicalVersion:
    def test_version_is_semver(self):
        assert SEMVER_RE.match(APP_VERSION), f"__version__ '{APP_VERSION}' is not semver"

    def test_settings_app_version_matches(self):
        from src.config.settings import settings
        assert settings.app_version == APP_VERSION


class TestMcpVersionsSingleSourced:
    """Every surface that reports a version to a client must report the app version."""

    def test_mcp_package_version(self):
        import mcp
        assert mcp.__version__ == APP_VERSION

    def test_stdio_server_version(self):
        from mcp.server import MCPServer
        assert MCPServer().version == APP_VERSION

    def test_http_transport_version(self):
        import src.main as main
        assert main._MCP_SERVER_VERSION == APP_VERSION

    def test_stdio_and_http_agree(self):
        from mcp.server import MCPServer
        import src.main as main
        assert MCPServer().version == main._MCP_SERVER_VERSION


class TestNoHardcodedVersionsInSource:
    """Catch a literal version string being reintroduced next to a version assignment.

    This used to check a hand-maintained file list, which is exactly how
    mcp/server_azure.py's hardcoded "1.1.0" survived the original fix undetected —
    the list was written before that file was considered. Scanning every source
    file under mcp/ and src/ closes that class of gap: a new file with the same
    mistake fails automatically, with no list to remember to update.
    """

    SCAN_ROOTS = ("mcp", "src")

    # The one file allowed to hold the literal — it's the canonical source every
    # other assignment must derive from.
    CANONICAL_SOURCE = "src/__init__.py"

    # e.g.  self.version = "1.1.0"   /   _MCP_SERVER_VERSION = "1.2.3"
    HARDCODED_RE = re.compile(
        r'(?:^|\b)(?:__version__|_MCP_SERVER_VERSION|self\.version)\s*=\s*["\']\d+\.\d+\.\d+["\']'
    )

    def test_no_literal_version_assignments(self):
        offenders = []
        for root_name in self.SCAN_ROOTS:
            root = project_root / root_name
            for path in root.rglob("*.py"):
                rel = path.relative_to(project_root)
                if str(rel) == self.CANONICAL_SOURCE:
                    continue
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if self.HARDCODED_RE.search(line):
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")
        assert not offenders, (
            "Version literals must derive from src.__version__:\n" + "\n".join(offenders)
        )


class TestDocsDoNotFreezeVersions:
    """Doc headers must not pin a version — they go stale silently.

    static/whats-new/ is excluded: it is a historical changelog and its entries
    are supposed to name the version they shipped in.
    """

    DOC_DIR = project_root / "docs"
    HEADER_RE = re.compile(r"^\*\*Version\*\*:\s*v?(\d+\.\d+\.\d+)", re.M)

    def test_no_frozen_version_headers(self):
        offenders = []
        for path in sorted(self.DOC_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for match in self.HEADER_RE.finditer(text):
                found = match.group(1)
                if found != APP_VERSION:
                    lineno = text[: match.start()].count("\n") + 1
                    offenders.append(
                        f"docs/{path.name}:{lineno} pins version {found} "
                        f"(app is {APP_VERSION})"
                    )
        assert not offenders, (
            "Stale version headers — point them at src/__init__.py instead:\n"
            + "\n".join(offenders)
        )
