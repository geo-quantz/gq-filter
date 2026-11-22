"""The MCP tools (gqfilter_mcp.tools): schema parity with the CLI, dry-run, run, errors.

The tools must accept exactly the CLI's filter options, with the CLI's defaults, and go through
the CLI's own parser and pipeline builder. These tests hold them to that.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")

import cli
from gqfilter_mcp import tools

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "autzen-small.laz"
CASES_FILE = REPO / "apps" / "desktop" / "src" / "args" / "cases.json"
DESKTOP_CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
STATISTICAL_OUTLIER_POINTS = 7262  # README 評価レポート: k=8, 2σ
FIXTURE_POINTS = 7388  # autzen-small.laz header point count


def _pdal_available() -> bool:
    try:
        import pdal  # noqa: F401
    except Exception:  # noqa: BLE001 - bindings present but the C++ library fails to load
        return False
    return True


needs_pdal = pytest.mark.skipif(not _pdal_available(), reason="PDAL Python bindings not usable")


def _filter_actions():
    return [
        a for a in cli.build_parser()._actions
        if a.dest not in tools.NON_FILTER_DESTS and a.dest not in tools.IO_DESTS
    ]


def _io(tmp_path, name="out.las"):
    return {"input": str(FIXTURE), "output": str(tmp_path / name)}


def _cli_dry_run(argv):
    """The pipeline that `python cli.py --dry-run` prints."""
    proc = subprocess.run(
        [sys.executable, "cli.py", "--dry-run", *argv],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    body = proc.stdout.split("\n", 1)[1]  # first line is the DRY RUN header
    return json.loads(body)


# --- schema <-> CLI parity ----------------------------------------------------

def test_schema_has_every_cli_filter_option_and_nothing_else():
    props = tools.INPUT_SCHEMA["properties"]
    cli_dests = {a.dest for a in _filter_actions()}
    assert set(props) == cli_dests | set(tools.IO_DESTS) | set(tools.EXTRA_PROPERTIES)
    assert tools.INPUT_SCHEMA["required"] == list(tools.IO_DESTS)
    assert tools.INPUT_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize("action", _filter_actions(), ids=lambda a: a.dest)
def test_schema_type_and_default_match_cli(action):
    prop = tools.INPUT_SCHEMA["properties"][action.dest]
    if isinstance(action, argparse._StoreTrueAction):
        assert prop["type"] == "boolean"
        assert prop["default"] is False
        return
    if action.nargs == "+":
        assert prop["type"] == "array"
        assert prop["minItems"] == 1
        prop = prop["items"]
    if action.choices:
        assert prop["enum"] == list(action.choices)
    elif action.type is float:
        assert prop["type"] == "number"
    elif action.type is int:
        assert prop["type"] == "integer"
    else:
        pytest.fail(f"unexpected CLI option type for {action.dest}")
    if action.default is None:
        assert "default" not in prop
    else:
        assert prop["default"] == action.default


def test_ground_lengths_must_be_positive_as_in_cli():
    for name in ("ground_cell", "ground_window", "ground_threshold"):
        assert tools.INPUT_SCHEMA["properties"][name]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("case", DESKTOP_CASES, ids=[c["name"] for c in DESKTOP_CASES])
def test_tool_arguments_round_trip_to_the_same_cli_options(case, tmp_path):
    """Every option set the desktop app sends: CLI argv -> tool arguments -> same FilterOptions."""
    io = _io(tmp_path)
    cli_args = cli.parse_args(["--input", io["input"], "--output", io["output"], *case["argv"]])
    arguments = {**io, **tools.arguments_from_namespace(cli_args)}
    tool_args = tools.parse_arguments(arguments)
    assert cli.assemble_config(tool_args) == cli.assemble_config(cli_args)


# --- dry run ----------------------------------------------------------------

DRY_RUN_OPTION_SETS = [
    {},
    {"outlier_statistical": True},
    {"outlier_statistical": True, "outlier_mean_k": 16, "outlier_multiplier": 1.5},
    {"keep_classes": [2, 6], "keep_returns": [1], "z_min": 440.0, "z_max": 480.5},
    {"clip_xmin": -10.0, "clip_ymax": 5.0, "sample_radius": 0.5, "deduplicate": True},
    {"range_min": 0.5, "range_max": 25.0, "incidence_angle_max": 86.0, "intensity_min": 10.0},
    {"ground_classify": "csf", "ground_cell": 3.0, "ground_threshold": 1.5, "ground_only": True},
    {"outlier_radius": True, "outlier_radius_value": 0.5, "outlier_min_k": 5},
]


@pytest.mark.parametrize("options", DRY_RUN_OPTION_SETS, ids=lambda o: ",".join(o) or "none")
def test_dry_run_equals_cli_dry_run(options, tmp_path):
    io = _io(tmp_path)
    payload = tools.dry_run({**io, **options})
    argv = tools.to_argv({**io, **options})
    assert payload["pipeline"] == _cli_dry_run(argv)
    assert not Path(io["output"]).exists()


def test_to_argv_is_what_a_person_would_type(tmp_path):
    io = _io(tmp_path)
    argv = tools.to_argv({**io, "outlier_statistical": True, "keep_classes": [2, 6], "z_min": -1.5})
    assert argv == [
        f"--input={io['input']}", f"--output={io['output']}",
        "--outlier-statistical", "--keep-classes", "2", "6", "--z-min=-1.5",
    ]


# --- run ----------------------------------------------------------------------

@needs_pdal
def test_run_statistical_outlier_on_fixture(tmp_path):
    io = _io(tmp_path)
    payload = tools.run({**io, "outlier_statistical": True})
    assert payload["points_processed"] == STATISTICAL_OUTLIER_POINTS
    assert payload["points_input"] == FIXTURE_POINTS
    assert payload["points_removed"] == FIXTURE_POINTS - STATISTICAL_OUTLIER_POINTS
    assert payload["output"] == io["output"]
    assert Path(io["output"]).is_file()


@needs_pdal
def test_run_points_processed_is_the_number_written(tmp_path):
    """points_processed = points in the output file; the summary says so in words."""
    import pdal

    io = _io(tmp_path)
    payload = tools.run({**io, "outlier_statistical": True})
    written = pdal.Pipeline(json.dumps({"pipeline": [io["output"]]}))
    assert written.execute() == payload["points_processed"]
    summary = payload["summary"]
    assert "7262 points written" in summary
    assert "7388" in summary and "126 removed" in summary
    assert "points_processed" in summary


@needs_pdal
def test_run_with_nothing_removed(tmp_path):
    payload = tools.run({**_io(tmp_path), "clip_xmin": 0.0})
    assert payload["points_processed"] == payload["points_input"] == FIXTURE_POINTS
    assert payload["points_removed"] == 0


@needs_pdal
def test_run_does_not_report_a_negative_removed_count(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "input_point_count", lambda _md: 10)
    monkeypatch.setattr(tools, "_quickinfo_point_count", lambda _path: None)
    payload = tools.run(_io(tmp_path))
    assert payload["points_processed"] == FIXTURE_POINTS
    assert payload["points_input"] is None and payload["points_removed"] is None


def test_input_point_count_from_reader_metadata():
    metadata = {"metadata": {"readers.las": {"count": 7388}, "writers.las": {}}}
    assert tools.input_point_count(metadata) == 7388
    assert tools.input_point_count({"metadata": {"readers.las": [{"count": 5}]}}) == 5
    assert tools.input_point_count({"metadata": {"readers.e57": {}}}) is None
    assert tools.input_point_count({}) is None
    assert tools.input_point_count(None) is None


@needs_pdal
def test_run_refuses_to_overwrite_unless_asked(tmp_path):
    io = _io(tmp_path)
    tools.run({**io, "outlier_statistical": True})
    with pytest.raises(tools.ToolError, match="already exists"):
        tools.run({**io, "outlier_statistical": True})
    payload = tools.run({**io, "outlier_statistical": True, "overwrite": True})
    assert payload["points_processed"] == STATISTICAL_OUTLIER_POINTS


# --- errors -------------------------------------------------------------------

@pytest.mark.parametrize("fn", [tools.run, tools.dry_run], ids=["run", "dry_run"])
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"input": "/nonexistent/in.las"}, "input file does not exist"),
        ({"input": "relative.las"}, "absolute path"),
        ({"output": "/nonexistent-dir/out.las"}, "output folder does not exist"),
        ({"output": "OUT"}, "absolute path"),
        ({"input": str(REPO / "README.md")}, "input must be"),
        ({"output_ext": ".ply"}, "output must be"),
        ({"outlier_mean_k": "8"}, "outlier_mean_k"),
        ({"outlier_mean_k": 2.5}, "outlier_mean_k"),
        ({"keep_classes": []}, "keep_classes"),
        ({"ground_classify": "tin"}, "ground_classify"),
        ({"ground_classify": "csf", "ground_cell": 0}, "ground_cell"),
        ({"ground_classify": "csf", "ground_window": 10.0}, "does not use window"),
        ({"ground_only": True}, "require --ground-classify"),
        ({"folder": "/tmp"}, "folder"),
        ({"intensity_min": float("nan")}, "finite"),
        ({"z_max": float("inf")}, "finite"),
        ({"output_same_as_input": True}, "same file as the input"),
    ],
)
def test_bad_arguments_are_tool_errors(fn, change, message, tmp_path):
    arguments = _io(tmp_path)
    change = dict(change)
    if change.pop("output_ext", None):
        arguments["output"] = str(tmp_path / "out.ply")
    if change.pop("output_same_as_input", None):
        arguments["output"] = arguments["input"]
    arguments.update(change)
    with pytest.raises(tools.ToolError, match=message):
        fn(arguments)


def test_missing_required_argument_is_a_tool_error():
    with pytest.raises(tools.ToolError, match="output"):
        tools.dry_run({"input": str(FIXTURE)})


@needs_pdal
def test_pdal_failure_is_a_tool_error(tmp_path):
    broken = tmp_path / "broken.las"
    broken.write_bytes(b"not a las file")
    with pytest.raises(tools.ToolError, match="PDAL"):
        tools.run({"input": str(broken), "output": str(tmp_path / "out.las")})


# --- info ---------------------------------------------------------------------

def test_info_lists_options_and_limits():
    info = tools.info()
    assert info["version"] == tools.VERSION
    assert set(info["options"]) == set(tools.INPUT_SCHEMA["properties"])
    assert ".laz" in info["input_extensions"]
    assert "batch" in info["notes"].lower()


def test_version_matches_pyproject():
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert tools.VERSION == pyproject["project"]["version"]
