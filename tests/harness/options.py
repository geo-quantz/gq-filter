"""
Scenario `filter_params` dict -> lib.filter.FilterOptions.

Shared by runner.py (gq-filter execution) and reference.py (PDAL CLI
reference) so both sides build options from a scenario in exactly one way.
"""
from __future__ import annotations

from lib.classify import GroundClassifyParams
from lib.filter import (
    DuplicateParams,
    FilterOptions,
    IncidenceAngleParams,
    IntensityParams,
    RangeParams,
)


def _has_any(fp: dict, *keys: str) -> bool:
    return any(fp.get(k) is not None for k in keys)


def _ground(fp: dict) -> GroundClassifyParams | None:
    g = fp.get("ground")
    if g is None:
        return None
    return GroundClassifyParams(**g)


def options_from_params(fp: dict) -> FilterOptions:
    """Build FilterOptions from a harness scenario's filter_params."""
    return FilterOptions(
        incidence=(
            IncidenceAngleParams(max_angle=fp["incidence_angle_max"])
            if fp.get("incidence_angle_max") is not None else None
        ),
        intensity=(
            IntensityParams(
                min_intensity=fp.get("intensity_min"),
                max_intensity=fp.get("intensity_max"),
            )
            if _has_any(fp, "intensity_min", "intensity_max") else None
        ),
        range_dist=(
            RangeParams(
                min_distance=fp.get("range_min"),
                max_distance=fp.get("range_max"),
            )
            if _has_any(fp, "range_min", "range_max") else None
        ),
        duplicate=DuplicateParams(enabled=True) if fp.get("duplicate") else None,
        ground=_ground(fp),
    )
