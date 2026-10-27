"""Validated offline grid and battery parameters for dashboard scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping


MAX_PARAMETER = 1_000_000_000_000.0
MAX_TOKEN_CHARS = 128
DEVICE_FIELDS = (
    "grid_max_kw",
    "capacity_kwh",
    "initial_kwh",
    "terminal_kwh",
    "max_charge_kw",
    "max_discharge_kw",
    "charge_efficiency",
    "discharge_efficiency",
)


class DeviceParameterError(ValueError):
    """Device parameters cannot be represented by the existing model contract."""


def _json_number(value: float) -> int | float:
    """Keep integer-valued defaults byte-compatible with the existing template."""
    return int(value) if value.is_integer() else value


def _number(value, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or value is None:
        raise DeviceParameterError(f"{field}: expected a finite number")
    token = str(value).strip()
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise DeviceParameterError(f"{field}: expected a finite number")
    try:
        result = float(token)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DeviceParameterError(f"{field}: expected a finite number") from exc
    if not isfinite(result) or result > MAX_PARAMETER or result < 0 or (positive and result == 0):
        boundary = "positive" if positive else "nonnegative"
        raise DeviceParameterError(
            f"{field}: expected a finite {boundary} number at most {MAX_PARAMETER:g}")
    return result


@dataclass(frozen=True)
class DeviceParameters:
    """The grid and battery fields consumed by ``core.parse_scenario``."""

    grid_max_kw: float
    capacity_kwh: float
    initial_kwh: float
    terminal_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    charge_efficiency: float
    discharge_efficiency: float

    def __post_init__(self) -> None:
        for field in DEVICE_FIELDS:
            value = _number(getattr(self, field), field,
                            positive=field in {"capacity_kwh", "charge_efficiency",
                                               "discharge_efficiency"})
            object.__setattr__(self, field, value)
        if self.initial_kwh > self.capacity_kwh:
            raise DeviceParameterError("initial_kwh: exceeds battery capacity_kwh")
        if self.terminal_kwh > self.capacity_kwh:
            raise DeviceParameterError("terminal_kwh: exceeds battery capacity_kwh")
        for field in ("charge_efficiency", "discharge_efficiency"):
            if getattr(self, field) > 1:
                raise DeviceParameterError(f"{field}: must be in (0, 1]")

    def to_scenario_fields(self) -> dict:
        return {
            "grid_max_kw": _json_number(self.grid_max_kw),
            "battery": {
                field: _json_number(getattr(self, field))
                for field in DEVICE_FIELDS[1:]
            },
        }

    @classmethod
    def from_scenario(cls, scenario: Mapping) -> "DeviceParameters":
        try:
            battery = scenario["battery"]
            values = {"grid_max_kw": scenario["grid_max_kw"]}
            values.update({field: battery[field] for field in DEVICE_FIELDS[1:]})
        except (KeyError, TypeError) as exc:
            raise DeviceParameterError(f"scenario device parameters missing or invalid: {exc}") from exc
        return parse_device_parameters(values)


def parse_device_parameters(values: Mapping) -> DeviceParameters:
    """Parse a complete UI/config mapping and reject missing or extra fields."""
    if not isinstance(values, Mapping):
        raise DeviceParameterError("device parameters: expected a mapping")
    missing = sorted(set(DEVICE_FIELDS) - set(values))
    unexpected = sorted(set(values) - set(DEVICE_FIELDS))
    if missing:
        raise DeviceParameterError(f"device parameters: missing {', '.join(missing)}")
    if unexpected:
        raise DeviceParameterError(f"device parameters: unexpected {', '.join(unexpected)}")
    parsed = {
        field: _number(values[field], field,
                       positive=field in {"capacity_kwh", "charge_efficiency",
                                          "discharge_efficiency"})
        for field in DEVICE_FIELDS
    }
    return DeviceParameters(**parsed)
