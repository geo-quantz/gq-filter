"""The help text describes what the incidence and range filters really compute."""
import cli
from gq_filter.filter import FilterOptions, IncidenceAngleParams, build_pipeline


def _help(dest):
    return next(a.help for a in cli.build_parser()._actions if a.dest == dest)


def test_incidence_help_describes_normal_and_scanner_position():
    text = _help("incidence_angle_max")
    assert "normal" in text
    assert "scanner position" in text and "(0,0,0)" in text
    assert "uses absolute ScanAngleRank" not in text


def test_range_help_measures_from_the_same_scanner_position():
    for dest in ("range_min", "range_max"):
        text = _help(dest)
        assert "scanner position" in text and "(0,0,0)" in text


def test_incidence_filter_uses_normals_and_origin_not_scan_angle():
    stages = build_pipeline(
        "in.las", "out.las", FilterOptions(incidence=IncidenceAngleParams(max_angle=86.0))
    )["pipeline"]
    assert {"type": "filters.normal", "knn": 8} in stages
    (expr,) = [s["expression"] for s in stages if isinstance(s, dict)
               and s.get("type") == "filters.expression"]
    assert "NormalX" in expr and "(X - 0.0)" in expr
    assert "ScanAngleRank" not in expr
