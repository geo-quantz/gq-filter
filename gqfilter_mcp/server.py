"""The MCP server on stdin/stdout (`gqfilter mcp`), built on the low-level `mcp.server.Server`.

A call runs the tool in a worker thread (PDAL blocks) and returns its JSON object as text and
as structured content. A bad call or a PDAL failure is a tool error (`is_error`), never a crash
of the server. Logs go to stderr; the `mcp` stdio transport keeps stray output off the wire.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Mapping
from typing import Any, Final

from gqfilter_mcp import tools

SERVER_NAME: Final = "gqfilter"
INSTRUCTIONS: Final = (
    "gqfilter filters one point cloud file (LAS / LAZ / E57) with PDAL. Paths are absolute. "
    "Use gqfilter_dry_run to check the pipeline, then gqfilter_run to write the output. "
    "For several files, call gqfilter_run once per file."
)

_log = logging.getLogger("gqfilter.mcp")
_run_lock = threading.Lock()  # one PDAL pipeline at a time

TOOLS: Final[dict[str, dict[str, Any]]] = {
    "gqfilter_run": {
        "fn": tools.run,
        "schema": tools.INPUT_SCHEMA,
        "description": (
            "Filter one point cloud file and write the result (same as `gqfilter -i IN -o OUT "
            "[options]`). Returns the number of points written and the output path. Refuses "
            "to replace an existing output unless overwrite is true."
        ),
    },
    "gqfilter_dry_run": {
        "fn": tools.dry_run,
        "schema": tools.INPUT_SCHEMA,
        "description": (
            "Return the PDAL pipeline JSON that gqfilter_run would execute for the same "
            "arguments (same as `gqfilter --dry-run`). Writes nothing."
        ),
    },
    "gqfilter_info": {
        "fn": lambda _arguments: tools.info(),
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "description": "gqfilter version, accepted file types and every filter option with its default.",
    },
}


def call(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Run a tool and return its JSON object; failures become `{"ok": false, "error": ...}`."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        with _run_lock:
            return tool["fn"](arguments)
    except tools.ToolError as err:
        return {"ok": False, "error": str(err)}
    except Exception as exc:  # noqa: BLE001 - the server must answer, whatever happened
        _log.error("unexpected error in tool %s", name, exc_info=True)
        return {"ok": False, "error": f"unexpected error ({type(exc).__name__}): {exc}"}


def build_server() -> Any:
    """A low-level MCP `Server` exposing TOOLS."""
    import anyio.to_thread
    import mcp_types as types
    from mcp.server import Server

    async def list_tools(_ctx: Any, _params: Any) -> Any:
        return types.ListToolsResult(tools=[
            types.Tool(name=name, description=t["description"], input_schema=dict(t["schema"]))
            for name, t in TOOLS.items()
        ])

    async def call_tool(_ctx: Any, params: Any) -> Any:
        arguments = dict(params.arguments or {})
        payload = await anyio.to_thread.run_sync(call, params.name, arguments)
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=payload,
            is_error=not payload.get("ok", False),
        )

    return Server(
        SERVER_NAME,
        version=tools.VERSION,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def serve() -> None:
    """Serve on stdin/stdout until the client closes."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        import anyio
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        print(f"Error: the MCP server needs the 'mcp' package (2.x): {exc}", file=sys.stderr)
        sys.exit(1)

    server = build_server()

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)
