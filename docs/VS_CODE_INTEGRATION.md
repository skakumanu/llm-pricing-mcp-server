# VS Code Integration Guide

> **Version**: 1.1  
> **Target**: Developers & MCP Client Users  
> **Updated**: August 10, 2026

## Overview

This guide explains how to use Visual Studio Code with the LLM Pricing MCP Server for both **development** and **client usage**.

## Quick Start

### Prerequisites
- VS Code 1.85 or later
- Python 3.9+ installed
- Git (for cloning the repository)

### Initial Setup

1. **Clone and Open Repository**
   ```bash
   git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
   cd llm-pricing-mcp-server
   code .
   ```

2. **Install Recommended Extensions**
   - When opening the workspace, VS Code will prompt you to install recommended extensions
   - Click **"Install All"** to get the full development experience
   - Core extensions: Python, Pylance, Black Formatter, Flake8

3. **Create Virtual Environment**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   # or
   source .venv/bin/activate  # macOS/Linux
   ```

4. **Install Dependencies**
   - Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (macOS)
   - Type: **"Tasks: Run Task"**
   - Select: **"Install Dependencies"**
   
   Or manually:
   ```bash
   pip install -r requirements.txt
   ```

## Development Workflow

### Running the MCP Server

#### Method 1: Using VS Code Tasks (Recommended)

Press `Ctrl+Shift+P` → **"Tasks: Run Task"** → Choose:

- **Run MCP Server (STDIO)** - Start MCP server in STDIO mode for Claude Desktop integration
- **Run REST API Server** - Start FastAPI server on port 8000 with hot reload
- **Quick Validate MCP Server** - Run 6 fast validation tests (~15 seconds)
- **Test MCP Server Integration** - Full integration tests (~30 seconds)

#### Method 2: Using Terminal

```bash
# STDIO mode (for MCP clients like Claude Desktop, Cline, Continue)
python mcp/server.py

# REST API mode
python -m uvicorn src.main:app --reload --port 8000
```

### Debugging

VS Code provides 8 debug configurations accessible via **Run and Debug** panel (`Ctrl+Shift+D`):

| Configuration | Purpose | When to Use |
|--------------|---------|-------------|
| **Debug MCP Server (STDIO)** | Debug MCP protocol communication | Testing MCP client integration |
| **Debug REST API Server** | Debug FastAPI endpoints | REST API development |
| **Debug Quick Validate** | Debug validation script | Troubleshooting pre-deployment tests |
| **Debug Current Test File** | Debug active test file | Writing/fixing unit tests |
| **Debug All Tests** | Debug entire test suite | Comprehensive testing |
| **Debug MCP Blue-Green Deploy** | Debug deployment process | Deployment troubleshooting |
| **Debug Schema Generator** | Debug JSON schema generation | Schema development |
| **Python: Remote Attach** | Attach to running process | Production debugging |

**To debug:**
1. Set breakpoints by clicking left of line numbers
2. Press `F5` or select configuration from Run panel
3. Use debug toolbar: Continue (`F5`), Step Over (`F10`), Step Into (`F11`), Step Out (`Shift+F11`)

### Testing

#### Run Tests via Tasks

Press `Ctrl+Shift+P` → **"Tasks: Run Task"**:

- **Run All Tests** - Execute the full test suite
- **Run Tests with Coverage** - Generate coverage report
- **Quick Validate MCP Server** - Fast pre-deployment validation
- **Validate MCP Client** - Comprehensive 16-scenario validation

#### Run Tests via Testing Panel

1. Click **Testing** icon in Activity Bar (beaker icon)
2. Tests auto-discovered from `tests/` directory
3. Click play button next to test to run
4. Green check = passed, Red X = failed

#### Run Tests via Command Line

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_api.py -v

# With coverage
pytest tests/ --cov=src --cov-report=html

# Quick validation (recommended before deployment)
python scripts/quick_validate.py
```

### Code Quality

#### Auto-formatting on Save
- Enabled by default in workspace settings
- Uses **Black** formatter (88 character line length)
- Imports auto-organized on save

#### Manual Formatting & Linting

Press `Ctrl+Shift+P` → **"Tasks: Run Task"**:
- **Format Code (Black)** - Format all Python files
- **Lint Code (Flake8)** - Check code quality

Or via terminal:
```bash
black src/ tests/ scripts/
flake8 src/ tests/ scripts/
```

## Workspace Configuration

### Settings Overview

The workspace includes optimized settings in `.vscode/settings.json`:

| Setting | Value | Purpose |
|---------|-------|---------|
| `python.defaultInterpreterPath` | `.venv/Scripts/python.exe` | Auto-detect virtual environment |
| `python.testing.pytestEnabled` | `true` | Enable pytest framework |
| `editor.formatOnSave` | `true` | Auto-format on save |
| `editor.rulers` | `[88, 120]` | Visual line length guides |
| `python.analysis.typeCheckingMode` | `basic` | Enable type checking |

### Customizing Settings

1. Open **Settings** (`Ctrl+,`)
2. Switch to **Workspace** tab (overrides user settings)
3. Search for setting (e.g., "python.testing")
4. Modify as needed

## MCP Client Usage in VS Code

### Option 1: Remote HTTP (Recommended, No Install)

Every MCP-capable client — Copilot, Cline, Continue, Claude Desktop — can point directly at the hosted server instead of running a local process:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "url": "https://llm-pricing-api.fly.dev/mcp"
    }
  }
}
```

Where this JSON goes depends on your client — see Options 2 and 3 below. This is the config used across the [`/mcp-setup`](../static/mcp-setup/index.html) page and the project README; local STDIO (Options 4–5) is only needed for offline use or when developing the server itself.

### Option 2: GitHub Copilot (Agent Mode)

Copilot's built-in MCP support uses its own config shape — a `servers` key with an explicit `type`, not `mcpServers`:

1. Command Palette (`Ctrl+Shift+P`) → **MCP: Open User Configuration** (all workspaces), or create `.vscode/mcp.json` in this workspace for a project-only server.
2. Add:
   ```json
   {
     "servers": {
       "llm-pricing": {
         "type": "http",
         "url": "https://llm-pricing-api.fly.dev/mcp"
       }
     }
   }
   ```
3. Open the Copilot Chat panel, switch to **Agent** mode, click the tools icon, and enable **llm-pricing**.

### Option 3: Cline & Continue

1. In Cline: Settings (⚙) → MCP Servers → Edit JSON. In Continue: open the `mcpServers` section of its settings.
2. Add the `mcpServers`-shaped config from Option 1 above.
3. Command Palette → **Cline: Reload MCP Servers** (or restart VS Code for Continue).

### Option 4: Claude Desktop

Claude Desktop reads a separate config file, not a VS Code setting:
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Use the Option 1 JSON for the hosted server (no install), or for local STDIO:
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "C:\\Users\\YourUsername\\...\\llm-pricing-mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YourUsername\\...\\llm-pricing-mcp-server\\mcp\\server.py"],
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```
Replace `YourUsername` and paths with your actual paths, then restart Claude Desktop.

**See also**: [MCP_QUICK_START.md](MCP_QUICK_START.md) for the full client-setup reference.

### Option 5: VS Code Terminal (Manual JSON-RPC, Development/Testing)

Run the MCP server directly in the VS Code terminal to send raw JSON-RPC requests:

```bash
python mcp/server.py
# Server listens on STDIN/STDOUT for JSON-RPC 2.0 messages
```

Example request:
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
```

### Option 6: REST API Testing

The REST API is separate from the MCP tools — same underlying pricing data, plain HTTP instead of JSON-RPC. VS Code's REST client extensions (Thunder Client, REST Client) work well here:

1. **Create a test file** (e.g., `api-tests.http`):
   ```http
   ### Get all pricing data
   GET http://localhost:8000/pricing

   ### Estimate cost for one model
   POST http://localhost:8000/cost-estimate
   Content-Type: application/json

   {
     "model_name": "gpt-4o",
     "input_tokens": 1000,
     "output_tokens": 500
   }

   ### Compare costs across models
   POST http://localhost:8000/cost-estimate/batch
   Content-Type: application/json

   {
     "model_names": ["gpt-4o", "claude-sonnet-4-6", "gemini-2.5-pro"],
     "input_tokens": 1000,
     "output_tokens": 500
   }
   ```

2. **Run requests** by clicking **"Send Request"** above each section

## Common Tasks

### Task: Validate Before Deployment

```bash
# Run quick validation (6 tests, ~15 seconds)
python scripts/quick_validate.py

# If all pass, ready for deployment
python scripts/mcp_blue_green_deploy.py deploy --version 1.6.1
```

Or via VS Code:
- Press `Ctrl+Shift+P` → **"Tasks: Run Task"** → **"Quick Validate MCP Server"**

### Task: Test a Single Tool

1. Start MCP server in debug mode:
   - Press `F5` → Select **"Debug MCP Server (STDIO)"**
   - Set breakpoint in tool file (e.g., `mcp/tools/estimate_cost.py`)

2. Send tool request via test script:
   ```bash
   python scripts/test_mcp_server.py
   ```

3. Debugger will pause at breakpoint

### Task: Generate Updated Schemas

After modifying MCP tool schemas:

```bash
# Regenerate JSON schemas
python mcp/schema_generator.py

# Or via task
Ctrl+Shift+P → "Tasks: Run Task" → "Generate MCP Schemas"
```

### Task: Deploy New Version Locally

```bash
# Test blue-green deployment locally
python scripts/mcp_blue_green_deploy.py deploy --version 1.6.1

# Rollback if needed
python scripts/mcp_blue_green_deploy.py rollback
```

Or via VS Code Task: **"Blue-Green Deploy (Local Test)"**

## Troubleshooting

### Issue: Tests Not Discovered

**Solution:**
1. Ensure virtual environment is activated
2. Check Python interpreter: Click Python version in status bar → Select `.venv` interpreter
3. Reload window: `Ctrl+Shift+P` → **"Developer: Reload Window"**

### Issue: Import Errors

**Solution:**
1. Verify `PYTHONPATH` includes workspace root:
   ```bash
   echo $env:PYTHONPATH  # Windows PowerShell
   echo $PYTHONPATH      # macOS/Linux
   ```
2. Should include: `C:\Users\...\llm-pricing-mcp-server`
3. Restart VS Code terminal

### Issue: MCP Server Won't Start

**Solution:**
1. Check Python version: `python --version` (must be 3.9+)
2. Verify dependencies installed: `pip list | grep mcp`
3. Check logs in VS Code terminal for specific errors
4. Ensure no other process using same port (for REST API mode)

### Issue: Debugging Breakpoints Not Hit

**Solution:**
1. Ensure `justMyCode` is set to `false` in launch.json (already configured)
2. Check breakpoint is in executed code path
3. Verify correct debug configuration selected
4. Clear breakpoints and re-add: `Ctrl+Shift+F9`

### Issue: Slow Test Execution

**Solution:**
1. Run specific test file instead of entire suite:
   ```bash
   pytest tests/test_api.py -v
   ```
2. Use quick validation for fast checks:
   ```bash
   python scripts/quick_validate.py
   ```
3. Skip slow tests: `pytest -m "not slow"`

## VS Code Extensions Reference

### Essential Extensions (Auto-installed)

| Extension | Purpose | Usage |
|-----------|---------|-------|
| **Python** | Python language support | Auto-enabled |
| **Pylance** | Fast Python language server | Type checking, IntelliSense |
| **Black Formatter** | Code formatting | Auto-format on save |
| **Flake8** | Code linting | Real-time error detection |

### Recommended Extensions

| Extension | Purpose | Usage |
|-----------|---------|-------|
| **Thunder Client** | REST API testing | Test FastAPI endpoints |
| **GitLens** | Git supercharged | View blame, history inline |
| **Git Graph** | Visualize git branches | View commit graph |
| **Docker** | Container management | Build/deploy containers |
| **Markdown All in One** | Markdown editing | Edit documentation |
| **TODO Tree** | Track TODOs | Find TODO/FIXME comments |

### Optional Extensions

- **Python Test Adapter** - Visual test runner in sidebar
- **Code Spell Checker** - Catch typos in code/docs
- **Better Comments** - Colorize comment types
- **Remote - SSH** - Connect to remote servers

## Keyboard Shortcuts

### Essential Shortcuts

| Action | Windows/Linux | macOS |
|--------|--------------|-------|
| Command Palette | `Ctrl+Shift+P` | `Cmd+Shift+P` |
| Quick Open File | `Ctrl+P` | `Cmd+P` |
| Toggle Terminal | `Ctrl+` ` | `Cmd+` ` |
| Run/Debug | `F5` | `F5` |
| Run Task | `Ctrl+Shift+B` | `Cmd+Shift+B` |
| Find in Files | `Ctrl+Shift+F` | `Cmd+Shift+F` |
| Go to Definition | `F12` | `F12` |
| Find References | `Shift+F12` | `Shift+F12` |
| Format Document | `Shift+Alt+F` | `Shift+Option+F` |
| Toggle Comment | `Ctrl+/` | `Cmd+/` |

### Testing Shortcuts

| Action | Shortcut |
|--------|----------|
| Run Test at Cursor | `Ctrl+Shift+T` (custom) |
| Debug Test at Cursor | `Ctrl+Shift+D` (custom) |
| Rerun Last Test | `Ctrl+Shift+R` (custom) |
| Toggle Test Panel | `Ctrl+Shift+Alt+T` |

## Tips & Best Practices

### Tip 1: Use Workspace Folders
If working on multiple MCP servers or related projects, use VS Code Multi-root Workspaces:
```json
// workspace.code-workspace
{
  "folders": [
    {"path": "llm-pricing-mcp-server"},
    {"path": "another-mcp-project"}
  ]
}
```

### Tip 2: Quick Task Execution
Add to `keybindings.json` for one-key task execution:
```json
[
  {
    "key": "ctrl+shift+t",
    "command": "workbench.action.tasks.runTask",
    "args": "Quick Validate MCP Server"
  }
]
```

### Tip 3: Live Share for Collaboration
Install **Live Share** extension to pair program on MCP development:
1. Install: `ms-vsliveshare.vsliveshare`
2. Click **Live Share** in status bar
3. Share link with collaborators

### Tip 4: Use Snippets
Create Python snippets for common MCP patterns:
- File → Preferences → Configure User Snippets → Python
- Add snippets for tool definitions, test cases, etc.

### Tip 5: Zen Mode for Focus
Press `Ctrl+K Z` to enter Zen Mode (distraction-free coding).

## Integration with MCP Documentation

This guide complements other MCP documentation:

- **[MCP_QUICK_START.md](MCP_QUICK_START.md)** - Initial MCP setup (5-minute guide)
- **[MCP_CLAUDE_INTEGRATION.md](MCP_CLAUDE_INTEGRATION.md)** - Claude Desktop configuration (10-minute guide)
- **[MCP_TESTING.md](MCP_TESTING.md)** - Complete testing guide (3 test suites)
- **[MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md)** - Zero-downtime deployments
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System architecture overview

## Next Steps

### For Developers
1. ✅ Set up VS Code workspace (this guide)
2. ✅ Run quick validation: `python scripts/quick_validate.py`
3. ✅ Explore codebase: Start with `src/main.py` → `mcp/server.py` → `mcp/tools/`
4. ✅ Run tests: `pytest tests/ -v`
5. ✅ Make changes and test locally
6. ✅ Read [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines

### For MCP Client Users
1. ✅ Set up Claude Desktop: Follow [MCP_CLAUDE_INTEGRATION.md](MCP_CLAUDE_INTEGRATION.md)
2. ✅ Configure MCP server in Claude config
3. ✅ Test with simple query: "What's the pricing for GPT-4?"
4. ✅ Explore 6 MCP tools via Claude

### For DevOps/Deployers
1. ✅ Understand deployment: Read [MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md)
2. ✅ Test locally: `python scripts/mcp_blue_green_deploy.py deploy --version test`
3. ✅ Validate: `python scripts/quick_validate.py`
4. ✅ Deploy to production: Follow deployment guide

## Additional Resources

- **MCP Specification**: https://spec.modelcontextprotocol.io/
- **VS Code Python Tutorial**: https://code.visualstudio.com/docs/python/python-tutorial
- **VS Code Debugging**: https://code.visualstudio.com/docs/editor/debugging
- **FastAPI Documentation**: https://fastapi.tiangolo.com/
- **pytest Documentation**: https://docs.pytest.org/

## Support & Contribution

- **Issues**: Report bugs via GitHub Issues
- **Discussions**: Ask questions in GitHub Discussions
- **Contributing**: See [CONTRIBUTING.md](CONTRIBUTING.md)
- **Security**: Report vulnerabilities per [SECURITY_PATCH_PROCEDURE.md](SECURITY_PATCH_PROCEDURE.md)

---

**Version History**
- v1.1 (2026-08-10): Fixed the MCP stdio entrypoint (was `src/main.py`, actually `mcp/server.py`); added GitHub Copilot and Cline/Continue MCP setup, previously missing despite the guide's title; corrected REST API example paths and request bodies to match the live endpoints
- v1.0 (2026-02-20): Initial VS Code integration guide
