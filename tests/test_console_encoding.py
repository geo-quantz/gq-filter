"""--help must not crash on consoles that cannot encode non-ASCII text.

Reproduces the v1.1.0 Windows build failure ('charmap' codec can't encode),
caused by the "Ground Classification (地表分類)" help group on a cp1252 console.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entry", ["cli.py", "main.py"])
@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_help_survives_narrow_console_encoding(entry, encoding):
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    result = subprocess.run(
        [sys.executable, str(ROOT / entry), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"Ground Classification" in result.stdout
