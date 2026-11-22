"""The MCP tools: one input file, one output file, the CLI's filter options.

The input schema is read from the CLI's own argument parser (`cli.build_parser`), so a tool
accepts exactly the options `gqfilter` accepts, with the same defaults. A call is turned back
into the argv of the equivalent `gqfilter` command and goes through `cli.parse_args`,
`cli.assemble_config` and `build_pipeline` in this process (no child process).

There is deliberately no folder or batch tool: that is the paid edition's. An agent that wants
several files calls the single-file tool once per file.

Nothing here imports `mcp`; `gqfilter_mcp.server` wires these functions to the protocol.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import sys
from collections.abc import Mapping
from typing import Any, Final

import cli
from gq_filter import __version__ as VERSION
from gq_filter.filter import build_pipeline, execute_pipeline

IO_DESTS: Final = ("input", "output")
NON_FILTER_DESTS: Final = frozenset({"help", "dry_run"})
INPUT_EXTENSIONS: Final = (".las", ".laz", ".e57")
OUTPUT_EXTENSIONS: Final = (".las", ".laz", ".e57", ".txt", ".csv")
MAX_REASON_CHARS: Final = 300

# CLI rule restated for the schema: GroundClassifyParams rejects lengths <= 0.
_EXTRA_CONSTRAINTS: Final[dict[str, dict[str, Any]]] = {
    "ground_cell": {"exclusiveMinimum": 0},
    "ground_window": {"exclusiveMinimum": 0},
    "ground_threshold": {"exclusiveMinimum": 0},
}

EXTRA_PROPERTIES: Final[dict[str, dict[str, Any]]] = {
    "overwrite": {
        "type": "boolean",
        "default": False,
        "description": "Replace the output file if it already exists (gqfilter_run only).",
    },
}

_IO_PROPERTIES: Final[dict[str, dict[str, Any]]] = {
    "input": {
        "type": "string",
        "minLength": 1,
        "description": f"Absolute path of one input point cloud ({', '.join(INPUT_EXTENSIONS)}).",
    },
    "output": {
        "type": "string",
        "minLength": 1,
        "description": (
            "Absolute path of the output file; its folder must exist "
            f"({', '.join(OUTPUT_EXTENSIONS)}; .copc.laz writes COPC)."
        ),
    },
}


class ToolError(Exception):
    """A call that cannot be done; the message is shown to the caller as is."""


# --- schema -------------------------------------------------------------------

def _flag(action: argparse.Action) -> str:
    return next(s for s in action.option_strings if s.startswith("--"))


def _scalar_schema(action: argparse.Action) -> dict[str, Any]:
    if action.choices:
        return {"type": "string", "enum": list(action.choices)}
    if action.type is float:
        return {"type": "number"}
    if action.type is int:
        return {"type": "integer"}
    raise TypeError(f"CLI option {action.dest!r} has a type the MCP schema does not map")


def _property(action: argparse.Action) -> dict[str, Any]:
    if isinstance(action, argparse._StoreTrueAction):
        prop: dict[str, Any] = {"type": "boolean", "default": False}
    elif action.nargs == "+":
        prop = {"type": "array", "items": _scalar_schema(action), "minItems": 1}
    else:
        prop = _scalar_schema(action)
        if action.default is not None:
            prop["default"] = action.default
    prop.update(_EXTRA_CONSTRAINTS.get(action.dest, {}))
    prop["description"] = f"{_flag(action)}: {action.help}"
    return prop


def _filter_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        a for a in parser._actions
        if a.dest not in NON_FILTER_DESTS and a.dest not in IO_DESTS
    ]


def build_input_schema() -> dict[str, Any]:
    """JSON Schema of a tool call: input, output, then every CLI filter option."""
    properties: dict[str, Any] = dict(_IO_PROPERTIES)
    for action in _filter_actions(cli.build_parser()):
        properties[action.dest] = _property(action)
    properties.update(EXTRA_PROPERTIES)
    return {
        "type": "object",
        "properties": properties,
        "required": list(IO_DESTS),
        "additionalProperties": False,
    }


INPUT_SCHEMA: Final = build_input_schema()
_ACTIONS: Final = {a.dest: a for a in cli.build_parser()._actions}


# --- arguments <-> CLI argv ---------------------------------------------------

def _check_finite(arguments: Mapping[str, Any]) -> None:
    """JSON parsers accept NaN / Infinity and the schema's `number` lets them through."""
    for name, value in arguments.items():
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            raise ToolError(f"Invalid argument {name}: must be a finite number")


def _check_schema(arguments: Mapping[str, Any]) -> None:
    import jsonschema  # comes with mcp; only needed when a tool is called

    _check_finite(arguments)

    validator = jsonschema.Draft202012Validator(INPUT_SCHEMA)
    problems = sorted(validator.iter_errors(dict(arguments)), key=lambda e: list(e.path))
    if problems:
        first = problems[0]
        where = ".".join(str(p) for p in first.path) or "arguments"
        raise ToolError(f"Invalid argument {where}: {first.message[:MAX_REASON_CHARS]}")


def _value_text(action: argparse.Action, value: Any) -> str:
    return str(int(value)) if action.type is int else str(value)


def to_argv(arguments: Mapping[str, Any]) -> list[str]:
    """The `gqfilter` argv equivalent to the tool arguments (no --dry-run)."""
    argv = [f"--input={arguments['input']}", f"--output={arguments['output']}"]
    for dest, value in arguments.items():
        action = _ACTIONS.get(dest)
        if action is None or dest in IO_DESTS or value is None:
            continue
        flag = _flag(action)
        if isinstance(action, argparse._StoreTrueAction):
            if value:
                argv.append(flag)
        elif action.nargs == "+":
            argv += [flag, *(_value_text(action, v) for v in value)]
        else:
            argv.append(f"{flag}={_value_text(action, value)}")
    return argv


def arguments_from_namespace(args: argparse.Namespace) -> dict[str, Any]:
    """Tool arguments for a parsed CLI command (options left at their default are omitted)."""
    parser = cli.build_parser()
    return {
        a.dest: getattr(args, a.dest)
        for a in _filter_actions(parser)
        if getattr(args, a.dest) != parser.get_default(a.dest)
    }


def parse_arguments(arguments: Mapping[str, Any]) -> argparse.Namespace:
    """Check the arguments and parse them with the CLI's parser."""
    _check_schema(arguments)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            return cli.parse_args(to_argv(arguments))
    except SystemExit:
        reason = stderr.getvalue().strip().splitlines()[-1:] or ["invalid arguments"]
        raise ToolError(f"gqfilter rejected the arguments: {reason[0][:MAX_REASON_CHARS]}") from None


# --- boundary checks ----------------------------------------------------------

def _has_extension(path: str, allowed: tuple) -> bool:
    return path.lower().endswith(allowed)


def _is_absolute(path: str) -> bool:
    # On Windows (Python < 3.13) isabs("\\foo") is True although it is drive-relative.
    if os.name == "nt" and not os.path.splitdrive(path)[0]:
        return False
    return os.path.isabs(path)


def _check_paths(input_path: str, output_path: str, overwrite: bool) -> None:
    for name, path in (("input", input_path), ("output", output_path)):
        if not _is_absolute(path):
            raise ToolError(f"{name} must be an absolute path (got {path!r})")
    if not os.path.isfile(input_path):
        raise ToolError(f"input file does not exist: {input_path}")
    if not _has_extension(input_path, INPUT_EXTENSIONS):
        raise ToolError(f"input must be one of {', '.join(INPUT_EXTENSIONS)}: {input_path}")
    if not _has_extension(output_path, OUTPUT_EXTENSIONS):
        raise ToolError(f"output must be one of {', '.join(OUTPUT_EXTENSIONS)}: {output_path}")
    out_dir = os.path.dirname(output_path)
    if not os.path.isdir(out_dir):
        raise ToolError(f"output folder does not exist: {out_dir}")
    if os.path.exists(output_path):
        if os.path.samefile(input_path, output_path):
            raise ToolError("output is the same file as the input")
        if os.path.isdir(output_path):
            raise ToolError(f"output is a folder, not a file: {output_path}")
        if not overwrite:
            raise ToolError(f"output already exists (pass overwrite: true to replace it): {output_path}")


def _prepare(arguments: Mapping[str, Any], overwrite: bool) -> dict[str, Any]:
    """Validated CLI args and the pipeline, exactly as `gqfilter` would build them."""
    args = parse_arguments(arguments)
    _check_paths(args.input, args.output, overwrite)
    try:
        options = cli.assemble_config(args)
        with contextlib.redirect_stdout(sys.stderr):  # E57 origin lookup prints progress
            pipeline = build_pipeline(args.input, args.output, options)
    except ValueError as err:
        raise ToolError(str(err)) from err
    return {"args": args, "pipeline": pipeline}


# --- point counts -------------------------------------------------------------

def input_point_count(metadata: Any) -> int | None:
    """The input's point count from the executed pipeline's reader metadata (its header)."""
    stages = metadata.get("metadata") if isinstance(metadata, Mapping) else None
    if not isinstance(stages, Mapping):
        return None
    for name, stage in stages.items():
        if not name.startswith("readers."):
            continue
        entries = stage if isinstance(stage, list) else [stage]
        for entry in entries:
            count = entry.get("count") if isinstance(entry, Mapping) else None
            if isinstance(count, int) and count >= 0:
                return count
    return None


def _quickinfo_point_count(path: str) -> int | None:
    """Fallback: the reader's quick header read (no points loaded)."""
    import pdal  # lazy, as in execute_pipeline

    try:
        info = pdal.Pipeline(json.dumps({"pipeline": [path]})).quickinfo
    except Exception:  # noqa: BLE001 - a missing count must not fail a run that succeeded
        return None
    for stage in info.values():
        count = stage.get("num_points") if isinstance(stage, Mapping) else None
        if isinstance(count, int) and count >= 0:
            return count
    return None


def _summary(written: int, total: int | None, output: str) -> str:
    text = f"{written} points written to {output}"
    if total is not None:
        text += f" (input had {total} points; {total - written} removed)"
    return text + ". points_processed = the number of points written to the output file."


# --- tools --------------------------------------------------------------------

def dry_run(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """gqfilter_dry_run: the PDAL pipeline `gqfilter --dry-run` prints. Writes nothing."""
    prepared = _prepare(arguments, overwrite=True)
    return {"ok": True, "pipeline": prepared["pipeline"]}


def run(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """gqfilter_run: filter one file, as `gqfilter -i ... -o ...` does.

    `points_processed` is the number of points written to the output file (the CLI's
    "Points processed" line). `points_input` is the input header's count and
    `points_removed` the difference; both are null if the reader reports no count.
    """
    prepared = _prepare(arguments, overwrite=bool(arguments.get("overwrite", False)))
    args = prepared["args"]
    with contextlib.redirect_stdout(sys.stderr):
        result = execute_pipeline(prepared["pipeline"])
    if not result["success"]:
        hint = (" (a partial output file was left; pass overwrite: true to retry)"
                if os.path.exists(args.output) else "")
        raise ToolError(
            f"PDAL pipeline failed: {str(result['error'])[:MAX_REASON_CHARS * 3]}{hint}")
    written = int(result["points_processed"])
    total = input_point_count(result.get("metadata"))
    if total is None:
        total = _quickinfo_point_count(args.input)
    payload = {
        "ok": True,
        "input": args.input,
        "output": args.output,
        "points_processed": written,
        "points_input": total,
        "points_removed": None if total is None else total - written,
        "summary": _summary(written, total, args.output),
    }
    if "note" in result:
        payload["note"] = result["note"]
    return payload


def info() -> dict[str, Any]:
    """gqfilter_info: version, accepted file types and the options with their defaults."""
    return {
        "ok": True,
        "version": VERSION,
        "input_extensions": list(INPUT_EXTENSIONS),
        "output_extensions": list(OUTPUT_EXTENSIONS),
        "options": INPUT_SCHEMA["properties"],
        "notes": (
            "One input file per call. There is no folder / batch tool in the free edition; "
            "call gqfilter_run once per file. Lengths are in the units of the input CRS."
        ),
    }
