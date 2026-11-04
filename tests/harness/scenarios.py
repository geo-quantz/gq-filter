"""
Harness scenarios: what filter combinations to run against each fixture.

Each scenario is a dict with:
  name           str   — unique name within a fixture
  filter_params  dict  — passed to lib.filter and reference.build_reference_pipeline()
  description    str   — human-readable intent (not a pass/fail criterion)
"""
from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "name": "incidence_angle_15",
        "description": "Keep only near-nadir returns (|ScanAngleRank| ≤ 15°)",
        "filter_params": {
            "incidence_angle_max": 15.0,
        },
    },
    {
        "name": "intensity_10_500",
        "description": "Keep returns with intensity in [10, 500]",
        "filter_params": {
            "intensity_min": 10.0,
            "intensity_max": 500.0,
        },
    },
    {
        "name": "duplicate_removal",
        "description": "Remove exact duplicate XYZ points",
        "filter_params": {
            "duplicate": True,
        },
    },
    {
        "name": "combined_all",
        "description": "All filters applied together (incidence + intensity + duplicate)",
        "filter_params": {
            "incidence_angle_max": 15.0,
            "intensity_min": 10.0,
            "intensity_max": 500.0,
            "duplicate": True,
        },
    },
    # --- Ground classification (Issue #3) ---
    # autzen-small is in feet and ~95% of it is one building roof (Class 6).
    # Only 385 pts are labelled ground (Class 2) in the source data.
    {
        "name": "ground_csf_classify",
        "description": "CSF ground classification only (no points dropped; Classification rewritten)",
        "filter_params": {
            "ground": {"method": "csf", "cell": 3.0, "threshold": 1.5},
        },
    },
    {
        "name": "ground_csf_only",
        "description": "CSF tuned for feet (resolution 3ft, threshold 1.5ft), keep ground only",
        "filter_params": {
            "ground": {"method": "csf", "cell": 3.0, "threshold": 1.5, "ground_only": True},
        },
    },
    {
        "name": "ground_smrf_only",
        "description": "SMRF with PDAL defaults, keep ground only (known to misclassify the roof)",
        "filter_params": {
            "ground": {"method": "smrf", "ground_only": True},
        },
    },
    {
        "name": "ground_pmf_only",
        "description": "PMF with max_window_size 200ft, keep ground only",
        "filter_params": {
            "ground": {"method": "pmf", "window": 200.0, "ground_only": True},
        },
    },
]
