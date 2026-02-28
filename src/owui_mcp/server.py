"""
MCP server that auto-discovers owui_client methods and exposes them as tools.

Configuration via environment variables:
    OWUI_API_URL  – Open WebUI API base URL  (default: http://127.0.0.1:8080/api)
    OWUI_API_KEY  – Bearer token / API key   (default: None)
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import os
import sys
import types
from typing import Any, Union, get_args, get_origin

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pydantic import BaseModel

from owui_client import OpenWebUI
from owui_client.client_base import ResourceBase

logger = logging.getLogger("owui_mcp")

# ---------------------------------------------------------------------------
# JSON-Schema helpers
# ---------------------------------------------------------------------------

_PRIMITIVE_MAP: dict[type, dict] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    bytes: {"type": "string", "description": "Base64-encoded binary data"},
}


def _build_type_schema(param_type: Any) -> tuple[dict, dict]:
    """Return ``(property_schema, $defs)`` for a Python type annotation."""
    defs: dict = {}

    if param_type is type(None):
        return {"type": "null"}, defs

    origin = get_origin(param_type)

    # --- Union / Optional ------------------------------------------------
    if origin is Union or isinstance(param_type, types.UnionType):
        args = get_args(param_type)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _build_type_schema(non_none[0])
        any_of = []
        for t in non_none:
            s, d = _build_type_schema(t)
            any_of.append(s)
            defs.update(d)
        return {"anyOf": any_of}, defs

    # --- list[T] ---------------------------------------------------------
    if origin is list:
        args = get_args(param_type)
        if args:
            items_schema, items_defs = _build_type_schema(args[0])
            defs.update(items_defs)
            return {"type": "array", "items": items_schema}, defs
        return {"type": "array"}, defs

    # --- dict[K, V] / plain dict -----------------------------------------
    if origin is dict or param_type is dict:
        return {"type": "object"}, defs

    # --- Pydantic models --------------------------------------------------
    if isinstance(param_type, type) and issubclass(param_type, BaseModel):
        schema = param_type.model_json_schema()
        if "$defs" in schema:
            defs.update(schema.pop("$defs"))
        return schema, defs

    # --- Primitives -------------------------------------------------------
    if param_type in _PRIMITIVE_MAP:
        return _PRIMITIVE_MAP[param_type].copy(), defs

    # --- Fallback ---------------------------------------------------------
    return {"type": "string"}, defs


# ---------------------------------------------------------------------------
# Type-hint helpers
# ---------------------------------------------------------------------------

def _get_type_hints_safe(method: Any) -> dict[str, Any]:
    """``get_type_hints`` with a silent fallback to raw annotations."""
    try:
        from typing import get_type_hints
        return get_type_hints(method)
    except Exception:
        sig = inspect.signature(method)
        return {
            name: p.annotation
            for name, p in sig.parameters.items()
            if p.annotation is not inspect.Parameter.empty
        }


def _is_optional(param_type: Any) -> bool:
    """Return True when *param_type* is ``Optional[T]`` / ``T | None``."""
    origin = get_origin(param_type)
    if origin is Union or isinstance(param_type, types.UnionType):
        return type(None) in get_args(param_type)
    return False


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

ToolHandler = tuple[Any, dict[str, Any]]  # (bound_method, type_hints)


def _discover_tools(client: OpenWebUI) -> tuple[list[Tool], dict[str, ToolHandler]]:
    """Walk every ``ResourceBase`` router on *client* and register tools.

    Individual methods that fail introspection are skipped with a warning
    so that one bad type-hint does not prevent the entire server from starting.
    """
    tools: list[Tool] = []
    handlers: dict[str, ToolHandler] = {}

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
            if method is None or not asyncio.iscoroutinefunction(method):
                continue

            tool_name = f"{router_name}__{method_name}"

            try:
                description = inspect.getdoc(method) or f"{router_name}.{method_name}"

                sig = inspect.signature(method)
                hints = _get_type_hints_safe(method)

                properties: dict[str, Any] = {}
                required: list[str] = []
                all_defs: dict[str, Any] = {}

                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue

                    param_type = hints.get(param_name, str)
                    prop_schema, prop_defs = _build_type_schema(param_type)
                    all_defs.update(prop_defs)
                    properties[param_name] = prop_schema

                    # Required only when no default AND not Optional
                    if param.default is inspect.Parameter.empty and not _is_optional(param_type):
                        required.append(param_name)

                input_schema: dict[str, Any] = {
                    "type": "object",
                    "properties": properties,
                }
                if required:
                    input_schema["required"] = required
                if all_defs:
                    input_schema["$defs"] = all_defs

                tools.append(
                    Tool(name=tool_name, description=description, inputSchema=input_schema)
                )
                handlers[tool_name] = (method, hints)

            except Exception:
                logger.warning(
                    "Skipping tool %s — introspection failed", tool_name, exc_info=True
                )

    return tools, handlers


# ---------------------------------------------------------------------------
# Argument deserialization & result serialization
# ---------------------------------------------------------------------------

def _deserialize_arg(value: Any, param_type: Any) -> Any:
    """Coerce a raw JSON value into the Python type expected by owui_client."""
    if value is None:
        return None

    # Unwrap Optional
    origin = get_origin(param_type)
    if origin is Union or isinstance(param_type, types.UnionType):
        args = get_args(param_type)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            param_type = non_none[0]
            origin = get_origin(param_type)

    # Pydantic model
    if isinstance(param_type, type) and issubclass(param_type, BaseModel):
        return param_type.model_validate(value)

    # bytes ← base64
    if param_type is bytes:
        return base64.b64decode(value)

    # list[BaseModel]
    if origin is list:
        args = get_args(param_type)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return [args[0].model_validate(item) for item in value]

    return value


def _serialize_result(result: Any) -> str:
    """Turn any owui_client return value into a JSON string."""
    if result is None:
        return json.dumps(None)

    if isinstance(result, bytes):
        return json.dumps({"_base64": base64.b64encode(result).decode()})

    if isinstance(result, BaseModel):
        return result.model_dump_json()

    if isinstance(result, list):
        items = [
            item.model_dump() if isinstance(item, BaseModel) else item
            for item in result
        ]
        return json.dumps(items, default=str)

    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server() -> Server:
    """Build and return a configured :class:`mcp.server.Server`."""
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

    from owui_mcp import __version__

    server = Server("owui_mcp", version=__version__)

    tools, handlers = _discover_tools(client)
    logger.info("Discovered %d tools from owui_client", len(tools))

    # -- list_tools --------------------------------------------------------
    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tools

    # -- call_tool ---------------------------------------------------------
    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        if name not in handlers:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"Unknown tool: {name}"}),
                )
            ]

        method, hints = handlers[name]
        sig = inspect.signature(method)
        arguments = arguments or {}

        try:
            kwargs: dict[str, Any] = {}
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                if param_name in arguments:
                    ptype = hints.get(param_name)
                    raw = arguments[param_name]
                    kwargs[param_name] = (
                        _deserialize_arg(raw, ptype) if ptype else raw
                    )
                # omitted params fall back to the method's own defaults

            result = await method(**kwargs)
            return [TextContent(type="text", text=_serialize_result(result))]

        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"error": f"{type(exc).__name__}: {exc}"},
                        default=str,
                    ),
                )
            ]

    return server


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

async def run_stdio() -> None:
    """Run the MCP server over stdio transport."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Console-script entry point (``owui-mcp``)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        asyncio.run(run_stdio())
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.critical("owui_mcp crashed", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
