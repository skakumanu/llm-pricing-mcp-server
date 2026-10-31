# VS Code Workspace

| File | Holds |
|---|---|
| `settings.json` | Interpreter, Black on save, pytest, Flake8 |
| `tasks.json` | Run server / REST API, tests, quick validate, format, lint, schemas |
| `launch.json` | Debug: MCP Server (STDIO), MCP Server (Debug), FastAPI Server |
| `extensions.json` | Recommended extensions |

- Accept the recommended-extensions prompt on first open.
- Run tasks with `Ctrl+Shift+P` → **Tasks: Run Task**. Debug with `F5`.
- Paths point at the Windows venv layout (`.venv/Scripts/python.exe`).
- On macOS/Linux, choose `.venv/bin/python` via **Python: Select Interpreter**.

Full guide: [docs/VS_CODE_INTEGRATION.md](../docs/VS_CODE_INTEGRATION.md)
