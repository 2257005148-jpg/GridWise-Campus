"""Independently audit GridWise exports using only the Python standard library.

All power values are kW, energy values kWh, prices CNY/kWh, and step_hours hours.
Comparisons use absolute Decimal tolerance 0.00001 in the relevant output unit.
No rounding is applied before validation; displayed totals are rounded to 2 decimals.
"""

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path


TOL = Decimal("0.00001")
MODEL_VERSION = "cost-mip-v1"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_CSV_LINE_BYTES = 64 * 1024
MAX_NUMERIC_CHARS = 128
MAX_ABS_VALUE = Decimal("1000000000000")
MAX_PERIODS = 50_000
MAX_STEP_HOURS = Decimal("24")
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


def exact_fields(mapping, expected, where):
    if not isinstance(mapping, dict):
        fail(where, "expected object")
    extras = sorted(set(mapping) - set(expected))
    if extras:
        fail(where, f"unexpected field {extras[0]}")
    for name in expected:
        field(mapping, name, where)


def number(value, where, *, minimum=None, positive=False, csv_text=False):
    if isinstance(value, bool) or isinstance(value, (dict, list)) or (isinstance(value, str) and not csv_text):
        fail(where, "expected finite number")
    if len(str(value)) > MAX_NUMERIC_CHARS:
        fail(where, f"numeric token exceeds {MAX_NUMERIC_CHARS} characters")
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
    if abs(result) > MAX_ABS_VALUE:
        fail(where, f"absolute value exceeds {MAX_ABS_VALUE}")
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


def read_limited_bytes(path, kind):
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        fail(str(path), f"cannot read {kind}: {exc}")
    if len(data) > MAX_FILE_BYTES:
        fail(str(path), f"{kind} exceeds {MAX_FILE_BYTES} bytes")
    return data


def parse_json_bytes(data, path):
    def reject_constant(value):
        fail(str(path), f"non-finite JSON constant {value}")
    def parse_number(value):
        if len(value) > MAX_NUMERIC_CHARS:
            fail(str(path), f"numeric token exceeds {MAX_NUMERIC_CHARS} characters")
        return Decimal(value)
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail(str(path), f"duplicate JSON key {key}")
            result[key] = value
        return result
    try:
        decoded = data.decode("utf-8-sig")
        return json.loads(decoded, parse_float=parse_number, parse_int=parse_number,
                          parse_constant=reject_constant, object_pairs_hook=unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        fail(str(path), f"cannot read JSON: {exc}")


def read_json(path):
    return parse_json_bytes(read_limited_bytes(path, "JSON"), path)


def read_csv(path):
    try:
        data = read_limited_bytes(path, "CSV")
        line_stream = io.BytesIO(data)
        line_number = 0
        while line := line_stream.readline(MAX_CSV_LINE_BYTES + 1):
            line_number += 1
            if line_number > MAX_PERIODS + 1:
                fail(str(path), f"CSV physical line count exceeds {MAX_PERIODS + 1}")
            if len(line) > MAX_CSV_LINE_BYTES:
                fail(f"{path} line {line_number}", f"exceeds {MAX_CSV_LINE_BYTES} bytes")
            if not line.strip():
                fail(f"{path} line {line_number}", "blank CSV line")
        csv.field_size_limit(MAX_CSV_LINE_BYTES)
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        if reader.fieldnames is None:
            fail(str(path), "missing CSV header")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            fail(str(path), "duplicate CSV header")
        missing = set(ROW_FIELDS) - set(reader.fieldnames)
        if missing:
            fail(str(path), f"missing CSV field {sorted(missing)[0]}")
        extra = set(reader.fieldnames) - set(ROW_FIELDS)
        if extra:
            fail(str(path), f"unexpected CSV field {sorted(extra)[0]}")
        if tuple(reader.fieldnames) != ROW_FIELDS:
            fail(str(path), "unexpected CSV column order")
        rows = []
        for row in reader:
            if len(rows) >= MAX_PERIODS:
                fail(str(path), f"CSV row count exceeds {MAX_PERIODS}")
            rows.append(row)
    except (UnicodeError, csv.Error) as exc:
        fail(str(path), f"cannot read CSV: {exc}")
    for i, row in enumerate(rows):
        if None in row:
            fail(f"{path} row {i}", "extra CSV column")
    return rows


def audit(scenario_path, optimized_path, baseline_path, summary_path):
    source_bytes = read_limited_bytes(scenario_path, "JSON")
    source = parse_json_bytes(source_bytes, scenario_path)
    exact_fields(source, ("power_unit", "energy_unit", "price_unit", "step_hours",
                          "grid_max_kw", "battery", "timeseries"), "scenario")
    for name, expected in (("power_unit", "kW"), ("energy_unit", "kWh"),
                           ("price_unit", "CNY/kWh")):
        if field(source, name, "scenario") != expected:
            fail("scenario", f"{name} must be {expected}")
    step = number(field(source, "step_hours", "scenario"), "scenario.step_hours", positive=True)
    if step > MAX_STEP_HOURS:
        fail("scenario.step_hours", f"exceeds {MAX_STEP_HOURS} hours")
    grid_max = number(field(source, "grid_max_kw", "scenario"), "scenario.grid_max_kw", minimum=0)
    battery = field(source, "battery", "scenario")
    exact_fields(battery, BATTERY_FIELDS, "scenario.battery")
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
    if len(periods) > MAX_PERIODS:
        fail("scenario.timeseries", f"row count exceeds {MAX_PERIODS}")
    stamps = []
    for i, row in enumerate(periods):
        where = f"scenario.timeseries[{i}]"
        exact_fields(row, INPUT_FIELDS, where)
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
    exact_fields(summary, ("run", "optimized", "baseline"), "summary")
    run = field(summary, "run", "summary")
    exact_fields(run, ("model_version", "scenario_sha256", "solver_name", "status"), "summary.run")
    if field(run, "model_version", "summary.run") != MODEL_VERSION:
        fail("summary.run.model_version", f"must be {MODEL_VERSION}")
    expected_hash = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    if field(run, "scenario_sha256", "summary.run") != expected_hash:
        fail("summary.run.scenario_sha256", "does not match source JSON bytes")
    if field(run, "solver_name", "summary.run") != "appsi_highs":
        fail("summary.run.solver_name", "must be appsi_highs")
    if field(run, "status", "summary.run") != "optimal":
        fail("summary.run.status", "must be optimal")

    results = {}
    for kind, path, enabled in (("optimized", optimized_path, True), ("baseline", baseline_path, False)):
        result = field(summary, kind, "summary")
        exact_fields(result, ("status", "with_battery", "rows", *METRICS), f"summary.{kind}")
        if field(result, "status", f"summary.{kind}") != "optimal":
            fail(f"summary.{kind}.status", "must be optimal")
        if field(result, "with_battery", f"summary.{kind}") is not enabled:
            fail(f"summary.{kind}.with_battery", f"must be {enabled}")
        csv_rows = read_csv(path)
        json_rows = field(result, "rows", f"summary.{kind}")
        if not isinstance(json_rows, list):
            fail(f"summary.{kind}.rows", "must be a list")
        if len(json_rows) > MAX_PERIODS:
            fail(f"summary.{kind}.rows", f"row count exceeds {MAX_PERIODS}")
        if len(csv_rows) != len(periods) or len(json_rows) != len(periods):
            fail(kind, f"row count mismatch: input={len(periods)}, CSV={len(csv_rows)}, summary={len(json_rows)}")
        prior = values["initial_kwh"]
        costs, energies, curtailments, grids = [], [], [], []
        for i, (source_row, csv_row, json_row, stamp) in enumerate(zip(periods, csv_rows, json_rows, stamps)):
            where = f"{kind} row {i} ({stamp.isoformat()})"
            exact_fields(json_row, ROW_FIELDS, f"summary.{where}")
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
