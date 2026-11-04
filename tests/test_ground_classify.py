"""Unit tests for ground classification (Issue #3).

Pure pipeline-dictionary tests: no PDAL bindings or fixture files required.
End-to-end behaviour on real data is covered by the regression harness
(`make harness`, scenarios ground_*).
"""
import argparse

import pytest

from lib.classify import (
    GROUND_CLASS,
    GroundClassifyParams,
    add_ground_args,
    build_ground_classify_stages,
    ground_params_from_args,
)
from lib.filter import FilterOptions, VoxelParams, build_pipeline


def _types(stages):
    return [s.get("type") for s in stages if isinstance(s, dict)]


# ---------------------------------------------------------------------------
# Params validation
# ---------------------------------------------------------------------------

def test_rejects_unknown_method():
    with pytest.raises(ValueError, match="method"):
        GroundClassifyParams(method="lidar-magic")


@pytest.mark.parametrize("field", ["cell", "window", "threshold"])
def test_rejects_non_positive_values(field):
    with pytest.raises(ValueError, match=field):
        GroundClassifyParams(method="smrf", **{field: 0})


def test_csf_rejects_window():
    with pytest.raises(ValueError, match="window"):
        GroundClassifyParams(method="csf", window=10.0)


# ---------------------------------------------------------------------------
# Stage builder
# ---------------------------------------------------------------------------

def test_disabled_returns_empty():
    assert build_ground_classify_stages(None) == []


@pytest.mark.parametrize("method", ["smrf", "pmf", "csf"])
def test_default_stages_reset_then_classify(method):
    stages = build_ground_classify_stages(GroundClassifyParams(method=method))
    # Existing classes are reset so the output only carries the new result.
    assert stages[0] == {"type": "filters.assign", "value": "Classification = 0"}
    # PDAL defaults are used when no tuning value is given.
    assert stages[1] == {"type": f"filters.{method}"}
    assert len(stages) == 2


def test_keep_existing_skips_reset():
    stages = build_ground_classify_stages(
        GroundClassifyParams(method="smrf", reset_classes=False)
    )
    assert _types(stages) == ["filters.smrf"]


def test_smrf_parameter_mapping():
    stages = build_ground_classify_stages(
        GroundClassifyParams(method="smrf", cell=2.0, window=60.0, threshold=1.0)
    )
    assert stages[1] == {
        "type": "filters.smrf", "cell": 2.0, "window": 60.0, "threshold": 1.0,
    }


def test_pmf_parameter_mapping():
    stages = build_ground_classify_stages(
        GroundClassifyParams(method="pmf", cell=2.0, window=200.0, threshold=3.0)
    )
    assert stages[1] == {
        "type": "filters.pmf",
        "cell_size": 2.0,
        "max_window_size": 200.0,
        "max_distance": 3.0,
    }


def test_csf_parameter_mapping():
    stages = build_ground_classify_stages(
        GroundClassifyParams(method="csf", cell=3.0, threshold=1.5)
    )
    assert stages[1] == {"type": "filters.csf", "resolution": 3.0, "threshold": 1.5}


def test_ground_only_appends_expression():
    stages = build_ground_classify_stages(
        GroundClassifyParams(method="csf", ground_only=True)
    )
    assert stages[-1] == {
        "type": "filters.expression",
        "expression": f"Classification == {GROUND_CLASS}",
    }


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def test_pipeline_places_classification_before_voxel():
    options = FilterOptions(
        ground=GroundClassifyParams(method="csf"),
        voxel=VoxelParams(cell_size=0.05),
    )
    types = _types(build_pipeline("in.las", "out.las", options)["pipeline"])
    assert types.index("filters.csf") < types.index(
        "filters.voxelcentroidnearestneighbor"
    )


def test_pipeline_without_ground_is_unchanged():
    types = _types(build_pipeline("in.las", "out.las", FilterOptions())["pipeline"])
    assert types == ["writers.las"]


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _parse(argv):
    parser = argparse.ArgumentParser()
    add_ground_args(parser)
    return parser.parse_args(argv)


def test_cli_no_flag_means_disabled():
    assert ground_params_from_args(_parse([])) is None


def test_cli_full_options():
    args = _parse([
        "--ground-classify", "pmf",
        "--ground-only",
        "--ground-cell", "2",
        "--ground-window", "200",
        "--ground-threshold", "3",
        "--ground-keep-classes",
    ])
    assert ground_params_from_args(args) == GroundClassifyParams(
        method="pmf",
        ground_only=True,
        cell=2.0,
        window=200.0,
        threshold=3.0,
        reset_classes=False,
    )


def test_cli_rejects_unknown_method():
    with pytest.raises(SystemExit):
        _parse(["--ground-classify", "nope"])


def test_cli_tuning_without_method_is_error():
    with pytest.raises(ValueError, match="--ground-classify"):
        ground_params_from_args(_parse(["--ground-cell", "2"]))


@pytest.mark.parametrize("module_name", ["main", "cli"])
def test_entrypoints_expose_ground_options(module_name):
    module = __import__(module_name)
    args = module.parse_args(
        ["-i", "in.las", "-o", "out.las", "--ground-classify", "csf", "--ground-only"]
    )
    options = module.assemble_config(args)
    assert options.ground == GroundClassifyParams(method="csf", ground_only=True)
