"""
MCP server that auto-discovers owui_client methods and exposes them as tools.

Configuration via environment variables:
    OWUI_API_URL  – Open WebUI API base URL  (default: http://127.0.0.1:8080/api)
    OWUI_API_KEY  – Bearer token / API key   (default: None)
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import sys

from fastmcp import FastMCP


from owui_client import OpenWebUI
from owui_client.client_base import ResourceBase

from owui_mcp import __version__

logger = logging.getLogger("owui_mcp")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="owui-mcp",
        description=(
            "MCP server that auto-discovers owui_client methods and exposes them as tools.\n\n"
            "Configuration via environment variables:\n"
            "  OWUI_API_URL  – Open WebUI API base URL  (default: http://127.0.0.1:8080/api)\n"
            "  OWUI_API_KEY  – Bearer token / API key   (default: None)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--code-mode",
        action="store_true",
        default=False,
        help=(
            "Enable FastMCP Code Mode: exposes meta-tools for discovery and sandboxed "
            "code execution instead of direct tool access. Off by default."
        ),
    )
    return parser.parse_args(argv)


def create_server(code_mode: bool = False) -> FastMCP:
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

    if code_mode:
        from fastmcp.experimental.transforms.code_mode import (
            CodeMode, Search, GetSchemas, MontySandboxProvider,
        )
        sandbox = MontySandboxProvider(
            limits={"max_duration_secs": 60, "max_memory": 200_000_000},
        )
        _code_mode_transform = CodeMode(
            discovery_tools=[
                Search(default_detail="detailed"),
                GetSchemas(),
            ],
            sandbox_provider=sandbox,
        )
        transforms = [_code_mode_transform]
    else:
        transforms = []

    mcp = FastMCP("owui_mcp", version=__version__, transforms=transforms)

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
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger.info("Code mode: %s", "enabled" if args.code_mode else "disabled")
    try:
        server = create_server(code_mode=args.code_mode)
        server.run(transport='stdio')
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.critical("owui_mcp crashed", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
