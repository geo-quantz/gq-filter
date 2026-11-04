"""Ground (地表) classification using PDAL's built-in filters (Issue #3).

Supported methods (all bundled with standard PDAL builds):

- ``smrf``: Simple Morphological Filter (Pingel et al., 2013)
- ``pmf``:  Progressive Morphological Filter (Zhang et al., 2003)
- ``csf``:  Cloth Simulation Filter (Zhang et al., 2016)

Each filter writes Classification=2 (ground) or 1 (non-ground) for the points
it considers (by default PDAL looks at last/only returns). Unlike the other
gq-filter stages this does not drop points unless ``ground_only`` is set.

Length parameters are in the units of the input CRS (metres for most survey
data, feet for the autzen-small fixture). ``None`` means "use PDAL default".
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

GROUND_CLASS = 2
GROUND_METHODS = ("smrf", "pmf", "csf")

# Generic option name -> PDAL option name, per method.
# A method missing a key does not support that option.
_PDAL_OPTION_NAMES: Dict[str, Dict[str, str]] = {
    "smrf": {"cell": "cell", "window": "window", "threshold": "threshold"},
    "pmf": {"cell": "cell_size", "window": "max_window_size", "threshold": "max_distance"},
    "csf": {"cell": "resolution", "threshold": "threshold"},
}

_TUNING_FIELDS = ("cell", "window", "threshold")


@dataclass(frozen=True)
class GroundClassifyParams:
    """Parameters for ground classification.

    Attributes:
        method:        One of GROUND_METHODS.
        ground_only:   Keep only ground points (Classification == 2) in output.
        cell:          Grid size (smrf: cell, pmf: cell_size, csf: resolution).
        window:        Max window size (smrf: window, pmf: max_window_size).
                       Not used by csf.
        threshold:     Height threshold for ground (smrf: threshold,
                       pmf: max_distance, csf: threshold).
        reset_classes: Reset existing Classification to 0 before classifying,
                       so pre-existing classes (e.g. building=6) do not leak
                       into the result for points the filter ignores.
    """
    method: str
    ground_only: bool = False
    cell: Optional[float] = None
    window: Optional[float] = None
    threshold: Optional[float] = None
    reset_classes: bool = True

    def __post_init__(self) -> None:
        if self.method not in GROUND_METHODS:
            raise ValueError(
                f"Unknown ground classification method {self.method!r}; "
                f"choose from {', '.join(GROUND_METHODS)}"
            )
        supported = _PDAL_OPTION_NAMES[self.method]
        for name in _TUNING_FIELDS:
            value = getattr(self, name)
            if value is None:
                continue
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Ground classification {name} must be a positive number (got {value})")
            if name not in supported:
                raise ValueError(f"Ground classification method {self.method!r} does not use {name}")
        if self.cell is not None and self.window is not None and self.window < self.cell:
            raise ValueError(
                f"Ground classification window ({self.window}) must be >= cell ({self.cell})"
            )


def build_ground_classify_stages(
        params: Optional[GroundClassifyParams],
) -> List[Dict[str, Any]]:
    """Build PDAL stages for ground classification. Empty list when disabled."""
    if params is None:
        return []

    stages: List[Dict[str, Any]] = []
    if params.reset_classes:
        stages.append({"type": "filters.assign", "value": "Classification = 0"})

    classify_stage: Dict[str, Any] = {"type": f"filters.{params.method}"}
    for name, pdal_name in _PDAL_OPTION_NAMES[params.method].items():
        value = getattr(params, name)
        if value is not None:
            classify_stage[pdal_name] = value
    stages.append(classify_stage)

    if params.ground_only:
        stages.append({
            "type": "filters.expression",
            "expression": f"Classification == {GROUND_CLASS}",
        })
    return stages


# -------------------------------
# CLI helpers (shared by main.py and cli.py)
# -------------------------------

def add_ground_args(parser: argparse.ArgumentParser) -> None:
    """Register the ground classification argument group on a parser."""
    group = parser.add_argument_group("Ground Classification (地表分類)")
    group.add_argument(
        "--ground-classify",
        choices=GROUND_METHODS,
        help="Classify ground points (Class 2) with PDAL smrf / pmf / csf.",
    )
    group.add_argument(
        "--ground-only",
        action="store_true",
        help=(
            "Keep only ground points (Class 2) after classification. PDAL only "
            "classifies last/only returns, so other returns are dropped too."
        ),
    )
    group.add_argument(
        "--ground-cell", type=float, metavar="C",
        help="Grid size in CRS units (smrf cell / pmf cell_size / csf resolution).",
    )
    group.add_argument(
        "--ground-window", type=float, metavar="W",
        help="Max window size in CRS units (smrf / pmf only). Should exceed the largest building.",
    )
    group.add_argument(
        "--ground-threshold", type=float, metavar="T",
        help="Height threshold for ground in CRS units (smrf threshold / pmf max_distance / csf threshold).",
    )
    group.add_argument(
        "--ground-keep-classes",
        action="store_true",
        help="Do not reset existing Classification values before classifying.",
    )


def ground_params_from_args(args: argparse.Namespace) -> Optional[GroundClassifyParams]:
    """Map parsed CLI args to GroundClassifyParams (None when not requested)."""
    if args.ground_classify is None:
        tuning_given = any(
            getattr(args, f"ground_{name}") is not None for name in _TUNING_FIELDS
        ) or args.ground_only or args.ground_keep_classes
        if tuning_given:
            raise ValueError("--ground-* options require --ground-classify {smrf,pmf,csf}")
        return None

    return GroundClassifyParams(
        method=args.ground_classify,
        ground_only=args.ground_only,
        cell=args.ground_cell,
        window=args.ground_window,
        threshold=args.ground_threshold,
        reset_classes=not args.ground_keep_classes,
    )
