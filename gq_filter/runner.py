"""入力ファイル（LAS / LAZ / E57）から出力 1 つまでを実行する。

通常は ``build_pipeline`` → ``run_pipeline`` と同じ。次のときだけ入力を分けて処理する。

- E57 を pye57 で読む必要があるとき（``gq_filter.e57.pye57_reasons``）: スキャンごとに
  LAS に書き出してから処理する
- 器械点からの距離（``--range-*``）・入射角（``--incidence-*``）を器械点の自動取得で使い、
  入力が複数あるか、E57 のスキャンごとに器械点が違うとき: 入力（スキャン）ごとに
  その器械点で距離・入射角を絞り（段階 1）、まとめて残りのフィルターをかける（段階 2）

段階 1 の距離は E57 の書き出しの中で、入射角は入力（スキャン）ごとの PDAL パイプラインで
かける。段階 2 は ``run_pipeline`` なので、ストリーム読込・タイル分割・並列処理がそのまま使える。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from gq_filter.e57 import (
    Origin,
    Pye57Missing,
    expand_e57,
    is_e57,
    pye57_reasons,
    read_scans,
)
from gq_filter.filter import FilterOptions, RangeParams, build_pipeline
from gq_filter.stream import StreamParams, run_pipeline

# 段階 1 でたくさんの LAS を同時に開くので、開けるファイル数の上限を上げる
_WANTED_OPEN_FILES = 4096


def needs_scanner_origin(options: FilterOptions) -> bool:
    """距離・入射角のフィルターを、器械点の自動取得で使うか。"""
    rd = options.range_dist
    uses_range = rd is not None and rd.enabled and (rd.min_distance is not None or rd.max_distance is not None)
    uses_incidence = options.incidence is not None and options.incidence.enabled
    manual = rd is not None and rd.manual_origin is not None
    return (uses_range or uses_incidence) and not manual


def plan_e57(path: str, options: FilterOptions, reader: str = "auto") -> Tuple[bool, List[str]]:
    """この入力を pye57 で読むか、とその理由。"""
    if not is_e57(path) or reader == "pdal":
        return False, []
    try:
        scans = read_scans(path)
    except Pye57Missing:
        if reader == "pye57":
            raise
        return False, []
    reasons = pye57_reasons(scans, needs_scanner_origin(options))
    if reader == "pye57":
        return True, reasons or ["--e57-reader pye57 を指定"]
    return bool(reasons), reasons


@dataclass(frozen=True)
class _Source:
    """段階 1 の単位: 1 つの入力ファイル、または E57 の 1 スキャン。"""
    files: Tuple[str, ...]
    origin: Optional[Origin]  # None: build_pipeline に器械点を任せる（LAS は原点）
    range_done: bool          # 距離フィルターをかけ終えた（E57 の書き出しの中で）
    label: str


@dataclass(frozen=True)
class _Stage1Task:
    source: _Source
    output: str
    options: FilterOptions
    stream: Optional[StreamParams]
    tile: Optional[Any]


def _stage1_options(source: _Source, options: FilterOptions) -> Optional[FilterOptions]:
    """段階 1 のフィルター（距離・入射角）。かけるものがなければ None。"""
    rd = options.range_dist
    if source.origin is not None:
        # 器械点は RangeParams.manual_origin で渡す（入射角も同じ器械点を使う）
        rng = RangeParams(
            min_distance=None if source.range_done or rd is None else rd.min_distance,
            max_distance=None if source.range_done or rd is None else rd.max_distance,
            manual_origin=source.origin,
        )
    else:
        rng = rd
    stage1 = FilterOptions(range_dist=rng, incidence=options.incidence)
    has_range = rng is not None and (rng.min_distance is not None or rng.max_distance is not None)
    if not has_range and stage1.incidence is None:
        return None
    return stage1


def _run_stage1(task: _Stage1Task) -> Dict[str, Any]:
    """入力（スキャン）1 つ分の段階 1（別プロセスでも呼べる）。"""
    stage1 = _stage1_options(task.source, task.options)
    try:
        if stage1 is None:
            if len(task.source.files) == 1:
                return {"success": True, "files": list(task.source.files), "label": task.source.label}
            stage1 = FilterOptions()  # 書き出した複数の LAS を 1 つにまとめるだけ
        pipeline = build_pipeline(list(task.source.files), task.output, stage1)
        result = run_pipeline(pipeline, task.stream, task.tile, jobs=1)
    except Exception as exc:  # 1 入力の失敗を結果として返す
        result = {"success": False, "error": str(exc)}
    result.pop("metadata", None)
    return {**result, "files": [task.output], "label": task.source.label}


def _raise_open_file_limit() -> None:
    try:
        import resource
    except ImportError:  # Windows
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = _WANTED_OPEN_FILES if hard == resource.RLIM_INFINITY else min(hard, _WANTED_OPEN_FILES)
    if soft < wanted:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
        except (ValueError, OSError):  # 上げられなくても続ける
            pass


def run_inputs(
        inputs: Union[str, Sequence[str]],
        output: str,
        options: FilterOptions,
        *,
        stream: Optional[StreamParams] = None,
        tile: Optional[Any] = None,
        jobs: Optional[int] = 1,
        e57_reader: str = "auto",
        work_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """入力（1 つまたは複数、複数ならまとめて 1 つ）を処理して output に書く。

    結果は ``run_pipeline`` と同じ形。E57 をスキャンごとに処理したときは ``e57`` に
    ``{"reader": "pye57", "scans": スキャン数, "reasons": [...]}`` が入る。
    """
    from gq_filter.parallel import process_map, resolve_jobs

    paths = [inputs] if isinstance(inputs, str) else list(inputs)
    n_jobs = resolve_jobs(jobs)
    plans = [plan_e57(p, options, e57_reader) for p in paths]
    expand = [use for use, _ in plans]
    per_source = needs_scanner_origin(options) and (len(paths) > 1 or any(expand))
    if not any(expand) and not per_source:
        return run_pipeline(build_pipeline(paths if len(paths) > 1 else paths[0], output, options),
                            stream, tile, jobs=n_jobs)

    _raise_open_file_limit()
    base = work_dir or (tile.work_dir if tile is not None and tile.work_dir else None) \
        or os.path.dirname(os.path.abspath(output))
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".gqfilter-e57-", dir=base)
    chunk = (stream or StreamParams()).chunk_size
    try:
        sources: List[_Source] = []
        scan_count = 0
        rd = options.range_dist
        for path, use in zip(paths, expand):
            if not use:
                sources.append(_Source((path,), None, False, os.path.basename(path)))
                continue
            scans = expand_e57(
                path, tmp, chunk_size=chunk,
                min_distance=rd.min_distance if per_source and rd else None,
                max_distance=rd.max_distance if per_source and rd else None,
                jobs=n_jobs,
            )
            scan_count += len(scans)
            for scan in scans:
                if scan.files:
                    sources.append(_Source(scan.files, scan.origin, per_source,
                                           f"{os.path.basename(path)}#{scan.index}"))

        if per_source:
            tasks = [
                _Stage1Task(src, os.path.join(tmp, f"stage1_{i:05d}.las"), options, stream, tile)
                for i, src in enumerate(sources)
            ]
            stage1 = process_map(_run_stage1, tasks, n_jobs)
            failed = [r for r in stage1 if not r["success"]]
            if failed:
                return {"success": False, "log": "",
                        "error": f"{failed[0]['label']} の距離・入射角フィルターに失敗しました: {failed[0]['error']}"}
            files = [f for r in stage1 for f in r["files"]
                     if os.path.exists(f) and (r.get("points_processed", 1) or 0) > 0]
            rest = replace(options, range_dist=None, incidence=None)
        else:
            files = [f for src in sources for f in src.files]
            rest = options

        if not files:
            from gq_filter.tiling import _write_empty

            writer = build_pipeline("empty.las", output, FilterOptions())["pipeline"][-1]
            result = _write_empty(writer, [("X", "f8"), ("Y", "f8"), ("Z", "f8")])
            result["mode"] = "standard"
            result["note"] = "距離・入射角のフィルターで全点が除かれました。"
            return result
        result = run_pipeline(build_pipeline(files if len(files) > 1 else files[0], output, rest),
                              stream, tile, jobs=n_jobs)
        reasons = [r for _, rs in plans for r in rs]
        if any(expand):
            result["e57"] = {"reader": "pye57", "scans": scan_count, "reasons": reasons}
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
