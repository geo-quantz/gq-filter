import json
import os

from gq_filter.filter import (
    FilterOptions,
    IncidenceAngleParams,
    IntensityParams,
    RangeParams,
    DuplicateParams,
    StatisticalOutlierParams,
    RadiusOutlierParams,
    ClassificationParams,
    ReturnNumberParams,
    SpatialClipParams,
    HeightZParams,
    RadiusSampleParams,
    build_pipeline,
    build_radius_sample_filter,
    build_height_z_filter,
    build_spatial_clip_filter,
    build_return_number_filter,
    build_classification_filter,
    build_radius_outlier_filter,
    build_statistical_outlier_filter,
    execute_pipeline,
)


def test_pipeline_builder():
    print("Testing pipeline builder...")

    # 1. Define parameters
    options = FilterOptions(
        incidence=IncidenceAngleParams(max_angle=15.0),
        intensity=IntensityParams(min_intensity=10, max_intensity=500),
        range_dist=RangeParams(min_distance=0.5, max_distance=100.0),
        duplicate=DuplicateParams(),
    )

    # 2. Build pipeline
    input_file = "../clamped.las"
    output_file = "test_output.las"

    # Ensure input file exists or use a dummy for structure test
    if not os.path.exists(input_file):
        print(
            f"Warning: {input_file} not found. Test will only verify dictionary structure."
        )
        # Create a tiny valid LAS if possible? Better to just check dict.

    pipeline_dict = build_pipeline(input_file, output_file, options)

    print("Generated Pipeline Dictionary:")
    print(json.dumps(pipeline_dict, indent=2))

    # Verify structure
    assert "pipeline" in pipeline_dict
    stage_types = [
        s if isinstance(s, str) else s["type"] for s in pipeline_dict["pipeline"]
    ]
    assert stage_types == [
        input_file,
        "filters.expression",  # range
        "filters.normal",  # incidence: normals
        "filters.expression",  # incidence: angle
        "filters.expression",  # intensity
        "filters.label_duplicates",  # duplicate: label
        "filters.expression",  # duplicate: drop
        "writers.las",
    ]
    # input + range(1) + normal(1) + incidence(1) + intensity(1) + duplicate(2) + writer(1) = 8
    assert len(pipeline_dict["pipeline"]) == 8
    # input + range(1) + normal(1) + incidence(1) + intensity(1) + duplicate(2) + writer(1) = 8
    assert len(pipeline_dict["pipeline"]) == 8
    assert pipeline_dict["pipeline"][0] == input_file
    assert pipeline_dict["pipeline"][-1]["type"] == "writers.las"

    print("Pipeline dictionary structure verified.")

    # 3. Test execution (only if file exists)
    if os.path.exists(input_file):
        print("\nExecuting pipeline...")
        result = execute_pipeline(pipeline_dict)
        if result["success"]:
            print(f"Success! Processed {result['points_processed']} points.")
            if os.path.exists(output_file):
                print(f"Output file {output_file} created.")
                os.remove(output_file)
        else:
            print(f"Pipeline execution failed: {result['error']}")
            # It might fail if 'Distance' dimension is missing, which is expected for standard LAS.
            if "Distance" in result["error"]:
                print("Note: 'Distance' dimension failure is expected if not in LAS.")


def test_statistical_outlier_filter_disabled():
    result = build_statistical_outlier_filter(None)
    assert result is None

    result = build_statistical_outlier_filter(StatisticalOutlierParams(enabled=False))
    assert result is None


def test_statistical_outlier_filter_defaults():
    params = StatisticalOutlierParams()
    stages = build_statistical_outlier_filter(params)
    # Returns a two-stage list: [filters.outlier, filters.expression]
    assert stages is not None
    assert isinstance(stages, list)
    assert len(stages) == 2
    outlier_stage = stages[0]
    assert outlier_stage["type"] == "filters.outlier"
    assert outlier_stage["method"] == "statistical"
    assert outlier_stage["mean_k"] == 8
    assert outlier_stage["multiplier"] == 2.0
    expression_stage = stages[1]
    assert expression_stage["type"] == "filters.expression"
    assert "Classification != 7" in expression_stage["expression"]


def test_statistical_outlier_filter_custom():
    params = StatisticalOutlierParams(mean_k=16, multiplier=1.5)
    stages = build_statistical_outlier_filter(params)
    assert isinstance(stages, list)
    assert stages[0]["mean_k"] == 16
    assert stages[0]["multiplier"] == 1.5
    # Expression stage must still be present
    assert stages[1]["type"] == "filters.expression"


def test_pipeline_includes_statistical_outlier():
    options = FilterOptions(
        statistical_outlier=StatisticalOutlierParams(mean_k=8, multiplier=2.0),
    )
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    types = [s.get("type") for s in stages if isinstance(s, dict)]
    # Both the marker stage and the removal expression must be present
    assert "filters.outlier" in types
    assert "filters.expression" in types
    outlier_stage = next(s for s in stages if isinstance(s, dict) and s.get("type") == "filters.outlier")
    assert outlier_stage["method"] == "statistical"
    expr_stage = next(s for s in stages if isinstance(s, dict) and s.get("type") == "filters.expression")
    assert "Classification != 7" in expr_stage["expression"]


def test_radius_outlier_filter_disabled():
    assert build_radius_outlier_filter(None) is None
    assert build_radius_outlier_filter(RadiusOutlierParams(enabled=False)) is None


def test_radius_outlier_filter_defaults():
    stages = build_radius_outlier_filter(RadiusOutlierParams())
    # Returns a two-stage list: [filters.outlier, filters.expression]
    assert stages is not None
    assert isinstance(stages, list)
    assert len(stages) == 2
    outlier_stage = stages[0]
    assert outlier_stage["type"] == "filters.outlier"
    assert outlier_stage["method"] == "radius"
    assert outlier_stage["radius"] == 1.0
    assert outlier_stage["min_k"] == 2
    expression_stage = stages[1]
    assert expression_stage["type"] == "filters.expression"
    assert "Classification != 7" in expression_stage["expression"]


def test_radius_outlier_filter_custom():
    stages = build_radius_outlier_filter(RadiusOutlierParams(radius=0.5, min_k=5))
    assert isinstance(stages, list)
    assert stages[0]["radius"] == 0.5
    assert stages[0]["min_k"] == 5
    # Expression stage must still be present
    assert stages[1]["type"] == "filters.expression"


def test_pipeline_includes_radius_outlier():
    options = FilterOptions(
        radius_outlier=RadiusOutlierParams(radius=0.5, min_k=3),
    )
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    types = [s.get("type") for s in stages if isinstance(s, dict)]
    # Both the marker stage and the removal expression must be present
    assert "filters.outlier" in types
    assert "filters.expression" in types
    outlier_stage = next(s for s in stages if isinstance(s, dict) and s.get("type") == "filters.outlier")
    assert outlier_stage["method"] == "radius"
    expr_stage = next(s for s in stages if isinstance(s, dict) and s.get("type") == "filters.expression")
    assert "Classification != 7" in expr_stage["expression"]


def test_classification_filter_disabled():
    assert build_classification_filter(None) is None
    assert build_classification_filter(ClassificationParams(enabled=False)) is None
    assert build_classification_filter(ClassificationParams(keep_codes=[])) is None


def test_classification_filter_single_code():
    stage = build_classification_filter(ClassificationParams(keep_codes=[2]))
    assert stage is not None
    assert stage["type"] == "filters.expression"
    assert "Classification == 2" in stage["expression"]


def test_classification_filter_multiple_codes():
    stage = build_classification_filter(ClassificationParams(keep_codes=[2, 6, 9]))
    assert stage is not None
    expr = stage["expression"]
    assert "Classification == 2" in expr
    assert "Classification == 6" in expr
    assert "Classification == 9" in expr
    assert "||" in expr


def test_pipeline_includes_classification():
    options = FilterOptions(
        classification=ClassificationParams(keep_codes=[2, 6]),
    )
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    expr_stages = [s for s in stages if isinstance(s, dict) and s.get("type") == "filters.expression"]
    assert any("Classification" in s.get("expression", "") for s in expr_stages)


def test_return_number_filter_disabled():
    assert build_return_number_filter(None) is None
    assert build_return_number_filter(ReturnNumberParams(enabled=False)) is None
    assert build_return_number_filter(ReturnNumberParams(keep_numbers=[])) is None


def test_return_number_filter_first_return():
    stage = build_return_number_filter(ReturnNumberParams(keep_numbers=[1]))
    assert stage is not None
    assert stage["type"] == "filters.expression"
    assert "ReturnNumber == 1" in stage["expression"]


def test_return_number_filter_multiple():
    stage = build_return_number_filter(ReturnNumberParams(keep_numbers=[1, 2]))
    assert stage is not None
    expr = stage["expression"]
    assert "ReturnNumber == 1" in expr
    assert "ReturnNumber == 2" in expr
    assert "||" in expr


def test_pipeline_includes_return_number():
    options = FilterOptions(
        return_number=ReturnNumberParams(keep_numbers=[1]),
    )
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    expr_stages = [s for s in stages if isinstance(s, dict) and s.get("type") == "filters.expression"]
    assert any("ReturnNumber" in s.get("expression", "") for s in expr_stages)


def test_spatial_clip_disabled():
    assert build_spatial_clip_filter(None) is None
    assert build_spatial_clip_filter(SpatialClipParams(enabled=False)) is None
    assert build_spatial_clip_filter(SpatialClipParams()) is None


def test_spatial_clip_xy_only():
    params = SpatialClipParams(x_min=100.0, x_max=200.0, y_min=50.0, y_max=150.0)
    stage = build_spatial_clip_filter(params)
    assert stage == {"type": "filters.range", "limits": "X[100.0:200.0],Y[50.0:150.0]"}


def test_spatial_clip_with_z():
    params = SpatialClipParams(x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0, z_min=1.0, z_max=5.0)
    stage = build_spatial_clip_filter(params)
    assert stage == {"type": "filters.range", "limits": "X[0.0:10.0],Y[0.0:10.0],Z[1.0:5.0]"}


def test_spatial_clip_partial_bounds():
    """Unset sides stay open (no ±inf, which PDAL rejects); unset axes are left out."""
    params = SpatialClipParams(x_min=100.0, y_max=200.0)
    stage = build_spatial_clip_filter(params)
    assert stage == {"type": "filters.range", "limits": "X[100.0:],Y[:200.0]"}
    assert "inf" not in stage["limits"]


def test_spatial_clip_z_only():
    stage = build_spatial_clip_filter(SpatialClipParams(z_max=0.0))
    assert stage == {"type": "filters.range", "limits": "Z[:0.0]"}


def test_pipeline_includes_spatial_clip():
    options = FilterOptions(
        spatial_clip=SpatialClipParams(x_min=0.0, x_max=100.0, y_min=0.0, y_max=100.0),
    )
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    types = [s.get("type") for s in stages if isinstance(s, dict)]
    assert "filters.range" in types


def test_height_z_filter_disabled():
    assert build_height_z_filter(None) is None
    assert build_height_z_filter(HeightZParams(enabled=False)) is None
    assert build_height_z_filter(HeightZParams()) is None


def test_height_z_filter_min_only():
    stage = build_height_z_filter(HeightZParams(z_min=-5.0))
    assert stage is not None
    assert stage["type"] == "filters.expression"
    assert "Z >= -5.0" in stage["expression"]
    assert "Z <=" not in stage["expression"]


def test_height_z_filter_max_only():
    stage = build_height_z_filter(HeightZParams(z_max=100.0))
    assert stage is not None
    assert "Z <= 100.0" in stage["expression"]
    assert "Z >=" not in stage["expression"]


def test_height_z_filter_range():
    stage = build_height_z_filter(HeightZParams(z_min=0.0, z_max=50.0))
    assert stage is not None
    expr = stage["expression"]
    assert "Z >= 0.0" in expr
    assert "Z <= 50.0" in expr
    assert "&&" in expr


def test_pipeline_includes_height_z():
    options = FilterOptions(height_z=HeightZParams(z_min=-2.0, z_max=200.0))
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    expr_stages = [s for s in stages if isinstance(s, dict) and s.get("type") == "filters.expression"]
    assert any("Z >=" in s.get("expression", "") for s in expr_stages)


def test_radius_sample_filter_disabled():
    assert build_radius_sample_filter(None) is None
    assert build_radius_sample_filter(RadiusSampleParams(enabled=False)) is None


def test_radius_sample_filter_defaults():
    stage = build_radius_sample_filter(RadiusSampleParams())
    assert stage is not None
    assert stage["type"] == "filters.sample"
    assert stage["radius"] == 0.05


def test_radius_sample_filter_custom():
    stage = build_radius_sample_filter(RadiusSampleParams(radius=0.1))
    assert stage["radius"] == 0.1


def test_pipeline_includes_radius_sample():
    options = FilterOptions(radius_sample=RadiusSampleParams(radius=0.05))
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    types = [s.get("type") for s in stages if isinstance(s, dict)]
    assert "filters.sample" in types


def test_pipeline_voxel_and_radius_sample_coexist():
    from gq_filter.filter import VoxelParams
    options = FilterOptions(
        voxel=VoxelParams(cell_size=0.01),
        radius_sample=RadiusSampleParams(radius=0.05),
    )
    pipeline = build_pipeline("input.las", "output.las", options)
    stages = pipeline["pipeline"]
    types = [s.get("type") for s in stages if isinstance(s, dict)]
    assert "filters.voxelcentroidnearestneighbor" in types
    assert "filters.sample" in types


if __name__ == "__main__":
    test_pipeline_builder()


def test_statistical_outlier_runs_before_ground_classification():
    """Noise must be removed before ground classification (see build_pipeline)."""
    from gq_filter.classify import GroundClassifyParams
    from gq_filter.filter import StatisticalOutlierParams

    options = FilterOptions(
        statistical_outlier=StatisticalOutlierParams(),
        ground=GroundClassifyParams(method="smrf"),
    )
    types = [s["type"] for s in build_pipeline("a.las", "b.las", options)["pipeline"][1:]]
    assert types.index("filters.outlier") < types.index("filters.smrf")


def test_radius_outlier_runs_before_ground_classification():
    """Noise must be removed before ground classification (see build_pipeline)."""
    from gq_filter.classify import GroundClassifyParams
    from gq_filter.filter import RadiusOutlierParams

    options = FilterOptions(
        radius_outlier=RadiusOutlierParams(),
        ground=GroundClassifyParams(method="smrf"),
    )
    types = [s["type"] for s in build_pipeline("a.las", "b.las", options)["pipeline"][1:]]
    assert types.index("filters.outlier") < types.index("filters.smrf")
