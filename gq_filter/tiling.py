"""タイル分割モード — ストリーム非対応のフィルターを大容量ファイルに使うための実行方法。

1. 分割: 入力を ``chunk_size`` 点ずつストリームで読み、XY のタイル（一辺 ``size``）に
   振り分けて一時ファイルに追記する。各タイルには周囲 ``buffer`` 幅の点（重なり）も入れ、
   タイル本体の点かどうかを ``GqTileCore`` ディメンションに記録する。
   パイプライン先頭のストリーム対応フィルター（範囲・Z・強度など）はこの読み込みの中で
   全点にかける。
2. 処理: タイルごとに残りのフィルター（外れ値除去・地表分類・ボクセルなど）を通常実行し、
   タイル本体の点だけを一時 LAS に書く。重なりの点は近傍探索のためだけに使う。
3. 結合: タイルの一時 LAS をストリームで 1 つの出力にまとめる。

メモリ使用量は「1 タイル分（重なりを含む）」とチャンク 1 つ分で決まり、入力全体の大きさに
依らない。

注意（タイルなしの実行と結果が変わりうるもの）:
- 統計的外れ値除去（filters.outlier statistical）の閾値は全点の平均・標準偏差で決まるため、
  タイルごとの統計になる。
- 地表分類は窓・布の範囲がタイル＋重なりに限られる。重なりを窓サイズ以上にする。
- ボクセル間引きの格子の原点はタイルごとに決まる。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from gq_filter.stream import (
    DEFAULT_CHUNK_SIZE,
    NON_STREAMABLE_STAGES,
    _execute,
    blocking_stages,
    centered_grid,
    common_grid,
    grid_fits,
    is_reader,
)

TILE_CORE_DIM = "GqTileCore"
DEFAULT_BUFFER_RATIO = 0.1
# LAS 以外の入力の一時 LAS の座標の刻み。int32 に収まらない大きなタイルは 1 mm にする
_FINE_SCALE = 0.0001
_COARSE_SCALE = 0.001

# 近傍を使うステージ → 必要な重なり幅を決めるオプション名と PDAL の既定値
_NEIGHBOURHOOD_LENGTHS: Dict[str, Tuple[str, float]] = {
    "filters.smrf": ("window", 18.0),
    "filters.pmf": ("max_window_size", 33.0),
    "filters.voxelcentroidnearestneighbor": ("cell", 1.0),
    "filters.sample": ("radius", 1.0),
    "filters.csf": ("resolution", 1.0),
}
# 近傍を点数（k）で決めるため、重なり幅を長さで見積もれないステージ
_KNN_STAGES = ("filters.normal", "filters.outlier")

TileKey = Tuple[int, int]


@dataclass(frozen=True)
class TileParams:
    """タイル分割の設定。

    Attributes:
        size:     タイルの一辺（入力の座標単位）
        buffer:   タイルの周囲に読み込む重なりの幅。None なら
                  max(フィルターの近傍の大きさ, size × 0.1)
        work_dir: 一時ファイルを置くディレクトリ。None なら出力ファイルと同じ場所
    """
    size: float
    buffer: Optional[float] = None
    work_dir: Optional[str] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.size) or self.size <= 0:
            raise ValueError(f"tile size must be a positive number (got {self.size})")
        if self.buffer is not None:
            if not math.isfinite(self.buffer) or self.buffer < 0:
                raise ValueError(f"tile buffer must be >= 0 (got {self.buffer})")
            if self.buffer >= self.size:
                raise ValueError(
                    f"tile buffer ({self.buffer}) must be smaller than the tile size ({self.size})"
                )


# -------------------------------
# パイプラインの分解
# -------------------------------

@dataclass(frozen=True)
class PipelineParts:
    readers: List[Any]
    prefix: List[Dict[str, Any]]  # ストリーム対応で、最初の非対応ステージより前
    rest: List[Dict[str, Any]]    # 最初の非対応ステージから writer の手前まで
    writer: Dict[str, Any]


def split_stages(pipeline: Dict[str, Any]) -> PipelineParts:
    """build_pipeline が作る直線的なパイプラインを readers / 前段 / 後段 / writer に分ける。"""
    stages = list(pipeline.get("pipeline", []))
    if any(isinstance(s, dict) and ("inputs" in s or "tag" in s) for s in stages):
        raise ValueError("tiled mode supports only linear pipelines (no tag / inputs)")
    if not stages or not isinstance(stages[-1], dict) or \
            not str(stages[-1].get("type", "")).startswith("writers."):
        raise ValueError("pipeline must end with a writers.* stage")
    writer = stages[-1]
    n_readers = 0
    while n_readers < len(stages) - 1 and is_reader(stages[n_readers]):
        n_readers += 1
    if n_readers == 0:
        raise ValueError("pipeline has no reader")
    readers = stages[:n_readers]
    if any(not isinstance(s, dict) for s in stages[n_readers:-1]):
        raise ValueError("readers must come first in the pipeline")
    filters = [s for s in stages[n_readers:-1] if s.get("type") != "filters.merge"]
    first_blocking = next(
        (i for i, s in enumerate(filters) if s.get("type") in NON_STREAMABLE_STAGES), len(filters)
    )
    return PipelineParts(readers, filters[:first_blocking], filters[first_blocking:], writer)


def required_buffer(stages: List[Dict[str, Any]]) -> float:
    """近傍を使うステージから、重なりに最低限必要な幅を見積もる（分からないものは 0）。"""
    need = 0.0
    for stage in stages:
        kind = stage.get("type")
        if kind == "filters.outlier" and stage.get("method") == "radius":
            need = max(need, float(stage.get("radius", 1.0)))
        elif kind in _NEIGHBOURHOOD_LENGTHS:
            option, default = _NEIGHBOURHOOD_LENGTHS[kind]
            need = max(need, float(stage.get(option, default)))
    return need


def resolve_buffer(tile: TileParams, rest: List[Dict[str, Any]]) -> Tuple[float, Optional[str]]:
    """使う重なり幅と、指定値が小さすぎるときの注意文。"""
    need = required_buffer(rest)
    if tile.buffer is None:
        buffer = max(need, tile.size * DEFAULT_BUFFER_RATIO)
        if buffer >= tile.size:
            raise ValueError(
                f"tile size ({tile.size}) is too small for the filters: the overlap must be at "
                f"least {need}; use a tile size larger than that"
            )
        return buffer, None
    knn = [s["type"] for s in rest if s.get("type") in _KNN_STAGES
           and not (s.get("type") == "filters.outlier" and s.get("method") == "radius")]
    if tile.buffer == 0 and knn:
        return 0.0, (
            f"タイルの重なり幅が 0 です。{', '.join(dict.fromkeys(knn))} は近傍の点を使うため、"
            "タイル境界付近の結果がタイルなしの実行と変わります。"
        )
    if tile.buffer < need:
        return tile.buffer, (
            f"タイルの重なり幅 {tile.buffer} がフィルターの近傍の大きさ {need} より小さいため、"
            "タイル境界付近の結果がタイルなしの実行と変わることがあります。"
        )
    return tile.buffer, None


# -------------------------------
# 1. 分割
# -------------------------------

def _neighbour_masks(frac, size: float, buffer: float):
    """座標のタイル内位置 frac から、(-1, 0, +1) 方向の隣接タイルに入るかのマスク。"""
    import numpy as np

    return {
        -1: frac < buffer,
        0: np.ones(frac.shape, dtype=bool),
        1: frac >= size - buffer,
    }


def _append_tiles(arr, size: float, buffer: float, tmp_dir: str,
                  files: Dict[TileKey, str], core_counts: Dict[TileKey, int]) -> None:
    """チャンク 1 つをタイルの一時ファイルに振り分けて追記する。"""
    import numpy as np

    ix = np.floor(arr["X"] / size).astype(np.int64)
    iy = np.floor(arr["Y"] / size).astype(np.int64)
    mx = _neighbour_masks(arr["X"] - ix * size, size, buffer)
    my = _neighbour_masks(arr["Y"] - iy * size, size, buffer)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            mask = mx[dx] & my[dy]
            if not mask.any():
                continue
            part = arr[mask].copy()
            part[TILE_CORE_DIM] = 1 if (dx, dy) == (0, 0) else 0
            keys = np.stack([ix[mask] + dx, iy[mask] + dy], axis=1)
            uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
            order = np.argsort(inverse.reshape(-1), kind="stable")
            bounds = np.cumsum(np.bincount(inverse.reshape(-1), minlength=len(uniq)))
            start = 0
            for (tx, ty), stop in zip(uniq, bounds):
                key = (int(tx), int(ty))
                path = files.setdefault(key, os.path.join(tmp_dir, f"tile_{key[0]}_{key[1]}.bin"))
                with open(path, "ab") as fh:
                    part[order[start:stop]].tofile(fh)
                if (dx, dy) == (0, 0):
                    core_counts[key] = core_counts.get(key, 0) + int(stop - start)
                start = stop


def _with_core_dim(dtype):
    import numpy as np

    return np.dtype(dtype.descr + [(TILE_CORE_DIM, "u1")])


def split_into_tiles(source: List[Any], size: float, buffer: float, chunk_size: int, tmp_dir: str):
    """readers + 前段ステージをストリームで読み、タイルの一時ファイルへ振り分ける。

    Returns: (タイル → 一時ファイル, タイル → 本体の点数, dtype, 読んだ点数)
    """
    import numpy as np
    import pdal

    files: Dict[TileKey, str] = {}
    core_counts: Dict[TileKey, int] = {}
    dtype = None
    total = 0
    stages = list(source)
    if sum(1 for s in stages if is_reader(s)) > 1:
        n = sum(1 for s in stages if is_reader(s))
        stages = stages[:n] + [{"type": "filters.merge"}] + stages[n:]
    for chunk in pdal.Pipeline(json.dumps({"pipeline": stages})).iterator(chunk_size=chunk_size):
        if len(chunk) == 0:
            continue
        if dtype is None:
            dtype = _with_core_dim(chunk.dtype)
        widened = np.zeros(len(chunk), dtype=dtype)
        for name in chunk.dtype.names:
            widened[name] = chunk[name]
        total += len(chunk)
        _append_tiles(widened, size, buffer, tmp_dir, files, core_counts)
    return files, core_counts, dtype, total


# -------------------------------
# 2. タイルごとの処理
# -------------------------------

def _tile_writer(path: str, key: TileKey, size: float, buffer: float, header: Dict[str, Any],
                 grid: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """タイルの一時 LAS の writer。LAS 入力は入力と同じ格子で書く（座標が変わらない）。"""
    writer: Dict[str, Any] = {"type": "writers.las", "filename": path}
    if grid is not None:
        for axis in ("x", "y", "z"):
            writer[f"scale_{axis}"] = grid["scale"][axis]
            writer[f"offset_{axis}"] = grid["offset"][axis]
    else:
        half_extent = size / 2 + buffer
        scale = _FINE_SCALE if half_extent / _FINE_SCALE < 2 ** 31 - 1 else _COARSE_SCALE
        writer.update({
            "scale_x": scale, "scale_y": scale, "scale_z": scale,
            "offset_x": round((key[0] + 0.5) * size), "offset_y": round((key[1] + 0.5) * size),
            "offset_z": header.get("offset_z", 0),
        })
    for name in ("dataformat_id", "minor_version", "a_srs"):
        if header.get(name) not in (None, ""):
            writer[name] = header[name]
    return writer


def process_tile(task: Dict[str, Any]) -> Dict[str, Any]:
    """タイル 1 つを処理する（別プロセスでも呼べるよう、引数は pickle できる dict）。"""
    import numpy as np
    import pdal

    stages = [
        *task["rest"],
        {"type": "filters.expression", "expression": f"{TILE_CORE_DIM} == 1"},
        task["writer"],
    ]
    try:
        arr = np.fromfile(task["input"], dtype=np.dtype([tuple(d) for d in task["dtype"]]))
        count = pdal.Pipeline(json.dumps(stages), arrays=[arr]).execute()
    except Exception as exc:  # PDAL は RuntimeError などで失敗を返す
        return {"key": task["key"], "success": False, "error": str(exc)}
    finally:
        if os.path.exists(task["input"]):
            os.remove(task["input"])
    return {"key": task["key"], "success": True, "points": count, "output": task["writer"]["filename"]}


def _serial_map(fn: Callable[[Dict[str, Any]], Dict[str, Any]], tasks: List[Dict[str, Any]]):
    """順に処理し、最初の失敗で止める。"""
    results = []
    for task in tasks:
        results.append(fn(task))
        if not results[-1]["success"]:
            break
    return results


# -------------------------------
# 入力の情報
# -------------------------------

def _source_header(readers: List[Any]) -> Dict[str, Any]:
    """一時 LAS とその後の出力に引き継ぐヘッダー情報（SRS・LAS の点形式）。"""
    import pdal

    first = readers[0]
    stage = {"filename": first} if isinstance(first, str) else dict(first)
    header: Dict[str, Any] = {}
    try:
        info = pdal.Pipeline(json.dumps({"pipeline": [stage]})).quickinfo
    except Exception:  # 情報が取れなくても処理は続けられる
        info = {}
    reader_info = next(iter(info.values()), {}) if info else {}
    srs = reader_info.get("srs") or {}
    header["a_srs"] = stage.get("override_srs") or (srs.get("wkt") if isinstance(srs, dict) else "") \
        or stage.get("default_srs") or ""
    md = reader_info.get("metadata") or {}
    if "readers.las" in info and md:
        header["dataformat_id"] = md.get("dataformat_id")
        header["minor_version"] = md.get("minor_version")
    bounds = reader_info.get("bounds") or {}
    if "minz" in bounds and "maxz" in bounds:
        header["offset_z"] = round((bounds["minz"] + bounds["maxz"]) / 2)
    return header


# -------------------------------
# 3. 結合と全体の実行
# -------------------------------

def _merge_outputs(outputs: List[str], writer: Dict[str, Any], chunk_size: int) -> Dict[str, Any]:
    stages: List[Any] = list(outputs)
    if len(outputs) > 1:
        stages.append({"type": "filters.merge"})
    stages.append(writer)
    pipeline = {"pipeline": stages}
    stream = not blocking_stages(pipeline)
    return _execute(pipeline, chunk_size if stream else None)


def _copc_note(writer: Dict[str, Any]) -> Optional[str]:
    if writer.get("type") in NON_STREAMABLE_STAGES:
        return (
            f"出力（{writer['type']}）はストリーム非対応のため、最後の結合は出力全体を"
            "メモリに読み込んで行いました。"
        )
    return None


def _write_empty(writer: Dict[str, Any], dtype) -> Dict[str, Any]:
    import numpy as np
    import pdal

    try:
        pdal.Pipeline(json.dumps([writer]), arrays=[np.zeros(0, dtype=dtype)]).execute()
    except Exception as exc:  # PDAL は RuntimeError などで失敗を返す
        return {"success": False, "error": str(exc), "log": ""}
    return {"success": True, "points_processed": 0, "log": ""}


def run_tiled(
        pipeline: Dict[str, Any],
        tile: TileParams,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        map_tasks: Optional[Callable] = None,
) -> Dict[str, Any]:
    """パイプラインをタイル分割で実行する。結果の形は execute_pipeline と同じ（mode="tile"）。

    ``map_tasks(fn, tasks)`` を渡すとタイルの処理をその関数で回す（並列実行用）。
    """
    parts = split_stages(pipeline)
    if not parts.rest:
        if blocking_stages(pipeline):  # 出力（COPC）だけがストリーム非対応
            result = _execute(pipeline, None)
            result["note"] = (
                f"ストリーム非対応なのは出力（{parts.writer['type']}）だけのため、タイル分割せずに"
                "通常実行しました。"
            )
            return result
        result = _execute(pipeline, chunk_size)
        result["note"] = "ストリーム非対応のフィルターがないため、タイル分割せずにストリームで実行しました。"
        return result

    buffer, note = resolve_buffer(tile, parts.rest)
    out_dir = tile.work_dir or os.path.dirname(os.path.abspath(parts.writer["filename"]))
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=".gqfilter-tiles-", dir=out_dir)
    try:
        try:
            files, core_counts, dtype, total = split_into_tiles(
                [*parts.readers, *parts.prefix], tile.size, buffer, chunk_size, tmp_dir
            )
        except Exception as exc:  # PDAL は RuntimeError などで失敗を返す
            return {"success": False, "error": str(exc), "log": ""}
        if dtype is None:
            result = _write_empty(parts.writer, [("X", "f8"), ("Y", "f8"), ("Z", "f8")])
            result["mode"] = "tile"
            result["tiles"] = 0
            return result

        header = _source_header(parts.readers)
        grid = common_grid(parts.readers)
        if grid is not None and not grid_fits(grid, grid["scale"], margin=buffer):
            grid = centered_grid(grid)
            if not grid_fits(grid, grid["scale"], margin=buffer):
                grid = None
        tasks = []
        for key, path in sorted(files.items()):
            if core_counts.get(key, 0) == 0:
                os.remove(path)
                continue
            out = os.path.join(tmp_dir, f"out_{key[0]}_{key[1]}.las")
            tasks.append({
                "key": key, "input": path, "dtype": dtype.descr, "rest": parts.rest,
                "writer": _tile_writer(out, key, tile.size, buffer, header, grid),
            })
        results = (map_tasks or _serial_map)(process_tile, tasks)
        failed = [r for r in results if not r["success"]]
        if failed:
            first = failed[0]
            return {
                "success": False,
                "error": f"タイル {first['key']} の処理に失敗しました: {first['error']}",
                "log": "",
            }
        outputs = [r["output"] for r in sorted(results, key=lambda r: r["key"]) if r["points"] > 0]
        if outputs:
            result = _merge_outputs(outputs, parts.writer, chunk_size)
        else:
            result = _write_empty(parts.writer, dtype)
        result["mode"] = "tile"
        result["tiles"] = len(tasks)
        result["points_read"] = total
        notes = [n for n in (note, _copc_note(parts.writer)) if n]
        if notes:
            result["note"] = " ".join(notes)
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# -------------------------------
# CLI helpers (shared by main.py and cli.py)
# -------------------------------

def add_tile_args(parser: argparse.ArgumentParser) -> None:
    """タイル分割モードの引数グループを登録する。"""
    group = parser.add_argument_group("Tiled Mode (タイル分割)")
    group.add_argument(
        "--tile",
        type=float,
        metavar="SIZE",
        help=(
            "Process in XY tiles of this size (CRS units) so that non-streamable filters "
            "(outlier, ground, voxel, deduplicate, incidence) fit in memory."
        ),
    )
    group.add_argument(
        "--tile-buffer",
        type=float,
        metavar="B",
        help=(
            "Overlap read around each tile (default: max(filter neighbourhood, 0.1 x tile size)). "
            "Points in the overlap are used as neighbours only."
        ),
    )
    group.add_argument(
        "--work-dir",
        metavar="DIR",
        help=(
            "Directory for temporary tile files (default: next to the output). They need about "
            "points x 40-60 bytes x (1 + overlap) of free space."
        ),
    )


def tile_params_from_args(args: argparse.Namespace) -> Optional[TileParams]:
    if args.tile is None:
        if args.tile_buffer is not None:
            raise ValueError("--tile-buffer requires --tile SIZE")
        return None
    return TileParams(size=args.tile, buffer=args.tile_buffer, work_dir=args.work_dir)
