"""Synthetic black-box and fault-injection tests for verified run bundles."""

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from gridwise import run_artifacts as artifacts


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "examples" / "synthetic-day.json"


@pytest.fixture
def published(tmp_path):
    return artifacts.publish_run(SCENARIO, tmp_path / "runs")


def _rewrite_manifest(directory, edit):
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    edit(manifest)
    payload = json.dumps(manifest).encode("utf-8")
    path.write_bytes(payload)
    (directory / "COMPLETE").write_bytes(
        b"sha256:" + hashlib.sha256(payload).hexdigest().encode() + b"\n")


def test_publish_and_verify_cli_and_same_run_metadata(tmp_path):
    parent = tmp_path / "runs"
    created = subprocess.run([sys.executable, "-m", "gridwise.run_artifacts", "publish",
                              str(SCENARIO), str(parent)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
    assert created.returncode == 0, created.stderr
    directories = list(parent.iterdir())
    assert len(directories) == 1
    directory = directories[0]
    assert set(path.name for path in directory.iterdir()) == set(artifacts.RUN_FILES) | {
        "manifest.json", "COMPLETE"}
    verified = subprocess.run([sys.executable, "-m", "gridwise.run_artifacts", "verify",
                               str(directory)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10)
    assert verified.returncode == 0, verified.stderr
    result = artifacts.read_verified_run(directory)
    assert result.metadata["status"] == "optimal"
    assert result.files["scenario.json"] == SCENARIO.read_bytes()
    summary = json.loads(result.files["summary.json"])
    assert summary["optimized"]["total_cost_cny"] == pytest.approx(131.0110803324)
    assert summary["baseline"]["total_cost_cny"] == pytest.approx(162.5)


def test_new_runs_do_not_replace_existing_run(published):
    before = {name: (published / name).read_bytes() for name in artifacts.RUN_FILES}
    second = artifacts.publish_run(SCENARIO, published.parent)
    assert second != published
    assert {name: (published / name).read_bytes() for name in artifacts.RUN_FILES} == before


@pytest.mark.parametrize("name", ["optimized.csv", "baseline.csv", "summary.json",
                                   "scenario.json", "grid-purchase.svg"])
def test_missing_or_corrupt_file_is_rejected(published, name):
    target = published / name
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(artifacts.RunArtifactError, match="size or SHA-256 mismatch"):
        artifacts.read_verified_run(published)
    target.unlink()
    completed = subprocess.run([sys.executable, "-m", "gridwise.run_artifacts", "verify",
                                str(published)], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 2


def test_missing_or_forged_marker_is_rejected(published):
    marker = published / "COMPLETE"
    marker.unlink()
    with pytest.raises(artifacts.RunArtifactError, match="COMPLETE"):
        artifacts.read_verified_run(published)
    marker.write_text("complete\n", encoding="utf-8")
    with pytest.raises(artifacts.RunArtifactError, match="COMPLETE"):
        artifacts.read_verified_run(published)


def test_manifest_change_without_marker_update_is_rejected(published):
    with (published / "manifest.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(artifacts.RunArtifactError, match="COMPLETE does not match"):
        artifacts.read_verified_run(published)


@pytest.mark.parametrize("bad_name", ["/absolute.csv", "../escape.csv", "optimized.csv"])
def test_manifest_refuses_unsafe_or_duplicate_names(published, bad_name):
    index = 2 if bad_name == "optimized.csv" else 1
    _rewrite_manifest(published, lambda value: value["files"][index].update(name=bad_name))
    with pytest.raises(artifacts.RunArtifactError, match="filename"):
        artifacts.read_verified_run(published)


def test_manifest_refuses_extra_file_and_unbounded_sizes(published):
    _rewrite_manifest(published, lambda value: value["files"].append(value["files"][0]))
    with pytest.raises(artifacts.RunArtifactError, match="file count"):
        artifacts.read_verified_run(published)


def test_manifest_refuses_oversize_token(published):
    (published / "manifest.json").write_bytes(b"x" * (artifacts.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(artifacts.RunArtifactError, match="bounded regular file"):
        artifacts.read_verified_run(published)


def test_unlisted_file_and_declared_file_over_limit_are_rejected(published):
    (published / "unexpected.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(artifacts.RunArtifactError, match="unexpected run entry"):
        artifacts.read_verified_run(published)
    (published / "unexpected.txt").unlink()
    _rewrite_manifest(published, lambda value: value["files"][1].update(
        size_bytes=artifacts.MAX_FILE_BYTES + 1))
    with pytest.raises(artifacts.RunArtifactError, match="invalid size"):
        artifacts.read_verified_run(published)


def test_reader_rejects_link_target_before_open(published, monkeypatch):
    target = published / "optimized.csv"
    original = Path.is_symlink

    def report_link(path):
        return path == target or original(path)

    monkeypatch.setattr(Path, "is_symlink", report_link)
    with pytest.raises(artifacts.RunArtifactError, match="link or junction"):
        artifacts.read_verified_run(published)


def test_reader_rejects_junction_target_before_open(published, monkeypatch):
    target = published / "baseline.csv"
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == target or original(path))
    with pytest.raises(artifacts.RunArtifactError, match="link or junction"):
        artifacts.read_verified_run(published)


def test_wrong_directory_and_incomplete_run_rejected(tmp_path, published):
    with pytest.raises(artifacts.RunArtifactError, match="published run directory"):
        artifacts.read_verified_run(published.parent)
    incomplete = tmp_path / ("run-" + "0" * 32)
    incomplete.mkdir()
    with pytest.raises(artifacts.RunArtifactError, match="COMPLETE"):
        artifacts.read_verified_run(incomplete)


def test_summary_metadata_mismatch_rejected_even_when_hashes_updated(published):
    summary = published / "summary.json"
    value = json.loads(summary.read_text(encoding="utf-8"))
    value["run"]["git_commit"] = "different"
    summary.write_text(json.dumps(value), encoding="utf-8")
    _rewrite_manifest(published, lambda manifest: manifest["files"][3].update(
        size_bytes=summary.stat().st_size, sha256=hashlib.sha256(summary.read_bytes()).hexdigest()))
    with pytest.raises(artifacts.RunArtifactError, match="metadata differs"):
        artifacts.read_verified_run(published)


def test_concurrent_readers_receive_complete_verified_bytes(published):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(artifacts.read_verified_run, [published] * 20))
    digests = {hashlib.sha256(result.files["summary.json"]).hexdigest() for result in results}
    assert len(digests) == 1


def test_audit_failure_leaves_incomplete_unreadable_run(tmp_path, monkeypatch):
    original = artifacts._execute

    def fail_audit(command, timeout, event):
        if str(artifacts.AUDITOR) in command:
            raise artifacts.RunArtifactError("simulated audit failure")
        return original(command, timeout, event)

    monkeypatch.setattr(artifacts, "_execute", fail_audit)
    with pytest.raises(artifacts.RunArtifactError, match="simulated audit failure") as caught:
        artifacts.publish_run(SCENARIO, tmp_path)
    assert caught.value.stage == "audit"
    directory, = tmp_path.iterdir()
    assert not (directory / "COMPLETE").exists()
    with pytest.raises(artifacts.RunArtifactError):
        artifacts.read_verified_run(directory)


def test_audited_file_changed_before_manifest_is_rejected(tmp_path, monkeypatch):
    original = artifacts._execute

    def alter_after_audit(command, timeout, event):
        result = original(command, timeout, event)
        if str(artifacts.AUDITOR) in command:
            summary = Path(command[-1])
            summary.write_bytes(summary.read_bytes() + b" ")
        return result

    monkeypatch.setattr(artifacts, "_execute", alter_after_audit)
    with pytest.raises(artifacts.RunArtifactError, match="changed across independent audit"):
        artifacts.publish_run(SCENARIO, tmp_path)
    directory, = tmp_path.iterdir()
    assert not (directory / "COMPLETE").exists()


def test_cancelled_run_never_completes(tmp_path):
    import threading
    event = threading.Event()
    event.set()
    with pytest.raises(artifacts.RunArtifactError, match="cancelled") as caught:
        artifacts.publish_run(SCENARIO, tmp_path, cancel_event=event)
    assert caught.value.stage == "solve"
    assert caught.value.reason == "cancelled"
    directory, = tmp_path.iterdir()
    assert not (directory / "COMPLETE").exists()


def test_publish_failure_leaves_incomplete_run(tmp_path, monkeypatch):
    real_replace = os.replace

    def fail_second(source, target):
        if Path(target).name == "baseline.csv":
            raise OSError("simulated publication failure")
        return real_replace(source, target)

    monkeypatch.setattr(artifacts.os, "replace", fail_second)
    with pytest.raises(artifacts.RunArtifactError, match="publication failure") as caught:
        artifacts.publish_run(SCENARIO, tmp_path)
    assert caught.value.stage == "artifact"
    assert caught.value.reason == "artifact_failure"
    directory, = tmp_path.iterdir()
    assert not (directory / "COMPLETE").exists()
    with pytest.raises(artifacts.RunArtifactError):
        artifacts.read_verified_run(directory)


def test_manifest_generation_failure_leaves_incomplete_run(tmp_path, monkeypatch):
    def fail_manifest(*_args):
        raise RuntimeError("simulated manifest failure")

    monkeypatch.setattr(artifacts, "_manifest", fail_manifest)
    with pytest.raises(artifacts.RunArtifactError, match="manifest failure"):
        artifacts.publish_run(SCENARIO, tmp_path)
    directory, = tmp_path.iterdir()
    assert not (directory / "COMPLETE").exists()


def test_final_verification_failure_removes_own_marker(tmp_path, monkeypatch):
    def fail_final(_directory):
        raise artifacts.RunArtifactError("simulated final verification failure")

    monkeypatch.setattr(artifacts, "read_verified_run", fail_final)
    with pytest.raises(artifacts.RunArtifactError, match="final verification failure"):
        artifacts.publish_run(SCENARIO, tmp_path)
    directory, = tmp_path.iterdir()
    assert not (directory / "COMPLETE").exists()


def test_reader_detects_modification_during_open(published, monkeypatch):
    target = published / "optimized.csv"
    original = artifacts.os.read
    changed = False

    identity = (target.stat().st_dev, target.stat().st_ino)

    def alter_after_read(descriptor, amount):
        nonlocal changed
        payload = original(descriptor, amount)
        opened = os.fstat(descriptor)
        if payload and not changed and (opened.st_dev, opened.st_ino) == identity:
            changed = True
            target.write_bytes(b"changed during read")
        return payload

    monkeypatch.setattr(artifacts.os, "read", alter_after_read)
    with pytest.raises(artifacts.RunArtifactError, match="changed while reading"):
        artifacts.read_verified_run(published)


def test_reader_detects_replacement_during_open(published, tmp_path, monkeypatch):
    target = published / "baseline.csv"
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(target.read_bytes())
    identity = (target.stat().st_dev, target.stat().st_ino)
    original = artifacts.os.read
    real_replace = os.replace
    replaced = False

    def replace_after_read(descriptor, amount):
        nonlocal replaced
        payload = original(descriptor, amount)
        opened = os.fstat(descriptor)
        if payload and not replaced and (opened.st_dev, opened.st_ino) == identity:
            replaced = True
            real_replace(replacement, target)
        return payload

    monkeypatch.setattr(artifacts.os, "read", replace_after_read)
    with pytest.raises(artifacts.RunArtifactError, match="changed while reading|cannot safely read"):
        artifacts.read_verified_run(published)


def test_reader_detects_new_directory_entry_during_verification(published, monkeypatch):
    original = artifacts._read_file
    changed = False

    def add_entry(path, limit):
        nonlocal changed
        result = original(path, limit)
        if Path(path).name == "summary.json" and not changed:
            changed = True
            (published / "unexpected.txt").write_text("synthetic", encoding="utf-8")
        return result

    monkeypatch.setattr(artifacts, "_read_file", add_entry)
    with pytest.raises(artifacts.RunArtifactError, match="directory changed|unexpected run entry"):
        artifacts.read_verified_run(published)


def test_reader_detects_file_removed_after_open(published, monkeypatch):
    target = published / "optimized.csv"
    original = Path.lstat
    seen = 0

    def remove_before_final_stat(path):
        nonlocal seen
        if path == target:
            seen += 1
            if seen == 2:
                target.unlink()
        return original(path)

    monkeypatch.setattr(Path, "lstat", remove_before_final_stat)
    with pytest.raises(artifacts.RunArtifactError, match="cannot safely read"):
        artifacts.read_verified_run(published)


@pytest.mark.parametrize("name, injected", [
    ("manifest.json", b'"format_version":1,"format_version":1'),
    ("manifest.json", b'"format_version":NaN'),
])
def test_reader_rejects_duplicate_or_nonfinite_manifest_json(published, name, injected):
    target = published / name
    payload = target.read_bytes().replace(b'"format_version":1', injected, 1)
    target.write_bytes(payload)
    (published / "COMPLETE").write_bytes(
        b"sha256:" + hashlib.sha256(payload).hexdigest().encode() + b"\n")
    with pytest.raises(artifacts.RunArtifactError, match="duplicate JSON key|non-finite"):
        artifacts.read_verified_run(published)


@pytest.mark.parametrize("injected", [b'"run":{},"run":{}', b'"run":NaN'])
def test_reader_rejects_duplicate_or_nonfinite_summary_json(published, injected):
    target = published / "summary.json"
    payload = target.read_bytes().replace(b'"run":', injected + b',"unused":', 1)
    target.write_bytes(payload)
    _rewrite_manifest(published, lambda manifest: manifest["files"][3].update(
        size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
    with pytest.raises(artifacts.RunArtifactError, match="duplicate JSON key|non-finite"):
        artifacts.read_verified_run(published)


def test_forced_termination_leaves_no_readable_run(tmp_path):
    marker = tmp_path / "child-ready"
    code = """
import sys, time
from pathlib import Path
from gridwise import run_artifacts as module
marker = Path(sys.argv[3])
real = module._execute
def pause(command, timeout, event):
    result = real(command, timeout, event)
    if '-m' in command and 'gridwise.cli' in command:
        marker.write_text('ready', encoding='utf-8')
        time.sleep(30)
    return result
module._execute = pause
module.publish_run(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(SCENARIO),
                                str(tmp_path / "runs"), str(marker)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), process.communicate(timeout=2)
        process.kill()  # Only this test's child process.
        process.communicate(timeout=5)
        assert process.returncode != 0
        directory, = (tmp_path / "runs").iterdir()
        assert not (directory / "COMPLETE").exists()
        with pytest.raises(artifacts.RunArtifactError):
            artifacts.read_verified_run(directory)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
