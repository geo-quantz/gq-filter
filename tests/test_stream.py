"""大容量ファイルモード（ストリーム読込）のテスト。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gq_filter.classify import GroundClassifyParams
from gq_filter.filter import (
    DuplicateParams,
    FilterOptions,
    HeightZParams,
    IncidenceAngleParams,
    IntensityParams,
    RadiusOutlierParams,
    RadiusSampleParams,
    RangeParams,
    SpatialClipParams,
    StatisticalOutlierParams,
    VoxelParams,
    build_pipeline,
)
from gq_filter.stream import (
    NON_STREAMABLE_STAGES,
    NotStreamableError,
    StreamParams,
    blocking_stages,
    input_files,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parents[1]

STREAMABLE_OPTIONS = FilterOptions(
    range_dist=RangeParams(max_distance=10_000.0, manual_origin=(1000.0, 2000.0, 50.0)),
    height_z=HeightZParams(z_min=0.0),
    spatial_clip=SpatialClipParams(x_min=1010.0, x_max=1090.0, y_min=2000.0, y_max=2100.0),
    intensity=IntensityParams(max_intensity=900),
    radius_sample=RadiusSampleParams(radius=0.5),
)

# gq-filter の各フィルターが PDAL でストリーム可能か（PDAL 2.10 で確認）
OPTION_CASES = {
    "range": (FilterOptions(range_dist=RangeParams(max_distance=50.0)), True),
    "height_z": (FilterOptions(height_z=HeightZParams(z_min=0.0)), True),
    "clip": (FilterOptions(spatial_clip=SpatialClipParams(x_min=0.0, x_max=1e9, y_min=0.0, y_max=1e9)), True),
    "intensity": (FilterOptions(intensity=IntensityParams(min_intensity=1)), True),
    "sample": (FilterOptions(radius_sample=RadiusSampleParams(radius=1.0)), True),
    "incidence": (FilterOptions(incidence=IncidenceAngleParams(max_angle=80.0)), False),
    "statistical": (FilterOptions(statistical_outlier=StatisticalOutlierParams()), False),
    "radius_outlier": (FilterOptions(radius_outlier=RadiusOutlierParams()), False),
    "voxel": (FilterOptions(voxel=VoxelParams(cell_size=1.0)), False),
    "duplicate": (FilterOptions(duplicate=DuplicateParams()), False),
    "smrf": (FilterOptions(ground=GroundClassifyParams(method="smrf")), False),
    "pmf": (FilterOptions(ground=GroundClassifyParams(method="pmf")), False),
    "csf": (FilterOptions(ground=GroundClassifyParams(method="csf")), False),
}


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    pytest.importorskip("pdal")
    from tests.cloud_helpers import make_cloud, write_las

    path = tmp_path_factory.mktemp("stream") / "cloud.las"
    return write_las(path, make_cloud(20_000, outliers=50))


# ---- 判定（PDAL 不要） ----------------------------------------------------------


def test_blocking_stages_empty_for_streamable_filters():
    pipeline = build_pipeline("in.las", "out.las", STREAMABLE_OPTIONS)
    assert blocking_stages(pipeline) == []


@pytest.mark.parametrize("name", [k for k, (_, ok) in OPTION_CASES.items() if not ok])
def test_blocking_stages_names_the_filter(name):
    options, _ = OPTION_CASES[name]
    blocked = blocking_stages(build_pipeline("in.las", "out.las", options))
    assert blocked, name
    assert all(stage in NON_STREAMABLE_STAGES for stage in blocked)


def test_copc_writer_is_not_streamable():
    pipeline = build_pipeline("in.las", "out.copc.laz", FilterOptions())
    assert blocking_stages(pipeline) == ["writers.copc"]


def test_input_files_handles_strings_and_reader_dicts():
    pipeline = build_pipeline(["a.las", "b.laz"], "out.las", FilterOptions())
    pipeline["pipeline"][1] = {"filename": "b.laz", "default_srs": "EPSG:6677"}
    assert input_files(pipeline) == ["a.las", "b.laz"]


@pytest.mark.parametrize(
    "kwargs",
    [{"mode": "fast"}, {"chunk_size": 0}, {"chunk_size": -5}, {"threshold_bytes": -1}],
)
def test_stream_params_rejects_bad_values(kwargs):
    with pytest.raises(ValueError):
        StreamParams(**kwargs)


# ---- PDAL の判定と表が一致すること ---------------------------------------------


@pytest.mark.parametrize("name", sorted(OPTION_CASES))
def test_table_matches_pdal(name, cloud, tmp_path):
    import pdal

    options, expected = OPTION_CASES[name]
    pipeline = build_pipeline(str(cloud), str(tmp_path / "out.las"), options)
    assert pdal.Pipeline(json.dumps(pipeline)).streamable is expected
    assert (blocking_stages(pipeline) == []) is expected


# ---- 実行 -----------------------------------------------------------------------


def test_stream_on_matches_standard(cloud, tmp_path):
    from tests.cloud_helpers import read_points, sorted_xyz

    std = run_pipeline(build_pipeline(str(cloud), str(tmp_path / "std.las"), STREAMABLE_OPTIONS))
    stm = run_pipeline(
        build_pipeline(str(cloud), str(tmp_path / "stm.las"), STREAMABLE_OPTIONS),
        StreamParams(mode="on", chunk_size=1_000),
    )
    assert std["success"] and stm["success"], (std, stm)
    assert std["mode"] == "standard" and stm["mode"] == "stream"
    assert 0 < stm["points_processed"] == std["points_processed"]
    a = sorted_xyz(read_points(tmp_path / "std.las"))
    b = sorted_xyz(read_points(tmp_path / "stm.las"))
    assert a.shape == b.shape
    assert abs(a - b).max() < 1e-6


def test_output_coordinates_stay_on_input_grid(cloud, tmp_path):
    """offset: auto のままだと最大 0.5 mm ずれる。入力の格子に合わせて座標を変えない。"""
    from tests.cloud_helpers import read_points, sorted_xyz

    options = FilterOptions(height_z=HeightZParams(z_min=0.0), spatial_clip=STREAMABLE_OPTIONS.spatial_clip)
    for mode in ("off", "on"):
        out = tmp_path / f"{mode}.las"
        assert run_pipeline(build_pipeline(str(cloud), str(out), options), StreamParams(mode=mode))["success"]
        got = sorted_xyz(read_points(out))
        src = read_points(cloud)
        clip = options.spatial_clip
        keep = (src["X"] >= clip.x_min) & (src["X"] <= clip.x_max)
        expected = sorted_xyz(src[keep])
        assert got.shape == expected.shape
        assert abs(got - expected).max() < 1e-6, mode


def test_stream_on_rejects_non_streamable_with_filter_name(cloud, tmp_path):
    options = FilterOptions(statistical_outlier=StatisticalOutlierParams())
    pipeline = build_pipeline(str(cloud), str(tmp_path / "o.las"), options)
    with pytest.raises(NotStreamableError) as err:
        run_pipeline(pipeline, StreamParams(mode="on"))
    assert err.value.stages == ["filters.outlier"]
    assert "filters.outlier" in str(err.value)
    assert "--outlier-statistical" in str(err.value)
    assert not (tmp_path / "o.las").exists()


def test_auto_streams_above_threshold(cloud, tmp_path):
    pipeline = build_pipeline(str(cloud), str(tmp_path / "o.las"), STREAMABLE_OPTIONS)
    result = run_pipeline(pipeline, StreamParams(mode="auto", threshold_bytes=1))
    assert result["success"] and result["mode"] == "stream"


def test_auto_stays_standard_below_threshold(cloud, tmp_path):
    pipeline = build_pipeline(str(cloud), str(tmp_path / "o.las"), STREAMABLE_OPTIONS)
    result = run_pipeline(pipeline, StreamParams(mode="auto", threshold_bytes=10**12))
    assert result["success"] and result["mode"] == "standard"
    assert "note" not in result


def test_auto_falls_back_with_reason_when_not_streamable(cloud, tmp_path):
    options = FilterOptions(radius_outlier=RadiusOutlierParams(radius=2.0))
    pipeline = build_pipeline(str(cloud), str(tmp_path / "o.las"), options)
    result = run_pipeline(pipeline, StreamParams(mode="auto", threshold_bytes=1))
    assert result["success"] and result["mode"] == "standard"
    assert "filters.outlier" in result["note"]
    assert "--tile" in result["note"]


def test_stream_failure_is_reported_not_raised(tmp_path):
    pytest.importorskip("pdal")
    pipeline = build_pipeline(str(tmp_path / "missing.las"), str(tmp_path / "o.las"), FilterOptions())
    result = run_pipeline(pipeline, StreamParams(mode="on"))
    assert result["success"] is False
    assert result["error"]


# ---- CLI ----------------------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )


def test_cli_stream_on(cloud, tmp_path):
    out = tmp_path / "cli.las"
    r = _cli("-i", str(cloud), "-o", str(out), "--z-min", "0", "--stream", "on", "--chunk-size", "5000")
    assert r.returncode == 0, r.stderr
    assert "ストリーム" in r.stdout
    assert out.exists()


def test_cli_stream_on_not_streamable_exits_with_reason(cloud, tmp_path):
    out = tmp_path / "cli.las"
    r = _cli("-i", str(cloud), "-o", str(out), "--outlier-statistical", "--stream", "on")
    assert r.returncode == 1
    assert "filters.outlier" in r.stderr
    assert not out.exists()


def test_cli_dry_run_reports_streamability():
    r = _cli("-i", "in.las", "-o", "out.las", "--outlier-radius", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "filters.outlier" in r.stdout
