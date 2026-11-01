"""Behavior tests for versioned scenario-preset storage."""

import json
from pathlib import Path

import pytest

from gridwise.device import DeviceParameters
from gridwise.scenario_presets import (MAX_FILE_BYTES, MAX_PRESETS,
                                       OptimizationPolicy, ScenarioPreset,
                                       ScenarioPresetError, create_preset,
                                       delete_preset, load_presets, save_presets,
                                       update_preset)


DEVICES = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)


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
