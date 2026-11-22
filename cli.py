import argparse
import json
import sys
from typing import List

from gq_filter.classify import add_ground_args, ground_params_from_args
from gq_filter.filter import (
    DuplicateParams,
    FilterOptions,
    IncidenceAngleParams,
    IntensityParams,
    RangeParams,
    RadiusSampleParams,
    HeightZParams,
    SpatialClipParams,
    ReturnNumberParams,
    ClassificationParams,
    RadiusOutlierParams,
    StatisticalOutlierParams,
    build_pipeline,
    execute_pipeline,
)


MCP_COMMAND = "mcp"


def build_parser() -> argparse.ArgumentParser:
    """The CLI's argument parser (shared with the MCP server's tool schema)."""
    parser = argparse.ArgumentParser(
        description=(
            "Execute a PDAL filter pipeline on a point cloud. "
            f"Run '{MCP_COMMAND}' as the only argument to start the MCP server on stdio."
        )
    )

    # Core IO arguments
    parser.add_argument(
        "--input", "-i", required=True, help="Path to the input point cloud file."
    )
    parser.add_argument(
        "--output", "-o", required=True, help="Path to the output point cloud file."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated PDAL pipeline dictionary without executing it.",
    )

    # Incidence Angle Filter Group
    incidence_group = parser.add_argument_group("Incidence Angle Filter")
    incidence_group.add_argument(
        "--incidence-angle-max",
        type=float,
        metavar="DEG",
        help=(
            "Maximum incidence angle in degrees: the angle between the surface normal "
            "(estimated from the 8 nearest points) and the line from the scanner position "
            "to the point. Points hit at a larger (more grazing) angle are removed. The "
            "scanner position is the scan pose in a structured E57 header, otherwise the "
            "coordinate origin (0,0,0). Not based on ScanAngleRank."
        ),
    )

    # Intensity Filter Group
    intensity_group = parser.add_argument_group("Intensity Filter")
    intensity_group.add_argument(
        "--intensity-min", type=float, help="Minimum intensity value."
    )
    intensity_group.add_argument(
        "--intensity-max", type=float, help="Maximum intensity value."
    )

    # Measurement Distance (Range) Filter Group
    range_group = parser.add_argument_group("Measurement Distance Filter")
    range_group.add_argument(
        "--range-min",
        type=float,
        help=(
            "Minimum distance from the scanner position (the scan pose in a structured E57 "
            "header, otherwise the coordinate origin (0,0,0))."
        ),
    )
    range_group.add_argument(
        "--range-max",
        type=float,
        help=(
            "Maximum distance from the scanner position (the scan pose in a structured E57 "
            "header, otherwise the coordinate origin (0,0,0))."
        ),
    )

    # Duplicate Filter Group
    duplicate_group = parser.add_argument_group("Duplicate Filter")
    duplicate_group.add_argument(
        "--deduplicate",
        action="store_true",
        help="Enable duplicate point removal (removes exact XYZ matches).",
    )

    add_ground_args(parser)
    # Radius-based Thinning Group
    sample_group = parser.add_argument_group("Radius-based Thinning")
    sample_group.add_argument(
        "--sample-radius",
        type=float,
        metavar="R",
        help="Minimum spacing between retained points (Poisson disk sampling).",
    )
    # Height (Z) Filter Group
    z_group = parser.add_argument_group("Height (Z) Filter")
    z_group.add_argument("--z-min", type=float, metavar="Z", help="Minimum absolute Z elevation to retain.")
    z_group.add_argument("--z-max", type=float, metavar="Z", help="Maximum absolute Z elevation to retain.")
    # Spatial Clip Group
    clip_group = parser.add_argument_group("Spatial Clip (Bounding Box)")
    clip_group.add_argument("--clip-xmin", type=float, metavar="X", help="Minimum X coordinate.")
    clip_group.add_argument("--clip-xmax", type=float, metavar="X", help="Maximum X coordinate.")
    clip_group.add_argument("--clip-ymin", type=float, metavar="Y", help="Minimum Y coordinate.")
    clip_group.add_argument("--clip-ymax", type=float, metavar="Y", help="Maximum Y coordinate.")
    clip_group.add_argument("--clip-zmin", type=float, metavar="Z", help="Minimum Z coordinate.")
    clip_group.add_argument("--clip-zmax", type=float, metavar="Z", help="Maximum Z coordinate.")
    # Return Number Filter Group
    ret_group = parser.add_argument_group("Return Number Filter")
    ret_group.add_argument(
        "--keep-returns",
        type=int,
        nargs="+",
        metavar="N",
        help=(
            "Retain only points with these LAS ReturnNumber values. "
            "e.g. --keep-returns 1  (first return only)"
        ),
    )
    # Classification Filter Group
    cls_group = parser.add_argument_group("Classification Filter")
    cls_group.add_argument(
        "--keep-classes",
        type=int,
        nargs="+",
        metavar="CODE",
        help=(
            "Retain only points with these ASPRS classification codes. "
            "Common codes: 2=Ground 3=LowVeg 4=MedVeg 5=HighVeg 6=Building "
            "7=Noise 9=Water 17=Bridge"
        ),
    )
    # Radius Outlier Removal Group
    ror_group = parser.add_argument_group("Radius Outlier Removal")
    ror_group.add_argument(
        "--outlier-radius",
        action="store_true",
        help="Enable radius-based outlier removal (filters.outlier method=radius).",
    )
    ror_group.add_argument(
        "--outlier-radius-value",
        type=float,
        default=1.0,
        metavar="R",
        help="Search radius for neighbor counting (default: 1.0).",
    )
    ror_group.add_argument(
        "--outlier-min-k",
        type=int,
        default=2,
        metavar="K",
        help="Minimum neighbors required within radius (default: 2).",
    )
    # Statistical Outlier Removal Group
    sor_group = parser.add_argument_group("Statistical Outlier Removal")
    sor_group.add_argument(
        "--outlier-statistical",
        action="store_true",
        help="Enable statistical outlier removal (filters.outlier method=statistical).",
    )
    sor_group.add_argument(
        "--outlier-mean-k",
        type=int,
        default=8,
        metavar="K",
        help="Number of nearest neighbors for outlier detection (default: 8).",
    )
    sor_group.add_argument(
        "--outlier-multiplier",
        type=float,
        default=2.0,
        metavar="M",
        help="Standard deviation multiplier threshold (default: 2.0).",
    )

    return parser


def parse_args(args: List[str]) -> argparse.Namespace:
    """Parses command-line arguments for the PDAL filter pipeline."""
    return build_parser().parse_args(args)


def assemble_config(args: argparse.Namespace) -> FilterOptions:
    """
    Maps CLI arguments to the structured FilterOptions configuration object.
    Filters are only enabled if relevant arguments are provided.
    """
    # Incidence filter: enabled if max angle is provided
    incidence = None
    if args.incidence_angle_max is not None:
        incidence = IncidenceAngleParams(max_angle=args.incidence_angle_max)

    # Intensity filter: enabled if either min or max intensity is provided
    intensity = None
    if args.intensity_min is not None or args.intensity_max is not None:
        intensity = IntensityParams(
            min_intensity=args.intensity_min, max_intensity=args.intensity_max
        )

    # Range filter: enabled if either min or max distance is provided
    range_dist = None
    if args.range_min is not None or args.range_max is not None:
        range_dist = RangeParams(
            min_distance=args.range_min, max_distance=args.range_max
        )

    # Duplicate filter: explicitly enabled via flag
    duplicate = None
    if args.deduplicate:
        duplicate = DuplicateParams(enabled=True)

    # Statistical outlier removal
    statistical_outlier = None
    if args.outlier_statistical:
        statistical_outlier = StatisticalOutlierParams(
            mean_k=args.outlier_mean_k,
            multiplier=args.outlier_multiplier,
        )

    # Radius outlier removal
    radius_outlier = None
    if args.outlier_radius:
        radius_outlier = RadiusOutlierParams(
            radius=args.outlier_radius_value,
            min_k=args.outlier_min_k,
        )

    # Classification filter
    classification = None
    if args.keep_classes:
        classification = ClassificationParams(keep_codes=list(args.keep_classes))

    # Return number filter
    return_number = None
    if args.keep_returns:
        return_number = ReturnNumberParams(keep_numbers=list(args.keep_returns))

    # Spatial clip
    spatial_clip = None
    clip_vals = [args.clip_xmin, args.clip_xmax, args.clip_ymin, args.clip_ymax, args.clip_zmin, args.clip_zmax]
    if any(v is not None for v in clip_vals):
        spatial_clip = SpatialClipParams(
            x_min=args.clip_xmin, x_max=args.clip_xmax,
            y_min=args.clip_ymin, y_max=args.clip_ymax,
            z_min=args.clip_zmin, z_max=args.clip_zmax,
        )

    # Height (Z) filter
    height_z = None
    if args.z_min is not None or args.z_max is not None:
        height_z = HeightZParams(z_min=args.z_min, z_max=args.z_max)

    # Radius-based thinning
    radius_sample = None
    if args.sample_radius is not None:
        radius_sample = RadiusSampleParams(radius=args.sample_radius)

    return FilterOptions(
        incidence=incidence,
        intensity=intensity,
        range_dist=range_dist,
        duplicate=duplicate,
        ground=ground_params_from_args(args),
        radius_sample=radius_sample,
        height_z=height_z,
        spatial_clip=spatial_clip,
        return_number=return_number,
        classification=classification,
        radius_outlier=radius_outlier,
        statistical_outlier=statistical_outlier,
    )


def _make_console_output_safe() -> None:
    """Replace characters the console cannot encode instead of crashing.

    Help text contains Japanese (the ground classification group title); a cp1252
    Windows console would otherwise raise UnicodeEncodeError and abort.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def _run_mcp(extra: List[str]) -> None:
    """`gqfilter mcp`: serve the filter tools over MCP on stdin/stdout."""
    if extra:
        print(
            f"usage: gqfilter {MCP_COMMAND}\n"
            "Starts the MCP server on stdin/stdout (no other arguments).",
            file=sys.stderr,
        )
        sys.exit(0 if extra in (["-h"], ["--help"]) else 2)
    from gqfilter_mcp.server import serve  # lazy: the normal CLI does not load mcp

    serve()


def main():
    """Main entry point for the CLI tool."""
    _make_console_output_safe()
    if sys.argv[1:2] == [MCP_COMMAND]:
        _run_mcp(sys.argv[2:])
        return
    try:
        args = parse_args(sys.argv[1:])
        filter_params = assemble_config(args)

        # Build the pipeline dictionary using the existing builder logic
        pipeline_dict = build_pipeline(args.input, args.output, filter_params)

        if args.dry_run:
            print("--- DRY RUN: Generated PDAL Pipeline ---")
            print(json.dumps(pipeline_dict, indent=4))
            return

        # Execute the pipeline using the existing executor logic
        print(f"Executing pipeline: {args.input} -> {args.output}")
        result = execute_pipeline(pipeline_dict)

        if result["success"]:
            print("Pipeline execution completed successfully.")
            print(f"Points processed: {result['points_processed']}")
            if "note" in result:
                print(f"Note: {result['note']}")
        else:
            print(f"Pipeline execution failed: {result['error']}", file=sys.stderr)
            if result.get("log"):
                print(f"Log: {result['log']}", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
