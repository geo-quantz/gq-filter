"""Every min/max option pair works with only one side given (the other side left open).

The spatial clip used to write `filters.crop` bounds such as `([-inf,0.0],[-inf,inf])`, which
PDAL rejects ("No valid minimum value for range"). These tests hold each pair to:
  - the dry-run pipeline only names the bound that was given, and
  - a real run on the fixture keeps exactly the points on the kept side of that bound.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import cli
from gq_filter.filter import build_pipeline, execute_pipeline

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "autzen-small.laz"


def _pdal_available() -> bool:
    try:
        import pdal  # noqa: F401
    except Exception:  # noqa: BLE001 - bindings present but the C++ library fails to load
        return False
    return True


needs_pdal = pytest.mark.skipif(not _pdal_available(), reason="PDAL Python bindings not usable")


def _dry_run(argv):
    """The pipeline that `python cli.py --dry-run` prints."""
    proc = subprocess.run(
        [sys.executable, "cli.py", "--dry-run", "-i", str(FIXTURE), "-o", "out.las", *argv],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout.split("\n", 1)[1])["pipeline"]


def _filter_stages(argv):
    return [s for s in _dry_run(argv) if isinstance(s, dict) and s["type"].startswith("filters.")]


# --- dry-run: the spatial clip names only the bounds that were given ----------

CLIP_CASES = [
    (["--clip-xmin", "636560"], "X[636560.0:]"),
    (["--clip-xmax", "0"], "X[:0.0]"),
    (["--clip-ymin", "851760"], "Y[851760.0:]"),
    (["--clip-ymax", "851760"], "Y[:851760.0]"),
    (["--clip-zmin", "450"], "Z[450.0:]"),
    (["--clip-zmax", "450"], "Z[:450.0]"),
    (["--clip-xmin", "-10.5", "--clip-ymax", "5"], "X[-10.5:],Y[:5.0]"),
    (["--clip-xmin", "0", "--clip-xmax", "1e9"], "X[0.0:1000000000.0]"),
    (
        ["--clip-xmin", "1", "--clip-xmax", "2", "--clip-ymin", "3", "--clip-ymax", "4",
         "--clip-zmin", "5", "--clip-zmax", "6"],
        "X[1.0:2.0],Y[3.0:4.0],Z[5.0:6.0]",
    ),
]


@pytest.mark.parametrize(("argv", "limits"), CLIP_CASES, ids=[" ".join(a) for a, _ in CLIP_CASES])
def test_dry_run_clip_names_only_the_given_bounds(argv, limits):
    assert _filter_stages(argv) == [{"type": "filters.range", "limits": limits}]


EXPRESSION_CASES = [
    (["--z-min", "450"], "Z >= 450.0"),
    (["--z-max", "450"], "Z <= 450.0"),
    (["--intensity-min", "50"], "Intensity >= 50.0"),
    (["--intensity-max", "50"], "Intensity <= 50.0"),
]


@pytest.mark.parametrize(
    ("argv", "expression"), EXPRESSION_CASES, ids=[" ".join(a) for a, _ in EXPRESSION_CASES])
def test_dry_run_one_sided_expression(argv, expression):
    assert _filter_stages(argv) == [{"type": "filters.expression", "expression": expression}]


@pytest.mark.parametrize(("flag", "op"), [("--range-min", ">="), ("--range-max", "<=")])
def test_dry_run_one_sided_range(flag, op):
    (stage,) = _filter_stages([flag, "2"])
    assert stage["type"] == "filters.expression"
    assert " && " not in stage["expression"]
    assert stage["expression"].endswith(f"{op} 4.0)")


# --- real runs: each side keeps exactly the points on its side ---------------

@pytest.fixture(scope="module")
def points():
    import pdal

    pipeline = pdal.Pipeline(json.dumps({"pipeline": [str(FIXTURE)]}))
    pipeline.execute()
    return pipeline.arrays[0]


def _median(values):
    return float(sorted(values)[len(values) // 2])


def _run(tmp_path, argv):
    args = cli.parse_args(["-i", str(FIXTURE), "-o", str(tmp_path / "out.las"), *argv])
    result = execute_pipeline(build_pipeline(args.input, args.output, cli.assemble_config(args)))
    assert result["success"], result.get("error")
    return int(result["points_processed"])


def _distance(points):
    return (points["X"] ** 2 + points["Y"] ** 2 + points["Z"] ** 2) ** 0.5


# option pair -> the per-point value it bounds
PAIRS = {
    ("--clip-xmin", "--clip-xmax"): lambda p: p["X"],
    ("--clip-ymin", "--clip-ymax"): lambda p: p["Y"],
    ("--clip-zmin", "--clip-zmax"): lambda p: p["Z"],
    ("--z-min", "--z-max"): lambda p: p["Z"],
    ("--intensity-min", "--intensity-max"): lambda p: p["Intensity"],
    ("--range-min", "--range-max"): _distance,
}
RUN_CASES = [(pair, side) for pair in PAIRS for side in ("min", "max")]


@needs_pdal
@pytest.mark.parametrize(
    ("pair", "side"), RUN_CASES, ids=[f"{p[0 if s == 'min' else 1]}" for p, s in RUN_CASES])
def test_one_sided_run_keeps_the_points_on_that_side(tmp_path, points, pair, side):
    values = PAIRS[pair](points)
    threshold = _median(values)
    flag = pair[0] if side == "min" else pair[1]
    expected = int((values >= threshold).sum() if side == "min" else (values <= threshold).sum())
    assert 0 < expected < len(points)

    assert _run(tmp_path, [flag, repr(threshold)]) == expected
