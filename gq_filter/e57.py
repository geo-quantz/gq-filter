"""E57 ファイルの読み込み（構造化・非構造化・複数スキャン・器械点）。

E57 には 1 ファイルに複数のスキャンが入り、スキャンごとに器械点（スキャナーの位置）と
向き（pose: 回転の四元数と並進）が記録される。点はスキャナー座標系で、直交座標
（cartesianX/Y/Z）または球座標（sphericalRange/Azimuth/Elevation）で記録される。
構造化スキャンは行 × 列の格子で、欠測した格子点は無効点（InvalidState）として入っている。

PDAL の readers.e57 は全スキャンを読み、pose を適用する。ただし PDAL 2.10 では次の点が
足りない。

- 点がどのスキャンのものか分からないため、スキャンごとの器械点からの距離・入射角で
  絞れない（gq-filter はこれまで先頭スキャンの器械点だけを使っていた）
- 無効点を ``Omit`` ディメンションで印を付けるだけで残す（器械点の位置に点が出る）
  → ``build_pipeline`` で ``Omit == 0`` を足して除く
- 直交座標のスキャンと球座標のスキャンが混ざったファイルで、直交座標のスキャンの座標が
  正しく読めない
- 強度の最小値（intensityLimits）が 0 でないとき、強度の換算が合わない

このモジュールは pye57（任意の依存）でスキャンを 1 つずつ、チャンクに分けて読み、
pose を適用して LAS に書き出す（``expand_e57``）。スキャンごとの器械点を使う処理は
``gq_filter.runner`` がこの結果を使って行う。
"""
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

E57_READERS = ("auto", "pdal", "pye57")
INTENSITY_MAX = 65535
DEFAULT_E57_CHUNK = 100_000
# 器械点が同じとみなす距離（座標単位）
SAME_ORIGIN_TOLERANCE = 1e-6

Origin = Tuple[float, float, float]

_CARTESIAN = ("cartesianX", "cartesianY", "cartesianZ")
_SPHERICAL = ("sphericalRange", "sphericalAzimuth", "sphericalElevation")
_COLORS = ("colorRed", "colorGreen", "colorBlue")


class Pye57Missing(ImportError):
    pass


def _pye57():
    try:
        import pye57
    except ImportError as exc:  # pye57 は任意の依存
        raise Pye57Missing(
            "E57 のスキャンごとの読み込みには pye57 が必要です（pip install pye57）"
        ) from exc
    return pye57


def is_e57(path: Any) -> bool:
    return isinstance(path, str) and path.lower().endswith(".e57")


# -------------------------------
# スキャンの情報
# -------------------------------

@dataclass(frozen=True)
class E57Scan:
    """E57 の 1 スキャンの情報。

    Attributes:
        origin:      器械点（pose の並進。ファイルの座標系）
        rotation:    スキャナーの向き（四元数 w, x, y, z）
        coordinates: ``cartesian`` / ``spherical``
        structured:  行 × 列の格子のスキャン（rowIndex / columnIndex あり）
        rows, columns: 構造化スキャンの行数・列数
        has_invalid: 無効点（InvalidState）のフィールドがある
        intensity_limits: 強度の最小・最大（換算に使う）
        color_limits: 色の最小・最大
    """
    index: int
    name: str
    point_count: int
    origin: Origin
    rotation: Tuple[float, float, float, float]
    has_pose: bool
    coordinates: str
    structured: bool
    rows: Optional[int]
    columns: Optional[int]
    has_invalid: bool
    has_intensity: bool
    has_color: bool
    intensity_limits: Optional[Tuple[float, float]]
    color_limits: Optional[Tuple[float, float]]


def _node_limits(header, proto, limits_node: str, lo_name: str, hi_name: str, field: str):
    """ヘッダーの *Limits、なければ点のプロトタイプの最小・最大。"""
    from pye57.utils import get_node

    if header.node.isDefined(limits_node):
        node = header.node[limits_node]
        if node.isDefined(lo_name) and node.isDefined(hi_name):
            return float(node[lo_name].value()), float(node[hi_name].value())
    try:
        proto_node = get_node(proto, field)
    except Exception:  # プロトタイプにない・型が分からないときは使わない
        return None
    for lo, hi in (("scaledMinimum", "scaledMaximum"), ("minimum", "maximum")):
        if hasattr(proto_node, lo) and hasattr(proto_node, hi):
            return float(getattr(proto_node, lo)()), float(getattr(proto_node, hi)())
    return None


def _scan_info(e57, index: int) -> E57Scan:
    from pye57 import libe57

    header = e57.get_header(index)
    fields = list(header.point_fields)
    if all(f in fields for f in _CARTESIAN):
        coordinates = "cartesian"
    elif all(f in fields for f in _SPHERICAL):
        coordinates = "spherical"
    else:
        raise ValueError(f"scan {index} has neither cartesian nor spherical coordinates")
    proto = libe57.StructureNode(header.points.prototype())
    structured = "rowIndex" in fields and "columnIndex" in fields
    rows = columns = None
    if structured and header.node.isDefined("indexBounds"):
        rows = int(header.rowMaximum) - int(header.rowMinimum) + 1
        columns = int(header.columnMaximum) - int(header.columnMinimum) + 1
    has_color = all(c in fields for c in _COLORS)
    name = header.node["name"].value() if header.node.isDefined("name") else f"scan{index}"
    translation = header.translation
    rotation = header.rotation if header.has_pose() else (1.0, 0.0, 0.0, 0.0)
    return E57Scan(
        index=index,
        name=str(name),
        point_count=int(header.point_count),
        origin=(float(translation[0]), float(translation[1]), float(translation[2])),
        rotation=tuple(float(v) for v in rotation),
        has_pose=bool(header.has_pose()),
        coordinates=coordinates,
        structured=structured,
        rows=rows,
        columns=columns,
        has_invalid=("cartesianInvalidState" in fields or "sphericalInvalidState" in fields),
        has_intensity="intensity" in fields,
        has_color=has_color,
        intensity_limits=(
            _node_limits(header, proto, "intensityLimits", "intensityMinimum", "intensityMaximum", "intensity")
            if "intensity" in fields else None
        ),
        color_limits=(
            _node_limits(header, proto, "colorLimits", "colorRedMinimum", "colorRedMaximum", "colorRed")
            if has_color else None
        ),
    )


def read_scans(path: str) -> List[E57Scan]:
    """E57 の全スキャンの情報（pye57 が必要）。"""
    pye57 = _pye57()
    e57 = pye57.E57(path)
    try:
        return [_scan_info(e57, i) for i in range(e57.scan_count)]
    finally:
        e57.close()


def shared_origin(scans: Sequence[E57Scan]) -> Optional[Origin]:
    """全スキャンの器械点が同じならその点、違えば None。"""
    if not scans:
        return None
    first = scans[0].origin
    for scan in scans[1:]:
        if math.dist(first, scan.origin) > SAME_ORIGIN_TOLERANCE:
            return None
    return first


def pye57_reasons(scans: Sequence[E57Scan], needs_origin: bool) -> List[str]:
    """PDAL の readers.e57 では正しく読めない・処理できない理由（なければ空）。"""
    reasons = []
    if needs_origin and len(scans) > 1 and shared_origin(scans) is None:
        reasons.append(f"器械点の違うスキャンが {len(scans)} 個あり、スキャンごとの器械点からの距離・入射角で絞る")
    kinds = {s.coordinates for s in scans}
    if len(kinds) > 1:
        reasons.append("直交座標と球座標のスキャンが混ざっている（PDAL では直交座標のスキャンが読めない）")
    if any(s.intensity_limits and s.intensity_limits[0] != 0 for s in scans):
        reasons.append("強度の最小値が 0 でない（PDAL では強度の換算が合わない）")
    return reasons


def describe_scans(path: str, scans: Sequence[E57Scan]) -> str:
    """--e57-info の表示。"""
    lines = [f"{path}: {len(scans)} スキャン"]
    for s in scans:
        kind = f"構造化 {s.rows}×{s.columns}" if s.structured else "非構造化"
        ox, oy, oz = s.origin
        lines.append(
            f"  [{s.index}] {s.name}: {s.point_count} 点, {kind}, {s.coordinates}, "
            f"器械点=({ox:.3f}, {oy:.3f}, {oz:.3f}), pose={'あり' if s.has_pose else 'なし'}, "
            f"無効点フィールド={'あり' if s.has_invalid else 'なし'}"
        )
    origin = shared_origin(scans)
    lines.append("  器械点: 全スキャン共通" if origin is not None else "  器械点: スキャンごとに違う")
    return "\n".join(lines)


# -------------------------------
# 点の読み込み
# -------------------------------

def _rotation_matrix(q: Sequence[float]):
    import numpy as np

    w, x, y, z = q
    norm = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _scale_to_u16(values, limits: Optional[Tuple[float, float]], default: Tuple[float, float]):
    import numpy as np

    lo, hi = limits or default
    if not hi > lo:
        lo, hi = default
    scaled = (values.astype("f8") - lo) / (hi - lo) * INTENSITY_MAX
    return np.clip(np.rint(scaled), 0, INTENSITY_MAX).astype("u2")


def point_dtype(scan: E57Scan):
    import numpy as np

    fields = [("X", "f8"), ("Y", "f8"), ("Z", "f8")]
    if scan.has_intensity:
        fields.append(("Intensity", "u2"))
    if scan.has_color:
        fields += [("Red", "u2"), ("Green", "u2"), ("Blue", "u2")]
    fields.append(("PointSourceId", "u2"))
    return np.dtype(fields)


def _to_points(raw: Dict[str, Any], scan: E57Scan):
    """生のフィールド（1 チャンク）→ PDAL のディメンション名の構造化配列（全体座標）。"""
    import numpy as np
    from pye57.utils import convert_spherical_to_cartesian

    state = raw.get("cartesianInvalidState", raw.get("sphericalInvalidState"))
    valid = np.ones(len(next(iter(raw.values()))), dtype=bool) if state is None else (state == 0)
    if scan.coordinates == "cartesian":
        local = np.column_stack([raw[f][valid] for f in _CARTESIAN])
    else:
        rae = np.column_stack([raw[f][valid] for f in _SPHERICAL])
        local = convert_spherical_to_cartesian(rae)
    xyz = local @ _rotation_matrix(scan.rotation).T + np.asarray(scan.origin) if scan.has_pose else local
    out = np.zeros(len(xyz), dtype=point_dtype(scan))
    out["X"], out["Y"], out["Z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    if scan.has_intensity:
        out["Intensity"] = _scale_to_u16(raw["intensity"][valid], scan.intensity_limits, (0.0, 1.0))
    if scan.has_color:
        for src, dst in zip(_COLORS, ("Red", "Green", "Blue")):
            out[dst] = _scale_to_u16(raw[src][valid], scan.color_limits, (0.0, 255.0))
    out["PointSourceId"] = min(scan.index, 65535)
    return out


def iter_scan_points(path: str, index: int, chunk_size: int = DEFAULT_E57_CHUNK) -> Iterator[Any]:
    """1 スキャンの点を chunk_size 点ずつ返す（無効点を除き、pose を適用した全体座標）。

    ディメンションは X, Y, Z, Intensity（0〜65535）, Red/Green/Blue（0〜65535）,
    PointSourceId（スキャン番号）。
    """
    pye57 = _pye57()
    e57 = pye57.E57(path)
    try:
        scan = _scan_info(e57, index)
        header = e57.get_header(index)
        wanted = list(_CARTESIAN if scan.coordinates == "cartesian" else _SPHERICAL)
        if scan.has_intensity:
            wanted.append("intensity")
        if scan.has_color:
            wanted += list(_COLORS)
        for state in ("cartesianInvalidState", "sphericalInvalidState"):
            if state in header.point_fields:
                wanted.append(state)
                break
        buffers_data, buffers = e57.make_buffers(wanted, chunk_size)
        reader = header.points.reader(buffers)
        try:
            while True:
                count = reader.read()
                if count == 0:
                    break
                raw = {name: arr[:count] for name, arr in buffers_data.items()}
                points = _to_points(raw, scan)
                if len(points):
                    yield points
        finally:
            reader.close()
    finally:
        e57.close()


# -------------------------------
# LAS への書き出し（スキャンごと）
# -------------------------------

def range_mask(points, origin: Origin, min_distance: Optional[float], max_distance: Optional[float]):
    """器械点からの距離で残す点（filter.build_range_filter と同じ式: 距離の 2 乗で比べる）。"""
    import numpy as np

    x0, y0, z0 = origin
    d2 = (points["X"] - x0) * (points["X"] - x0) + (points["Y"] - y0) * (points["Y"] - y0) \
        + (points["Z"] - z0) * (points["Z"] - z0)
    keep = np.ones(len(points), dtype=bool)
    if min_distance is not None:
        keep &= d2 >= float(min_distance * min_distance)
    if max_distance is not None:
        keep &= d2 <= float(max_distance * max_distance)
    return keep


@dataclass(frozen=True)
class ExpandedScan:
    """LAS に書き出した 1 スキャン。files はチャンクごとの LAS（点のないチャンクは書かない）。"""
    index: int
    name: str
    origin: Origin
    files: Tuple[str, ...]
    points_read: int
    points_written: int


@dataclass(frozen=True)
class _ExpandTask:
    path: str
    index: int
    out_dir: str
    chunk_size: int
    min_distance: Optional[float]
    max_distance: Optional[float]
    origin_override: Optional[Origin]


# 1 つの LAS に書く点数の目安（チャンクをためて書き、ファイル数を抑える）
PART_POINTS = 5_000_000


def _part_writer(filename: str, origin: Origin, has_color: bool) -> Dict[str, Any]:
    # 器械点のまわり ±214 km まで 0.1 mm で書ける
    return {
        "type": "writers.las", "filename": filename,
        "dataformat_id": 3 if has_color else 1, "minor_version": 4,
        "scale_x": 0.0001, "scale_y": 0.0001, "scale_z": 0.0001,
        "offset_x": round(origin[0]), "offset_y": round(origin[1]), "offset_z": round(origin[2]),
    }


def expand_scan(task: _ExpandTask) -> ExpandedScan:
    """1 スキャンを LAS に書き出す（別プロセスでも呼べる）。距離フィルターもここでかける。"""
    import numpy as np
    import pdal

    scan = read_scans(task.path)[task.index]
    origin = task.origin_override or scan.origin
    files: List[str] = []
    pending: List[Any] = []
    read = written = 0

    def flush() -> None:
        nonlocal written
        if not pending:
            return
        points = np.concatenate(pending)
        pending.clear()
        filename = os.path.join(task.out_dir, f"scan{task.index:04d}_{len(files):04d}.las")
        writer = _part_writer(filename, scan.origin, scan.has_color)
        pdal.Pipeline(json.dumps([writer]), arrays=[points]).execute()
        files.append(filename)
        written += len(points)

    for points in iter_scan_points(task.path, task.index, task.chunk_size):
        read += len(points)
        if task.min_distance is not None or task.max_distance is not None:
            points = points[range_mask(points, origin, task.min_distance, task.max_distance)]
        if len(points):
            pending.append(points)
        if sum(len(p) for p in pending) >= PART_POINTS:
            flush()
    flush()
    return ExpandedScan(task.index, scan.name, origin, tuple(files), read, written)


def expand_e57(
        path: str,
        out_dir: str,
        *,
        chunk_size: int = DEFAULT_E57_CHUNK,
        min_distance: Optional[float] = None,
        max_distance: Optional[float] = None,
        origin_override: Optional[Origin] = None,
        jobs: int = 1,
) -> List[ExpandedScan]:
    """E57 の全スキャンを LAS に書き出す（スキャンごとに並列、メモリはチャンク分）。

    min/max_distance を渡すと、各スキャンの器械点（origin_override があればその点）からの
    距離で絞ってから書く。
    """
    from gq_filter.parallel import process_map

    scans = read_scans(path)
    tasks = [
        _ExpandTask(path, s.index, out_dir, chunk_size, min_distance, max_distance, origin_override)
        for s in scans
    ]
    return process_map(expand_scan, tasks, jobs)


# -------------------------------
# build_pipeline 用（PDAL の readers.e57 で読むとき）
# -------------------------------

def has_invalid_points(path: str) -> bool:
    """readers.e57 が無効点に Omit を付けるか（無効点のフィールドがあるか）。

    pye57 があればヘッダーで、なければ PDAL の quickinfo で調べる。どちらもなければ False。
    """
    try:
        return any(s.has_invalid for s in read_scans(path))
    except Pye57Missing:
        pass
    except Exception:  # 壊れたファイルなどは PDAL の実行時のエラーに任せる
        return False
    try:
        import pdal

        info = pdal.Pipeline(json.dumps([path])).quickinfo.get("readers.e57", {})
        return "Omit" in [d.strip() for d in str(info.get("dimensions", "")).split(",")]
    except Exception:  # PDAL がない・読めないときは付けない
        return False


# -------------------------------
# CLI helpers (shared by main.py and cli.py)
# -------------------------------

def add_e57_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("E57 Input (E57 の読み込み)")
    group.add_argument(
        "--e57-reader",
        choices=E57_READERS,
        default="auto",
        help=(
            "How to read E57: pdal (readers.e57), pye57 (scan by scan, each scan's scanner "
            "position used for --range-* / --incidence-*), auto (default: pye57 when it is "
            "installed and the file needs it, e.g. scans with different scanner positions)."
        ),
    )
    group.add_argument(
        "--e57-info",
        action="store_true",
        help="Print the scans of each E57 input (structured or not, scanner position, pose) and exit.",
    )
