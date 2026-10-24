"""Observed run environment; metadata is descriptive, not an authenticity proof."""

import hashlib
import platform
from importlib import metadata
from pathlib import Path


RUNTIME_PACKAGES = ("pyomo", "highspy", "numpy", "ply")
LOCK_PATH = Path(__file__).resolve().parents[2] / "requirements" / "verified-win-py312.txt"


def dependency_versions():
    versions = {}
    for name in RUNTIME_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def lock_sha256():
    try:
        return "sha256:" + hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest()
    except OSError:
        return "unavailable"


def run_environment():
    versions = dependency_versions()
    return {
        "python_version": platform.python_version(),
        "operating_system": platform.platform(),
        "solver_version": versions["highspy"],
        "dependency_versions": versions,
        "dependency_lock_sha256": lock_sha256(),
    }
