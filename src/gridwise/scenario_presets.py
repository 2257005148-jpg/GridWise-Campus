"""Versioned local presets for device parameters and optimization policy.

The store is intentionally independent from ``desktop.json``.  Writes use a
private same-directory temporary file and atomic replacement, but coordination
between multiple application processes is outside this module's guarantees.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .device import (DEVICE_FIELDS, DeviceParameterError, DeviceParameters,
                     parse_device_parameters)


FORMAT_VERSION = 1
MAX_FILE_BYTES = 128 * 1024
MAX_PRESETS = 100
MAX_NAME_CHARS = 80
MAX_ID_CHARS = 64
PRESET_ID = re.compile(
    rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_ID_CHARS - 1}}}")


class ScenarioPresetError(ValueError):
    """A scenario preset or its local store is invalid."""


def _finite_nonnegative(value, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ScenarioPresetError(f"{field}: expected a finite nonnegative number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScenarioPresetError(f"{field}: expected a finite nonnegative number") from exc
    if not math.isfinite(number) or number < 0:
        raise ScenarioPresetError(f"{field}: expected a finite nonnegative number")
    return number


@dataclass(frozen=True)
class OptimizationPolicy:
    """One explicitly selected optimization strategy."""

    mode: str
    peak_cap_kw: float | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"cost", "peak_cap"}:
            raise ScenarioPresetError(f"optimization.mode: unsupported value {self.mode!r}")
        if self.mode == "cost":
            if self.peak_cap_kw is not None:
                raise ScenarioPresetError("optimization.peak_cap_kw: must be null in cost mode")
            return
        if self.peak_cap_kw is None:
            raise ScenarioPresetError("optimization.peak_cap_kw: required in peak_cap mode")
        object.__setattr__(self, "peak_cap_kw",
                           _finite_nonnegative(self.peak_cap_kw, "optimization.peak_cap_kw"))


@dataclass(frozen=True)
class ScenarioPreset:
    """A named immutable equipment and policy configuration."""

    preset_id: str
    name: str
    device_parameters: DeviceParameters
    optimization: OptimizationPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.preset_id, str):
            raise ScenarioPresetError("preset.id: expected text")
        if not isinstance(self.name, str):
            raise ScenarioPresetError("preset.name: expected text")
        preset_id = self.preset_id.strip()
        name = self.name.strip()
        if PRESET_ID.fullmatch(preset_id) is None:
            raise ScenarioPresetError(
                f"preset.id: expected 1..{MAX_ID_CHARS} ASCII letters, digits, "
                "dot, underscore or hyphen")
        if not name or len(name) > MAX_NAME_CHARS or any(ord(char) < 32 for char in name):
            raise ScenarioPresetError(
                f"preset.name: expected 1..{MAX_NAME_CHARS} visible characters")
        if not isinstance(self.device_parameters, DeviceParameters):
            raise ScenarioPresetError("preset.device_parameters: expected DeviceParameters")
        if not isinstance(self.optimization, OptimizationPolicy):
            raise ScenarioPresetError("preset.optimization: expected OptimizationPolicy")
        if self.optimization.peak_cap_kw is not None and \
                self.optimization.peak_cap_kw > self.device_parameters.grid_max_kw:
            raise ScenarioPresetError(
                "optimization.peak_cap_kw: exceeds device grid_max_kw")
        object.__setattr__(self, "preset_id", preset_id)
        object.__setattr__(self, "name", name)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioPresetError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_mapping(value, fields: set[str], location: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ScenarioPresetError(f"{location}: expected an object")
    missing = sorted(fields - set(value))
    extra = sorted(set(value) - fields)
    if missing:
        raise ScenarioPresetError(f"{location}: missing {', '.join(missing)}")
    if extra:
        raise ScenarioPresetError(f"{location}: unexpected {', '.join(extra)}")
    return value


def _preset_from_mapping(value) -> ScenarioPreset:
    item = _exact_mapping(
        value, {"id", "name", "device_parameters", "optimization"}, "preset")
    policy_data = _exact_mapping(
        item["optimization"], {"mode", "peak_cap_kw"}, "preset.optimization")
    try:
        devices = parse_device_parameters(_exact_mapping(
            item["device_parameters"], set(DEVICE_FIELDS), "preset.device_parameters"))
    except DeviceParameterError as exc:
        raise ScenarioPresetError(f"preset.device_parameters: {exc}") from exc
    return ScenarioPreset(
        item["id"], item["name"], devices,
        OptimizationPolicy(policy_data["mode"], policy_data["peak_cap_kw"]))


def _validate_collection(presets: Iterable[ScenarioPreset]) -> tuple[ScenarioPreset, ...]:
    items = tuple(presets)
    if len(items) > MAX_PRESETS:
        raise ScenarioPresetError(f"preset count exceeds {MAX_PRESETS}")
    ids = set()
    names = set()
    for item in items:
        if not isinstance(item, ScenarioPreset):
            raise ScenarioPresetError("presets: expected ScenarioPreset values")
        if item.preset_id in ids:
            raise ScenarioPresetError(f"duplicate preset id: {item.preset_id}")
        folded = item.name.casefold()
        if folded in names:
            raise ScenarioPresetError(f"duplicate preset name: {item.name}")
        ids.add(item.preset_id)
        names.add(folded)
    return items


def _preset_mapping(preset: ScenarioPreset) -> dict:
    return {
        "id": preset.preset_id,
        "name": preset.name,
        "device_parameters": {
            field: getattr(preset.device_parameters, field) for field in DEVICE_FIELDS
        },
        "optimization": {
            "mode": preset.optimization.mode,
            "peak_cap_kw": preset.optimization.peak_cap_kw,
        },
    }


def create_preset(presets: Iterable[ScenarioPreset], *, name: str,
                  device_parameters: DeviceParameters,
                  optimization: OptimizationPolicy,
                  preset_id: str | None = None) -> tuple[ScenarioPreset, ...]:
    """Return a new collection with one validated preset appended."""
    items = _validate_collection(presets)
    created = ScenarioPreset(
        preset_id or uuid.uuid4().hex, name, device_parameters, optimization)
    return _validate_collection((*items, created))


def update_preset(presets: Iterable[ScenarioPreset], preset_id: str, *,
                  name: str | None = None,
                  device_parameters: DeviceParameters | None = None,
                  optimization: OptimizationPolicy | None = None
                  ) -> tuple[ScenarioPreset, ...]:
    """Return a new collection with one stable-id preset replaced."""
    items = _validate_collection(presets)
    updated = []
    found = False
    for item in items:
        if item.preset_id != preset_id:
            updated.append(item)
            continue
        found = True
        updated.append(ScenarioPreset(
            item.preset_id, item.name if name is None else name,
            item.device_parameters if device_parameters is None else device_parameters,
            item.optimization if optimization is None else optimization))
    if not found:
        raise ScenarioPresetError(f"preset id not found: {preset_id}")
    return _validate_collection(updated)


def delete_preset(presets: Iterable[ScenarioPreset],
                  preset_id: str) -> tuple[ScenarioPreset, ...]:
    """Return a new collection without the selected stable-id preset."""
    items = _validate_collection(presets)
    remaining = tuple(item for item in items if item.preset_id != preset_id)
    if len(remaining) == len(items):
        raise ScenarioPresetError(f"preset id not found: {preset_id}")
    return remaining


def _reject_links(path: Path) -> None:
    for item in (path, *path.parents):
        try:
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise ScenarioPresetError(f"link or junction path refused: {item}")
        except OSError as exc:
            raise ScenarioPresetError(f"cannot inspect path {item}: {exc}") from exc


def _safe_path(path: Path) -> Path:
    result = Path(path)
    if not result.is_absolute() or ".." in result.parts:
        raise ScenarioPresetError(f"preset path must be absolute: {result}")
    _reject_links(result)
    return result


def load_presets(path: Path) -> tuple[ScenarioPreset, ...]:
    """Read and strictly validate a bounded UTF-8 preset store."""
    source = _safe_path(path)
    try:
        try:
            info = source.lstat()
        except FileNotFoundError:
            return ()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ScenarioPresetError(f"preset store is not a bounded regular file: {source}")
        with source.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ScenarioPresetError(f"preset store exceeds {MAX_FILE_BYTES} bytes: {source}")
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ScenarioPresetError(f"non-finite JSON value: {value}")))
        root = _exact_mapping(data, {"format_version", "presets"}, "preset store")
        if type(root["format_version"]) is not int or root["format_version"] != FORMAT_VERSION:
            raise ScenarioPresetError("preset store format_version is unsupported")
        if not isinstance(root["presets"], list):
            raise ScenarioPresetError("preset store presets: expected an array")
        return _validate_collection(_preset_from_mapping(item) for item in root["presets"])
    except ScenarioPresetError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ScenarioPresetError(f"cannot read preset store {source}: {exc}") from exc


def save_presets(path: Path, presets: Iterable[ScenarioPreset]) -> Path:
    """Atomically replace a validated preset store using a private temp file."""
    destination = _safe_path(path)
    items = _validate_collection(presets)
    payload = (json.dumps(
        {"format_version": FORMAT_VERSION,
         "presets": [_preset_mapping(item) for item in items]},
        ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        raise ScenarioPresetError(f"preset store exceeds {MAX_FILE_BYTES} bytes")
    if destination.exists():
        load_presets(destination)  # Never overwrite an unknown or malformed file.
    temporary = None
    save_error = None
    cleanup_error = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _reject_links(destination)
        descriptor, temporary = tempfile.mkstemp(
            prefix="scenario-presets-", suffix=".tmp", dir=destination.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_links(destination)
        os.replace(temporary, destination)
        temporary = None
    except Exception as exc:
        save_error = exc
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError as exc:
                cleanup_error = exc
    if cleanup_error is not None:
        detail = (f"private preset temporary cleanup failed at "
                  f"{Path(temporary).absolute()}: {cleanup_error!r}")
        if save_error is not None:
            raise ScenarioPresetError(
                f"cannot save preset store {destination}: {save_error}; {detail}") from save_error
        raise ScenarioPresetError(detail) from cleanup_error
    if save_error is not None:
        if isinstance(save_error, ScenarioPresetError):
            raise save_error
        raise ScenarioPresetError(
            f"cannot save preset store {destination}: {save_error}") from save_error
    return destination
