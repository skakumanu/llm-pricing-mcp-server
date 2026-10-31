# MCP Server Testing

- **Version**: see `src/__init__.py`

## Three ways to test

| Method | Purpose | In CI? |
|---|---|---|
| `pytest tests/` | Authoritative: gates every PR and merge | yes |
| Manual STDIO / HTTP | Poke at the server while developing | no |
| `scripts/quick_validate.py` | Local smoke test of the 5 core pricing tools over STDIO | no |

## pytest suite

```bash
pytest tests/ -q
```

- CI's `test` job (`.github/workflows/ci-cd.yml`) runs it with `--cov=src`.
- `test` is one of five gates (`test`, `lint`, `osv_scan`, `security`, `secret_scan`). All five must pass before `deploy_fly`.
- Current passing count: see [README.md](../README.md).
- Pricing changes also need the data-accuracy check in `CLAUDE.md` (Release Checklist item 7).

## MCP test files

| File | Guards |
|---|---|
| `test_mcp_http.py` | `POST /mcp`: `initialize`, notifications, `tools/list`, `tools/call` (TestClient, no network) |
| `test_tool_registry.py` | Registry matches `EXPECTED_TOOLS`, agent bindings, tool counts quoted in docs (`DOC_FILES`) |
| `test_mcp_org_scoping.py` | Service-key tools: `customer_key_required`, `org_mismatch` |
| `test_mcp_operator_only.py` | Operator tools refuse anyone without `MCP_API_KEY` |
| `test_mcp_dispatch.py` | `dispatch` over HTTP (mocked), plus the STDIO `dispatch_requires_hosted_mcp` refusal |
| `test_mcp_body_cap.py` | Request-body cap on `POST /mcp` |
| `test_currency_mcp.py` | `currency` / `country` on the pricing-output tools |
| `test_mcp_registry_publish_workflow.py` | `server.json` and the registry publish workflow |

- Adding a tool: update `EXPECTED_TOOLS`.
- A new doc that quotes the tool count goes in `DOC_FILES`.
- Per-tool behaviour lives in each service's own `tests/test_*.py`.

## Manual checks

Pipe JSON-RPC, one request per line, into the STDIO server:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"estimate_cost","arguments":{"model_name":"gpt-4o","input_tokens":1000,"output_tokens":500}}}' \
  | python mcp/server.py
```

- For HTTP, run `python src/main.py`, then `POST http://localhost:8000/mcp` with the same bodies.
- More curl examples: [MCP_QUICK_START.md](MCP_QUICK_START.md).

## quick_validate.py

- Spawns `mcp/server.py` and checks `get_all_pricing`, `estimate_cost`, `compare_costs`, `get_performance_metrics` and `get_use_cases`.
- Also checks that an unknown tool name returns an error.
- Run `python scripts/quick_validate.py`. Exit `0` means pass, `1` means fail.

**See also**: [MCP_QUICK_START.md](MCP_QUICK_START.md) · [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
