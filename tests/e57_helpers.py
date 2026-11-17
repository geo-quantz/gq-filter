"""テスト用の E57 ファイルを pye57 で作る補助関数。

- 非構造化スキャン: 点の並びだけ（rowIndex / columnIndex なし）
- 構造化スキャン: 行 × 列の格子（rowIndex / columnIndex あり、無効点あり）
- 球座標のスキャン: sphericalRange / Azimuth / Elevation で記録（pye57 の writer は
  直交座標しか書けないので、libe57 のノードを直接組み立てる）

各スキャンの点はスキャナー座標系で作り、pose（回転・器械点）をヘッダーに書く。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def quat_z(degrees: float) -> np.ndarray:
    """Z 軸まわりの回転の四元数 (w, x, y, z)。"""
    half = math.radians(degrees) / 2
    return np.array([math.cos(half), 0.0, 0.0, math.sin(half)])


def rotate(q: Sequence[float], xyz: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    r = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    return xyz @ r.T


@dataclass
class Scan:
    """1 スキャン分。local はスキャナー座標系の XYZ（N, 3）。"""
    local: np.ndarray
    translation: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: Sequence[float] = (1.0, 0.0, 0.0, 0.0)
    intensity: Optional[np.ndarray] = None
    rgb: Optional[np.ndarray] = None
    rows: Optional[int] = None  # 構造化スキャンの行数（列数は N / rows）
    invalid: Optional[np.ndarray] = None  # 無効点（構造化スキャンの欠測）
    spherical: bool = False
    name: str = ""
    extra: Dict[str, np.ndarray] = field(default_factory=dict)

    def global_xyz(self, valid_only: bool = True) -> np.ndarray:
        xyz = rotate(self.rotation, self.local) + np.asarray(self.translation)
        if valid_only and self.invalid is not None:
            xyz = xyz[~self.invalid]
        return xyz


def ring_scan(n_rows: int, n_cols: int, *, radius=(3.0, 8.0), seed: int = 0, invalid_every: int = 0) -> Dict:
    """スキャナーの周りの点（格子状）。行 = 仰角、列 = 方位角。"""
    rng = np.random.default_rng(seed)
    rows, cols = np.meshgrid(np.arange(n_rows), np.arange(n_cols), indexing="ij")
    az = cols.ravel() / n_cols * 2 * np.pi
    el = (rows.ravel() / max(n_rows - 1, 1) - 0.5) * 1.2
    rng_ = radius[0] + rng.random(rows.size) * (radius[1] - radius[0])
    local = np.column_stack([rng_ * np.cos(el) * np.cos(az), rng_ * np.cos(el) * np.sin(az), rng_ * np.sin(el)])
    invalid = None
    if invalid_every:
        invalid = np.zeros(rows.size, dtype=bool)
        invalid[::invalid_every] = True
    intensity = rng.random(rows.size).astype("f4")
    intensity[1], intensity[2] = 0.0, 1.0  # 強度の範囲を 0〜1 にする
    return {"local": local, "rows": n_rows, "invalid": invalid,
            "intensity": intensity, "rgb": rng.integers(0, 256, (rows.size, 3))}


def nearest(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """a の各点に最も近い b の点の番号と距離（小さな点群用の総当たり）。"""
    d2 = ((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
    idx = d2.argmin(axis=1)
    return idx, np.sqrt(d2[np.arange(len(a)), idx])


def assert_same_points(got: np.ndarray, expected: np.ndarray, atol: float) -> np.ndarray:
    """順序によらず同じ点の集まりか（float32 で記録された座標の誤差を atol まで許す）。

    Returns: expected の各点に対応する got の番号。
    """
    assert got.shape == expected.shape, (got.shape, expected.shape)
    idx, dist = nearest(expected, got)
    assert dist.max() <= atol, dist.max()
    assert len(set(idx.tolist())) == len(idx)
    return idx


def _cartesian_data(scan: Scan) -> Dict[str, np.ndarray]:
    n = len(scan.local)
    local = scan.local.copy()
    data: Dict[str, np.ndarray] = {}
    if scan.invalid is not None:
        local[scan.invalid] = 0.0
        data["cartesianInvalidState"] = scan.invalid.astype("i1")
    data["cartesianX"], data["cartesianY"], data["cartesianZ"] = local.T.copy()
    if scan.intensity is not None:
        data["intensity"] = scan.intensity.astype("f4")
    if scan.rgb is not None:
        data["colorRed"], data["colorGreen"], data["colorBlue"] = (scan.rgb.T.astype("u1")).copy()
    if scan.rows:
        cols = n // scan.rows
        r, c = np.meshgrid(np.arange(scan.rows), np.arange(cols), indexing="ij")
        data["rowIndex"], data["columnIndex"] = r.ravel().astype("u2"), c.ravel().astype("u2")
    return data


def _write_spherical(e57, scan: Scan) -> None:
    """pye57 の write_scan_raw の球座標版（必要なノードだけ）。"""
    from pye57 import libe57

    imf = e57.image_file
    local = scan.local
    rng = np.linalg.norm(local, axis=1)
    az = np.arctan2(local[:, 1], local[:, 0])
    el = np.arcsin(np.clip(local[:, 2] / np.where(rng == 0, 1, rng), -1, 1))
    invalid = scan.invalid if scan.invalid is not None else np.zeros(len(local), dtype=bool)
    rng = np.where(invalid, 0.0, rng)

    node = libe57.StructureNode(imf)
    node.set("guid", libe57.StringNode(imf, "{%s}" % uuid.uuid4()))
    node.set("name", libe57.StringNode(imf, scan.name or "spherical"))
    pose = libe57.StructureNode(imf)
    rot = libe57.StructureNode(imf)
    for key, value in zip("wxyz", scan.rotation):
        rot.set(key, libe57.FloatNode(imf, float(value)))
    tr = libe57.StructureNode(imf)
    for key, value in zip("xyz", scan.translation):
        tr.set(key, libe57.FloatNode(imf, float(value)))
    pose.set("rotation", rot)
    pose.set("translation", tr)
    node.set("pose", pose)
    if scan.intensity is not None:
        limits = libe57.StructureNode(imf)
        limits.set("intensityMinimum", libe57.FloatNode(imf, 0.0))
        limits.set("intensityMaximum", libe57.FloatNode(imf, 1.0))
        node.set("intensityLimits", limits)

    proto = libe57.StructureNode(imf)
    single = libe57.E57_DOUBLE
    proto.set("sphericalRange", libe57.FloatNode(imf, 0.0, single, 0.0, float(rng.max())))
    proto.set("sphericalAzimuth", libe57.FloatNode(imf, 0.0, single, -math.pi, math.pi))
    proto.set("sphericalElevation", libe57.FloatNode(imf, 0.0, single, -math.pi / 2, math.pi / 2))
    proto.set("sphericalInvalidState", libe57.IntegerNode(imf, 0, 0, 2))
    names = ["sphericalRange", "sphericalAzimuth", "sphericalElevation", "sphericalInvalidState"]
    arrays = {
        "sphericalRange": rng.astype("f8"), "sphericalAzimuth": az.astype("f8"),
        "sphericalElevation": el.astype("f8"), "sphericalInvalidState": invalid.astype("i1"),
    }
    if scan.intensity is not None:
        proto.set("intensity", libe57.FloatNode(imf, 0.0, libe57.E57_SINGLE, 0.0, 1.0))
        names.append("intensity")
        arrays["intensity"] = scan.intensity.astype("f4")
    codecs = libe57.VectorNode(imf, True)
    points = libe57.CompressedVectorNode(imf, proto, codecs)
    node.set("points", points)
    e57.data3d.append(node)

    buffers = libe57.VectorSourceDestBuffer()
    keep = []
    for name in names:
        arr = np.ascontiguousarray(arrays[name])
        keep.append(arr)
        buffers.append(libe57.SourceDestBuffer(imf, name, arr, len(arr), True, True))
    writer = points.writer(buffers)
    writer.write(len(local))
    writer.close()


def write_e57(path: Path, scans: List[Scan]) -> Path:
    import pye57

    with pye57.E57(str(path), mode="w") as e57:
        for i, scan in enumerate(scans):
            if scan.spherical:
                _write_spherical(e57, scan)
                continue
            e57.write_scan_raw(
                _cartesian_data(scan),
                name=scan.name or f"scan{i}",
                rotation=np.asarray(scan.rotation, dtype=float),
                translation=np.asarray(scan.translation, dtype=float),
            )
    return path
