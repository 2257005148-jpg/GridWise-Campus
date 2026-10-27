"""Behavior tests for validated custom grid and battery parameters."""

import math

import pytest

from gridwise.device import (DEVICE_FIELDS, DeviceParameterError,
                             DeviceParameters, parse_device_parameters)


DEFAULT_VALUES = {
    "grid_max_kw": "120",
    "capacity_kwh": "80",
    "initial_kwh": "40",
    "terminal_kwh": "40",
    "max_charge_kw": "30",
    "max_discharge_kw": "30",
    "charge_efficiency": "0.95",
    "discharge_efficiency": "0.95",
}


def test_valid_device_parameters_round_trip_to_scenario_fields():
    parameters = parse_device_parameters(DEFAULT_VALUES)

    assert parameters == DeviceParameters(
        grid_max_kw=120,
        capacity_kwh=80,
        initial_kwh=40,
        terminal_kwh=40,
        max_charge_kw=30,
        max_discharge_kw=30,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
    )
    scenario_fields = parameters.to_scenario_fields()
    assert scenario_fields == {
        "grid_max_kw": 120.0,
        "battery": {
            "capacity_kwh": 80.0,
            "initial_kwh": 40.0,
            "terminal_kwh": 40.0,
            "max_charge_kw": 30.0,
            "max_discharge_kw": 30.0,
            "charge_efficiency": 0.95,
            "discharge_efficiency": 0.95,
        },
    }
    assert DeviceParameters.from_scenario(scenario_fields) == parameters


@pytest.mark.parametrize(("field", "value", "message"), [
    ("grid_max_kw", "", "grid_max_kw"),
    ("grid_max_kw", "not-a-number", "grid_max_kw"),
    ("grid_max_kw", "NaN", "grid_max_kw"),
    ("grid_max_kw", "Infinity", "grid_max_kw"),
    ("grid_max_kw", "-1", "grid_max_kw"),
    ("capacity_kwh", "0", "capacity_kwh"),
    ("max_charge_kw", "-1", "max_charge_kw"),
    ("max_discharge_kw", "-1", "max_discharge_kw"),
    ("charge_efficiency", "0", "charge_efficiency"),
    ("charge_efficiency", "1.01", "charge_efficiency"),
    ("discharge_efficiency", "0", "discharge_efficiency"),
    ("discharge_efficiency", "1.01", "discharge_efficiency"),
])
def test_invalid_device_parameter_is_rejected_at_public_parser(field, value, message):
    values = dict(DEFAULT_VALUES)
    values[field] = value

    with pytest.raises(DeviceParameterError, match=message):
        parse_device_parameters(values)


@pytest.mark.parametrize("field", ["initial_kwh", "terminal_kwh"])
def test_soc_energy_cannot_exceed_capacity(field):
    values = dict(DEFAULT_VALUES, capacity_kwh="20", initial_kwh="0", terminal_kwh="0")
    values[field] = "21"

    with pytest.raises(DeviceParameterError, match=f"{field}.*capacity"):
        parse_device_parameters(values)


def test_parameter_mapping_must_have_exact_fields():
    missing = dict(DEFAULT_VALUES)
    missing.pop("terminal_kwh")
    with pytest.raises(DeviceParameterError, match="missing.*terminal_kwh"):
        parse_device_parameters(missing)

    with pytest.raises(DeviceParameterError, match="unexpected.*extra"):
        parse_device_parameters(dict(DEFAULT_VALUES, extra="1"))


def test_empty_and_full_soc_with_unit_efficiency_are_valid_boundaries():
    empty = parse_device_parameters(dict(
        DEFAULT_VALUES, initial_kwh="0", terminal_kwh="0",
        charge_efficiency="1", discharge_efficiency="1"))
    full = parse_device_parameters(dict(
        DEFAULT_VALUES, initial_kwh="80", terminal_kwh="80",
        charge_efficiency="1", discharge_efficiency="1"))

    assert empty.initial_kwh == empty.terminal_kwh == 0
    assert full.initial_kwh == full.terminal_kwh == full.capacity_kwh == 80
    assert empty.charge_efficiency == empty.discharge_efficiency == 1


def test_direct_dataclass_construction_rejects_nonfinite_values():
    values = {name: float(DEFAULT_VALUES[name]) for name in DEVICE_FIELDS}
    values["grid_max_kw"] = math.nan
    with pytest.raises(DeviceParameterError, match="grid_max_kw"):
        DeviceParameters(**values)
