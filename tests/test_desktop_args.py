"""The desktop app builds gqfilter command lines; check them against cli.py.

apps/desktop/src/args/cases.json is shared with the app's own tests
(form -> argv). Here each argv is parsed by the real CLI parser, so an option
name or value format the CLI does not accept fails this test.
"""
import dataclasses
import json
from pathlib import Path

import pytest

import cli

CASES_FILE = Path(__file__).resolve().parent.parent / "apps" / "desktop" / "src" / "args" / "cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
OPTION_FIELDS = [f.name for f in dataclasses.fields(cli.FilterOptions)]


def _options_for(argv):
    args = cli.parse_args(["--input", "in.las", "--output", "out.las", *argv])
    return cli.assemble_config(args)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_app_argv_is_accepted_by_cli(case):
    options = _options_for(case["argv"])
    expect = case["expect"]

    for field in OPTION_FIELDS:
        value = getattr(options, field)
        if field not in expect:
            assert value is None, f"{field} should not be enabled by {case['argv']}"
            continue
        assert value is not None, f"{field} should be enabled by {case['argv']}"
        for attr, wanted in expect[field].items():
            got = getattr(value, attr)
            if isinstance(wanted, float):
                assert got == pytest.approx(wanted), attr
            else:
                assert got == wanted, attr


def test_every_cli_option_is_covered_by_a_case(monkeypatch):
    """A new CLI option should show up here so the app can be updated."""
    parser_flags = set()
    original = cli.argparse.ArgumentParser.parse_args

    def capture(self, args=None, namespace=None):
        for action in self._actions:
            parser_flags.update(s for s in action.option_strings if s.startswith("--"))
        return original(self, args, namespace)

    monkeypatch.setattr(cli.argparse.ArgumentParser, "parse_args", capture)
    cli.parse_args(["--input", "a.las", "--output", "b.las"])

    used = {token for case in CASES for token in case["argv"] if token.startswith("--")}
    not_for_the_app = {"--help", "--input", "--output", "--dry-run"}
    assert parser_flags - not_for_the_app == used
