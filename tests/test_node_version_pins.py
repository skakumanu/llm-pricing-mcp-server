"""Node.js version floors for everything in this repo that runs Node.

The October 2026 Node.js security release (22.23.4 / 24.22.1 / 26.12.1) fixed a
Brotli decoder RCE (CVE-2026-111497) and two TLS hostname-verification bypasses.
Two places here run Node and must not drift below the patched builds:

- the npm publish workflows (``actions/setup-node``): a bare major such as
  ``24`` lets the runner use whatever 24.x is in its toolcache, which can lag
  the security release by weeks. They must pin an exact version at or above
  the floor and set ``check-latest: true``.
- the published bridge itself (``npm/llm-pricing-mcp``): it uses Node's
  built-in ``fetch``, which transparently decodes compressed responses, so
  ``engines.node`` must exclude EOL lines (18, 20) and point at the oldest
  patched release.

When Node ships its next security release, raise FLOORS here and the pins in
the files together; this test exists so neither moves alone.
"""
import json
import re
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
WORKFLOWS = project_root / ".github" / "workflows"
PACKAGE_JSON = project_root / "npm" / "llm-pricing-mcp" / "package.json"
PACKAGE_README = project_root / "npm" / "llm-pricing-mcp" / "README.md"

# Oldest patched release per active line, from the Oct 21 2026 security release.
FLOORS = {22: (22, 23, 4), 24: (24, 22, 1), 26: (26, 12, 1)}
OLDEST_PATCHED = FLOORS[22]

NODE_WORKFLOWS = [
    p for p in sorted(WORKFLOWS.glob("*.yml")) if "actions/setup-node" in p.read_text(encoding="utf-8")
]


def _parse(version: str):
    parts = version.strip().strip("'\"").split(".")
    assert len(parts) == 3 and all(x.isdigit() for x in parts), (
        f"node-version must be an exact x.y.z pin, got {version!r}"
    )
    return tuple(int(x) for x in parts)


def test_at_least_one_workflow_runs_node():
    # If this ever fails, Node left the repo entirely and this file can go.
    assert NODE_WORKFLOWS, "expected the npm publish workflows to use actions/setup-node"


@pytest.mark.parametrize("workflow", NODE_WORKFLOWS, ids=lambda p: p.name)
class TestWorkflowNodePins:
    def test_exact_pin_at_or_above_floor(self, workflow):
        text = workflow.read_text(encoding="utf-8")
        pins = re.findall(r"^\s*node-version:\s*(\S+)", text, re.MULTILINE)
        assert pins, f"{workflow.name} uses setup-node without node-version"
        for pin in pins:
            v = _parse(pin)
            assert v[0] in FLOORS, f"{workflow.name}: Node {v[0]}.x is not an active line with a known floor"
            assert v >= FLOORS[v[0]], (
                f"{workflow.name}: node-version {pin} is below the patched floor "
                f"{'.'.join(map(str, FLOORS[v[0]]))}"
            )

    def test_check_latest_enabled(self, workflow):
        text = workflow.read_text(encoding="utf-8")
        assert re.search(r"^\s*check-latest:\s*true", text, re.MULTILINE), (
            f"{workflow.name}: setup-node needs check-latest: true, or a cached toolcache build may be used"
        )


class TestPackageEngines:
    def test_engines_floor_excludes_eol_lines(self):
        engines = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["engines"]["node"]
        m = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", engines.strip())
        assert m, f"engines.node must be a plain >=x.y.z floor, got {engines!r}"
        floor = tuple(int(x) for x in m.groups())
        assert floor >= OLDEST_PATCHED, (
            f"engines.node {engines!r} admits Node builds below the oldest patched release "
            f"{'.'.join(map(str, OLDEST_PATCHED))}"
        )

    def test_readme_names_the_patched_minimums(self):
        text = PACKAGE_README.read_text(encoding="utf-8")
        for floor in FLOORS.values():
            assert ".".join(map(str, floor)) in text, (
                f"npm README should tell users the patched minimum {'.'.join(map(str, floor))}"
            )
