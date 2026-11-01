#!/usr/bin/env python3
"""Print one release's What's New entry as Markdown, for GitHub Release notes.

static/whats-new/index.html is the product's release record (append-only, one
``<!-- vX.Y.Z -->`` marker per entry). The deploy job calls this after the
post-deploy health check confirms the version, and feeds the output to
``gh release create`` so the GitHub Releases page stops drifting from reality
(it sat at v1.11.1 while the product reached v1.82.x).

Usage:
    python scripts/release_notes_from_whats_new.py 1.82.2            # Markdown body
    python scripts/release_notes_from_whats_new.py v1.82.2 --title   # title line only

Exit codes: 0 ok, 1 usage, 2 version has no What's New entry.
"""
import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The parser moved to src/services/whats_new.py in v1.89.0 (the document writer runs in the production
# image, which has no scripts/); this file stays the CLI and re-exports the names.
from src.services.whats_new import (  # noqa: E402,F401
    DEFAULT_FILE, SITE, _MARKER_RE, _first, _inline, _sections, entry_title, entry_to_markdown, extract_entry,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="e.g. 1.82.2 or v1.82.2")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--title", action="store_true", help="print only the entry title")
    args = ap.parse_args(argv)

    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"not a semver version: {args.version}", file=sys.stderr)
        return 1
    text = args.file.read_text(encoding="utf-8")
    try:
        block = extract_entry(text, version)
    except LookupError:
        print(f"no What's New entry for v{version} in {args.file}", file=sys.stderr)
        return 2
    if args.title:
        print(entry_title(block) or f"v{version}")
    else:
        sys.stdout.write(entry_to_markdown(block, version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
