"""CPU コアを使い切る並列処理。

PDAL は 1 本のパイプラインを 1 スレッドで実行する。そこで、互いに独立した処理を
別々のプロセスで同時に走らせる。

- 複数の入力ファイル: ファイルごとにパイプラインを作り、``jobs`` 本ずつ同時に実行する
- タイル分割（``gq_filter.tiling``）: タイルの処理を ``jobs`` 本ずつ同時に実行する

スレッドではなくプロセスを使う（PDAL の Python バインディングと numpy の処理が GIL を
手放す保証がないため）。開始方法は OS によらず ``spawn`` にそろえる。

メモリ使用量はおおむね「1 本分 × jobs」になる。大きなファイルを並列にするときは
``jobs`` を減らすか、ストリーム読込・タイル分割と組み合わせる。
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, TypeVar

from gq_filter.filter import FilterOptions, build_pipeline
from gq_filter.stream import StreamParams, run_pipeline

T = TypeVar("T")
R = TypeVar("R")

INPUT_EXTENSIONS = (".las", ".laz", ".e57")


def resolve_jobs(jobs: Optional[int]) -> int:
    """並列数。None なら CPU コア数。"""
    if jobs is None:
        return os.cpu_count() or 1
    if jobs < 1:
        raise ValueError(f"jobs must be >= 1 (got {jobs})")
    return jobs


def process_map(
        fn: Callable[[T], R],
        items: Sequence[T],
        jobs: Optional[int] = None,
        stop: Optional[Callable[[R], bool]] = None,
) -> List[R]:
    """``fn`` を items に別プロセスで並列に適用し、items の順に結果を返す。

    ``fn`` と items は pickle できる必要がある（モジュールの最上位で定義した関数）。
    jobs=1 か items が 1 つなら、このプロセスでそのまま実行する。
    ``stop(result)`` が真になったら、まだ始まっていない items は実行しない（返り値は
    終わった分だけ、items の順）。
    """
    n = min(resolve_jobs(jobs), len(items))
    if n <= 1:
        results: List[R] = []
        for item in items:
            results.append(fn(item))
            if stop is not None and stop(results[-1]):
                break
        return results
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=n, mp_context=context) as pool:
        futures = [pool.submit(fn, item) for item in items]
        if stop is not None:
            for future in as_completed(futures):
                if stop(future.result()):
                    for other in futures:
                        other.cancel()
                    break
        return [f.result() for f in futures if not f.cancelled()]


def stop_on_failure(result: Dict[str, Any]) -> bool:
    """process_map の stop 用: 結果の dict が失敗なら止める。"""
    return not result.get("success", False)


# -------------------------------
# 複数ファイル
# -------------------------------

@dataclass(frozen=True)
class FileJob:
    input: str
    output: str


def collect_inputs(paths: Iterable[str]) -> List[str]:
    """入力パスの一覧。ディレクトリは直下の .las / .laz / .e57 に展開する（名前順、重複なし）。"""
    found: List[str] = []
    for path in paths:
        if os.path.isdir(path):
            names = sorted(
                n for n in os.listdir(path)
                if n.lower().endswith(INPUT_EXTENSIONS) and os.path.isfile(os.path.join(path, n))
            )
            found.extend(os.path.join(path, n) for n in names)
        elif os.path.isfile(path):
            found.append(path)
        else:
            raise FileNotFoundError(f"input not found: {path}")
    unique: Dict[str, str] = {}
    for path in found:
        unique.setdefault(os.path.realpath(path), path)
    return list(unique.values())


def batch_outputs(inputs: Sequence[str], output_dir: str, ext: str = ".las") -> List[FileJob]:
    """入力ごとの出力先（output_dir/<入力名>。E57 は ext に変える）。同名の出力は拒否する。"""
    jobs: List[FileJob] = []
    seen: Dict[str, str] = {}
    for path in inputs:
        base, in_ext = os.path.splitext(os.path.basename(path))
        out_name = base + (ext if in_ext.lower() == ".e57" else in_ext)
        if out_name in seen:
            raise ValueError(f"two inputs would write the same output {out_name}: {seen[out_name]}, {path}")
        seen[out_name] = path
        jobs.append(FileJob(path, os.path.join(output_dir, out_name)))
    return jobs


@dataclass(frozen=True)
class _FileTask:
    job: FileJob
    options: FilterOptions
    stream: Optional[StreamParams]
    tile: Optional[Any]


def _run_file(task: _FileTask) -> Dict[str, Any]:
    """1 ファイル分（別プロセスで実行される）。例外も結果の dict にして返す。"""
    try:
        out_dir = os.path.dirname(os.path.abspath(task.job.output))
        os.makedirs(out_dir, exist_ok=True)
        pipeline = build_pipeline(task.job.input, task.job.output, task.options)
        result = run_pipeline(pipeline, task.stream, task.tile)
    except Exception as exc:  # 1 ファイルの失敗で他のファイルを止めない
        result = {"success": False, "error": str(exc), "log": ""}
    result.pop("metadata", None)  # プロセス間で大きな dict を送らない
    return {**result, "input": task.job.input, "output": task.job.output}


def run_files(
        jobs_list: Sequence[FileJob],
        options: FilterOptions,
        *,
        stream: Optional[StreamParams] = None,
        tile: Optional[Any] = None,
        jobs: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """複数のファイルをそれぞれ処理する（ファイル単位で並列）。結果は jobs_list の順。

    各結果は run_pipeline の結果に ``input`` / ``output`` を足したもの。1 つが失敗しても
    残りは処理する。ファイルを並列にするときタイルは各プロセスの中で順に処理する。
    """
    tasks = [_FileTask(job, options, stream, tile) for job in jobs_list]
    return process_map(_run_file, tasks, jobs)


# -------------------------------
# CLI helpers (shared by main.py and cli.py)
# -------------------------------

def add_jobs_arg(parser: argparse.ArgumentParser, *aliases: str) -> None:
    group = parser.add_argument_group("Parallel Processing (並列処理)")
    group.add_argument(
        "--jobs", "-j", *aliases,
        dest="jobs",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Number of processes used for several input files or for tiles "
            "(default: number of CPU cores). Memory use grows with N."
        ),
    )
