"""
MCP server that auto-discovers owui_client methods and exposes them as tools.

Configuration via environment variables:
    OWUI_API_URL  – Open WebUI API base URL  (default: http://127.0.0.1:8080/api)
    OWUI_API_KEY  – Bearer token / API key   (default: None)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys

from fastmcp import FastMCP
from fastmcp.experimental.transforms.code_mode import CodeMode, Search, GetSchemas, MontySandboxProvider


from owui_client import OpenWebUI
from owui_client.client_base import ResourceBase

from owui_mcp import __version__

logger = logging.getLogger("owui_mcp")


def create_server() -> FastMCP:
    """Build and return a configured FastMCP server."""
    api_url = os.environ.get("OWUI_API_URL", "http://127.0.0.1:8080/api")
    api_key = os.environ.get("OWUI_API_KEY")

    logger.info("owui_mcp starting — api_url=%s", api_url)

    if not api_key:
        logger.warning("OWUI_API_KEY is not set — requests will be unauthenticated.")

    try:
        client = OpenWebUI(api_url=api_url, api_key=api_key)
    except Exception:
        logger.critical("Failed to create OpenWebUI client", exc_info=True)
        raise

    # Configure the Monty sandbox with memory and execution time limits
    sandbox = MontySandboxProvider(
        limits={"max_duration_secs": 60, "max_memory": 200_000_000},
    )

    code_mode = CodeMode(
        discovery_tools=[
            Search(default_detail="detailed"),
            GetSchemas(),
        ],
        sandbox_provider=sandbox,
    )

    mcp = FastMCP("owui_mcp", version=__version__, transforms=[code_mode])

    tool_count = 0
    for router_name in sorted(dir(client)):
        if router_name.startswith("_"):
            continue
        router = getattr(client, router_name, None)
        if not isinstance(router, ResourceBase):
            continue

        for method_name in sorted(dir(router)):
            if method_name.startswith("_"):
                continue
            method = getattr(router, method_name, None)
            
            # Use inspect.iscoroutinefunction for Python 3.14+ compatibility
            if method is None or not inspect.iscoroutinefunction(method):
                continue

            tool_name = f"{router_name}__{method_name}"

            try:
                description = inspect.getdoc(method) or f"{router_name}.{method_name}"
                
                # Bind the method directly to FastMCP
                mcp.tool(name=tool_name, description=description)(method)
                tool_count += 1

            except Exception:
                logger.warning(
                    "Skipping tool %s — registration failed", tool_name, exc_info=True
                )

    logger.info("Discovered %d tools from owui_client", tool_count)
    return mcp


def main() -> None:
    """Console-script entry point (``owui-mcp``)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        server = create_server()
        server.run(transport='stdio')
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.critical("owui_mcp crashed", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
