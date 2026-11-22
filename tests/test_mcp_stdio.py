"""End to end: `python cli.py mcp` spoken to over stdio by the MCP SDK's own client."""
import json
import os
import sys

import pytest

pytest.importorskip("mcp")

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from gqfilter_mcp import tools
from tests.test_mcp_tools import FIXTURE, REPO, STATISTICAL_OUTLIER_POINTS, needs_pdal

TIMEOUT_SECONDS = 120
EXPECTED_TOOLS = {"gqfilter_run", "gqfilter_dry_run", "gqfilter_info"}


def _server_command():
    """`GQFILTER_MCP_COMMAND` (e.g. a built bundle) or this checkout's cli.py."""
    command = os.environ.get("GQFILTER_MCP_COMMAND")
    if command:
        return StdioServerParameters(command=command, args=["mcp"])
    return StdioServerParameters(command=sys.executable, args=["cli.py", "mcp"], cwd=str(REPO))


async def _session(steps):
    with anyio.fail_after(TIMEOUT_SECONDS):
        async with (
            stdio_client(_server_command()) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            return init, await steps(session)


def _payload(result):
    return json.loads(result.content[0].text)


def test_initialize_list_tools_and_dry_run(tmp_path):
    output = tmp_path / "out.las"
    arguments = {"input": str(FIXTURE), "output": str(output), "outlier_statistical": True}

    async def steps(session):
        listed = await session.list_tools()
        dry = await session.call_tool("gqfilter_dry_run", arguments)
        bad = await session.call_tool("gqfilter_dry_run", {**arguments, "folder": "/tmp"})
        unknown = await session.call_tool("gqfilter_batch", {})
        return listed, dry, bad, unknown

    init, (listed, dry, bad, unknown) = anyio.run(_session, steps)

    assert init.server_info.name == "gqfilter"
    assert init.server_info.version == tools.VERSION
    assert {t.name for t in listed.tools} == EXPECTED_TOOLS
    by_name = {t.name: t for t in listed.tools}
    assert by_name["gqfilter_run"].input_schema["properties"] == tools.INPUT_SCHEMA["properties"]

    assert not dry.is_error
    assert _payload(dry)["pipeline"] == tools.dry_run(arguments)["pipeline"]
    assert not output.exists()

    assert bad.is_error and "folder" in _payload(bad)["error"]
    assert unknown.is_error and "unknown tool" in _payload(unknown)["error"]


@needs_pdal
def test_run_over_stdio(tmp_path):
    output = tmp_path / "out.las"
    arguments = {"input": str(FIXTURE), "output": str(output), "outlier_statistical": True}

    async def steps(session):
        first = await session.call_tool("gqfilter_run", arguments)
        again = await session.call_tool("gqfilter_run", arguments)
        info = await session.call_tool("gqfilter_info", {})
        return first, again, info

    _init, (first, again, info) = anyio.run(_session, steps)

    assert not first.is_error, first.content[0].text
    assert _payload(first)["points_processed"] == STATISTICAL_OUTLIER_POINTS
    assert first.structured_content["output"] == str(output)
    assert output.is_file()
    assert again.is_error and "already exists" in _payload(again)["error"]
    assert not info.is_error and _payload(info)["version"] == tools.VERSION
