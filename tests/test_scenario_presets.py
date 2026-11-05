"""Behavior tests for versioned scenario-preset storage."""

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from gridwise.device import DeviceParameters
from gridwise.scenario_presets import (MAX_FILE_BYTES, MAX_PRESETS,
                                       OptimizationPolicy, ScenarioPreset,
                                       ScenarioPresetError, create_preset,
                                       delete_preset, export_preset_exchange,
                                       load_preset_exchange, load_presets,
                                       save_presets, serialize_preset_exchange,
                                       update_preset)


DEVICES = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)


def _exchange_document(*, name="教学楼", mode="cost", peak_cap_kw=None,
                       devices=None):
    return {
        "document_type": "gridwise-scenario-preset",
        "format_version": 1,
        "preset": {
            "name": name,
            "device_parameters": devices or {
                "grid_max_kw": 120,
                "capacity_kwh": 80,
                "initial_kwh": 40,
                "terminal_kwh": 40,
                "max_charge_kw": 30,
                "max_discharge_kw": 30,
                "charge_efficiency": 0.95,
                "discharge_efficiency": 0.95,
            },
            "optimization": {"mode": mode, "peak_cap_kw": peak_cap_kw},
        },
    }


def _write_exchange(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("policy", [
    OptimizationPolicy("cost"),
    OptimizationPolicy("peak_cap", 55),
])
def test_single_preset_exchange_round_trip_uses_new_id_and_utf8(
        tmp_path, policy):
    source = ScenarioPreset("private-source-id", "教学楼午间削峰", DEVICES, policy)
    payload = serialize_preset_exchange(source)
    decoded = json.loads(payload.decode("utf-8"))

    assert decoded == {
        "document_type": "gridwise-scenario-preset",
        "format_version": 1,
        "preset": {
            "name": "教学楼午间削峰",
            "device_parameters": {
                "grid_max_kw": 120,
                "capacity_kwh": 80,
                "initial_kwh": 40,
                "terminal_kwh": 40,
                "max_charge_kw": 30,
                "max_discharge_kw": 30,
                "charge_efficiency": 0.95,
                "discharge_efficiency": 0.95,
            },
            "optimization": {
                "mode": policy.mode,
                "peak_cap_kw": policy.peak_cap_kw,
            },
        },
    }
    assert "private-source-id" not in payload.decode("utf-8")

    exchange_path = tmp_path / "教学楼预设.json"
    exchange_path.write_bytes(payload)
    imported = load_preset_exchange(exchange_path)

    assert imported.preset_id != source.preset_id
    assert imported.name == source.name
    assert imported.device_parameters == source.device_parameters
    assert imported.optimization == source.optimization

    store_path = tmp_path / "scenario-presets.json"
    save_presets(store_path, (source,))
    exported_path = tmp_path / "已导出预设.json"
    assert export_preset_exchange(
        store_path, source.preset_id, exported_path) == exported_path
    reloaded = load_preset_exchange(exported_path)
    assert reloaded.name == source.name
    assert reloaded.device_parameters == source.device_parameters
    assert reloaded.optimization == source.optimization


@pytest.mark.parametrize(("change", "message"), [
    (lambda value: value.update(format_version=2), "format_version"),
    (lambda value: value.update(document_type="other-preset"), "document_type"),
    (lambda value: value.update(extra=True), "unexpected extra"),
    (lambda value: value["preset"].update(extra=True), "unexpected extra"),
    (lambda value: value["preset"].pop("name"), "missing name"),
])
def test_exchange_rejects_unknown_identity_and_schema(tmp_path, change, message):
    document = _exchange_document()
    change(document)
    path = _write_exchange(tmp_path / "invalid.json", document)

    with pytest.raises(ScenarioPresetError, match=message):
        load_preset_exchange(path)


@pytest.mark.parametrize(("raw", "message"), [
    (b'{"document_type":"gridwise-scenario-preset","format_version":1,'
     b'"format_version":1,"preset":{}}', "duplicate JSON key"),
    (b'{"document_type":"gridwise-scenario-preset","format_version":1,'
     b'"preset":{"name":"x","device_parameters":NaN,'
     b'"optimization":{}}}', "non-finite JSON value"),
    (b'{"document_type":"gridwise-scenario-preset","format_version":1,'
     b'"preset":{"name":"x","device_parameters":Infinity,'
     b'"optimization":{}}}', "non-finite JSON value"),
    (b"\xff\xfe\x00\x00", "cannot read"),
])
def test_exchange_rejects_duplicate_nonfinite_and_non_utf8_json(
        tmp_path, raw, message):
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)

    with pytest.raises(ScenarioPresetError, match=message):
        load_preset_exchange(path)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("grid_max_kw", True, "device_parameters"),
    ("capacity_kwh", 0, "device_parameters"),
    ("initial_kwh", 81, "device_parameters"),
])
def test_exchange_reuses_device_validation(tmp_path, field, value, message):
    document = _exchange_document()
    document["preset"]["device_parameters"][field] = value
    path = _write_exchange(tmp_path / "invalid-device.json", document)

    with pytest.raises(ScenarioPresetError, match=message):
        load_preset_exchange(path)


@pytest.mark.parametrize(("mode", "cap", "message"), [
    ("unknown", None, "unsupported"),
    ("peak_cap", True, "finite"),
    ("peak_cap", 121, "grid_max_kw"),
])
def test_exchange_reuses_optimization_validation(tmp_path, mode, cap, message):
    path = _write_exchange(
        tmp_path / "invalid-policy.json",
        _exchange_document(mode=mode, peak_cap_kw=cap))

    with pytest.raises(ScenarioPresetError, match=message):
        load_preset_exchange(path)


def test_exchange_input_size_and_file_type_are_bounded(tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_FILE_BYTES + 1))
    with pytest.raises(ScenarioPresetError, match="bounded|exceeds"):
        load_preset_exchange(oversized)

    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(ScenarioPresetError, match="regular file"):
        load_preset_exchange(directory)


def test_exchange_link_or_junction_is_refused(tmp_path, monkeypatch):
    path = _write_exchange(tmp_path / "linked.json", _exchange_document())
    original = Path.is_symlink

    def pretend_link(candidate):
        return candidate == path or original(candidate)

    monkeypatch.setattr(Path, "is_symlink", pretend_link)
    with pytest.raises(ScenarioPresetError, match="link|junction"):
        load_preset_exchange(path)


def test_invalid_exchange_does_not_change_preset_store(tmp_path):
    store = tmp_path / "scenario-presets.json"
    save_presets(store, (ScenarioPreset(
        "saved", "已保存", DEVICES, OptimizationPolicy("cost")),))
    before = hashlib.sha256(store.read_bytes()).hexdigest()
    invalid = _write_exchange(
        tmp_path / "invalid.json",
        _exchange_document(mode="peak_cap", peak_cap_kw=121))

    with pytest.raises(ScenarioPresetError):
        load_preset_exchange(invalid)

    assert hashlib.sha256(store.read_bytes()).hexdigest() == before


def _saved_exchange_source(tmp_path):
    source = ScenarioPreset(
        "saved-source", "可导出预设", DEVICES,
        OptimizationPolicy("peak_cap", 55))
    store = tmp_path / "scenario-presets.json"
    save_presets(store, (source,))
    return store, source


def test_export_requires_a_saved_preset_id(tmp_path):
    store, _source = _saved_exchange_source(tmp_path)
    target = tmp_path / "export.json"

    with pytest.raises(ScenarioPresetError, match="preset id not found"):
        export_preset_exchange(store, "not-saved", target)

    assert not target.exists()


def test_export_refuses_existing_target_without_changing_bytes(tmp_path):
    store, source = _saved_exchange_source(tmp_path)
    target = tmp_path / "export.json"
    target.write_bytes(b"existing user bytes\n")
    before = target.read_bytes()

    with pytest.raises(ScenarioPresetError, match="already exists"):
        export_preset_exchange(store, source.preset_id, target)

    assert target.read_bytes() == before


def test_export_publish_race_does_not_overwrite_competing_file(
        tmp_path, monkeypatch):
    store, source = _saved_exchange_source(tmp_path)
    target = tmp_path / "export.json"

    def competing_link(_source, destination, **_kwargs):
        Path(destination).write_bytes(b"created by another writer\n")
        raise FileExistsError("synthetic competing target")

    monkeypatch.setattr("gridwise.scenario_presets.os.link", competing_link)
    with pytest.raises(ScenarioPresetError, match="synthetic competing target") as failure:
        export_preset_exchange(store, source.preset_id, target)

    assert isinstance(failure.value.__cause__, FileExistsError)
    assert target.read_bytes() == b"created by another writer\n"
    assert list(tmp_path.glob("scenario-preset-exchange-*.tmp")) == []


def test_export_publish_and_cleanup_failures_preserve_both_errors_and_residual(
        tmp_path, monkeypatch):
    store, source = _saved_exchange_source(tmp_path)
    target = tmp_path / "export.json"
    real_unlink = Path.unlink
    residual = []

    def deny_cleanup(path, *args, **kwargs):
        if path.name.startswith("scenario-preset-exchange-"):
            residual.append(path.absolute())
            raise PermissionError("synthetic exchange cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        "gridwise.scenario_presets.os.link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic exchange publish failure")))
    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    try:
        with pytest.raises(ScenarioPresetError) as failure:
            export_preset_exchange(store, source.preset_id, target)

        message = str(failure.value)
        assert "synthetic exchange publish failure" in message
        assert "synthetic exchange cleanup denial" in message
        assert residual and str(residual[0]) in message
        assert isinstance(failure.value.__cause__, RuntimeError)
        assert not target.exists()
    finally:
        for path in residual:
            if path.exists():
                real_unlink(path)


def test_export_reports_cleanup_failure_after_successful_publication(
        tmp_path, monkeypatch):
    store, source = _saved_exchange_source(tmp_path)
    target = tmp_path / "export.json"
    real_unlink = Path.unlink
    residual = []

    def deny_cleanup(path, *args, **kwargs):
        if path.name.startswith("scenario-preset-exchange-"):
            residual.append(path.absolute())
            raise PermissionError("synthetic post-publish cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    try:
        with pytest.raises(ScenarioPresetError, match="was published") as failure:
            export_preset_exchange(store, source.preset_id, target)

        assert "synthetic post-publish cleanup denial" in str(failure.value)
        assert residual and str(residual[0]) in str(failure.value)
        assert isinstance(failure.value.__cause__, PermissionError)
        loaded = load_preset_exchange(target)
        assert loaded.name == source.name
        assert loaded.optimization == source.optimization
    finally:
        for path in residual:
            if path.exists():
                real_unlink(path)


def test_export_rejects_nonregular_and_link_targets(tmp_path, monkeypatch):
    store, source = _saved_exchange_source(tmp_path)
    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(ScenarioPresetError, match="already exists"):
        export_preset_exchange(store, source.preset_id, directory)

    linked = tmp_path / "linked.json"
    original = Path.is_symlink

    def pretend_link(candidate):
        return candidate == linked or original(candidate)

    monkeypatch.setattr(Path, "is_symlink", pretend_link)
    with pytest.raises(ScenarioPresetError, match="link|junction"):
        export_preset_exchange(store, source.preset_id, linked)


def test_export_contains_only_portable_configuration_fields(tmp_path):
    store, source = _saved_exchange_source(tmp_path)
    target = tmp_path / "export.json"

    export_preset_exchange(store, source.preset_id, target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert set(payload) == {"document_type", "format_version", "preset"}
    assert set(payload["preset"]) == {
        "name", "device_parameters", "optimization"}
    text = target.read_text(encoding="utf-8")
    assert source.preset_id not in text
    assert str(store) not in text
    for forbidden in ("csv", "result", "report", "history", "runs_root"):
        assert forbidden not in text.casefold()


def test_cost_and_peak_cap_presets_round_trip_utf8_json(tmp_path):
    path = tmp_path / "scenario-presets.json"
    presets = (
        ScenarioPreset("cost-default", "默认电费", DEVICES, OptimizationPolicy("cost")),
        ScenarioPreset("peak-55", "晚高峰削峰", DEVICES,
                       OptimizationPolicy("peak_cap", 55)),
    )

    save_presets(path, presets)

    assert load_presets(path) == presets
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert [item["name"] for item in payload["presets"]] == ["默认电费", "晚高峰削峰"]
    assert payload["presets"][0]["optimization"] == {
        "mode": "cost", "peak_cap_kw": None}
    assert payload["presets"][1]["optimization"] == {
        "mode": "peak_cap", "peak_cap_kw": 55.0}


def test_create_update_and_delete_return_new_validated_collections():
    original = ()
    created = create_preset(
        original, name="工作日", device_parameters=DEVICES,
        optimization=OptimizationPolicy("cost"), preset_id="weekday")
    changed_devices = DeviceParameters(100, 60, 20, 25, 15, 18, 0.9, 0.88)

    updated = update_preset(
        created, "weekday", name="工作日削峰", device_parameters=changed_devices,
        optimization=OptimizationPolicy("peak_cap", 50))
    deleted = delete_preset(updated, "weekday")

    assert original == ()
    assert created[0].name == "工作日"
    assert created[0].optimization == OptimizationPolicy("cost")
    assert updated == (ScenarioPreset(
        "weekday", "工作日削峰", changed_devices,
        OptimizationPolicy("peak_cap", 50)),)
    assert deleted == ()


@pytest.mark.parametrize("field", ["preset_id", "name"])
def test_duplicate_ids_and_casefolded_names_are_rejected(tmp_path, field):
    first = ScenarioPreset("one", "Weekday", DEVICES, OptimizationPolicy("cost"))
    second = ScenarioPreset(
        "one" if field == "preset_id" else "two",
        "Weekend" if field == "preset_id" else "weekday",
        DEVICES, OptimizationPolicy("cost"))

    with pytest.raises(ScenarioPresetError, match="duplicate preset"):
        save_presets(tmp_path / "scenario-presets.json", (first, second))


@pytest.mark.parametrize("device_field,bad_value", [
    ("capacity_kwh", 0),
    ("initial_kwh", 81),
    ("charge_efficiency", 1.1),
])
def test_load_reuses_device_parameter_validation(tmp_path, device_field, bad_value):
    path = tmp_path / "scenario-presets.json"
    devices = {field: getattr(DEVICES, field) for field in (
        "grid_max_kw", "capacity_kwh", "initial_kwh", "terminal_kwh",
        "max_charge_kw", "max_discharge_kw", "charge_efficiency",
        "discharge_efficiency")}
    devices[device_field] = bad_value
    path.write_text(json.dumps({
        "format_version": 1,
        "presets": [{
            "id": "bad-device", "name": "非法设备",
            "device_parameters": devices,
            "optimization": {"mode": "cost", "peak_cap_kw": None},
        }],
    }, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ScenarioPresetError, match="device_parameters"):
        load_presets(path)


@pytest.mark.parametrize("policy,message", [
    (("unknown", None), "unsupported"),
    (("cost", 55), "must be null"),
    (("peak_cap", None), "required"),
    (("peak_cap", -1), "nonnegative"),
    (("peak_cap", float("nan")), "finite"),
])
def test_invalid_optimization_policy_is_rejected(policy, message):
    with pytest.raises(ScenarioPresetError, match=message):
        OptimizationPolicy(*policy)


def test_peak_cap_cannot_exceed_preset_grid_limit():
    with pytest.raises(ScenarioPresetError, match="grid_max_kw"):
        ScenarioPreset("too-high", "过高上限", DEVICES,
                       OptimizationPolicy("peak_cap", 121))


@pytest.mark.parametrize(("preset_id", "name", "message"), [
    (None, "有效名称", "preset.id"),
    ("valid-id", None, "preset.name"),
    ("contains/slash", "有效名称", "preset.id"),
])
def test_identifier_and_name_require_strict_text(preset_id, name, message):
    with pytest.raises(ScenarioPresetError, match=message):
        ScenarioPreset(preset_id, name, DEVICES, OptimizationPolicy("cost"))


@pytest.mark.parametrize("raw,message", [
    (b"{", "cannot read"),
    (b'{"format_version":1,"presets":[],"presets":[]}', "duplicate JSON key"),
    (b'{"format_version":1,"presets":[{"id":"x","name":"x",'
     b'"device_parameters":{"grid_max_kw":NaN}}]}', "non-finite JSON value"),
    (b'{"format_version":1,"presets":[{"id":"x","name":"x",'
     b'"device_parameters":{"grid_max_kw":Infinity}}]}', "non-finite JSON value"),
    (b'{"format_version":1,"presets":[],"extra":true}', "unexpected extra"),
])
def test_malformed_duplicate_nonfinite_and_extra_json_fail_closed(tmp_path, raw, message):
    path = tmp_path / "scenario-presets.json"
    path.write_bytes(raw)

    with pytest.raises(ScenarioPresetError, match=message):
        load_presets(path)


def test_missing_store_returns_empty_collection(tmp_path):
    assert load_presets(tmp_path / "scenario-presets.json") == ()


def test_file_size_and_preset_count_are_bounded(tmp_path):
    path = tmp_path / "scenario-presets.json"
    path.write_bytes(b" " * (MAX_FILE_BYTES + 1))
    with pytest.raises(ScenarioPresetError, match="bounded|exceeds"):
        load_presets(path)

    presets = tuple(
        ScenarioPreset(f"id-{index}", f"预设 {index}", DEVICES,
                       OptimizationPolicy("cost"))
        for index in range(MAX_PRESETS + 1))
    with pytest.raises(ScenarioPresetError, match="count"):
        save_presets(tmp_path / "other-presets.json", presets)


def test_link_or_junction_target_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "scenario-presets.json"
    original = Path.is_symlink

    def pretend_link(candidate):
        return candidate == path or original(candidate)

    monkeypatch.setattr(Path, "is_symlink", pretend_link)
    with pytest.raises(ScenarioPresetError, match="link|junction"):
        load_presets(path)
    with pytest.raises(ScenarioPresetError, match="link|junction"):
        save_presets(path, ())


def test_atomic_replace_failure_preserves_original_bytes(tmp_path, monkeypatch):
    path = tmp_path / "scenario-presets.json"
    original = (ScenarioPreset(
        "original", "原设置", DEVICES, OptimizationPolicy("cost")),)
    replacement = (ScenarioPreset(
        "replacement", "新设置", DEVICES, OptimizationPolicy("peak_cap", 55)),)
    save_presets(path, original)
    before = path.read_bytes()

    monkeypatch.setattr(
        "gridwise.scenario_presets.os.replace",
        lambda *_args: (_ for _ in ()).throw(
            PermissionError("synthetic preset replace denial")))

    with pytest.raises(ScenarioPresetError, match="synthetic preset replace denial") as failure:
        save_presets(path, replacement)

    assert isinstance(failure.value.__cause__, PermissionError)
    assert path.read_bytes() == before
    assert load_presets(path) == original
    assert list(path.parent.glob("scenario-presets-*.tmp")) == []
