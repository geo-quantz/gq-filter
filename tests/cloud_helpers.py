"""テスト用の小さな点群を作る・読むための補助関数。

点は numpy の乱数（シード固定）で作り、PDAL の writers.las で書き出す。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np

LAS_DTYPE = [
    ("X", "f8"),
    ("Y", "f8"),
    ("Z", "f8"),
    ("Intensity", "u2"),
    ("ReturnNumber", "u1"),
    ("NumberOfReturns", "u1"),
    ("Classification", "u1"),
]


def make_cloud(
        n: int,
        *,
        seed: int = 0,
        extent: float = 100.0,
        origin: Sequence[float] = (1000.0, 2000.0, 50.0),
        outliers: int = 0,
) -> np.ndarray:
    """平面に近い一様な点群（任意で外れ値つき）を作る。"""
    rng = np.random.default_rng(seed)
    arr = np.zeros(n + outliers, dtype=LAS_DTYPE)
    x0, y0, z0 = origin
    arr["X"][:n] = x0 + rng.random(n) * extent
    arr["Y"][:n] = y0 + rng.random(n) * extent
    arr["Z"][:n] = z0 + rng.normal(0.0, 0.05, n)
    if outliers:
        arr["X"][n:] = x0 + rng.random(outliers) * extent
        arr["Y"][n:] = y0 + rng.random(outliers) * extent
        arr["Z"][n:] = z0 + 5.0 + rng.random(outliers) * 20.0
    arr["Intensity"] = rng.integers(0, 1000, n + outliers)
    arr["ReturnNumber"] = 1
    arr["NumberOfReturns"] = 1
    arr["Classification"] = 1
    return arr


def write_las(path: Path, arr: np.ndarray) -> Path:
    import pdal

    writer = {
        "type": "writers.las",
        "filename": str(path),
        "scale_x": 0.001, "scale_y": 0.001, "scale_z": 0.001,
        "offset_x": "auto", "offset_y": "auto", "offset_z": "auto",
    }
    pdal.Pipeline(json.dumps([writer]), arrays=[arr]).execute()
    return path


def read_points(path: Path) -> np.ndarray:
    import pdal

    pipeline = pdal.Pipeline(json.dumps([str(path)]))
    pipeline.execute()
    return pipeline.arrays[0]


def sorted_xyz(arr: np.ndarray) -> np.ndarray:
    """点の順序に依らずに比べるため、XYZ を辞書順に並べた (N, 3) 配列を返す。"""
    xyz = np.column_stack([arr["X"], arr["Y"], arr["Z"]])
    order = np.lexsort((xyz[:, 2], xyz[:, 1], xyz[:, 0]))
    return xyz[order]
