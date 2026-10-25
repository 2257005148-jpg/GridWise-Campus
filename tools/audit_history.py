"""Read-only prototype for one reviewed v0.1.5 export schema.

Only the pinned, standard-library v0.1.5 auditor may run in a detached
temporary worktree. The four supplied artifacts are never written.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import tomllib
from importlib import metadata
from pathlib import Path

from audit_exports import (AuditError, LOCK_PATH, MAX_FILE_BYTES,
                           git_provenance, model_version, parse_json_bytes)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "run-v015"
AUDITOR_SHA256 = "acb15253f3b9f0ea450e0d9dca175aa0299ba3f5a7c4d2dd2eb30eda5ee68c9a"
RUN_FIELDS = {"model_version", "git_commit", "source_state", "scenario_sha256",
              "solver_name", "status", "python_version", "operating_system",
              "solver_version", "dependency_versions", "dependency_lock_sha256"}
RUNTIME_PACKAGES = ("pyomo", "highspy", "numpy", "ply")
LOCK_NAME = "requirements/verified-win-py312.txt"


class ContextError(ValueError):
    status = "FAIL"


class MissingEvidence(ContextError):
    status = "UNKNOWN"


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def artifact_digest(path):
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ContextError(f"{path}: exceeds {MAX_FILE_BYTES} bytes")
            value.update(chunk)
    return value.hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=10,
                          check=False)


def git_blob(repo, commit, path):
    result = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"],
                            capture_output=True, timeout=10, check=False)
    if result.returncode:
        raise MissingEvidence(f"historical Git object missing: {path}")
    if len(result.stdout) > MAX_FILE_BYTES:
        raise ContextError(f"historical Git object too large: {path}")
    return result.stdout


def checked_schema(run):
    if not isinstance(run, dict):
        raise MissingEvidence("summary.run: missing or invalid run metadata")
    missing = RUN_FIELDS - set(run)
    extra = set(run) - RUN_FIELDS
    if missing:
        raise MissingEvidence(f"summary.run: missing field {sorted(missing)[0]}")
    if extra:
        raise ContextError(f"summary.run: unexpected field {sorted(extra)[0]}")


def read_run(path):
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ContextError("summary.json exceeds audit size limit")
    try:
        summary = parse_json_bytes(data, path)
    except AuditError as exc:
        raise ContextError(str(exc)) from exc
    if not isinstance(summary, dict):
        raise ContextError("summary: expected object")
    run = summary.get("run")
    checked_schema(run)
    return run


def check_current(paths):
    run = read_run(paths[3])
    commit, state = git_provenance()
    if (run["git_commit"], run["source_state"]) != (commit, state):
        raise ContextError(f"current Git context differs: HEAD={commit}, state={state}")
    if run["model_version"] != model_version():
        raise ContextError("current model source differs")
    try:
        lock = LOCK_PATH.read_bytes()
    except OSError as exc:
        raise ContextError("current dependency lock unavailable") from exc
    if run["dependency_lock_sha256"] != digest(lock):
        raise ContextError("current dependency lock differs")
    if run["scenario_sha256"] != "sha256:" + artifact_digest(paths[0]):
        raise ContextError("current scenario bytes differ")
    versions = run["dependency_versions"]
    if not isinstance(versions, dict) or set(versions) != set(RUNTIME_PACKAGES):
        raise ContextError("current runtime package set differs")
    for name in RUNTIME_PACKAGES:
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise MissingEvidence(f"current dependency unavailable: {name}") from exc
        if versions[name] != installed:
            raise ContextError(f"current dependency version differs: {name}")
    if run["python_version"] != platform.python_version() or run["operating_system"] != platform.platform():
        raise ContextError("current Python or OS differs")
    if run["solver_name"] != "appsi_highs" or run["status"] != "optimal":
        raise ContextError("current solver name or status differs")


def check_history(repo, commit, schema, paths):
    if schema != SCHEMA:
        raise MissingEvidence(f"schema: expected {SCHEMA}, got {schema or 'missing'}")
    if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise MissingEvidence("historical commit: requires a full 40-character lowercase SHA")
    top = git(repo, "rev-parse", "--show-toplevel")
    if top.returncode or Path(top.stdout.strip()).resolve() != repo.resolve():
        raise MissingEvidence("Git unavailable or repository path mismatch")
    kind = git(repo, "cat-file", "-t", commit)
    if kind.returncode or kind.stdout.strip() != "commit":
        raise MissingEvidence(f"historical commit: object missing or not a commit ({commit})")
    tracked = git(repo, "ls-tree", "-r", "--name-only", commit)
    if tracked.returncode:
        raise MissingEvidence("historical tree cannot be listed")
    if any(name.rsplit("/", 1)[-1] == ".gitattributes" for name in tracked.stdout.splitlines()):
        raise ContextError("historical tree contains Git attributes; checkout policy not reviewed")
    info = git(repo, "rev-parse", "--git-path", "info/attributes")
    if info.returncode:
        raise MissingEvidence("repository Git attributes path unavailable")
    info_path = Path(info.stdout.strip())
    if not info_path.is_absolute():
        info_path = repo / info_path
    if info_path.is_file():
        try:
            attributes = info_path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise ContextError("repository info/attributes is not UTF-8") from exc
        if any(line.strip() and not line.lstrip().startswith("#")
               for line in attributes.splitlines()):
            raise ContextError("repository info/attributes is active; checkout policy not reviewed")
    for name in ("src/gridwise/core.py", "tools/audit_exports.py", LOCK_NAME, "pyproject.toml"):
        entry = git(repo, "ls-tree", commit, "--", name)
        if entry.returncode or not entry.stdout.startswith(("100644 blob ", "100755 blob ")):
            raise ContextError(f"historical file is missing or not regular: {name}")
    run = read_run(paths[3])
    if run["git_commit"] != commit:
        raise ContextError("summary.run.git_commit: differs from requested historical commit")
    if run["source_state"] != "clean":
        error = MissingEvidence if run["source_state"] in ("unavailable", "unknown", None) else ContextError
        raise error("summary.run.source_state: historical source must be clean")
    if run["scenario_sha256"] != "sha256:" + artifact_digest(paths[0]):
        raise ContextError("summary.run.scenario_sha256: differs from scenario bytes")
    core = git_blob(repo, commit, "src/gridwise/core.py")
    normalized = core.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    expected_model = "cost-mip-v1+sha256." + hashlib.sha256(normalized).hexdigest()
    if run["model_version"] != expected_model:
        raise ContextError("summary.run.model_version: differs from historical core.py")
    lock = git_blob(repo, commit, LOCK_NAME)
    if run["dependency_lock_sha256"] != digest(lock):
        raise ContextError("summary.run.dependency_lock_sha256: differs from historical lock")
    try:
        lock_lines = lock.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ContextError("historical lock: not UTF-8") from exc
    pins = {}
    for line in lock_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise ContextError("historical lock: invalid exact pin")
        name, version = line.split("==")
        key = name.lower()
        if not name or not version or key in pins:
            raise ContextError("historical lock: missing or repeated pin")
        pins[key] = version
    versions = run["dependency_versions"]
    if not isinstance(versions, dict) or set(versions) != set(RUNTIME_PACKAGES):
        raise ContextError("summary.run.dependency_versions: invalid runtime package set")
    for name in RUNTIME_PACKAGES:
        if versions[name] != pins.get(name):
            raise ContextError(f"summary.run.dependency_versions.{name}: differs from historical lock")
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise MissingEvidence(f"dependency unavailable: {name}") from exc
        if installed != versions[name]:
            raise ContextError(f"dependency mismatch: {name} installed {installed}, export {versions[name]}")
    if run["solver_version"] != versions["highspy"]:
        raise ContextError("summary.run.solver_version: differs from highspy")
    if run["python_version"] != platform.python_version():
        raise ContextError("summary.run.python_version: differs from audit interpreter")
    if run["operating_system"] != platform.platform():
        raise ContextError("summary.run.operating_system: differs from audit OS")
    if run["solver_name"] != "appsi_highs" or run["status"] != "optimal":
        raise ContextError("summary.run: solver name or status mismatch")
    auditor = git_blob(repo, commit, "tools/audit_exports.py")
    if hashlib.sha256(auditor).hexdigest() != AUDITOR_SHA256:
        raise ContextError("historical auditor bytes are not the reviewed v0.1.5 version")
    try:
        project = tomllib.loads(git_blob(repo, commit, "pyproject.toml").decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ContextError("historical pyproject.toml cannot be parsed") from exc
    declared = project.get("project", {}).get("dependencies", [])
    if not all(f"{name}=={versions[name]}" in declared for name in ("pyomo", "highspy")):
        raise ContextError("historical pyproject.toml direct dependency mismatch")
    return run


def create_worktree(repo, path, commit):
    hooks = path.parent / "empty-hooks"
    hooks.mkdir()
    return subprocess.run(["git", "-c", "core.autocrlf=false",
                           "-c", f"core.attributesFile={os.devnull}",
                           "-c", f"core.hooksPath={hooks}", "-c", "core.fsmonitor=false",
                           "-C", str(repo),
                           "worktree", "add", "--detach", "--", str(path), commit],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=10, check=False)


def remove_worktree(repo, path):
    return git(repo, "worktree", "remove", "--", str(path))


def audit_process(script, paths):
    return subprocess.run([sys.executable, "-I", str(script), *(str(p) for p in paths)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, check=False)


def short_detail(result):
    return (result.stderr or result.stdout).strip()[:500]


def snapshot_artifacts(paths, hashes, root):
    snapshots = []
    for index, (source, expected) in enumerate(zip(paths, hashes, strict=True)):
        target = root / f"artifact-{index}{source.suffix}"
        actual = hashlib.sha256()
        size = 0
        with source.open("rb") as reader, target.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ContextError(f"{source}: exceeds {MAX_FILE_BYTES} bytes during snapshot")
                actual.update(chunk)
                writer.write(chunk)
        if actual.hexdigest() != expected:
            raise ContextError(f"{source}: changed before stable audit snapshot")
        snapshots.append(target)
    return tuple(snapshots)


def audit_history(repo, commit, schema, paths, *, temp_base=None):
    repo = Path(repo).resolve()
    paths = tuple(Path(path).resolve() for path in paths)
    answer = {"schema": schema or "UNKNOWN", "historical_context": "UNKNOWN",
              "current_context": "UNKNOWN", "arithmetic_physics": "NOT_RUN",
              "artifacts_unchanged": "UNKNOWN", "details": []}
    try:
        before = tuple(artifact_digest(path) for path in paths)
    except (OSError, ContextError) as exc:
        answer["details"].append(str(exc))
        return answer
    try:
        check_current(paths)
        answer["current_context"] = "PASS"
    except ContextError as exc:
        answer["current_context"] = exc.status
        answer["details"].append(f"current: {exc}")
    except OSError as exc:
        answer["current_context"] = "UNKNOWN"
        answer["details"].append(f"current: {exc}")
    try:
        check_history(repo, commit, schema, paths)
        answer["historical_context"] = "PASS"
    except ContextError as exc:
        answer["historical_context"] = exc.status
        answer["details"].append(f"historical: {exc}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        answer["details"].append(f"historical: {exc}")
    if answer["historical_context"] == "PASS":
        base = Path(temp_base) if temp_base else Path(tempfile.gettempdir())
        try:
            temp_root = Path(tempfile.mkdtemp(prefix="gridwise-history-", dir=base)).resolve()
        except OSError as exc:
            temp_root = None
            answer["historical_context"] = "FAIL"
            answer["details"].append(f"temporary worktree root creation failed: {exc}")
    if answer["historical_context"] == "PASS":
        checkout = temp_root / "checkout"
        try:
            if checkout.resolve().parent != temp_root:
                raise ContextError("worktree path escaped temporary root")
            snapshots = snapshot_artifacts(paths, before, temp_root)
            created = create_worktree(repo, checkout, commit)
            if created.returncode:
                raise ContextError("worktree creation failed: " + short_detail(created))
            head = git(checkout, "rev-parse", "HEAD")
            if head.returncode or head.stdout.strip() != commit:
                raise ContextError("worktree HEAD differs from requested commit")
            script = checkout / "tools" / "audit_exports.py"
            script_bytes = script.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if hashlib.sha256(script_bytes).hexdigest() != AUDITOR_SHA256:
                raise ContextError("worktree auditor bytes differ from reviewed version")
            if (checkout / LOCK_NAME).read_bytes() != git_blob(repo, commit, LOCK_NAME):
                raise ContextError("worktree lock bytes differ from historical Git object")
            historical = audit_process(script, snapshots)
            answer["arithmetic_physics"] = ("PASS" if historical.returncode == 0 else
                                             "FAIL" if historical.returncode == 2 else "UNKNOWN")
            if historical.returncode:
                answer["details"].append("historical audit: " + short_detail(historical))
        except (ContextError, OSError, subprocess.TimeoutExpired) as exc:
            answer["historical_context"] = "FAIL"
            answer["details"].append(str(exc))
        finally:
            if checkout.exists():
                try:
                    removed = remove_worktree(repo, checkout)
                    removed_ok = removed.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    removed_ok = False
                if not removed_ok:
                    answer["historical_context"] = "FAIL"
                    answer["details"].append("worktree cleanup failed; preserved at " + str(checkout))
            if not checkout.exists() or removed_ok:
                try:
                    for snapshot in temp_root.glob("artifact-[0-3].*"):
                        snapshot.unlink()
                    hooks = temp_root / "empty-hooks"
                    if hooks.exists():
                        hooks.rmdir()
                    temp_root.rmdir()
                except OSError as exc:
                    answer["historical_context"] = "FAIL"
                    answer["details"].append(f"temporary root cleanup failed: {exc}")
    try:
        after = tuple(artifact_digest(path) for path in paths)
        answer["artifacts_unchanged"] = "PASS" if after == before else "FAIL"
        if after != before:
            answer["historical_context"] = "FAIL"
            if answer["arithmetic_physics"] != "NOT_RUN":
                answer["arithmetic_physics"] = "FAIL"
            answer["details"].append("original artifact bytes changed during audit")
    except (OSError, ContextError) as exc:
        answer["artifacts_unchanged"] = "UNKNOWN"
        answer["details"].append(f"cannot recheck original artifacts: {exc}")
    return answer


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only reviewed v0.1.5 historical export audit")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--ref", help="full historical Git commit SHA")
    parser.add_argument("--schema", help=f"supported schema: {SCHEMA}")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("optimized_csv", type=Path)
    parser.add_argument("baseline_csv", type=Path)
    parser.add_argument("summary_json", type=Path)
    args = parser.parse_args(argv)
    result = audit_history(args.repo, args.ref, args.schema,
                           (args.scenario, args.optimized_csv, args.baseline_csv, args.summary_json))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(result[key] == "PASS" for key in
                    ("historical_context", "arithmetic_physics", "artifacts_unchanged")) else 2


if __name__ == "__main__":
    sys.exit(main())
