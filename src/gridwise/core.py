"""Small, auditable offline campus dispatch model. Power is in kW, energy in kWh."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite


class InputError(ValueError):
    """Input data violate the documented contract."""


class SolverError(RuntimeError):
    """A usable optimum was not obtained."""


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float
    initial_kwh: float
    terminal_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    charge_efficiency: float
    discharge_efficiency: float


@dataclass(frozen=True)
class Scenario:
    timestamps: tuple[datetime, ...]
    step_hours: float
    load_kw: tuple[float, ...]
    pv_kw: tuple[float, ...]
    price_cny_per_kwh: tuple[float, ...]
    grid_max_kw: float
    battery: Battery


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise InputError(f"{label} must be a finite number")
    if (positive and value <= 0) or (not positive and value < 0):
        raise InputError(f"{label} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def parse_scenario(data: dict) -> Scenario:
    try:
        if data["power_unit"] != "kW" or data["energy_unit"] != "kWh" or data["price_unit"] != "CNY/kWh":
            raise InputError("units must be kW, kWh, and CNY/kWh")
        step = _number(data["step_hours"], "step_hours", positive=True)
        rows = data["timeseries"]
        if not isinstance(rows, list) or not rows:
            raise InputError("timeseries must be a nonempty list")
        stamps, load, pv, price = [], [], [], []
        for i, row in enumerate(rows):
            stamp = datetime.fromisoformat(row["timestamp"])
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise InputError("timestamps must contain a UTC offset")
            stamps.append(stamp)
            load.append(_number(row["load_kw"], f"timeseries[{i}].load_kw"))
            pv.append(_number(row["pv_kw"], f"timeseries[{i}].pv_kw"))
            price.append(_number(row["price_cny_per_kwh"], f"timeseries[{i}].price_cny_per_kwh"))
        if any((b - a).total_seconds() != step * 3600 for a, b in zip(stamps, stamps[1:])):
            raise InputError("timestamps must be consecutive at step_hours spacing")
        raw = data["battery"]
        battery = Battery(*(_number(raw[k], k, positive=k in {"capacity_kwh", "charge_efficiency", "discharge_efficiency"}) for k in (
            "capacity_kwh", "initial_kwh", "terminal_kwh", "max_charge_kw", "max_discharge_kw", "charge_efficiency", "discharge_efficiency")))
        if battery.initial_kwh > battery.capacity_kwh or battery.terminal_kwh > battery.capacity_kwh:
            raise InputError("battery energy exceeds capacity")
        if battery.charge_efficiency > 1 or battery.discharge_efficiency > 1:
            raise InputError("battery efficiencies must be at most 1")
        return Scenario(tuple(stamps), step, tuple(load), tuple(pv), tuple(price),
                        _number(data["grid_max_kw"], "grid_max_kw"), battery)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"missing or invalid scenario field: {exc}") from exc


def solve(s: Scenario, *, solver_name="appsi_highs", with_battery=True) -> dict:
    import pyomo.environ as pyo

    try:
        solver = pyo.SolverFactory(solver_name)
        if solver is None or not solver.available(exception_flag=False):
            raise SolverError(f"solver unavailable: {solver_name}")
    except (ImportError, RuntimeError, ValueError) as exc:
        raise SolverError(f"solver unavailable: {solver_name}") from exc
    b = s.battery
    n = len(s.load_kw)
    m = pyo.ConcreteModel()
    m.T = pyo.RangeSet(0, n - 1)
    m.grid = pyo.Var(m.T, bounds=(0, s.grid_max_kw))
    m.charge = pyo.Var(m.T, bounds=(0, b.max_charge_kw if with_battery else 0))
    m.discharge = pyo.Var(m.T, bounds=(0, b.max_discharge_kw if with_battery else 0))
    m.pv_used = pyo.Var(m.T, bounds=lambda _, t: (0, s.pv_kw[t]))
    m.soc = pyo.Var(m.T, bounds=(0, b.capacity_kwh))
    m.mode = pyo.Var(m.T, within=pyo.Binary)
    m.balance = pyo.Constraint(m.T, rule=lambda _, t: m.grid[t] + m.discharge[t] + m.pv_used[t] == s.load_kw[t] + m.charge[t])
    m.soc_flow = pyo.Constraint(m.T, rule=lambda _, t: m.soc[t] ==
        (b.initial_kwh if t == 0 else m.soc[t - 1]) +
        s.step_hours * (b.charge_efficiency * m.charge[t] - m.discharge[t] / b.discharge_efficiency))
    m.charge_mode = pyo.Constraint(m.T, rule=lambda _, t: m.charge[t] <= b.max_charge_kw * m.mode[t])
    m.discharge_mode = pyo.Constraint(m.T, rule=lambda _, t: m.discharge[t] <= b.max_discharge_kw * (1 - m.mode[t]))
    m.terminal = pyo.Constraint(expr=m.soc[n - 1] == b.terminal_kwh if with_battery else m.soc[n - 1] == b.initial_kwh)
    m.cost = pyo.Objective(expr=sum(m.grid[t] * s.price_cny_per_kwh[t] * s.step_hours for t in m.T))
    try:
        result = solver.solve(m, load_solutions=False)
    except Exception as exc:
        raise SolverError(f"solver failed: {exc}") from exc
    if result.solver.status != pyo.SolverStatus.ok or result.solver.termination_condition != pyo.TerminationCondition.optimal:
        raise SolverError(f"no optimal solution: {result.solver.status}, {result.solver.termination_condition}")
    m.solutions.load_from(result)
    rows = []
    for t in range(n):
        rows.append({"timestamp": s.timestamps[t].isoformat(), "load_kw": s.load_kw[t], "pv_available_kw": s.pv_kw[t],
                     "pv_used_kw": pyo.value(m.pv_used[t]), "pv_curtailed_kw": s.pv_kw[t] - pyo.value(m.pv_used[t]),
                     "grid_kw": pyo.value(m.grid[t]), "charge_kw": pyo.value(m.charge[t]),
                     "discharge_kw": pyo.value(m.discharge[t]), "soc_kwh": pyo.value(m.soc[t]),
                     "price_cny_per_kwh": s.price_cny_per_kwh[t]})
    answer = {"status": "optimal", "with_battery": with_battery, "rows": rows,
              "total_cost_cny": sum(r["grid_kw"] * r["price_cny_per_kwh"] * s.step_hours for r in rows),
              "grid_energy_kwh": sum(r["grid_kw"] * s.step_hours for r in rows),
              "peak_grid_kw": max(r["grid_kw"] for r in rows),
              "pv_curtailed_kwh": sum(r["pv_curtailed_kw"] * s.step_hours for r in rows)}
    validate_result(s, answer)
    return answer


def validate_result(s: Scenario, answer: dict, tolerance=1e-5) -> None:
    """Check exported numbers with Python arithmetic, without consulting Pyomo."""
    rows = answer["rows"]
    if len(rows) != len(s.load_kw):
        raise ValueError("wrong result length")
    b = s.battery
    prior = b.initial_kwh
    for t, r in enumerate(rows):
        if (r["timestamp"] != s.timestamps[t].isoformat()
                or r["load_kw"] != s.load_kw[t]
                or r["pv_available_kw"] != s.pv_kw[t]
                or r["price_cny_per_kwh"] != s.price_cny_per_kwh[t]):
            raise ValueError(f"input echo failed at {t}")
        for key in ("grid_kw", "charge_kw", "discharge_kw", "pv_used_kw", "pv_curtailed_kw", "soc_kwh"):
            if not isfinite(r[key]) or r[key] < -tolerance:
                raise ValueError(f"invalid {key} at {t}")
        if abs(r["grid_kw"] + r["discharge_kw"] + r["pv_used_kw"] - s.load_kw[t] - r["charge_kw"]) > tolerance:
            raise ValueError(f"power balance failed at {t}")
        if abs(r["pv_used_kw"] + r["pv_curtailed_kw"] - s.pv_kw[t]) > tolerance:
            raise ValueError(f"PV split failed at {t}")
        expected = prior + s.step_hours * (b.charge_efficiency * r["charge_kw"] - r["discharge_kw"] / b.discharge_efficiency)
        if abs(r["soc_kwh"] - expected) > tolerance:
            raise ValueError(f"SOC flow failed at {t}")
        if r["soc_kwh"] > b.capacity_kwh + tolerance or r["grid_kw"] > s.grid_max_kw + tolerance:
            raise ValueError(f"capacity failed at {t}")
        if r["charge_kw"] > b.max_charge_kw + tolerance or r["discharge_kw"] > b.max_discharge_kw + tolerance:
            raise ValueError(f"battery power failed at {t}")
        if min(r["charge_kw"], r["discharge_kw"]) > tolerance:
            raise ValueError(f"simultaneous charge/discharge at {t}")
        prior = r["soc_kwh"]
    target = b.terminal_kwh if answer["with_battery"] else b.initial_kwh
    if abs(prior - target) > tolerance:
        raise ValueError("terminal SOC failed")
    if not answer["with_battery"] and any(r["charge_kw"] > tolerance or r["discharge_kw"] > tolerance for r in rows):
        raise ValueError("baseline used battery")
    computed = (sum(r["grid_kw"] * s.price_cny_per_kwh[t] * s.step_hours for t, r in enumerate(rows)),
                sum(r["grid_kw"] * s.step_hours for r in rows),
                max(r["grid_kw"] for r in rows),
                sum(r["pv_curtailed_kw"] * s.step_hours for r in rows))
    reported = (answer["total_cost_cny"], answer["grid_energy_kwh"], answer["peak_grid_kw"], answer["pv_curtailed_kwh"])
    if any(abs(a - b) > tolerance for a, b in zip(computed, reported)):
        raise ValueError("summary does not match rows")
