"""Independently audit GridWise exports using only the Python standard library.

All power values are kW, energy values kWh, prices CNY/kWh, and step_hours hours.
Comparisons use absolute Decimal tolerance 0.00001 in the relevant output unit.
No rounding is applied before validation; displayed totals are rounded to 2 decimals.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path


TOL = Decimal("0.00001")
INPUT_FIELDS = ("timestamp", "load_kw", "pv_kw", "price_cny_per_kwh")
ROW_FIELDS = ("timestamp", "load_kw", "pv_available_kw", "pv_used_kw", "pv_curtailed_kw",
              "grid_kw", "charge_kw", "discharge_kw", "soc_kwh", "price_cny_per_kwh")
NUMERIC_ROW_FIELDS = tuple(field for field in ROW_FIELDS if field != "timestamp")
METRICS = ("total_cost_cny", "grid_energy_kwh", "peak_grid_kw", "pv_curtailed_kwh")
BATTERY_FIELDS = ("capacity_kwh", "initial_kwh", "terminal_kwh", "max_charge_kw",
                  "max_discharge_kw", "charge_efficiency", "discharge_efficiency")


class AuditError(ValueError):
    """An input, export, or numerical check failed."""


def fail(where, detail):
    raise AuditError(f"{where}: {detail}")


def field(mapping, name, where):
    if not isinstance(mapping, dict) or name not in mapping or mapping[name] is None:
        fail(where, f"missing field {name}")
    return mapping[name]


def number(value, where, *, minimum=None, positive=False, csv_text=False):
    if isinstance(value, bool) or isinstance(value, (dict, list)) or (isinstance(value, str) and not csv_text):
        fail(where, "expected finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail(where, "expected finite number")
    if not result.is_finite():
        fail(where, "non-finite number")
    try:
        if not isfinite(float(result)):
            fail(where, "outside finite Python float range")
    except OverflowError:
        fail(where, "outside finite Python float range")
    if positive and result <= 0:
        fail(where, "must be positive")
    if minimum is not None and result < minimum:
        fail(where, f"must be at least {minimum}")
    return result


def close(actual, expected, where):
    if abs(actual - expected) > TOL:
        fail(where, f"expected {expected}, got {actual}; tolerance {TOL}")


def timestamp(value, where):
    if not isinstance(value, str):
        fail(where, "timestamp must be an ISO 8601 string")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        fail(where, "invalid ISO 8601 timestamp")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        fail(where, "timestamp needs a UTC offset")
    return stamp


def read_json(path):
    def reject_constant(value):
        fail(str(path), f"non-finite JSON constant {value}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal,
                          parse_int=Decimal, parse_constant=reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(str(path), f"cannot read JSON: {exc}")


def read_csv(path):
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None:
                fail(str(path), "missing CSV header")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                fail(str(path), "duplicate CSV header")
            for name in ROW_FIELDS:
                if name not in reader.fieldnames:
                    fail(str(path), f"missing CSV field {name}")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        fail(str(path), f"cannot read CSV: {exc}")
    for i, row in enumerate(rows):
        if None in row:
            fail(f"{path} row {i}", "extra CSV column")
    return rows


def audit(scenario_path, optimized_path, baseline_path, summary_path):
    source = read_json(scenario_path)
    for name, expected in (("power_unit", "kW"), ("energy_unit", "kWh"),
                           ("price_unit", "CNY/kWh")):
        if field(source, name, "scenario") != expected:
            fail("scenario", f"{name} must be {expected}")
    step = number(field(source, "step_hours", "scenario"), "scenario.step_hours", positive=True)
    grid_max = number(field(source, "grid_max_kw", "scenario"), "scenario.grid_max_kw", minimum=0)
    battery = field(source, "battery", "scenario")
    values = {name: number(field(battery, name, "scenario.battery"),
                           f"scenario.battery.{name}", minimum=0) for name in BATTERY_FIELDS}
    if values["capacity_kwh"] <= 0:
        fail("scenario.battery.capacity_kwh", "must be positive")
    for name in ("charge_efficiency", "discharge_efficiency"):
        if not 0 < values[name] <= 1:
            fail(f"scenario.battery.{name}", "must be in (0, 1]")
    for name in ("initial_kwh", "terminal_kwh"):
        if values[name] > values["capacity_kwh"]:
            fail(f"scenario.battery.{name}", "exceeds capacity")
    periods = field(source, "timeseries", "scenario")
    if not isinstance(periods, list) or not periods:
        fail("scenario.timeseries", "must be a nonempty list")
    stamps = []
    for i, row in enumerate(periods):
        where = f"scenario.timeseries[{i}]"
        stamp = timestamp(field(row, "timestamp", where), where)
        if stamps:
            delta = Decimal(str((stamp - stamps[-1]).total_seconds()))
            if delta == 0:
                fail(where, "duplicate timestamp")
            if delta < 0:
                fail(where, "out-of-order timestamp")
            if delta != step * 3600:
                fail(f"{where}.spacing_seconds", f"expected {step * 3600}, got {delta}")
        stamps.append(stamp)
        for name in INPUT_FIELDS[1:]:
            number(field(row, name, where), f"{where}.{name}", minimum=0)

    summary = read_json(summary_path)
    results = {}
    for kind, path, enabled in (("optimized", optimized_path, True), ("baseline", baseline_path, False)):
        result = field(summary, kind, "summary")
        if field(result, "status", f"summary.{kind}") != "optimal":
            fail(f"summary.{kind}.status", "must be optimal")
        if field(result, "with_battery", f"summary.{kind}") is not enabled:
            fail(f"summary.{kind}.with_battery", f"must be {enabled}")
        csv_rows = read_csv(path)
        json_rows = field(result, "rows", f"summary.{kind}")
        if not isinstance(json_rows, list):
            fail(f"summary.{kind}.rows", "must be a list")
        if len(csv_rows) != len(periods) or len(json_rows) != len(periods):
            fail(kind, f"row count mismatch: input={len(periods)}, CSV={len(csv_rows)}, summary={len(json_rows)}")
        prior = values["initial_kwh"]
        costs, energies, curtailments, grids = [], [], [], []
        for i, (source_row, csv_row, json_row, stamp) in enumerate(zip(periods, csv_rows, json_rows, stamps)):
            where = f"{kind} row {i} ({stamp.isoformat()})"
            expected_stamp = stamp.isoformat()
            if field(csv_row, "timestamp", where) != expected_stamp:
                fail(f"{where}.timestamp", "CSV timestamp differs from input or is duplicated/out of order")
            if field(json_row, "timestamp", f"summary.{where}") != expected_stamp:
                fail(f"summary.{where}.timestamp", "differs from input")
            row = {}
            for name in NUMERIC_ROW_FIELDS:
                row[name] = number(field(csv_row, name, where), f"{where}.{name}", csv_text=True)
                summary_number = number(field(json_row, name, f"summary.{where}"), f"summary.{where}.{name}")
                close(summary_number, row[name], f"summary.{where}.{name}")
            for name, source_name in (("load_kw", "load_kw"), ("pv_available_kw", "pv_kw"),
                                      ("price_cny_per_kwh", "price_cny_per_kwh")):
                close(row[name], number(source_row[source_name], f"scenario.timeseries[{i}].{source_name}"),
                      f"{where}.{name} input echo")
            for name in ("grid_kw", "charge_kw", "discharge_kw", "pv_used_kw", "pv_curtailed_kw", "soc_kwh"):
                if row[name] < -TOL:
                    fail(f"{where}.{name}", "negative value")
            close(row["grid_kw"] + row["discharge_kw"] + row["pv_used_kw"],
                  row["load_kw"] + row["charge_kw"], f"{where}.power_balance_kw")
            close(row["pv_used_kw"] + row["pv_curtailed_kw"], row["pv_available_kw"],
                  f"{where}.pv_split_kw")
            close(row["soc_kwh"], prior + step *
                  (values["charge_efficiency"] * row["charge_kw"] -
                   row["discharge_kw"] / values["discharge_efficiency"]), f"{where}.soc_flow_kwh")
            for name, maximum in (("grid_kw", grid_max), ("charge_kw", values["max_charge_kw"]),
                                  ("discharge_kw", values["max_discharge_kw"]),
                                  ("soc_kwh", values["capacity_kwh"]),
                                  ("pv_used_kw", row["pv_available_kw"])):
                if row[name] > maximum + TOL:
                    fail(f"{where}.{name}", f"exceeds maximum {maximum}")
            if min(row["charge_kw"], row["discharge_kw"]) > TOL:
                fail(where, "simultaneous charge and discharge")
            if not enabled and (abs(row["charge_kw"]) > TOL or abs(row["discharge_kw"]) > TOL):
                fail(where, "no-storage baseline used battery")
            costs.append(row["grid_kw"] * row["price_cny_per_kwh"] * step)
            energies.append(row["grid_kw"] * step)
            curtailments.append(row["pv_curtailed_kw"] * step)
            grids.append(row["grid_kw"])
            prior = row["soc_kwh"]
        terminal = values["terminal_kwh"] if enabled else values["initial_kwh"]
        close(prior, terminal, f"{kind}.terminal_soc_kwh")
        computed = dict(zip(METRICS, (sum(costs), sum(energies), max(grids), sum(curtailments))))
        for name, value in computed.items():
            reported = number(field(result, name, f"summary.{kind}"), f"summary.{kind}.{name}")
            close(reported, value, f"summary.{kind}.{name}")
        results[kind] = computed
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit exported offline dispatch without Pyomo or HiGHS")
    parser.add_argument("scenario", type=Path)
    parser.add_argument("optimized_csv", type=Path)
    parser.add_argument("baseline_csv", type=Path)
    parser.add_argument("summary_json", type=Path)
    args = parser.parse_args(argv)
    try:
        results = audit(args.scenario, args.optimized_csv, args.baseline_csv, args.summary_json)
    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 2
    for kind, metrics in results.items():
        print(f"{kind}: " + ", ".join(f"{name}={value:.2f}" for name, value in metrics.items()))
    print(f"AUDIT PASSED (absolute tolerance {TOL}; no pre-validation rounding)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
