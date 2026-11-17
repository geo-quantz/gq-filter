"""タイル分割モードのテスト。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from gq_filter.classify import GroundClassifyParams
from gq_filter.filter import (
    DuplicateParams,
    FilterOptions,
    HeightZParams,
    RadiusOutlierParams,
    RadiusSampleParams,
    build_pipeline,
)
from gq_filter.stream import StreamParams, run_pipeline
from gq_filter.tiling import (
    TileParams,
    required_buffer,
    resolve_buffer,
    run_tiled,
    split_stages,
)

ROOT = Path(__file__).resolve().parents[1]
pdal = pytest.importorskip("pdal")

from tests.cloud_helpers import make_cloud, read_points, sorted_xyz, write_las  # noqa: E402


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    path = tmp_path_factory.mktemp("tiles") / "cloud.las"
    return write_las(path, make_cloud(30_000, outliers=200))


def _run_both(cloud: Path, tmp_path: Path, options: FilterOptions, tile: TileParams, chunk: int = 4_000):
    whole = run_pipeline(build_pipeline(str(cloud), str(tmp_path / "whole.las"), options))
    tiled = run_pipeline(
        build_pipeline(str(cloud), str(tmp_path / "tiled.las"), options),
        StreamParams(chunk_size=chunk),
        tile=tile,
    )
    assert whole["success"], whole
    assert tiled["success"], tiled
    return whole, tiled


# ---- パイプラインの分解・重なり幅（PDAL を実行しない） -------------------------


def test_split_stages_separates_streamable_prefix():
    options = FilterOptions(
        height_z=HeightZParams(z_min=0.0),
        radius_outlier=RadiusOutlierParams(radius=2.0),
        duplicate=DuplicateParams(),
    )
    parts = split_stages(build_pipeline(["a.las", "b.las"], "out.las", options))
    assert parts.readers == ["a.las", "b.las"]
    assert [s["type"] for s in parts.prefix] == ["filters.expression"]
    assert parts.rest[0]["type"] == "filters.outlier"
    assert parts.writer["filename"] == "out.las"


def test_required_buffer_uses_filter_neighbourhood():
    options = FilterOptions(
        radius_outlier=RadiusOutlierParams(radius=3.0),
        ground=GroundClassifyParams(method="smrf", window=12.0),
    )
    rest = split_stages(build_pipeline("a.las", "o.las", options)).rest
    assert required_buffer(rest) == 12.0


def test_default_buffer_and_small_buffer_note():
    rest = split_stages(build_pipeline(
        "a.las", "o.las", FilterOptions(radius_outlier=RadiusOutlierParams(radius=3.0)))).rest
    assert resolve_buffer(TileParams(size=100.0), rest) == (10.0, None)
    assert resolve_buffer(TileParams(size=10.0), rest) == (3.0, None)
    buffer, note = resolve_buffer(TileParams(size=100.0, buffer=1.0), rest)
    assert buffer == 1.0 and "重なり" in note


@pytest.mark.parametrize("kwargs", [{"size": 0}, {"size": -1}, {"size": float("nan")},
                                    {"size": 10, "buffer": -1}, {"size": 10, "buffer": 10}])
def test_tile_params_rejects_bad_values(kwargs):
    with pytest.raises(ValueError):
        TileParams(**kwargs)


def test_split_stages_rejects_non_linear_pipeline():
    with pytest.raises(ValueError):
        split_stages({"pipeline": ["a.las", {"type": "filters.merge", "tag": "m"},
                                   {"type": "writers.las", "filename": "o.las"}]})


# ---- 実行：タイルなしと同じ結果になること ------------------------------------


def test_radius_outlier_tiled_equals_whole(cloud, tmp_path):
    options = FilterOptions(radius_outlier=RadiusOutlierParams(radius=1.5, min_k=3))
    whole, tiled = _run_both(cloud, tmp_path, options, TileParams(size=30.0, buffer=3.0))
    assert tiled["mode"] == "tile" and tiled["tiles"] == 16
    assert tiled["points_processed"] == whole["points_processed"]
    a, b = sorted_xyz(read_points(tmp_path / "whole.las")), sorted_xyz(read_points(tmp_path / "tiled.las"))
    assert a.shape == b.shape
    assert abs(a - b).max() <= 0.001
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".gqfilter-tiles-")]


def test_duplicates_on_tile_edges_removed_once(tmp_path):
    arr = make_cloud(2_000, origin=(0.0, 0.0, 0.0), extent=20.0)
    edge = arr[:40].copy()
    edge["X"] = 10.0  # タイル境界ちょうどの点（size=10 → x=10 は右のタイル）
    edge["Y"] = np.linspace(0.5, 19.5, 40)
    corner = arr[:5].copy()
    corner["X"], corner["Y"] = 10.0, 10.0
    cloud = write_las(tmp_path / "edge.las", np.concatenate([arr, edge, edge, corner, corner]))
    options = FilterOptions(duplicate=DuplicateParams())
    whole, tiled = _run_both(cloud, tmp_path, options, TileParams(size=10.0, buffer=1.0), chunk=500)
    assert tiled["points_processed"] == whole["points_processed"]
    np.testing.assert_allclose(
        sorted_xyz(read_points(tmp_path / "whole.las")),
        sorted_xyz(read_points(tmp_path / "tiled.las")), atol=0.001)


def test_streamable_prefix_runs_before_split(cloud, tmp_path):
    options = FilterOptions(
        height_z=HeightZParams(z_max=51.0),
        radius_sample=RadiusSampleParams(radius=0.5),
        radius_outlier=RadiusOutlierParams(radius=2.0, min_k=2),
    )
    whole, tiled = _run_both(cloud, tmp_path, options, TileParams(size=50.0, buffer=4.0))
    assert tiled["points_read"] < 30_200  # 外れ値（Z>55）は分割前に落ちる
    assert tiled["points_processed"] == whole["points_processed"]


def test_crs_and_point_format_are_kept(tmp_path):
    arr = make_cloud(3_000)
    src = tmp_path / "crs.las"
    import json
    writer = {"type": "writers.las", "filename": str(src), "a_srs": "EPSG:6677",
              "dataformat_id": 1, "scale_x": 0.001, "scale_y": 0.001, "scale_z": 0.001,
              "offset_x": "auto", "offset_y": "auto", "offset_z": "auto"}
    pdal.Pipeline(json.dumps([writer]), arrays=[arr]).execute()
    out = tmp_path / "out.las"
    result = run_tiled(
        build_pipeline(str(src), str(out), FilterOptions(radius_outlier=RadiusOutlierParams())),
        TileParams(size=40.0),
    )
    assert result["success"], result
    info = pdal.Pipeline(json.dumps([str(out)])).quickinfo["readers.las"]
    assert "6677" in json.dumps(info["srs"]["json"])
    assert info["metadata"]["dataformat_id"] == 1


def test_tiled_without_blocking_filters_streams_directly(cloud, tmp_path):
    result = run_tiled(
        build_pipeline(str(cloud), str(tmp_path / "o.las"), FilterOptions(height_z=HeightZParams(z_min=0))),
        TileParams(size=10.0),
    )
    assert result["success"] and result["mode"] == "stream"
    assert "タイル分割せず" in result["note"]


def test_tile_failure_reports_error(cloud, tmp_path):
    pipeline = build_pipeline(str(cloud), str(tmp_path / "o.las"), FilterOptions(duplicate=DuplicateParams()))
    pipeline["pipeline"].insert(-1, {"type": "filters.expression", "expression": "NoSuchDim > 0"})
    result = run_tiled(pipeline, TileParams(size=50.0))
    assert result["success"] is False
    assert "タイル" in result["error"]
    assert not (tmp_path / "o.las").exists()


def test_cli_tile(cloud, tmp_path):
    out = tmp_path / "cli.las"
    r = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "-i", str(cloud), "-o", str(out),
         "--outlier-radius", "--outlier-radius-value", "1.5", "--tile", "25"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "タイル" in r.stdout
    assert out.exists()


def test_main_py_tile_and_stream_options(cloud, tmp_path):
    out = tmp_path / "main.las"
    r = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "-i", str(cloud), "-o", str(out),
         "--outlier-radius", "--tile", "40", "--chunk-size", "5000"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert out.exists()
