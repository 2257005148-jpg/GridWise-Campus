"""Synthetic black-box and failure tests for the one-schema history prototype."""

import hashlib
import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

from gridwise.cli import run


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "tools" / "audit_history.py"
sys.path.insert(0, str(ROOT / "tools"))
BASE = "93fe6058a237472483292f2c256045de2c420fdc"
PATHS = ("src/gridwise/core.py", "tools/audit_exports.py",
         "requirements/verified-win-py312.txt", "pyproject.toml")


def command(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)


@pytest.fixture
def historical_case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert command("git", "init", str(repo)).returncode == 0
    for name in PATHS:
        blob = subprocess.run(["git", "show", f"{BASE}:{name}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    runtime = {name: metadata.version(name) for name in ("pyomo", "highspy", "numpy", "ply")}
    lock = repo / "requirements" / "verified-win-py312.txt"
    lock.write_bytes("".join(f"{name}=={version}\n" for name, version in runtime.items())
                     .encode("utf-8"))
    assert command("git", "add", "--", *PATHS, cwd=repo).returncode == 0
    assert command("git", "-c", "user.name=GridWise Test", "-c",
                   "user.email=gridwise-test@example.invalid", "commit", "-m",
                   "synthetic historical schema", cwd=repo).returncode == 0
    commit = command("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    exports = tmp_path / "exports"
    exports.mkdir()
    scenario = exports / "scenario.json"
    scenario.write_bytes((ROOT / "examples" / "synthetic-day.json").read_bytes())
    run(scenario, exports)
    summary_path = exports / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["run"]["git_commit"] = commit
    summary["run"]["source_state"] = "clean"
    core = (repo / "src" / "gridwise" / "core.py").read_bytes().replace(b"\r\n", b"\n")
    summary["run"]["model_version"] = "cost-mip-v1+sha256." + hashlib.sha256(core).hexdigest()
    summary["run"]["dependency_lock_sha256"] = "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    paths = (scenario, exports / "optimized.csv", exports / "baseline.csv", summary_path)
    return repo, commit, paths, tmp_path


def cli(repo, commit, paths, *, schema="run-v015"):
    args = [sys.executable, str(HISTORY), "--repo", str(repo)]
    if commit is not None:
        args += ["--ref", commit]
    if schema is not None:
        args += ["--schema", schema]
    result = command(*args, *(str(path) for path in paths))
    return result, json.loads(result.stdout)


def hashes(paths):
    return tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)


def test_known_schema_passes_history_and_leaves_artifacts_unchanged(historical_case):
    repo, commit, paths, _ = historical_case
    before = hashes(paths)
    result, report = cli(repo, commit, paths)
    assert result.returncode == 0, result.stderr
    assert report["historical_context"] == "PASS"
    assert report["current_context"] == "FAIL"
    assert report["arithmetic_physics"] == "PASS"
    assert report["artifacts_unchanged"] == "PASS"
    assert hashes(paths) == before
    assert len(command("git", "worktree", "list", "--porcelain", cwd=repo).stdout.split("worktree ")) == 2


def test_current_context_can_pass_while_history_fails(historical_case):
    repo, commit, paths, _ = historical_case
    run(paths[0], paths[0].parent)
    result, report = cli(repo, commit, paths)
    assert result.returncode == 2
    assert report["current_context"] == "PASS"
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"


@pytest.mark.parametrize("commit,schema,detail", [
    (None, "run-v015", "historical commit"),
    ("present", None, "schema"),
])
def test_missing_commit_or_schema_fails_closed(historical_case, commit, schema, detail):
    repo, actual, paths, _ = historical_case
    result, report = cli(repo, actual if commit == "present" else commit, paths, schema=schema)
    assert result.returncode == 2
    assert report["historical_context"] == "UNKNOWN"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any(detail in item for item in report["details"])


def test_modified_historical_auditor_is_rejected(historical_case):
    repo, _, paths, _ = historical_case
    auditor = repo / "tools" / "audit_exports.py"
    auditor.write_bytes(auditor.read_bytes() + b"\n# changed\n")
    assert command("git", "add", "--", "tools/audit_exports.py", cwd=repo).returncode == 0
    assert command("git", "-c", "user.name=GridWise Test", "-c",
                   "user.email=gridwise-test@example.invalid", "commit", "-m", "alter auditor",
                   cwd=repo).returncode == 0
    changed = command("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    summary = json.loads(paths[3].read_text(encoding="utf-8"))
    summary["run"]["git_commit"] = changed
    paths[3].write_text(json.dumps(summary), encoding="utf-8")
    result, report = cli(repo, changed, paths)
    assert result.returncode == 2
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("reviewed" in item for item in report["details"])


@pytest.mark.parametrize("name,diagnostic", [
    ("src/gridwise/core.py", "model_version"),
    ("requirements/verified-win-py312.txt", "dependency_lock_sha256"),
])
def test_changed_historical_source_or_lock_is_rejected(historical_case, name, diagnostic):
    repo, _, paths, _ = historical_case
    target = repo / name
    target.write_bytes(target.read_bytes() + b"\n# changed\n")
    assert command("git", "add", "--", name, cwd=repo).returncode == 0
    assert command("git", "-c", "user.name=GridWise Test", "-c",
                   "user.email=gridwise-test@example.invalid", "commit", "-m", "change history",
                   cwd=repo).returncode == 0
    changed = command("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    summary = json.loads(paths[3].read_text(encoding="utf-8"))
    summary["run"]["git_commit"] = changed
    paths[3].write_text(json.dumps(summary), encoding="utf-8")
    result, report = cli(repo, changed, paths)
    assert result.returncode == 2
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any(diagnostic in item for item in report["details"])


def test_checkout_attributes_are_rejected_before_execution(historical_case):
    repo, _, paths, _ = historical_case
    (repo / ".gitattributes").write_text("*.txt filter=external\n", encoding="utf-8")
    assert command("git", "add", "--", ".gitattributes", cwd=repo).returncode == 0
    assert command("git", "-c", "user.name=GridWise Test", "-c",
                   "user.email=gridwise-test@example.invalid", "commit", "-m", "add attributes",
                   cwd=repo).returncode == 0
    changed = command("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    summary = json.loads(paths[3].read_text(encoding="utf-8"))
    summary["run"]["git_commit"] = changed
    paths[3].write_text(json.dumps(summary), encoding="utf-8")
    result, report = cli(repo, changed, paths)
    assert result.returncode == 2
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("Git attributes" in item for item in report["details"])


def test_invalid_encoding_in_repository_attributes_fails_closed(historical_case):
    repo, commit, paths, _ = historical_case
    (repo / ".git" / "info" / "attributes").write_bytes(b"\xff")
    result, report = cli(repo, commit, paths)
    assert result.returncode == 2
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("info/attributes" in item for item in report["details"])


def test_unknown_source_state_rejected(historical_case):
    repo, commit, paths, _ = historical_case
    summary = json.loads(paths[3].read_text(encoding="utf-8"))
    summary["run"]["source_state"] = "unavailable"
    paths[3].write_text(json.dumps(summary), encoding="utf-8")
    result, report = cli(repo, commit, paths)
    assert result.returncode == 2
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("source_state" in item for item in report["details"])


def test_historical_export_input_echo_mismatch_fails_arithmetic(historical_case):
    repo, commit, paths, _ = historical_case
    data = paths[1].read_bytes()
    assert b"60.0" in data
    paths[1].write_bytes(data.replace(b"60.0", b"61.0", 1))
    before = hashes(paths)
    result, report = cli(repo, commit, paths)
    assert result.returncode == 2
    assert report["historical_context"] == "PASS"
    assert report["arithmetic_physics"] == "FAIL"
    assert report["artifacts_unchanged"] == "PASS"
    assert hashes(paths) == before


def test_context_statuses_do_not_inherit_arithmetic_failure(historical_case, monkeypatch):
    import audit_history
    repo, commit, paths, root = historical_case
    monkeypatch.setattr(audit_history, "check_current", lambda _: None)
    data = paths[1].read_bytes()
    assert b"60.0" in data
    paths[1].write_bytes(data.replace(b"60.0", b"61.0", 1))
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "PASS"
    assert report["current_context"] == "PASS"
    assert report["arithmetic_physics"] == "FAIL"
    assert report["artifacts_unchanged"] == "PASS"


def test_unavailable_current_model_source_is_unknown_without_blocking_history(historical_case, monkeypatch):
    import audit_history

    repo, commit, paths, root = historical_case
    monkeypatch.setattr(audit_history, "git_provenance", lambda: (commit, "clean"))
    def unavailable_model():
        raise audit_history.AuditError("model source: cannot read core.py")
    monkeypatch.setattr(audit_history, "model_version", unavailable_model)
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["current_context"] == "UNKNOWN"
    assert report["historical_context"] == "PASS"
    assert report["arithmetic_physics"] == "PASS"
    assert report["artifacts_unchanged"] == "PASS"


def test_missing_git_object_is_unknown(historical_case):
    repo, _, paths, _ = historical_case
    result, report = cli(repo, "0" * 40, paths)
    assert result.returncode == 2
    assert report["historical_context"] == "UNKNOWN"
    assert report["arithmetic_physics"] == "NOT_RUN"


def test_git_unavailable_is_unknown_and_history_not_run(historical_case, monkeypatch):
    import audit_history
    repo, commit, paths, root = historical_case
    monkeypatch.setattr(audit_history, "git", lambda *args: (_ for _ in ()).throw(FileNotFoundError("git missing")))
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "UNKNOWN"
    assert report["arithmetic_physics"] == "NOT_RUN"


def test_missing_dependency_is_unknown_and_history_not_run(historical_case, monkeypatch):
    import audit_history
    repo, commit, paths, root = historical_case
    actual_version = audit_history.metadata.version
    def version(name):
        if name == "highspy":
            raise audit_history.metadata.PackageNotFoundError(name)
        return actual_version(name)
    monkeypatch.setattr(audit_history.metadata, "version", version)
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "UNKNOWN"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("dependency unavailable" in item for item in report["details"])


def test_artifact_changed_during_audit_is_detected(historical_case, monkeypatch):
    import audit_history
    repo, commit, paths, root = historical_case
    actual_process = audit_history.audit_process
    def mutate_after_check(script, supplied):
        result = actual_process(script, supplied)
        paths[1].write_bytes(paths[1].read_bytes() + b"\n")
        return result
    monkeypatch.setattr(audit_history, "audit_process", mutate_after_check)
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "FAIL"
    assert report["artifacts_unchanged"] == "FAIL"


def test_original_replaced_while_snapshot_is_audited_fails(historical_case, monkeypatch):
    import audit_history

    repo, commit, paths, root = historical_case
    actual_process = audit_history.audit_process
    original = paths[1].read_bytes()

    def replace_original(script, snapshots):
        assert snapshots[1] != paths[1]
        assert snapshots[1].read_bytes() == original
        paths[1].write_bytes(b"replaced during audit")
        return actual_process(script, snapshots)

    monkeypatch.setattr(audit_history, "audit_process", replace_original)
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "FAIL"
    assert report["artifacts_unchanged"] == "FAIL"
    assert any("original artifact bytes changed" in item for item in report["details"])


def test_original_changed_before_snapshot_prevents_execution(historical_case, monkeypatch):
    import audit_history

    repo, commit, paths, root = historical_case
    actual_snapshot = audit_history.snapshot_artifacts

    def change_before_copy(supplied, hashes_before, destination):
        paths[1].write_bytes(b"replaced before snapshot")
        return actual_snapshot(supplied, hashes_before, destination)

    monkeypatch.setattr(audit_history, "snapshot_artifacts", change_before_copy)
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert report["artifacts_unchanged"] == "FAIL"
    assert any("changed before stable audit snapshot" in item for item in report["details"])


def test_checkout_ignores_repository_hook(historical_case):
    repo, commit, paths, root = historical_case
    marker = root / "hook-ran"
    hooks = root / "repo-hooks"
    hooks.mkdir()
    hook = hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\nprintf executed > '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    assert command("git", "config", "core.hooksPath", str(hooks), cwd=repo).returncode == 0
    result, report = cli(repo, commit, paths)
    assert result.returncode == 0, report
    assert report["arithmetic_physics"] == "PASS"
    assert not marker.exists()


def test_checkout_lock_byte_mismatch_fails_before_execution(historical_case, monkeypatch):
    import audit_history

    repo, commit, paths, root = historical_case
    actual_create = audit_history.create_worktree

    def alter_checked_out_lock(repository, checkout, reference):
        result = actual_create(repository, checkout, reference)
        if result.returncode == 0:
            lock = checkout / audit_history.LOCK_NAME
            lock.write_bytes(lock.read_bytes().replace(b"\n", b"\r\n"))
        return result

    monkeypatch.setattr(audit_history, "create_worktree", alter_checked_out_lock)
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("lock bytes differ" in item for item in report["details"])


def test_worktree_creation_failure_is_reported(historical_case, monkeypatch):
    import audit_history
    repo, commit, paths, root = historical_case
    monkeypatch.setattr(audit_history, "create_worktree",
                        lambda *args: subprocess.CompletedProcess([], 1, "", "simulated create failure"))
    report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
    assert report["historical_context"] == "FAIL"
    assert report["arithmetic_physics"] == "NOT_RUN"
    assert any("worktree creation failed" in item for item in report["details"])


def test_worktree_cleanup_failure_is_reported_and_preserved(historical_case, monkeypatch):
    import audit_history
    repo, commit, paths, root = historical_case
    actual_remove = audit_history.remove_worktree
    monkeypatch.setattr(audit_history, "remove_worktree",
                        lambda *args: subprocess.CompletedProcess([], 1, "", "simulated cleanup failure"))
    try:
        report = audit_history.audit_history(repo, commit, "run-v015", paths, temp_base=root)
        assert report["historical_context"] == "FAIL"
        assert any("worktree cleanup failed" in item for item in report["details"])
        checkout = next(root.glob("gridwise-history-*/checkout"))
        assert checkout.exists()
    finally:
        for checkout in root.glob("gridwise-history-*/checkout"):
            assert checkout.resolve().is_relative_to(root.resolve())
            assert actual_remove(repo, checkout).returncode == 0
            for snapshot in checkout.parent.glob("artifact-[0-3].*"):
                snapshot.unlink()
            (checkout.parent / "empty-hooks").rmdir()
            checkout.parent.rmdir()
