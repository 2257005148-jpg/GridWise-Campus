"""Black-box safety checks for the synthetic CLI export boundary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gridwise.cli import run


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "synthetic-day.json"
NAMES = ("summary.json", "optimized.csv", "baseline.csv", "grid-purchase.svg")


def cli(source, output, *options):
    return subprocess.run([sys.executable, "-m", "gridwise.cli", str(source), str(output), *options],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_existing_export_is_preserved_by_default(tmp_path, name):
    target = tmp_path / name
    target.write_bytes(b"user-owned-content")
    completed = cli(EXAMPLE, tmp_path)
    assert completed.returncode == 2
    assert name in completed.stderr
    assert target.read_bytes() == b"user-owned-content"
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


def test_explicit_overwrite_replaces_only_export_files(tmp_path):
    unrelated = tmp_path / "keep.txt"
    unrelated.write_bytes(b"keep")
    for name in NAMES:
        (tmp_path / name).write_bytes(b"old")
    completed = cli(EXAMPLE, tmp_path, "--overwrite")
    assert completed.returncode == 0, completed.stderr
    assert unrelated.read_bytes() == b"keep"
    assert all((tmp_path / name).read_bytes() != b"old" for name in NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_export_target_directory_is_rejected_even_with_overwrite(tmp_path, name):
    (tmp_path / name).mkdir()
    completed = cli(EXAMPLE, tmp_path, "--overwrite")
    assert completed.returncode == 2
    assert name in completed.stderr
    assert (tmp_path / name).is_dir()
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


def test_input_file_alias_cannot_be_overwritten(tmp_path):
    source = tmp_path / "summary.json"
    source.write_bytes(EXAMPLE.read_bytes())
    before = source.read_bytes()
    completed = cli(source, tmp_path, "--overwrite")
    assert completed.returncode == 2
    assert source.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["summary.json"]


def test_symlink_target_is_rejected(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    output = tmp_path / "out"
    output.mkdir()
    try:
        (output / "summary.json").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    completed = cli(EXAMPLE, output, "--overwrite")
    assert completed.returncode == 2
    assert outside.read_bytes() == b"outside"


def test_default_publish_rolls_back_on_late_collision(tmp_path, monkeypatch):
    import gridwise.cli as module

    real_link = os.link
    def collide(source, target, *args, **kwargs):
        if Path(target).name == "baseline.csv":
            Path(target).write_bytes(b"concurrent")
            raise FileExistsError("simulated competing writer")
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", collide)
    with pytest.raises(OSError, match="competing writer"):
        run(EXAMPLE, tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["baseline.csv"]
    assert (tmp_path / "baseline.csv").read_bytes() == b"concurrent"


def test_explicit_overwrite_rolls_back_if_later_replace_fails(tmp_path, monkeypatch):
    import gridwise.cli as module

    for name in NAMES:
        (tmp_path / name).write_bytes(name.encode())
    original = {name: (tmp_path / name).read_bytes() for name in NAMES}
    real_replace = os.replace
    failed = False

    def fail_second(source, target):
        nonlocal failed
        if Path(target).name == "optimized.csv" and not failed:
            failed = True
            raise OSError("simulated replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated replace failure"):
        run(EXAMPLE, tmp_path, overwrite=True)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == original
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(NAMES)


def test_failed_staging_keeps_existing_exports_and_removes_stage(tmp_path, monkeypatch):
    import gridwise.cli as module

    for name in NAMES:
        (tmp_path / name).write_bytes(name.encode())
    original = {name: (tmp_path / name).read_bytes() for name in NAMES}
    def fail_chart(_results):
        raise ValueError("simulated chart failure")

    monkeypatch.setattr(module, "_chart", fail_chart)
    with pytest.raises(ValueError, match="simulated chart failure"):
        run(EXAMPLE, tmp_path, overwrite=True)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == original
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(NAMES)


def test_failed_rollback_retains_recovery_copy(tmp_path, monkeypatch):
    import gridwise.cli as module

    for name in NAMES:
        (tmp_path / name).write_bytes(name.encode())
    real_replace = os.replace

    def fail_publish_and_rollback(source, target):
        source = Path(source)
        if (Path(target).name == "optimized.csv" and
                source.name == "optimized.csv"):
            raise OSError("simulated publish failure")
        if source.name == "backup-summary.json":
            raise OSError("simulated rollback failure")
        return real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", fail_publish_and_rollback)
    with pytest.raises(module.ExportRollbackError, match="recovery files"):
        run(EXAMPLE, tmp_path, overwrite=True)
    stages = list(tmp_path.glob(".gridwise-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "backup-summary.json").read_bytes() == b"summary.json"
    assert all((tmp_path / name).read_bytes() == name.encode()
               for name in NAMES if name != "summary.json")


def test_non_oserror_during_publication_restores_old_files(tmp_path, monkeypatch):
    import gridwise.cli as module

    for name in NAMES:
        (tmp_path / name).write_bytes(name.encode())
    real_replace = os.replace
    failed = False

    def interrupt_second(source, target):
        nonlocal failed
        if Path(target).name == "optimized.csv" and not failed:
            failed = True
            raise RuntimeError("simulated publication interruption")
        return real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", interrupt_second)
    with pytest.raises(RuntimeError, match="publication interruption"):
        run(EXAMPLE, tmp_path, overwrite=True)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == {
        name: name.encode() for name in NAMES}
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(NAMES)


def test_backup_creation_failure_restores_previous_replacements(tmp_path, monkeypatch):
    import gridwise.cli as module

    for name in NAMES:
        (tmp_path / name).write_bytes(name.encode())
    real_link = os.link

    def fail_second_backup(source, target, *args, **kwargs):
        if Path(target).name == "backup-optimized.csv":
            raise OSError("simulated backup failure")
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", fail_second_backup)
    with pytest.raises(OSError, match="simulated backup failure"):
        run(EXAMPLE, tmp_path, overwrite=True)
    assert {name: (tmp_path / name).read_bytes() for name in NAMES} == {
        name: name.encode() for name in NAMES}
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(NAMES)


def test_cleanup_failure_reports_published_state_and_retains_backups(tmp_path, monkeypatch):
    import gridwise.cli as module

    for name in NAMES:
        (tmp_path / name).write_bytes(name.encode())

    def fail_cleanup(_path):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(module.shutil, "rmtree", fail_cleanup)
    with pytest.raises(module.ExportCleanupError, match="publication completed"):
        run(EXAMPLE, tmp_path, overwrite=True)
    assert all((tmp_path / name).read_bytes() != name.encode() for name in NAMES)
    stages = list(tmp_path.glob(".gridwise-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "backup-summary.json").read_bytes() == b"summary.json"


def test_cleanup_failure_after_publication_error_reports_both_causes(tmp_path, monkeypatch):
    import gridwise.cli as module

    def fail_publish(*_args):
        raise OSError("simulated publish failure")

    def fail_cleanup(_path):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(module, "_publish", fail_publish)
    monkeypatch.setattr(module.shutil, "rmtree", fail_cleanup)
    with pytest.raises(module.ExportCleanupError,
                       match="publication not completed; prior error: simulated publish failure"):
        run(EXAMPLE, tmp_path)
    assert all(not (tmp_path / name).exists() for name in NAMES)
    assert len(list(tmp_path.glob(".gridwise-stage-*"))) == 1
