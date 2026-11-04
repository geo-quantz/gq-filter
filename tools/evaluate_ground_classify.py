"""
Evaluate ground classification (Issue #3) against the fixture's own labels.

The source Classification of each point is copied to an extra dimension
(filters.ferry) before gq-filter's ground stages run, so predicted ground
(Class 2) can be compared point-by-point with the original Class 2.

Uses the PDAL CLI only (no Python bindings), like the regression harness.

Usage:
    uv run python tools/evaluate_ground_classify.py
    uv run python tools/evaluate_ground_classify.py --input path/to/labelled.laz
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.classify import GROUND_CLASS, GroundClassifyParams, build_ground_classify_stages  # noqa: E402

DEFAULT_INPUT = REPO_ROOT / "tests" / "fixtures" / "autzen-small.laz"

# Settings reported in README (fixture units are feet).
CASES: list[tuple[str, GroundClassifyParams]] = [
    ("smrf (PDAL default)", GroundClassifyParams(method="smrf")),
    ("smrf window=200ft", GroundClassifyParams(method="smrf", window=200.0)),
    ("pmf (PDAL default)", GroundClassifyParams(method="pmf")),
    ("pmf window=200ft", GroundClassifyParams(method="pmf", window=200.0)),
    ("csf (PDAL default)", GroundClassifyParams(method="csf")),
    ("csf res=3ft thr=1.5ft", GroundClassifyParams(method="csf", cell=3.0, threshold=1.5)),
]


@dataclass(frozen=True)
class Score:
    predicted_ground: int
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        return self.true_positive / max(1, self.true_positive + self.false_positive)

    @property
    def recall(self) -> float:
        return self.true_positive / max(1, self.true_positive + self.false_negative)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _run(input_path: Path, params: GroundClassifyParams, workdir: Path) -> Score:
    out_csv = workdir / "classified.csv"
    pipeline = {
        "pipeline": [
            str(input_path),
            {"type": "filters.ferry", "dimensions": "Classification => SourceClass"},
            *build_ground_classify_stages(params),
            {
                "type": "writers.text",
                "filename": str(out_csv),
                "order": "SourceClass,Classification",
                "keep_unspecified": False,
            },
        ]
    }
    pipeline_file = workdir / "pipeline.json"
    pipeline_file.write_text(json.dumps(pipeline))
    r = subprocess.run(["pdal", "pipeline", str(pipeline_file)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"PDAL failed: {r.stderr.strip()}")

    tp = fp = fn = predicted = 0
    with out_csv.open() as f:
        for row in csv.DictReader(f):
            truth = int(float(row["SourceClass"])) == GROUND_CLASS
            pred = int(float(row["Classification"])) == GROUND_CLASS
            predicted += pred
            tp += truth and pred
            fp += pred and not truth
            fn += truth and not pred
    return Score(predicted, tp, fp, fn)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="Labelled LAS/LAZ (Class 2 = ground). Default: autzen-small fixture.")
    args = parser.parse_args()
    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 1

    print("| 方式 | 設定 | 地表判定点数 | 適合率 | 再現率 | F1 |")
    print("| :--- | :--- | ---: | ---: | ---: | ---: |")
    with tempfile.TemporaryDirectory(prefix="gq-ground-") as tmp:
        for label, params in CASES:
            s = _run(args.input, params, Path(tmp))
            method, _, setting = label.partition(" ")
            print(f"| {method} | {setting} | {s.predicted_ground:,} | "
                  f"{s.precision:.3f} | {s.recall:.3f} | {s.f1:.3f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
