# owui_mcp

`owui_mcp` is a **Model Context Protocol (MCP)** server (stdio) that exposes the **Open WebUI API** as MCP **tools**, by wrapping the Python client library **[`owui_client`](https://github.com/whogben/owui_client)**.

This repo is intentionally a thin wrapper: **the source of truth stays in `owui_client`**. When `owui_client` adds/changes endpoints, `owui_mcp` picks them up automatically at startup.

## Base project: owui_client

- GitHub: https://github.com/whogben/owui_client
- PyPI: `pip install owui-client`
- Docs: https://whogben.github.io/owui_client/

`owui_client` is a fully typed, async client that mirrors Open WebUI's backend router structure (`client.chats.*`, `client.models.*`, `client.tools.*`, etc.).

## How it works

At startup, `owui_mcp`:

1. Creates an `owui_client.OpenWebUI` instance using env vars (`OWUI_API_URL`, `OWUI_API_KEY`)
2. Introspects the client's routers (all `ResourceBase` routers)
3. Registers **every public async method** as an MCP tool
4. Generates tool input schemas from Python type hints + Pydantic models in `owui_client`

### Tool naming

Each tool is named:

```
{router}__{method}
```

Examples:

- `auths__get_session_user`
- `chats__search`
- `models__get_models`
- `knowledge__create_new_knowledge`

## Requirements

- Python **3.10+**
- An Open WebUI instance (local or remote)
- An API key / bearer token (recommended)

## Install

### From source (this repo)

```bash
pip install -e .
```

### From PyPI (if published)

```bash
pip install owui-mcp
```

## Configuration

| Variable | Description | Default |
|---|---|---|
| `OWUI_API_URL` | Open WebUI API base URL (must include `/api`) | `http://127.0.0.1:8080/api` |
| `OWUI_API_KEY` | API key / bearer token (sent as `Authorization: Bearer ...`) | *(none)* |

If `OWUI_API_KEY` is not set, the server will still run, but requests may fail depending on your Open WebUI configuration.

## Run (stdio MCP server)

Normally, **your MCP client** (Claude Desktop / Cursor) launches the server. For local debugging you can run:

```bash
OWUI_API_URL="http://localhost:8080/api" \
OWUI_API_KEY="sk-..." \
owui-mcp
```

Or:

```bash
python -m owui_mcp
```

> Note: MCP stdio servers must keep **stdout** reserved for the protocol. This project logs to **stderr**.

## Connect to Claude Desktop (MCP)

Claude Desktop reads MCP server definitions from `claude_desktop_config.json`.

### Config file location

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Reference: https://modelcontextprotocol.io/docs/develop/connect-local-servers

### Minimal config (recommended)

```json
{
  "mcpServers": {
    "owui": {
      "command": "owui-mcp",
      "env": {
        "OWUI_API_URL": "http://localhost:8080/api",
        "OWUI_API_KEY": "sk-..."
      }
    }
  }
}
```

If `owui-mcp` is not available on your `PATH`, use an absolute command path.

### Alternative config (most reliable, uses python -m)

Use the Python interpreter you want Claude Desktop to run (venv/pipx/system python):

```json
{
  "mcpServers": {
    "owui": {
      "command": "/ABSOLUTE/PATH/TO/python",
      "args": ["-m", "owui_mcp"],
      "env": {
        "OWUI_API_URL": "http://localhost:8080/api",
        "OWUI_API_KEY": "sk-..."
      }
    }
  }
}
```

### Restart required

After editing the config, **fully quit and restart** Claude Desktop.

## Bonus: Cursor MCP

Cursor supports a similar `mcpServers` JSON config.

- **macOS**: `~/.cursor/mcp.json`
- **Windows**: `%APPDATA%\.cursor\mcp.json`

Use the same server definitions as the Claude examples above.

## Updating owui_client

`owui_mcp` auto-discovers `owui_client` methods at startup. Updating the client library automatically adds new endpoints as MCP tools:

```bash
pip install --upgrade owui-client
```

## License

MIT
