import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from gridwise.core import InputError, SolverError, parse_scenario, solve, validate_result
from gridwise.cli import run


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "synthetic-day.json"


def raw():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_solves_and_exports(tmp_path):
    results = run(EXAMPLE, tmp_path)
    assert results["optimized"]["total_cost_cny"] < results["baseline"]["total_cost_cny"]
    assert all((tmp_path / name).is_file() for name in ("summary.json", "optimized.csv", "baseline.csv", "grid-purchase.svg"))
    validate_result(parse_scenario(raw()), results["optimized"])


def test_explicit_peak_cap_shaves_sample_below_no_storage_baseline():
    scenario = parse_scenario(raw())
    capped = solve(scenario, peak_cap_kw=55)
    baseline = solve(scenario, with_battery=False)
    validate_result(scenario, capped, peak_cap_kw=55)
    assert capped["status"] == "optimal"
    assert capped["peak_grid_kw"] <= 55 + 1e-5
    assert capped["peak_grid_kw"] < baseline["peak_grid_kw"]
    assert capped["total_cost_cny"] >= 131.01108033241 - 1e-5
    assert capped["total_cost_cny"] <= 162.7700831025 + 1e-5
    assert all(row["grid_kw"] <= 55 + 1e-5 for row in capped["rows"])


@pytest.mark.parametrize("load,pv,initial", [(0, 0, 0), (10, 0, 0), (0, 10, 80), (10, 0, 80)])
def test_energy_boundaries(load, pv, initial):
    data = raw()
    data["battery"]["initial_kwh"] = initial
    data["battery"]["terminal_kwh"] = initial
    for row in data["timeseries"]:
        row["load_kw"], row["pv_kw"] = load, pv
    for enabled in (True, False):
        answer = solve(parse_scenario(data), with_battery=enabled)
        validate_result(parse_scenario(data), answer)
        assert answer["rows"][-1]["soc_kwh"] == pytest.approx(initial, abs=1e-5)
        if load == pv == 0:
            assert answer["total_cost_cny"] == pytest.approx(0, abs=1e-5)
        if not enabled:
            assert all(r["charge_kw"] == pytest.approx(0, abs=1e-5) and r["discharge_kw"] == pytest.approx(0, abs=1e-5) for r in answer["rows"])


def test_infeasible_grid_cap():
    data = raw()
    data["grid_max_kw"] = 0
    data["battery"]["initial_kwh"] = 0
    data["battery"]["terminal_kwh"] = 0
    with pytest.raises(SolverError, match="no optimal solution"):
        solve(parse_scenario(data))


@pytest.mark.parametrize("mutate", [
    lambda d: d["timeseries"][0].pop("load_kw"),
    lambda d: d.update(power_unit="MW"),
    lambda d: d["timeseries"][1].update(timestamp=d["timeseries"][0]["timestamp"]),
    lambda d: d["battery"].update(charge_efficiency=1.2),
])
def test_bad_input(mutate):
    data = raw()
    mutate(data)
    with pytest.raises(InputError):
        parse_scenario(data)


def test_solver_unavailable():
    with pytest.raises(SolverError, match="solver unavailable"):
        solve(parse_scenario(raw()), solver_name="no_such_solver_gridwise")


def test_independent_validator_catches_corruption():
    scenario = parse_scenario(raw())
    result = solve(scenario)
    broken = copy.deepcopy(result)
    broken["rows"][0]["grid_kw"] += 1
    with pytest.raises(ValueError, match="power balance"):
        validate_result(scenario, broken)


@pytest.mark.parametrize("field", ["timestamp", "load_kw", "pv_available_kw", "price_cny_per_kwh"])
def test_validator_rejects_changed_input_echo(field):
    scenario = parse_scenario(raw())
    broken = copy.deepcopy(solve(scenario))
    broken["rows"][0][field] = "wrong" if field == "timestamp" else 999
    with pytest.raises(ValueError, match="input echo"):
        validate_result(scenario, broken)


def test_efficiency_and_terminal_soc_at_nonunit_step():
    data = raw()
    data["step_hours"] = 0.5
    data["timeseries"] = data["timeseries"][:2]
    data["timeseries"][1]["timestamp"] = "2026-01-01T08:30:00+08:00"
    data["timeseries"][0].update(load_kw=0, pv_kw=10, price_cny_per_kwh=2)
    data["timeseries"][1].update(load_kw=10, pv_kw=0, price_cny_per_kwh=1)
    data["battery"].update(initial_kwh=0, terminal_kwh=0, charge_efficiency=0.8, discharge_efficiency=0.5)
    result = solve(parse_scenario(data))
    first, second = result["rows"]
    assert first["charge_kw"] == pytest.approx(10)
    assert first["soc_kwh"] == pytest.approx(4)
    assert second["discharge_kw"] == pytest.approx(4)
    assert second["soc_kwh"] == pytest.approx(0)
    assert result["total_cost_cny"] == pytest.approx(3)


def test_empty_battery_cannot_supply_load_without_import():
    data = raw()
    data["battery"].update(initial_kwh=0, terminal_kwh=0)
    data["timeseries"] = data["timeseries"][:1]
    data["timeseries"][0].update(load_kw=10, pv_kw=0)
    answer = solve(parse_scenario(data))
    assert answer["rows"][0]["grid_kw"] == pytest.approx(10)


def test_full_battery_cannot_accept_surplus_without_curtailment():
    data = raw()
    data["battery"].update(initial_kwh=80, terminal_kwh=80)
    data["timeseries"] = data["timeseries"][:1]
    data["timeseries"][0].update(load_kw=0, pv_kw=10)
    answer = solve(parse_scenario(data))
    assert answer["rows"][0]["pv_curtailed_kw"] == pytest.approx(10)


def test_no_battery_baseline_infeasible_when_grid_cap_too_low():
    data = raw()
    data["grid_max_kw"] = 0
    with pytest.raises(SolverError, match="no optimal solution"):
        solve(parse_scenario(data), with_battery=False)


@pytest.mark.parametrize("step", [0.25, 0.5, 1.0])
def test_time_step_and_extreme_valid_prices(step):
    data = raw()
    data["step_hours"] = step
    data["timeseries"] = data["timeseries"][:2]
    first = datetime.fromisoformat(data["timeseries"][0]["timestamp"])
    data["timeseries"][1]["timestamp"] = (first + timedelta(hours=step)).isoformat()
    data["battery"].update(initial_kwh=0, terminal_kwh=0, max_charge_kw=0, max_discharge_kw=0)
    data["timeseries"][0].update(load_kw=10, pv_kw=0, price_cny_per_kwh=0)
    data["timeseries"][1].update(load_kw=10, pv_kw=0, price_cny_per_kwh=1_000_000)
    for enabled in (True, False):
        result = solve(parse_scenario(data), with_battery=enabled)
        assert result["grid_energy_kwh"] == pytest.approx(20 * step)
        assert result["total_cost_cny"] == pytest.approx(10_000_000 * step)
        assert result["peak_grid_kw"] == pytest.approx(10)


def test_terminal_soc_can_make_model_infeasible():
    data = raw()
    data["timeseries"] = data["timeseries"][:1]
    data["timeseries"][0].update(load_kw=0, pv_kw=0)
    data["battery"].update(initial_kwh=0, terminal_kwh=80)
    with pytest.raises(SolverError):
        solve(parse_scenario(data))


@pytest.mark.parametrize("mutate", [
    lambda d: d["timeseries"][0].pop("timestamp"),
    lambda d: d["timeseries"][1].update(timestamp="2026-01-01T07:00:00+08:00"),
    lambda d: d["timeseries"][0].update(load_kw=float("nan")),
    lambda d: d["timeseries"][0].update(price_cny_per_kwh=float("inf")),
    lambda d: d.update(energy_unit="MWh"),
    lambda d: d.update(price_unit="USD/kWh"),
])
def test_additional_invalid_input(mutate):
    data = raw()
    mutate(data)
    with pytest.raises(InputError):
        parse_scenario(data)


def test_solver_nonoptimal_status_is_rejected(monkeypatch):
    import pyomo.environ as pyo

    class NonoptimalSolver:
        def available(self, exception_flag=False):
            return True

        def solve(self, model, load_solutions=False):
            assert load_solutions is False
            return SimpleNamespace(solver=SimpleNamespace(
                status=pyo.SolverStatus.ok,
                termination_condition=pyo.TerminationCondition.infeasible))

    monkeypatch.setattr(pyo, "SolverFactory", lambda name: NonoptimalSolver())
    with pytest.raises(SolverError, match="no optimal solution"):
        solve(parse_scenario(raw()))


@pytest.mark.parametrize("step", [25, 1000000])
def test_excessive_step_is_rejected_by_model_input(step):
    data = raw()
    data["step_hours"] = step
    with pytest.raises(InputError, match="step_hours must be at most 24"):
        parse_scenario(data)


def test_excessive_period_count_is_rejected_by_model_input():
    data = raw()
    data["timeseries"] = [data["timeseries"][0]] * 50_001
    with pytest.raises(InputError, match="timeseries exceeds 50000 periods"):
        parse_scenario(data)


def test_excessive_numeric_magnitude_is_rejected_by_model_input():
    data = raw()
    data["grid_max_kw"] = 1e13
    with pytest.raises(InputError, match="exceeds supported magnitude"):
        parse_scenario(data)
