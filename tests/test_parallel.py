"""並列処理（複数ファイル・タイル）のテスト。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gq_filter.filter import FilterOptions, HeightZParams, RadiusOutlierParams, build_pipeline
from gq_filter.parallel import (
    FileJob,
    batch_outputs,
    collect_inputs,
    process_map,
    resolve_jobs,
    run_files,
)
from gq_filter.stream import StreamParams, run_pipeline
from gq_filter.tiling import TileParams

ROOT = Path(__file__).resolve().parents[1]


def _square(x: int) -> int:
    return x * x


def _pid(_: int) -> int:
    return os.getpid()


# ---- PDAL 不要 ------------------------------------------------------------------


def test_resolve_jobs_defaults_to_cpu_count():
    assert resolve_jobs(None) == (os.cpu_count() or 1)
    assert resolve_jobs(3) == 3


@pytest.mark.parametrize("bad", [0, -2])
def test_resolve_jobs_rejects_non_positive(bad):
    with pytest.raises(ValueError):
        resolve_jobs(bad)


def test_process_map_keeps_order_and_uses_processes():
    assert process_map(_square, list(range(10)), jobs=3) == [x * x for x in range(10)]
    assert process_map(_square, [4], jobs=1) == [16]
    pids = set(process_map(_pid, list(range(8)), jobs=2))
    assert os.getpid() not in pids


def test_collect_inputs_expands_directories(tmp_path):
    for name in ("b.las", "a.LAZ", "c.e57", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    single = tmp_path / "x.las"
    single.write_bytes(b"")
    got = collect_inputs([str(tmp_path), str(single)])
    assert [os.path.basename(p) for p in got] == ["a.LAZ", "b.las", "c.e57", "x.las"]


def test_collect_inputs_rejects_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_inputs([str(tmp_path / "nope.las")])


def test_batch_outputs_maps_names_and_rejects_collisions(tmp_path):
    jobs = batch_outputs(["/d/a.las", "/d/b.e57"], str(tmp_path))
    assert jobs == [FileJob("/d/a.las", str(tmp_path / "a.las")), FileJob("/d/b.e57", str(tmp_path / "b.las"))]
    with pytest.raises(ValueError):
        batch_outputs(["/d1/a.las", "/d2/a.las"], str(tmp_path))


# ---- PDAL で実行 ---------------------------------------------------------------


@pytest.fixture(scope="module")
def clouds(tmp_path_factory):
    pytest.importorskip("pdal")
    from tests.cloud_helpers import make_cloud, write_las

    base = tmp_path_factory.mktemp("parallel")
    return [write_las(base / f"c{i}.las", make_cloud(5_000, seed=i, outliers=20)) for i in range(4)]


def test_run_files_parallel_matches_serial(clouds, tmp_path):
    options = FilterOptions(radius_outlier=RadiusOutlierParams(radius=2.0, min_k=2))
    serial = run_files(batch_outputs([str(c) for c in clouds], str(tmp_path / "s")), options, jobs=1)
    par = run_files(batch_outputs([str(c) for c in clouds], str(tmp_path / "p")), options, jobs=4)
    assert [r["success"] for r in par] == [True] * 4
    assert [r["points_processed"] for r in par] == [r["points_processed"] for r in serial]
    assert [os.path.basename(r["input"]) for r in par] == ["c0.las", "c1.las", "c2.las", "c3.las"]


def test_run_files_reports_each_failure(clouds, tmp_path):
    jobs = batch_outputs([str(clouds[0]), str(tmp_path / "broken.las")], str(tmp_path / "o"))
    (tmp_path / "broken.las").write_bytes(b"not a las file")
    results = run_files(jobs, FilterOptions(height_z=HeightZParams(z_min=0)), jobs=2)
    assert results[0]["success"] is True
    assert results[1]["success"] is False and results[1]["error"]


def test_tiles_in_parallel_match_serial(clouds, tmp_path):
    from tests.cloud_helpers import read_points, sorted_xyz

    options = FilterOptions(radius_outlier=RadiusOutlierParams(radius=2.0, min_k=2))
    tile = TileParams(size=25.0, buffer=4.0)
    out = {}
    for jobs in (1, 4):
        path = tmp_path / f"t{jobs}.las"
        result = run_pipeline(build_pipeline(str(clouds[0]), str(path), options), StreamParams(), tile, jobs=jobs)
        assert result["success"] and result["tiles"] == 16, result
        out[jobs] = sorted_xyz(read_points(path))
    assert (out[1] == out[4]).all()


def test_cli_batch_with_jobs(clouds, tmp_path):
    out_dir = tmp_path / "cli"
    r = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "-i", *map(str, clouds[:3]), "-o", str(out_dir),
         "--z-min", "0", "--jobs", "2"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr
    assert sorted(os.listdir(out_dir)) == ["c0.las", "c1.las", "c2.las"]
    assert "2 並列" in r.stdout


def test_cli_batch_exits_nonzero_when_a_file_fails(clouds, tmp_path):
    bad = tmp_path / "bad.las"
    bad.write_bytes(b"junk")
    r = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "-i", str(clouds[0]), str(bad), "-o", str(tmp_path / "o"),
         "--jobs", "2"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 1
    assert "bad.las" in r.stderr
    assert (tmp_path / "o" / "c0.las").exists()


def _fail_on_three(x: int) -> dict:
    import time

    time.sleep(0.05)
    return {"success": x != 3, "x": x}


def test_process_map_stops_scheduling_after_failure():
    from gq_filter.parallel import stop_on_failure

    results = process_map(_fail_on_three, list(range(40)), jobs=2, stop=stop_on_failure)
    assert any(not r["success"] for r in results)
    assert len(results) < 40
    assert [r["x"] for r in results] == sorted(r["x"] for r in results)
    serial = process_map(_fail_on_three, list(range(40)), jobs=1, stop=stop_on_failure)
    assert [r["x"] for r in serial] == [0, 1, 2, 3]


def _die_on_two(x: int) -> dict:
    if x == 2:
        os._exit(3)  # メモリ不足で落ちたワーカーの代わり
    return {"success": True, "x": x}


def _as_failure(x: int, exc: BaseException) -> dict:
    return {"success": False, "x": x, "error": repr(exc)}


def test_process_map_turns_crashed_worker_into_failure():
    results = process_map(_die_on_two, [0, 1, 2, 3], jobs=2, on_error=_as_failure)
    by_x = {r["x"]: r for r in results}
    assert by_x[2]["success"] is False
    assert len(results) == 4


def test_batch_outputs_refuses_to_overwrite_inputs(tmp_path):
    src = tmp_path / "a.las"
    src.write_bytes(b"")
    with pytest.raises(ValueError, match="overwrite"):
        batch_outputs([str(src)], str(tmp_path))


def test_cli_batch_rejects_file_like_output(clouds, tmp_path):
    r = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "-i", str(clouds[0]), str(clouds[1]), "-o", str(tmp_path / "m.las")],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 1
    assert "directory" in r.stderr
