"""E57 の読み込み（構造化・非構造化・球座標・複数スキャン・器械点）のテスト。"""
from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

pye57 = pytest.importorskip("pye57")
pdal = pytest.importorskip("pdal")

from gq_filter.e57 import (  # noqa: E402
    expand_e57,
    iter_scan_points,
    pye57_reasons,
    read_scans,
    shared_origin,
)
from gq_filter.filter import (  # noqa: E402
    FilterOptions,
    IncidenceAngleParams,
    IntensityParams,
    RangeParams,
    StatisticalOutlierParams,
    build_pipeline,
    get_origin_auto,
)
from gq_filter.runner import needs_scanner_origin, plan_e57, run_inputs  # noqa: E402
from gq_filter.stream import StreamParams  # noqa: E402
from gq_filter.tiling import TileParams  # noqa: E402
from tests.cloud_helpers import make_cloud, read_points, write_las  # noqa: E402
from tests.e57_helpers import Scan, assert_same_points, quat_z, ring_scan, write_e57  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _structured(**kw) -> Scan:
    return Scan(**ring_scan(12, 30, seed=1, invalid_every=7), **kw)


def _xyz(pts) -> np.ndarray:
    return np.column_stack([pts["X"], pts["Y"], pts["Z"]])


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("e57")
    scans = {
        "structured": _structured(translation=(100.0, 0.0, 0.0), rotation=quat_z(30)),
        "spherical": Scan(**ring_scan(10, 24, seed=2, invalid_every=11), translation=(0.0, 200.0, 5.0),
                          rotation=quat_z(90), spherical=True),
        "unstructured": Scan(local=np.random.default_rng(3).normal(0, 2, (400, 3)), translation=(10.0, 10.0, 10.0),
                             intensity=np.r_[0.0, 1.0, np.random.default_rng(4).random(398)].astype("f4")),
        # 強度の範囲が 0〜1 でない（最小値 > 0）スキャン
        "dim": Scan(local=np.random.default_rng(6).normal(0, 2, (300, 3)), translation=(-5.0, 0.0, 0.0),
                    intensity=(0.2 + 0.6 * np.random.default_rng(7).random(300)).astype("f4")),
        "same_origin_b": Scan(**ring_scan(6, 20, seed=5), translation=(100.0, 0.0, 0.0)),
    }
    out = {"scans": scans, "dir": d}
    out["multi"] = write_e57(d / "multi.e57", [scans["structured"], scans["unstructured"]])
    out["mixed"] = write_e57(d / "mixed.e57", [scans["structured"], scans["spherical"]])
    out["one_origin"] = write_e57(d / "one.e57", [scans["structured"], scans["same_origin_b"]])
    out["spherical"] = write_e57(d / "sph.e57", [scans["spherical"]])
    out["unstructured"] = write_e57(d / "uns.e57", [scans["unstructured"]])
    out["dim"] = write_e57(d / "dim.e57", [scans["dim"]])
    return out


def _expected_within(scan: Scan, max_distance: float) -> int:
    local = scan.local if scan.invalid is None else scan.local[~scan.invalid]
    return int((np.einsum("ij,ij->i", local, local) <= max_distance * max_distance).sum())


# ---- スキャンの情報 ------------------------------------------------------------


def test_read_scans_reports_structure_pose_and_origin(files):
    scans = read_scans(str(files["mixed"]))
    assert [s.coordinates for s in scans] == ["cartesian", "spherical"]
    first = scans[0]
    assert first.structured and (first.rows, first.columns) == (12, 30)
    assert first.has_invalid and first.has_intensity and first.has_color
    assert first.origin == (100.0, 0.0, 0.0)
    assert np.allclose(first.rotation, quat_z(30))
    uns = read_scans(str(files["unstructured"]))[0]
    assert not uns.structured and uns.rows is None and not uns.has_invalid


def test_shared_origin(files):
    assert shared_origin(read_scans(str(files["one_origin"]))) == (100.0, 0.0, 0.0)
    assert shared_origin(read_scans(str(files["multi"]))) is None


def test_reasons_for_pye57(files):
    multi = read_scans(str(files["multi"]))
    assert pye57_reasons(multi, needs_origin=True)
    assert pye57_reasons(multi, needs_origin=False) == []
    assert any("球座標" in r for r in pye57_reasons(read_scans(str(files["mixed"])), needs_origin=False))
    assert pye57_reasons(read_scans(str(files["one_origin"])), needs_origin=True) == []
    assert any("強度" in r for r in pye57_reasons(read_scans(str(files["dim"])), needs_origin=False))


# ---- 点の読み込み ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["structured", "spherical", "unstructured"])
def test_points_have_pose_applied_and_no_invalid(files, tmp_path, name):
    scan = files["scans"][name]
    path = write_e57(tmp_path / f"{name}.e57", [scan])
    chunks = list(iter_scan_points(str(path), 0, chunk_size=97))
    assert len(chunks) > 1
    pts = np.concatenate(chunks)
    assert_same_points(_xyz(pts), scan.global_xyz(), atol=1e-4)
    assert (pts["PointSourceId"] == 0).all()


def test_color_scaling_and_intensity_0_to_1(files):
    scan = files["scans"]["structured"]
    pts = np.concatenate(list(iter_scan_points(str(files["multi"]), 0)))
    valid = ~scan.invalid
    got = assert_same_points(_xyz(pts), scan.global_xyz(), atol=1e-4)
    np.testing.assert_allclose(pts["Intensity"][got], np.rint(scan.intensity[valid] * 65535), atol=1)
    np.testing.assert_array_equal(pts["Red"][got], scan.rgb[valid][:, 0] * 257)


def test_intensity_uses_limits_minimum(files):
    """強度の最小値が 0 でないとき、(値 - 最小) / (最大 - 最小) で 0〜65535 に換算する。"""
    scan = files["scans"]["dim"]
    pts = np.concatenate(list(iter_scan_points(str(files["dim"]), 0)))
    lo, hi = float(scan.intensity.min()), float(scan.intensity.max())
    assert lo > 0
    got = assert_same_points(_xyz(pts), scan.global_xyz(), atol=1e-4)
    expected = np.rint((scan.intensity.astype("f8") - lo) / (hi - lo) * 65535)
    np.testing.assert_allclose(pts["Intensity"][got], expected, atol=1)
    assert pts["Intensity"].min() == 0 and pts["Intensity"].max() == 65535


def test_expand_applies_range_per_scan(files, tmp_path):
    scans = expand_e57(str(files["multi"]), str(tmp_path), max_distance=5.0)
    assert [s.origin for s in scans] == [(100.0, 0.0, 0.0), (10.0, 10.0, 10.0)]
    for expanded, scan in zip(scans, [files["scans"]["structured"], files["scans"]["unstructured"]]):
        assert expanded.points_written == _expected_within(scan, 5.0)
        assert sum(len(read_points(Path(f))) for f in expanded.files) == expanded.points_written


# ---- build_pipeline（PDAL の readers.e57） ------------------------------------


def test_build_pipeline_drops_invalid_points(files, tmp_path):
    pipeline = build_pipeline(str(files["spherical"]), str(tmp_path / "o.las"), FilterOptions())
    assert {"type": "filters.expression", "expression": "Omit == 0"} in pipeline["pipeline"]
    pl = pdal.Pipeline(__import__("json").dumps(pipeline))
    assert pl.execute() == len(files["scans"]["spherical"].global_xyz())


def test_build_pipeline_no_invalid_filter_without_invalid_state(files, tmp_path):
    pipeline = build_pipeline(str(files["unstructured"]), str(tmp_path / "o.las"), FilterOptions())
    assert not any(isinstance(s, dict) and s.get("expression") == "Omit == 0" for s in pipeline["pipeline"])


def test_origin_auto_warns_for_different_scan_origins(files):
    with pytest.warns(UserWarning, match="器械点の違うスキャン"):
        assert get_origin_auto(str(files["multi"])) == (100.0, 0.0, 0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert get_origin_auto(str(files["one_origin"])) == (100.0, 0.0, 0.0)


# ---- 実行（runner） ------------------------------------------------------------


def test_needs_scanner_origin():
    assert needs_scanner_origin(FilterOptions(range_dist=RangeParams(max_distance=5)))
    assert needs_scanner_origin(FilterOptions(incidence=IncidenceAngleParams(max_angle=80)))
    assert not needs_scanner_origin(FilterOptions(range_dist=RangeParams(max_distance=5, manual_origin=(0, 0, 0))))
    assert not needs_scanner_origin(FilterOptions(intensity=IntensityParams(min_intensity=1)))


def test_plan_e57(files):
    rng = FilterOptions(range_dist=RangeParams(max_distance=5))
    assert plan_e57(str(files["multi"]), rng)[0] is True
    assert plan_e57(str(files["multi"]), FilterOptions())[0] is False
    assert plan_e57(str(files["multi"]), rng, "pdal")[0] is False
    assert plan_e57(str(files["one_origin"]), rng)[0] is False
    assert plan_e57(str(files["mixed"]), FilterOptions())[0] is True


def test_range_uses_each_scans_origin(files, tmp_path):
    out = tmp_path / "range.las"
    options = FilterOptions(range_dist=RangeParams(max_distance=5.0))
    result = run_inputs(str(files["multi"]), str(out), options)
    assert result["success"], result
    assert result["e57"]["reader"] == "pye57" and result["e57"]["scans"] == 2
    expected = _expected_within(files["scans"]["structured"], 5.0) + _expected_within(files["scans"]["unstructured"], 5.0)
    assert result["points_processed"] == expected == len(read_points(out))
    # 先頭スキャンの器械点だけを使う PDAL の読み込みでは 2 つ目のスキャンの点がすべて落ちる
    with pytest.warns(UserWarning):
        pdal_only = run_inputs(str(files["multi"]), str(tmp_path / "pdal.las"), options, e57_reader="pdal")
    assert pdal_only["points_processed"] == _expected_within(files["scans"]["structured"], 5.0)


def test_mixed_coordinate_systems_are_read_correctly(files, tmp_path):
    out = tmp_path / "mixed.las"
    result = run_inputs(str(files["mixed"]), str(out), FilterOptions())
    assert result["success"] and result["e57"]["reader"] == "pye57", result
    expected = np.vstack([files["scans"]["structured"].global_xyz(), files["scans"]["spherical"].global_xyz()])
    assert_same_points(_xyz(read_points(out)), expected, atol=0.001)


def test_incidence_per_scan_and_rest_on_merged(files, tmp_path):
    out = tmp_path / "inc.las"
    options = FilterOptions(
        range_dist=RangeParams(max_distance=7.0),
        incidence=IncidenceAngleParams(max_angle=89.0),
        statistical_outlier=StatisticalOutlierParams(mean_k=4, multiplier=3.0),
    )
    result = run_inputs(str(files["multi"]), str(out), options, jobs=2)
    assert result["success"], result
    assert 0 < result["points_processed"] <= _expected_within(files["scans"]["structured"], 7.0) \
        + _expected_within(files["scans"]["unstructured"], 7.0)


def test_e57_with_stream_and_tile(files, tmp_path):
    options = FilterOptions(range_dist=RangeParams(max_distance=5.0))
    s = run_inputs(str(files["multi"]), str(tmp_path / "s.las"), options, stream=StreamParams(mode="on"))
    t = run_inputs(str(files["multi"]), str(tmp_path / "t.las"),
                   FilterOptions(range_dist=RangeParams(max_distance=5.0),
                                 statistical_outlier=StatisticalOutlierParams()),
                   tile=TileParams(size=50.0))
    assert s["success"] and s["mode"] == "stream", s
    assert t["success"] and t["mode"] == "tile", t


def test_mixed_las_and_e57_inputs(files, tmp_path):
    las = write_las(files["dir"] / "near.las", make_cloud(500, origin=(98.0, -2.0, -1.0), extent=4.0))
    out = tmp_path / "both.las"
    result = run_inputs([str(files["multi"]), str(las)], str(out),
                        FilterOptions(range_dist=RangeParams(max_distance=5.0)))
    assert result["success"], result
    # LAS には器械点がないので原点 (0,0,0) からの距離になり、全点が落ちる
    assert result["points_processed"] == _expected_within(files["scans"]["structured"], 5.0) \
        + _expected_within(files["scans"]["unstructured"], 5.0)


# ---- CLI -----------------------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=180)


def test_cli_e57_info(files):
    r = _cli("-i", str(files["mixed"]), "--e57-info")
    assert r.returncode == 0, r.stderr
    assert "構造化 12×30" in r.stdout and "spherical" in r.stdout and "スキャンごとに違う" in r.stdout


def test_cli_range_per_scan(files, tmp_path):
    out = tmp_path / "cli.las"
    r = _cli("-i", str(files["multi"]), "-o", str(out), "--range-max", "5")
    assert r.returncode == 0, r.stderr
    assert "スキャンごと" in r.stdout
    expected = _expected_within(files["scans"]["structured"], 5.0) + _expected_within(files["scans"]["unstructured"], 5.0)
    assert len(read_points(out)) == expected


def test_cli_dry_run_shows_e57_plan(files, tmp_path):
    r = _cli("-i", str(files["multi"]), "-o", str(tmp_path / "x.las"), "--range-max", "5", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "pye57 でスキャンごとに読み込みます" in r.stdout


def test_scan_without_pose_in_large_coordinates(tmp_path):
    """pose なしで全体座標（大きな値）が記録されたスキャンでも LAS に書ける。"""
    local = np.random.default_rng(8).random((300, 3)) * 10 + np.array([500_000.0, 4_000_000.0, 30.0])
    path = write_e57(tmp_path / "big_coords.e57", [Scan(local=local), Scan(local=local + 1.0)])
    out = tmp_path / "o.las"
    result = run_inputs(str(path), str(out), FilterOptions(), e57_reader="pye57")
    assert result["success"], result
    # pye57 は座標を float32 で書くので、期待値も float32 に丸める
    expected = np.vstack([local, local + 1.0]).astype("f4").astype("f8")
    assert_same_points(_xyz(read_points(out)), expected, atol=0.001)


def test_disabled_range_is_not_applied(files, tmp_path):
    options = FilterOptions(range_dist=RangeParams(max_distance=1.0, enabled=False),
                            incidence=IncidenceAngleParams(min_angle=0.0, max_angle=90.0))
    result = run_inputs(str(files["multi"]), str(tmp_path / "o.las"), options)
    assert result["success"], result
    total = len(files["scans"]["structured"].global_xyz()) + len(files["scans"]["unstructured"].global_xyz())
    assert result["points_processed"] > _expected_within(files["scans"]["structured"], 1.0) \
        + _expected_within(files["scans"]["unstructured"], 1.0)
    assert result["points_processed"] <= total
