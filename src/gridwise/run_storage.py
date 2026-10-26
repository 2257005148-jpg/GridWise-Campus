"""Small, explicit local preference for a user-selected run directory."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path


MAX_CONFIG_BYTES = 4096
FORMAT_VERSION = 1


class RunStorageError(ValueError):
    """A saved run-directory preference cannot be used safely."""


def default_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "GridWiseCampus" / "desktop.json"


def _reject_links(path: Path) -> None:
    for item in (path, *path.parents):
        try:
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise RunStorageError(f"link or junction path refused: {item}")
        except OSError as exc:
            raise RunStorageError(f"cannot inspect path {item}: {exc}") from exc


def _runs_root(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise RunStorageError("settings.runs_root: expected nonempty path")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts:
        raise RunStorageError(f"settings.runs_root: expected absolute path: {value}")
    _reject_links(root)
    if not root.is_dir():
        raise RunStorageError(f"settings.runs_root: directory unavailable: {root}")
    return root


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RunStorageError(f"duplicate settings key: {key}")
        result[key] = value
    return result


def load_saved_runs_root(config_path: Path) -> Path | None:
    """Read a bounded preference; missing configuration preserves the temp default."""
    path = Path(config_path)
    _reject_links(path)
    try:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            raise RunStorageError(f"settings file is not bounded regular file: {path}")
        with path.open("rb") as stream:
            payload = stream.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise RunStorageError(f"settings file exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        settings = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(
                                  RunStorageError(f"non-finite settings value: {value}")))
        if not isinstance(settings, dict) or set(settings) != {"format_version", "runs_root"} or \
           type(settings["format_version"]) is not int or settings["format_version"] != FORMAT_VERSION:
            raise RunStorageError(f"settings format invalid: {path}")
        return _runs_root(settings["runs_root"])
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RunStorageError(f"cannot read settings {path}: {exc}") from exc


def save_runs_root(config_path: Path, runs_root: Path) -> Path:
    """Save an explicit choice using a private temp file and one-file replace."""
    root = _runs_root(str(Path(runs_root)))
    path = Path(config_path)
    _reject_links(path)
    load_saved_runs_root(path)  # Do not overwrite an unknown or malformed file.
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _reject_links(path)
        payload = json.dumps({"format_version": FORMAT_VERSION, "runs_root": str(root)},
                             ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_CONFIG_BYTES:
            raise RunStorageError(f"settings exceed {MAX_CONFIG_BYTES} bytes")
        descriptor, temporary = tempfile.mkstemp(prefix="desktop-", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_links(path)
        os.replace(temporary, path)
        temporary = None
        return root
    except OSError as exc:
        raise RunStorageError(f"cannot save settings {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass  # Only this call's private temporary file could remain.
