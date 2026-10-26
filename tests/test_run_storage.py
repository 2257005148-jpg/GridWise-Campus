"""Explicit local run-root preference; tests never touch the user's settings."""

import json
from pathlib import Path

import pytest

from gridwise.run_storage import RunStorageError, load_saved_runs_root, save_runs_root


def test_saved_root_survives_reopen(tmp_path):
    root = tmp_path / "persistent-runs"
    root.mkdir()
    config = tmp_path / "settings" / "desktop.json"
    assert load_saved_runs_root(config) is None
    assert save_runs_root(config, root) == root
    assert load_saved_runs_root(config) == root
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "format_version": 1, "runs_root": str(root)}


@pytest.mark.parametrize("payload", [
    b"", b"{", b'{"format_version":1,"runs_root":"relative"}',
    b'{"format_version":1,"runs_root":"a","runs_root":"b"}',
    b'{"format_version":1,"runs_root":"a","extra":true}',
    b'{"format_version":1,"runs_root":null}',
    b"x" * 4097,
])
def test_invalid_preference_fails_closed(tmp_path, payload):
    config = tmp_path / "desktop.json"
    config.write_bytes(payload)
    with pytest.raises(RunStorageError):
        load_saved_runs_root(config)


def test_missing_saved_directory_is_reported(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    config = tmp_path / "desktop.json"
    save_runs_root(config, root)
    root.rmdir()
    with pytest.raises(RunStorageError, match="runs_root"):
        load_saved_runs_root(config)


def test_unsafe_config_target_and_root_are_rejected(tmp_path, monkeypatch):
    config = tmp_path / "desktop.json"
    root = tmp_path / "runs"
    root.mkdir()
    original = Path.is_symlink

    def pretend_link(path):
        return path in (config, root) or original(path)

    monkeypatch.setattr(Path, "is_symlink", pretend_link)
    with pytest.raises(RunStorageError, match="link|junction"):
        save_runs_root(config, root)
    with pytest.raises(RunStorageError, match="link|junction"):
        load_saved_runs_root(config)


def test_settings_access_denial_is_located(tmp_path, monkeypatch):
    config = tmp_path / "desktop.json"
    config.write_text("{}", encoding="utf-8")
    original = Path.lstat

    def deny(path):
        if path == config:
            raise PermissionError("synthetic settings denial")
        return original(path)

    monkeypatch.setattr(Path, "lstat", deny)
    with pytest.raises(RunStorageError, match="desktop.json.*synthetic settings denial"):
        load_saved_runs_root(config)


def test_save_failure_preserves_existing_preference(tmp_path, monkeypatch):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    config = tmp_path / "settings" / "desktop.json"
    save_runs_root(config, first)
    before = config.read_bytes()
    monkeypatch.setattr("gridwise.run_storage.os.replace",
                        lambda *_args: (_ for _ in ()).throw(
                            PermissionError("synthetic replace denial")))
    with pytest.raises(RunStorageError, match="synthetic replace denial"):
        save_runs_root(config, second)
    assert config.read_bytes() == before
    assert list(config.parent.glob("desktop-*.tmp")) == []
