"""Subprocess observations of the current four-file publication boundary."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from gridwise.cli import EXPORT_NAMES


pytestmark = pytest.mark.integration


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "synthetic-day.json"


def test_two_cli_writers_allow_only_one_complete_export(tmp_path):
    output = tmp_path / "exports"
    command = [sys.executable, "-m", "gridwise.cli", str(EXAMPLE), str(output)]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace")
    second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace")
    try:
        first.communicate(timeout=20)
        second.communicate(timeout=20)
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert sorted((first.returncode, second.returncode)) == [0, 2]
    assert sorted(path.name for path in output.iterdir()) == sorted(EXPORT_NAMES)


def test_kill_after_first_overwrite_exposes_mixed_export(tmp_path):
    output = tmp_path / "exports"
    output.mkdir()
    stage = output / ".gridwise-stage-synthetic"
    stage.mkdir()
    for name in EXPORT_NAMES:
        (output / name).write_bytes(b"old-" + name.encode())
        (stage / name).write_bytes(b"new-" + name.encode())
    source = tmp_path / "input.json"
    source.write_bytes(b"synthetic")
    marker = tmp_path / "first-published"
    code = """
import os, sys, time
from pathlib import Path
from gridwise.cli import _publish
stage, output, source, marker = map(Path, sys.argv[1:])
real_replace = os.replace
def pause_after_first(old, new):
    real_replace(old, new)
    if Path(new).name == 'summary.json':
        marker.write_text('first', encoding='utf-8')
        time.sleep(30)
os.replace = pause_after_first
_publish(stage, output, source, True)
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(stage), str(output),
                                str(source), str(marker)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), process.communicate(timeout=2)
        assert (output / "summary.json").read_bytes() == b"new-summary.json"
        assert all((output / name).read_bytes() == b"old-" + name.encode()
                   for name in EXPORT_NAMES[1:])
        process.kill()  # Only the child created by this test.
        process.communicate(timeout=5)
        assert process.returncode != 0
        assert (stage / "backup-summary.json").read_bytes() == b"old-summary.json"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.poll() is not None
