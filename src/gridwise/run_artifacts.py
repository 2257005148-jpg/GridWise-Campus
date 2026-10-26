"""Publish and read bounded, independently audited local run bundles.

This is an opt-in interface. The existing four-file CLI remains unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDITOR = ROOT / "tools" / "audit_exports.py"
FORMAT_VERSION = 1
PEAK_FORMAT_VERSION = 2
RUN_FILES = ("scenario.json", "optimized.csv", "baseline.csv", "summary.json",
             "grid-purchase.svg")
METADATA_FIELDS = ("model_version", "git_commit", "source_state", "scenario_sha256",
                   "solver_name", "status")
PEAK_METADATA_FIELDS = (*METADATA_FIELDS, "optimization_mode", "peak_cap_kw")
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_MARKER_BYTES = 80
RUN_NAME = re.compile(r"run-[0-9a-f]{32}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class RunArtifactError(ValueError):
    """A run cannot be published or safely read under this contract."""


@dataclass(frozen=True)
class VerifiedRun:
    path: Path
    metadata: dict[str, str]
    files: dict[str, bytes]


def _reject_link_path(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise RunArtifactError(f"link or junction path refused: {item}")


def _read_file(path: Path, limit: int) -> bytes:
    """Read one regular file once; reject visible identity/size changes."""
    _reject_link_path(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise RunArtifactError(f"not a bounded regular file: {path}")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise RunArtifactError(f"file replaced before open: {path}")
            chunks, total = [], 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise RunArtifactError(f"file exceeds {limit} bytes: {path}")
                chunks.append(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) or \
           identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or \
           identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            raise RunArtifactError(f"file changed while reading: {path}")
        return b"".join(chunks)
    except (OSError, ValueError) as exc:
        if isinstance(exc, RunArtifactError):
            raise
        raise RunArtifactError(f"cannot safely read {path}: {exc}") from exc


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RunArtifactError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json(payload: bytes, where: Path) -> dict:
    try:
        parsed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(
                                RunArtifactError(f"non-finite JSON value: {value}")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RunArtifactError(f"invalid JSON {where}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RunArtifactError(f"expected JSON object: {where}")
    return parsed


def _metadata(summary: dict) -> dict[str, str | float]:
    run = summary.get("run")
    if not isinstance(run, dict):
        raise RunArtifactError("summary.json: missing run metadata")
    metadata = {}
    for field in METADATA_FIELDS:
        value = run.get(field)
        if not isinstance(value, str) or not value or len(value) > 256:
            raise RunArtifactError(f"summary.json: invalid run.{field}")
        metadata[field] = value
    if metadata["status"] != "optimal":
        raise RunArtifactError("summary.json: run.status is not optimal")
    if "optimization_mode" in run or "peak_cap_kw" in run:
        cap = run.get("peak_cap_kw")
        if run.get("optimization_mode") != "peak_cap" or type(cap) not in (int, float) or \
                not math.isfinite(cap) or not 0 <= cap <= 1_000_000_000_000:
            raise RunArtifactError("summary.json: invalid peak-cap run metadata")
        metadata.update(optimization_mode="peak_cap", peak_cap_kw=cap)
    return metadata


def _manifest(files: dict[str, bytes], metadata: dict[str, str | float]) -> dict:
    version = PEAK_FORMAT_VERSION if "optimization_mode" in metadata else FORMAT_VERSION
    return {"format_version": version, "metadata": metadata,
            "files": [{"name": name, "size_bytes": len(files[name]),
                       "sha256": hashlib.sha256(files[name]).hexdigest()}
                      for name in RUN_FILES]}


def _validate_manifest(manifest: dict) -> tuple[dict[str, str | float], list[dict]]:
    if set(manifest) != {"format_version", "metadata", "files"} or \
       type(manifest["format_version"]) is not int or \
       manifest["format_version"] not in (FORMAT_VERSION, PEAK_FORMAT_VERSION):
        raise RunArtifactError("manifest.json: unsupported or malformed format_version")
    metadata = manifest["metadata"]
    expected = PEAK_METADATA_FIELDS if manifest["format_version"] == PEAK_FORMAT_VERSION else METADATA_FIELDS
    if not isinstance(metadata, dict) or set(metadata) != set(expected) or \
       any(not isinstance(metadata[name], str) or not metadata[name] or len(metadata[name]) > 256
           for name in METADATA_FIELDS):
        raise RunArtifactError("manifest.json: invalid metadata")
    if manifest["format_version"] == PEAK_FORMAT_VERSION:
        cap = metadata["peak_cap_kw"]
        if metadata["optimization_mode"] != "peak_cap" or type(cap) not in (int, float) or \
                not math.isfinite(cap) or not 0 <= cap <= 1_000_000_000_000:
            raise RunArtifactError("manifest.json: invalid peak-cap metadata")
    entries = manifest["files"]
    if not isinstance(entries, list) or len(entries) != len(RUN_FILES):
        raise RunArtifactError("manifest.json: invalid file count")
    for expected, entry in zip(RUN_FILES, entries):
        if not isinstance(entry, dict) or set(entry) != {"name", "size_bytes", "sha256"}:
            raise RunArtifactError("manifest.json: invalid file entry")
        if entry["name"] != expected:
            raise RunArtifactError(f"manifest.json: unsafe, duplicate or unexpected filename: {entry['name']}")
        if type(entry["size_bytes"]) is not int or not 0 <= entry["size_bytes"] <= MAX_FILE_BYTES:
            raise RunArtifactError(f"manifest.json: invalid size for {expected}")
        if not isinstance(entry["sha256"], str) or not DIGEST.fullmatch(entry["sha256"]):
            raise RunArtifactError(f"manifest.json: invalid SHA-256 for {expected}")
    if sum(entry["size_bytes"] for entry in entries) > MAX_TOTAL_BYTES:
        raise RunArtifactError("manifest.json: total file size exceeds limit")
    return metadata, entries


def _verify_contents(directory: Path, *, require_complete: bool) -> VerifiedRun:
    _reject_link_path(directory)
    if not directory.is_dir():
        raise RunArtifactError(f"run directory missing: {directory}")
    if require_complete:
        if not RUN_NAME.fullmatch(directory.name):
            raise RunArtifactError(f"not a published run directory: {directory}")
        expected = set(RUN_FILES) | {"manifest.json", "COMPLETE"}
        seen = set()
        try:
            directory_before = directory.stat()
            for child in directory.iterdir():
                seen.add(child.name)
                if len(seen) > len(expected) or child.name not in expected:
                    raise RunArtifactError(f"unexpected run entry: {child}")
        except OSError as exc:
            raise RunArtifactError(f"cannot list run directory {directory}: {exc}") from exc
        if seen != expected:
            raise RunArtifactError(f"missing run entry: {sorted(expected - seen)}")
        marker = _read_file(directory / "COMPLETE", MAX_MARKER_BYTES)
        if len(marker) != 72 or not marker.startswith(b"sha256:") or marker[-1:] != b"\n" or \
           not DIGEST.fullmatch(marker[7:-1].decode("ascii", errors="replace")):
            raise RunArtifactError(f"invalid COMPLETE marker: {directory}")
    manifest_bytes = _read_file(directory / "manifest.json", MAX_MANIFEST_BYTES)
    if require_complete and marker != b"sha256:" + hashlib.sha256(manifest_bytes).hexdigest().encode() + b"\n":
        raise RunArtifactError(f"COMPLETE does not match manifest.json: {directory}")
    manifest = _parse_json(manifest_bytes, directory / "manifest.json")
    metadata, entries = _validate_manifest(manifest)
    files, total = {}, 0
    for entry in entries:
        name = entry["name"]
        payload = _read_file(directory / name, MAX_FILE_BYTES)
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise RunArtifactError(f"total file size exceeds limit: {directory}")
        if len(payload) != entry["size_bytes"] or \
           hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise RunArtifactError(f"size or SHA-256 mismatch: {directory / name}")
        files[name] = payload
    summary = _parse_json(files["summary.json"], directory / "summary.json")
    if _metadata(summary) != metadata:
        raise RunArtifactError("manifest.json metadata differs from summary.json")
    if metadata["scenario_sha256"] != "sha256:" + hashlib.sha256(files["scenario.json"]).hexdigest():
        raise RunArtifactError("scenario.json hash differs from summary.json")
    if require_complete and (marker != _read_file(directory / "COMPLETE", MAX_MARKER_BYTES) or
                             manifest_bytes != _read_file(directory / "manifest.json", MAX_MANIFEST_BYTES)):
        raise RunArtifactError(f"marker or manifest changed during verification: {directory}")
    if require_complete:
        _reject_link_path(directory)
        try:
            directory_after = directory.stat()
            final_entries = {child.name for child in directory.iterdir()}
        except OSError as exc:
            raise RunArtifactError(f"cannot recheck run directory {directory}: {exc}") from exc
        if (directory_before.st_dev, directory_before.st_ino, directory_before.st_mtime_ns) != \
           (directory_after.st_dev, directory_after.st_ino, directory_after.st_mtime_ns) or \
           final_entries != expected:
            raise RunArtifactError(f"run directory changed during verification: {directory}")
    return VerifiedRun(directory, metadata, files)


def read_verified_run(directory: Path) -> VerifiedRun:
    """Return verified bytes, never unverified paths or a claim of authenticity."""
    return _verify_contents(Path(directory), require_complete=True)


def _execute(command: list[str], timeout: float, cancel_event: threading.Event) -> tuple[str, str]:
    if cancel_event.is_set():
        raise RunArtifactError("run cancelled")
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               errors="replace")
    started = time.monotonic()
    try:
        while True:
            if cancel_event.is_set():
                raise RunArtifactError("run cancelled")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise RunArtifactError(f"command timed out after {timeout:g} s")
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                if process.returncode:
                    raise RunArtifactError(
                        f"command exited {process.returncode}: {(stderr or stdout).strip()}")
                return stdout, stderr
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def publish_run(scenario: Path, runs_parent: Path, *,
                cancel_event: threading.Event | None = None,
                timeout_seconds: float = 120,
                solve_timeout_seconds: float | None = None,
                audit_timeout_seconds: float | None = None,
                peak_cap_kw: float | None = None) -> Path:
    """Create a new run directory; leave failures incomplete for inspection."""
    solve_timeout = timeout_seconds if solve_timeout_seconds is None else solve_timeout_seconds
    audit_timeout = timeout_seconds if audit_timeout_seconds is None else audit_timeout_seconds
    if solve_timeout <= 0 or audit_timeout <= 0:
        raise RunArtifactError("process timeouts must be positive")
    parent = Path(os.path.abspath(runs_parent))
    _reject_link_path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    _reject_link_path(parent)
    if not parent.is_dir():
        raise RunArtifactError(f"runs parent is not a directory: {parent}")
    source = _read_file(Path(scenario), MAX_FILE_BYTES)
    directory = parent / f"run-{uuid.uuid4().hex}"
    try:
        directory.mkdir(exist_ok=False)
    except OSError as exc:
        raise RunArtifactError(f"cannot reserve new run directory {directory}: {exc}") from exc
    stage = directory / "stage"
    marker_created = False
    try:
        stage.mkdir()
        (stage / "scenario.json").write_bytes(source)
        event = cancel_event or threading.Event()
        command = [sys.executable, "-m", "gridwise.cli", str(stage / "scenario.json"),
                   str(stage)]
        if peak_cap_kw is not None:
            command.extend(("--peak-cap-kw", str(peak_cap_kw)))
        _execute(command, solve_timeout, event)
        before_audit = {name: hashlib.sha256(_read_file(stage / name, MAX_FILE_BYTES)).digest()
                        for name in RUN_FILES}
        audit, _ = _execute([sys.executable, str(AUDITOR), str(stage / "scenario.json"),
                             str(stage / "optimized.csv"), str(stage / "baseline.csv"),
                             str(stage / "summary.json")], audit_timeout, event)
        if "AUDIT PASSED" not in audit:
            raise RunArtifactError("independent auditor did not report AUDIT PASSED")
        if event.is_set():
            raise RunArtifactError("run cancelled")
        files = {name: _read_file(stage / name, MAX_FILE_BYTES) for name in RUN_FILES}
        if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
            raise RunArtifactError("run files exceed total size limit")
        for name in RUN_FILES:
            if hashlib.sha256(files[name]).digest() != before_audit[name]:
                raise RunArtifactError(f"file changed across independent audit: {stage / name}")
        summary = _parse_json(files["summary.json"], stage / "summary.json")
        metadata = _metadata(summary)
        manifest = _manifest(files, metadata)
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise RunArtifactError("manifest exceeds size limit")
        (stage / "manifest.json").write_bytes(manifest_bytes)
        _verify_contents(stage, require_complete=False)
        if event.is_set():
            raise RunArtifactError("run cancelled")
        for name in RUN_FILES:
            os.replace(stage / name, directory / name)
        os.replace(stage / "manifest.json", directory / "manifest.json")
        stage.rmdir()
        _verify_contents(directory, require_complete=False)
        if event.is_set():
            raise RunArtifactError("run cancelled")
        marker = b"sha256:" + hashlib.sha256(manifest_bytes).hexdigest().encode() + b"\n"
        with (directory / "COMPLETE").open("xb") as stream:
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())
        marker_created = True
        read_verified_run(directory)
        return directory
    except BaseException as exc:
        # Preserve this newly created incomplete directory; never delete unknown data.
        if marker_created:
            try:
                if (directory / "COMPLETE").read_bytes() == marker:
                    (directory / "COMPLETE").unlink()
            except OSError as cleanup_error:
                raise RunArtifactError(
                    f"publication failed: {exc}; marker cleanup failed: {cleanup_error}; "
                    f"inspect run: {directory}") from exc
        if isinstance(exc, RunArtifactError):
            raise RunArtifactError(f"{exc}; incomplete run retained: {directory}") from exc
        raise RunArtifactError(f"publication failed: {exc}; incomplete run retained: {directory}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Verified local run bundles / 已校验本地运行目录")
    sub = parser.add_subparsers(dest="operation", required=True)
    create = sub.add_parser("publish", help="solve, independently audit, publish a new run")
    create.add_argument("scenario", type=Path)
    create.add_argument("runs_parent", type=Path)
    create.add_argument("--peak-cap-kw", type=float,
                        help="optional storage grid-import cap in kW / 储能方案购电上限")
    verify = sub.add_parser("verify", help="verify marker, manifest and file bytes")
    verify.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "publish":
            path = publish_run(args.scenario, args.runs_parent,
                               peak_cap_kw=args.peak_cap_kw)
            print(f"PUBLISHED {path}")
        else:
            result = read_verified_run(args.run_directory)
            print(f"VERIFIED {result.path}")
    except (RunArtifactError, OSError) as exc:
        parser.exit(2, f"gridwise: {exc}\n")


if __name__ == "__main__":
    main()
