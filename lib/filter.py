import json
from dataclasses import dataclass
from typing import Optional, Dict, Any


# -------------------------------
# Parameter definitions
# -------------------------------

class FilterType:
    EXPRESSION = "filters.expression"
    UNIQUE = "filters.unique"
    DUPLICATE = "filters.duplicate"


@dataclass
class IncidenceAngleParams:
    """Parameters for incidence angle filter.
    Many datasets approximate incidence using the LAS 'ScanAngleRank' dimension.
    """

    max_angle: float
    enabled: bool = True


@dataclass
class IntensityParams:
    """Parameters for intensity (return intensity) filter."""

    min_intensity: Optional[float] = None
    max_intensity: Optional[float] = None
    enabled: bool = True


@dataclass
class RangeParams:
    """Parameters for measurement distance (range) filter.
    If the dataset does not provide a dedicated 'Distance' dimension, we compute
    Euclidean distance from origin using an expression: sqrt(X^2 + Y^2 + Z^2).
    """

    min_distance: Optional[float] = None
    max_distance: Optional[float] = None
    enabled: bool = True


@dataclass
class DuplicateParams:
    """Parameters for duplicate point filter.
    Uses PDAL's 'filters.unique,' which removes duplicate points (same XYZ).
    """

    enabled: bool = True


@dataclass
class FilterOptions:
    """Container for all filter parameters, used by the pipeline builder."""

    incidence: Optional[IncidenceAngleParams] = None
    intensity: Optional[IntensityParams] = None
    range_dist: Optional[RangeParams] = None
    duplicate: Optional[DuplicateParams] = None


# -------------------------------
# Filter builders (composable, data-driven)
# -------------------------------


def build_incidence_angle_filter(
        params: Optional[IncidenceAngleParams],
) -> Optional[Dict[str, Any]]:
    """Conditionally build incidence angle expression filter.
    - Disabled or missing params -> None
    - Otherwise, returns a PDAL 'filters.expression' stage.
    """
    if not params or not params.enabled:
        return None

    # Use absolute value to keep points within +/- max_angle from nadir.
    return {
        "type": FilterType.EXPRESSION,
        "expression": f"abs(ScanAngleRank) <= {params.max_angle}",
    }


def build_intensity_filter(
        params: Optional[IntensityParams],
) -> Optional[Dict[str, Any]]:
    """Conditionally build intensity range expression filter."""
    if not params or not params.enabled:
        return None

    expressions = []
    if params.min_intensity is not None:
        expressions.append(f"Intensity >= {params.min_intensity}")
    if params.max_intensity is not None:
        expressions.append(f"Intensity <= {params.max_intensity}")

    if not expressions:
        return None

    return {"type": FilterType.EXPRESSION, "expression": " && ".join(expressions)}


def build_range_filter(params: Optional[RangeParams]) -> Optional[Dict[str, Any]]:
    """Conditionally build measurement distance filter.
    We avoid assuming a specific distance dimension and instead use an
    expression over coordinates. This keeps behavior deterministic across
    inputs that may not have a 'Distance' or 'Range' dimension.
    """
    if not params or not params.enabled:
        return None

    conds = []
    # Compute Euclidean distance from the origin in the expression.
    dist_expr = "sqrt(X*X + Y*Y + Z*Z)"
    if params.min_distance is not None:
        conds.append(f"{dist_expr} >= {params.min_distance}")
    if params.max_distance is not None:
        conds.append(f"{dist_expr} <= {params.max_distance}")

    if not conds:
        return None

    return {"type": FilterType.EXPRESSION, "expression": " && ".join(conds)}


def build_duplicate_filter(
        params: Optional[DuplicateParams],
) -> Optional[Dict[str, Any]]:
    """Conditionally build duplicate removal filter using 'filters.unique'.
    'filters.unique' removes points sharing the same XYZ (exact duplicates).
    This avoids relying on optional plugins like 'filters.duplicate'.
    """
    if not params or not params.enabled:
        return None

    return {"type": FilterType.UNIQUE, "keep_first": True}


# -------------------------------
# Pipeline assembly & execution
# -------------------------------


def build_pipeline(
        input_path: str, output_path: str, filter_params: FilterOptions
) -> Dict[str, Any]:
    """Assemble a PDAL pipeline dictionary from enabled filters.
    - Starts with the reader (input path string)
    - Adds enabled filters in a stable, predefined order
    - Ends with a writer stage selected by output extension
    """
    stages = [input_path]

    # Ordered list of (builder, params) ensures predictable order.
    filter_builders = [
        (build_incidence_angle_filter, filter_params.incidence),
        (build_intensity_filter, filter_params.intensity),
        (build_range_filter, filter_params.range_dist),
        (build_duplicate_filter, filter_params.duplicate),
    ]

    for builder, p in filter_builders:
        f = builder(p)
        if f is not None:
            stages.append(f)

    # Choose a writer type based on a file extension
    writer_type = "writers.las"
    if output_path.endswith(".copc.laz"):
        writer_type = "writers.copc"
    elif output_path.endswith(".txt") or output_path.endswith(".csv"):
        writer_type = "writers.text"
    # PDAL's writers.las can write LAZ if LASzip support is built in.

    stages.append({"type": writer_type, "filename": output_path})

    return {"pipeline": stages}


def execute_pipeline(pipeline_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a PDAL pipeline from a Python dictionary.
    Returns a result dict with success flag, point count, and metadata/logs.

    Robustness: If the PDAL build lacks certain optional filters (e.g.,
    'filters.unique' or 'filters.duplicate'), we retry once with those
    stages removed to keep execution deterministic in plugin-light envs.
    """

    import pdal  # deferred: loading PDAL dylibs is expensive; keep out of --help path

    def _run(pdict: Dict[str, Any]) -> Dict[str, Any]:
        pl = pdal.Pipeline(json.dumps(pdict))
        pc = pl.execute()
        # PDAL may return metadata as a JSON string or a Python dict depending on version
        raw_md = pl.metadata
        md = json.loads(raw_md) if isinstance(raw_md, str) else raw_md
        return {
            "success": True,
            "points_processed": pc,
            "metadata": md,
            "log": pl.log,
        }

    try:
        return _run(pipeline_dict)
    except Exception as e:
        msg = str(e)
        # Detect missing plugin for duplicate/unique filters and retry without them
        if FilterType.UNIQUE in msg or FilterType.DUPLICATE in msg:
            # Remove any duplicate/unique stages and retry once
            stages = [
                s
                for s in pipeline_dict.get("pipeline", [])
                if not (
                        isinstance(s, dict)
                        and s.get("type") in {FilterType.UNIQUE, FilterType.DUPLICATE}
                )
            ]
            retry_dict = {"pipeline": stages}
            try:
                result = _run(retry_dict)
                result["note"] = (
                    "Duplicate/unique filter unavailable; executed without it."
                )
                return result
            except Exception as e2:
                return {"success": False, "error": str(e2), "log": ""}
        # Generic failure path
        return {"success": False, "error": msg, "log": ""}
