# MCP Server Testing Guide

**Version**: see `src/__init__.py` (single source of truth)

## Overview

This guide covers how the MCP server is actually tested: the pytest suite that gates
every PR and merge, plus a couple of manual/local checks useful during development.

| Method | Purpose | Runs in CI? | Location |
|--------|---------|-------------|----------|
| pytest suite | Full correctness coverage — tools, HTTP transport, pricing accuracy, endpoints | ✅ Yes, every PR and push | `tests/` |
| Manual STDIO check | Quick sanity check while developing locally | No | `mcp/server.py` |
| `quick_validate.py` | Scripted local smoke test of the 5 core pricing tools over STDIO | No | `scripts/quick_validate.py` |

## The pytest Suite (Authoritative)

This is what actually gates merges. See `README.md` for the current passing-test count.

```bash
pytest tests/ -q
```

CI runs the equivalent of this on every PR and push, as the `test` job in
`.github/workflows/ci-cd.yml`:

```bash
pytest tests/ -v --cov=src --cov-report=term-missing
```

`test` is one of five gates (`test`, `lint`, `osv_scan`, `security`, `secret_scan`) that
must all pass before `deploy_fly` runs on `master`. See `CLAUDE.md`'s "CI/CD Gates"
section.

### MCP-specific test files

- **`tests/test_mcp_http.py`** — tests the HTTP JSON-RPC transport (`POST /mcp`):
  `initialize`, `initialized` notification handling, `tools/list`, and `tools/call` for
  representative tools, using FastAPI's `TestClient` (no real subprocess or network
  calls).
- **`tests/test_tool_registry.py`** — guards against tool-registry drift. It asserts the
  live `ToolManager` registry matches an explicit `EXPECTED_TOOLS` set, checks every tool
  has an instance/description/schema, checks the ReAct agent binds every tool it should,
  and greps `README.md` plus a fixed list of docs for stale tool counts. This is what
  catches a doc saying "14 tools" after a tool was added — update `EXPECTED_TOOLS` and,
  if you add a new doc that states the tool count, add it to `DOC_FILES` in that file.
- Individual tool behavior (pricing math, cost estimation, alerts, conversation history,
  etc.) is covered by the corresponding `tests/test_*.py` file per service/tool, not
  duplicated here.

### Pricing-specific checks

If your change touches pricing data or the price oracle, `CLAUDE.md`'s Release Checklist
item 7 also requires running `get_data_quality` and the price-oracle/drift test files
before committing — see `CLAUDE.md` for the exact commands. That check is about data
accuracy (confirmed vs. withheld vs. never-priced models), which is a different concern
from the correctness testing this guide covers.

## Manual STDIO Testing (Local Development)

The MCP server also speaks JSON-RPC 2.0 over STDIO (`mcp/server.py`), which is what
Claude Desktop and other local MCP clients use. To poke at it directly:

```bash
# Terminal 1: start the server
python mcp/server.py
```

```bash
# Terminal 2: send JSON-RPC requests, one per line
```

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize"}
{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_all_pricing", "arguments": {}}}
{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "estimate_cost", "arguments": {"model_name": "gpt-4", "input_tokens": 1000, "output_tokens": 500}}}
{"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "compare_costs", "arguments": {"model_names": ["gpt-4", "claude-3-opus"], "input_tokens": 1000, "output_tokens": 500}}}
```

Paste each line into the terminal running the server and read the response on stdout.

The HTTP transport (`POST /mcp`, used by remote MCP clients) can be exercised the same
way with `curl` once the FastAPI server is running (`python src/main.py` or
`uvicorn src.main:app`):

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
```

## `scripts/quick_validate.py`

A standalone script that spawns `mcp/server.py` as a subprocess, drives it over STDIO,
and checks that the 5 core pricing tools (`get_all_pricing`, `estimate_cost`,
`compare_costs`, `get_performance_metrics`, `get_use_cases`) are discoverable and execute
without error, plus a basic error-handling check for an unknown tool name. It's a manual
convenience script, not wired into CI — use it as a quick local sanity check after
changing tool wiring:

```bash
python scripts/quick_validate.py
```

Exit code `0` means all checks passed; `1` means something failed.

## Related Documentation

- **[MCP_QUICK_START.md](MCP_QUICK_START.md)** - Quick start guide
- **[CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)** - Claude Desktop testing
