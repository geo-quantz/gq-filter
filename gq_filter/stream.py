"""大容量ファイルモード（ストリーム読込）。

PDAL の通常実行（``Pipeline.execute()``）は全点をメモリに読み込んでから処理する。
ストリーム実行（``Pipeline.execute_streaming(chunk_size)``）は ``chunk_size`` 点ずつ
読み込み・処理・書き出しを繰り返すので、メモリ使用量がファイルの大きさに依らない。

ただし、近傍点や全点の統計を使うフィルターはストリーム実行できない。
gq-filter のフィルターのうちストリーム実行できないものは ``NON_STREAMABLE_STAGES``
の表のとおり（PDAL 2.10 で ``Pipeline.streamable`` を確認、テストで PDAL の判定と
突き合わせている）。これらを使う大容量ファイルはタイル分割モード
（``gq_filter.tiling``）で処理する。
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from gq_filter.tiling import TileParams

STREAM_MODES = ("off", "on", "auto")
# 30M 点の LAS で 1,000,000 と速さが変わらず、タイル分割の振り分けでメモリが増えにくい値
DEFAULT_CHUNK_SIZE = 100_000
DEFAULT_THRESHOLD_MB = 1024
_MB = 1024 * 1024

# ストリーム実行できない PDAL ステージ → 対応する gq-filter の機能
NON_STREAMABLE_STAGES: Dict[str, str] = {
    "filters.normal": "入射角フィルター（--incidence-angle-min / --incidence-angle-max）",
    "filters.outlier": "外れ値除去（--outlier-statistical / --outlier-radius）",
    "filters.smrf": "地表分類（--ground-classify smrf）",
    "filters.pmf": "地表分類（--ground-classify pmf）",
    "filters.csf": "地表分類（--ground-classify csf）",
    "filters.voxelcentroidnearestneighbor": "ボクセル間引き（--voxel-size）",
    "filters.label_duplicates": "重複除去（--deduplicate / --merge）",
    "writers.copc": "COPC 出力（.copc.laz）",
}


class NotStreamableError(ValueError):
    """ストリーム実行を求められたが、パイプラインにストリーム非対応のステージがある。"""

    def __init__(self, stages: List[str]):
        self.stages = list(stages)
        super().__init__(
            "ストリーム読込（--stream on）では実行できません。"
            f"ストリーム非対応のフィルター: {describe_stages(self.stages)}。"
            "大容量ファイルでこれらのフィルターを使うときは --tile でタイル分割してください。"
        )


@dataclass(frozen=True)
class StreamParams:
    """ストリーム読込の設定。

    Attributes:
        mode:            ``off``（通常実行）/ ``on``（必ずストリーム）/
                         ``auto``（入力の合計サイズが閾値以上で、ストリーム可能ならストリーム）
        chunk_size:      ストリーム実行で一度に読む点数
        threshold_bytes: ``auto`` でストリームに切り替える入力の合計サイズ（バイト）
    """
    mode: str = "off"
    chunk_size: int = DEFAULT_CHUNK_SIZE
    threshold_bytes: int = DEFAULT_THRESHOLD_MB * _MB

    def __post_init__(self) -> None:
        if self.mode not in STREAM_MODES:
            raise ValueError(f"stream mode must be one of {', '.join(STREAM_MODES)} (got {self.mode!r})")
        if not isinstance(self.chunk_size, int) or self.chunk_size <= 0:
            raise ValueError(f"chunk_size must be a positive integer (got {self.chunk_size!r})")
        if self.threshold_bytes < 0:
            raise ValueError(f"threshold_bytes must be >= 0 (got {self.threshold_bytes!r})")


# -------------------------------
# パイプラインの読み取り
# -------------------------------

def _stage_type(stage: Any) -> Optional[str]:
    return stage.get("type") if isinstance(stage, dict) else None


def is_reader(stage: Any) -> bool:
    """文字列（ファイル名）か readers.* のステージ。type 省略で filename だけの dict も含む。"""
    if isinstance(stage, str):
        return True
    if not isinstance(stage, dict):
        return False
    kind = stage.get("type")
    return (kind or "").startswith("readers.") or (kind is None and "filename" in stage)


def input_files(pipeline: Dict[str, Any]) -> List[str]:
    """パイプラインが読む入力ファイル名。"""
    names = []
    for stage in pipeline.get("pipeline", []):
        if isinstance(stage, str):
            names.append(stage)
        elif is_reader(stage) and stage.get("filename"):
            names.append(stage["filename"])
    return names


def blocking_stages(pipeline: Dict[str, Any]) -> List[str]:
    """ストリーム実行を妨げるステージの type（重複なし、出現順）。"""
    found: List[str] = []
    for stage in pipeline.get("pipeline", []):
        kind = _stage_type(stage)
        if kind in NON_STREAMABLE_STAGES and kind not in found:
            found.append(kind)
    return found


def describe_stages(stages: List[str]) -> str:
    return "、".join(f"{NON_STREAMABLE_STAGES.get(s, s)} [{s}]" for s in stages)


def total_input_bytes(pipeline: Dict[str, Any]) -> int:
    return sum(os.path.getsize(p) for p in input_files(pipeline) if os.path.isfile(p))


# -------------------------------
# 座標の格子（ストリーム実行の offset）
# -------------------------------

_AXES = ("x", "y", "z")


def las_grid(reader: Any) -> Optional[Dict[str, Any]]:
    """LAS/LAZ 入力の座標の格子（scale と、データの最小値に近い格子上の offset）。

    writers.las の ``offset: auto`` は、通常実行では書き出す点の範囲の中央、ストリーム実行では
    最初のチャンクの点になり、どちらも入力の格子に乗らないことがある（scale 0.001 で最大
    0.5 mm ずれる）。入力の格子に乗る offset を使えば、入力の scale が 0.001 の倍数のとき
    書き出した座標が入力と同じになり、通常実行とストリーム実行の結果も一致する。LAS 以外は None。
    """
    import pdal

    stage = {"filename": reader} if isinstance(reader, str) else dict(reader)
    try:
        info = pdal.Pipeline(json.dumps({"pipeline": [stage]})).quickinfo
    except Exception:  # 情報が取れないときは PDAL の auto に任せる
        return None
    las = info.get("readers.las")
    if not las or not las.get("metadata") or not las.get("bounds"):
        return None
    md, bounds = las["metadata"], las["bounds"]
    scale, offset = {}, {}
    for axis in _AXES:
        s, o = float(md[f"scale_{axis}"]), float(md[f"offset_{axis}"])
        lo = float(bounds[f"min{axis}"])
        scale[axis] = s
        offset[axis] = o + round((lo - o) / s) * s
    return {"scale": scale, "offset": offset, "bounds": bounds}


def common_grid(readers: List[Any]) -> Optional[Dict[str, Any]]:
    """すべての入力に共通の格子。どれかが LAS でないか、scale が違えば None。

    offset は最初の入力の格子のもの、bounds は全入力の範囲。
    """
    grids = [las_grid(r) for r in readers]
    if not grids or any(g is None for g in grids):
        return None
    first = grids[0]
    if any(g["scale"] != first["scale"] for g in grids[1:]):
        return None
    bounds = {}
    for axis in _AXES:
        bounds[f"min{axis}"] = min(float(g["bounds"][f"min{axis}"]) for g in grids)
        bounds[f"max{axis}"] = max(float(g["bounds"][f"max{axis}"]) for g in grids)
    return {**first, "bounds": bounds}


def grid_fits(grid: Dict[str, Any], scale: Dict[str, float], margin: float = 0.0) -> bool:
    """grid の offset と与えた scale で、範囲（＋margin）の座標が LAS の int32 に収まるか。"""
    b = grid["bounds"]
    for axis in _AXES:
        o = grid["offset"][axis]
        span = max(abs(float(b[f"max{axis}"]) - o), abs(float(b[f"min{axis}"]) - o)) + margin
        if span / scale[axis] >= 2 ** 31 - 1:
            return False
    return True


def centered_grid(grid: Dict[str, Any]) -> Dict[str, Any]:
    """offset を範囲の中央に近い格子点に移した grid（離れた入力をまとめるとき用）。"""
    offset = {}
    for axis in _AXES:
        s, o = grid["scale"][axis], grid["offset"][axis]
        mid = (float(grid["bounds"][f"min{axis}"]) + float(grid["bounds"][f"max{axis}"])) / 2
        offset[axis] = o + round((mid - o) / s) * s
    return {**grid, "offset": offset}


def with_aligned_offsets(pipeline: Dict[str, Any]) -> Dict[str, Any]:
    """writers.las の offset が auto なら、入力の格子に乗る値に置き換えたコピーを返す。

    入力が LAS でない・入力ごとに scale が違う・int32 に収まらない、のどれかなら元のまま。
    """
    stages = pipeline.get("pipeline", [])
    if not stages or not isinstance(stages[-1], dict) or stages[-1].get("type") != "writers.las":
        return pipeline
    writer = stages[-1]
    if not any(writer.get(f"offset_{a}") == "auto" for a in _AXES):
        return pipeline
    grid = common_grid([s for s in stages if is_reader(s)])
    if grid is None:
        return pipeline
    scale = {}
    for axis in _AXES:
        value = writer.get(f"scale_{axis}")
        scale[axis] = float(value) if isinstance(value, (int, float)) else grid["scale"][axis]
    if not grid_fits(grid, scale):
        grid = centered_grid(grid)
        if not grid_fits(grid, scale):
            return pipeline
    new_writer = dict(writer)
    for axis in _AXES:
        if writer.get(f"offset_{axis}") == "auto":
            new_writer[f"offset_{axis}"] = grid["offset"][axis]
    return {**pipeline, "pipeline": [*stages[:-1], new_writer]}


# -------------------------------
# 実行
# -------------------------------

def _execute(pipeline: Dict[str, Any], chunk_size: Optional[int]) -> Dict[str, Any]:
    """PDAL を実行して execute_pipeline と同じ形の結果を返す。chunk_size があればストリーム。

    通常実行は ``execute_pipeline`` に任せる。どちらも LAS 出力の offset を入力の格子に合わせる。
    """
    pipeline = with_aligned_offsets(pipeline)
    if chunk_size is None:
        from gq_filter.filter import execute_pipeline

        return {**execute_pipeline(pipeline), "mode": "standard"}

    import pdal  # 遅延 import（PDAL の Python バインディングは実行時だけ必要）

    try:
        pl = pdal.Pipeline(json.dumps(pipeline))
        if not pl.streamable:
            raise NotStreamableError(blocking_stages(pipeline) or ["(PDAL がストリーム非対応と判定)"])
        count = pl.execute_streaming(chunk_size=chunk_size)
        raw_md = pl.metadata
        md = json.loads(raw_md) if isinstance(raw_md, str) else raw_md
        return {
            "success": True,
            "points_processed": count,
            "metadata": md,
            "log": pl.log,
            "mode": "stream",
        }
    except NotStreamableError:
        raise
    except Exception as exc:  # PDAL は RuntimeError などで失敗を返す
        return {"success": False, "error": str(exc), "log": ""}


def decide_mode(pipeline: Dict[str, Any], params: StreamParams) -> Dict[str, Any]:
    """ストリームで実行するかを決める。

    Returns: ``{"stream": bool, "note": str | None}``。``on`` でストリームできないときは
    NotStreamableError を送出する。
    """
    blocked = blocking_stages(pipeline)
    if params.mode == "off":
        return {"stream": False, "note": None}
    if params.mode == "on":
        if blocked:
            raise NotStreamableError(blocked)
        return {"stream": True, "note": None}
    size = total_input_bytes(pipeline)
    if size < params.threshold_bytes:
        return {"stream": False, "note": None}
    if blocked:
        return {
            "stream": False,
            "note": (
                f"入力が {size / _MB:.0f} MB と大きいのでストリーム読込を試みましたが、"
                f"ストリーム非対応のフィルターがあるため通常実行します: {describe_stages(blocked)}。"
                "メモリが足りないときは --tile でタイル分割してください。"
            ),
        }
    return {"stream": True, "note": None}


def run_pipeline(
        pipeline: Dict[str, Any],
        params: Optional[StreamParams] = None,
        tile: Optional["TileParams"] = None,
        jobs: Optional[int] = 1,
) -> Dict[str, Any]:
    """ストリーム・タイルの設定に従ってパイプラインを実行する。

    結果は ``execute_pipeline`` と同じ形の dict に ``mode``（``standard`` / ``stream`` /
    ``tile``）を足したもの。``params`` も ``tile`` も省略すると通常実行。
    ``tile`` を渡すとタイル分割で実行する（読み込みと結合は ``params.chunk_size`` のストリーム）。
    ``jobs`` はタイルを同時に処理するプロセス数（None で CPU コア数、既定 1）。
    """
    params = params or StreamParams()
    if tile is not None:
        from functools import partial

        from gq_filter.parallel import resolve_jobs
        from gq_filter.tiling import isolated_map, run_tiled

        map_tasks = partial(isolated_map, jobs=resolve_jobs(jobs))
        return run_tiled(pipeline, tile, chunk_size=params.chunk_size, map_tasks=map_tasks)
    decision = decide_mode(pipeline, params)
    try:
        result = _execute(pipeline, params.chunk_size if decision["stream"] else None)
    except NotStreamableError as exc:
        if params.mode == "on":
            raise
        # 表にないステージを PDAL がストリーム非対応と判定したとき（auto）は通常実行に戻す
        result = _execute(pipeline, None)
        result["note"] = f"ストリーム読込できないため通常実行しました: {describe_stages(exc.stages)}"
        return result
    if decision["note"]:
        result["note"] = decision["note"]
    return result


# -------------------------------
# CLI helpers (shared by main.py and cli.py)
# -------------------------------

def add_stream_args(parser: argparse.ArgumentParser) -> None:
    """大容量ファイルモードの引数グループを登録する。"""
    group = parser.add_argument_group("Large File Mode (大容量ファイル)")
    group.add_argument(
        "--stream",
        choices=STREAM_MODES,
        default="auto",
        help=(
            "Streaming read: off / on / auto (default: auto = stream when the inputs are "
            "larger than --stream-threshold-mb and every filter supports streaming)."
        ),
    )
    group.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        metavar="N",
        help=f"Points read per chunk in streaming mode (default: {DEFAULT_CHUNK_SIZE}).",
    )
    group.add_argument(
        "--stream-threshold-mb",
        type=float,
        default=DEFAULT_THRESHOLD_MB,
        metavar="MB",
        help=f"Input size that switches --stream auto to streaming (default: {DEFAULT_THRESHOLD_MB}).",
    )


def stream_params_from_args(args: argparse.Namespace) -> StreamParams:
    if args.stream_threshold_mb < 0:
        raise ValueError("--stream-threshold-mb must be >= 0")
    return StreamParams(
        mode=args.stream,
        chunk_size=args.chunk_size,
        threshold_bytes=int(args.stream_threshold_mb * _MB),
    )


def streamability_report(pipeline: Dict[str, Any]) -> str:
    """--dry-run で表示する一行。"""
    blocked = blocking_stages(pipeline)
    if not blocked:
        return "ストリーム読込: 可（すべてのフィルターがストリーム対応）"
    return f"ストリーム読込: 不可（{describe_stages(blocked)}）。大容量ファイルは --tile で処理"
