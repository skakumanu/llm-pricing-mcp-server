# VS Code Integration

Two uses: **connect VS Code's MCP clients** to the server, or **develop the server** in VS Code.

## Connect an MCP client

Point any client at the hosted server; no install. Tools and access levels: [tool reference](MCP_QUICK_START.md#tool-reference-31).

| Client | Where | Config shape |
|---|---|---|
| GitHub Copilot (Agent mode) | **MCP: Open User Configuration**, or `.vscode/mcp.json` for this workspace | `servers` + `"type": "http"` |
| Cline | Settings (⚙) → MCP Servers → Edit JSON | `mcpServers` |
| Continue | `mcpServers` section of its settings | `mcpServers` |
| Claude Desktop | Not a VS Code setting; see [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) | connector |

Copilot:

```json
{ "servers": { "llm-pricing": { "type": "http", "url": "https://llm-pricing-api.fly.dev/mcp" } } }
```

- Cline and Continue use the same entry under `mcpServers`, without `type`.
- **Copilot**: open Chat, switch to **Agent**, click the tools icon, enable **llm-pricing**.
- **Cline**: run **Cline: Reload MCP Servers**. **Continue**: restart VS Code.
- To send a service key, add an `x-api-key` header, or use the [npx bridge](MCP_QUICK_START.md#npx-bridge-stdio-no-clone).

## Develop the server

### Setup

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git && cd llm-pricing-mcp-server
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt && code .
```

- Needs VS Code 1.85+ and Python 3.11 (what CI and Docker use).
- Accept the **Install All** extension prompt: Python, Pylance, Black Formatter, Flake8.
- The interpreter path defaults to the Windows layout. On macOS/Linux, pick `.venv/bin/python` via **Python: Select Interpreter**.

### Tasks

`Ctrl+Shift+P` → **Tasks: Run Task** (defined in `.vscode/tasks.json`):

| Task | Does |
|---|---|
| Run MCP Server (STDIO) | `python mcp/server.py` |
| Run REST API Server | uvicorn on port 8000 with reload |
| Quick Validate MCP Server | `scripts/quick_validate.py` STDIO smoke test |
| Run All Tests / Run Tests with Coverage | pytest, optionally with `--cov=src` |
| Format Code (Black) / Lint Code (Flake8) | formatting and lint over `src/ tests/ scripts/` |
| Install Dependencies | `pip install -r requirements.txt` |
| Generate MCP Schemas | `python mcp/schema_generator.py`, after editing tool schemas |

- Tasks call `.venv/Scripts/python.exe`, the Windows layout. On macOS/Linux, run the commands in the terminal instead.

### Debug

`F5` or the Run and Debug panel (`Ctrl+Shift+D`), from `.vscode/launch.json`:

| Configuration | Use |
|---|---|
| MCP Server (STDIO) | Breakpoints in the STDIO server and tools |
| MCP Server (Debug) | Same, with `justMyCode: false` and `DEBUG=1` to step into libraries |
| FastAPI Server | Breakpoints in the REST API (`src/main.py`) |

- To debug a test, use the **Testing** panel's per-test debug button; no launch entry is needed.
- To hit a tool breakpoint, start **MCP Server (STDIO)** and call the tool directly:
  `python -c "import asyncio; from mcp.tools.estimate_cost import EstimateCostTool; print(asyncio.run(EstimateCostTool().execute({'model_name':'gpt-4o','input_tokens':1000,'output_tokens':500})))"`

### Test

- The **Testing** panel (beaker icon) auto-discovers `tests/`.
- Defaults: `Ctrl+; A` runs all, `Ctrl+; C` runs the test at the cursor, `Ctrl+; L` reruns the last run.
- CLI: `pytest tests/ -q`; skip slow ones with `-m "not slow"`.
- What each test file covers: [MCP_TESTING.md](MCP_TESTING.md).

### Code style

- Black formats on save at 88 columns; flake8 allows 119 (`.flake8`). Rulers are set at 88 and 120.
- Imports are organised on save. Type checking is `basic`.

### REST requests from the editor

With REST Client or Thunder Client and the API running locally, save this as an `.http` file:

```http
POST http://localhost:8000/cost-estimate/batch
Content-Type: application/json

{"model_names": ["gpt-4o", "claude-sonnet-4-6", "gemini-2.5-pro"], "input_tokens": 1000, "output_tokens": 500}
```

- All endpoints: https://llm-pricing-api.fly.dev/api-docs

### Ship a change

- There is no local deploy. Validate with Quick Validate and `pytest tests/ -q`.
- Then follow git flow: `feature/*` → PR to `develop` → PR to `master` ([CONTRIBUTING.md](CONTRIBUTING.md)).
- Merging to `master` runs `.github/workflows/ci-cd.yml`, which tests, deploys to Fly.io and runs a health check.
- Runbook: [DEPLOYMENT.md](DEPLOYMENT.md). [DEPLOYMENT_IMPLEMENTATION.md](archive/DEPLOYMENT_IMPLEMENTATION.md) is a historical record, not a runbook.

## Troubleshooting

| Problem | Fix |
|---|---|
| Tests not discovered | Activate the venv, select the `.venv` interpreter, **Developer: Reload Window** |
| Import errors | Run from the workspace root (it must be on `PYTHONPATH`); restart the terminal |
| MCP server won't start | Python 3.11; reinstall requirements. `mcp/` is a local package, not the `mcp` pip package |
| Breakpoints not hit | Use **MCP Server (Debug)** (`justMyCode: false`) |
| REST port busy | Stop the other process on 8000 |

## More

- Recommended extras: Thunder Client, GitLens, Docker.
- Workspace files: [`.vscode/README.md`](../.vscode/README.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- MCP spec: https://spec.modelcontextprotocol.io/
