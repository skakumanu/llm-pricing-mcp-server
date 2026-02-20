# VS Code Configuration

This directory contains VS Code workspace configuration for optimal development experience.

## Files

- **settings.json** - Workspace settings (Python interpreter, formatting, testing)
- **tasks.json** - Common development tasks (run server, tests, validation)
- **extensions.json** - Recommended VS Code extensions
- **launch.json** - Debug configurations for various scenarios

## Quick Start

1. Open this workspace in VS Code
2. Install recommended extensions when prompted (or manually via Extensions panel)
3. VS Code will automatically:
   - Detect the `.venv` Python virtual environment
   - Enable pytest for testing
   - Configure Black formatter
   - Set up Flake8 linting

## Usage

### Running Tasks
Press `Ctrl+Shift+P` → **"Tasks: Run Task"** → Select task:
- Run MCP Server (STDIO)
- Run REST API Server
- Quick Validate MCP Server
- Run All Tests
- And more...

### Debugging
Press `F5` or go to Run and Debug panel (`Ctrl+Shift+D`):
- Debug MCP Server (STDIO)
- Debug REST API Server
- Debug Current Test File
- And more...

## Documentation

For complete VS Code integration guide, see:
**[docs/VS_CODE_INTEGRATION.md](../docs/VS_CODE_INTEGRATION.md)**

This guide includes:
- Detailed setup instructions
- Development workflow
- Testing procedures
- Troubleshooting tips
- Keyboard shortcuts
- Integration with MCP tools
