"""Offline CSV quality checks and chart data preparation. No solver or device access."""

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


HEADERS = ("timestamp", "load_kw", "pv_kw", "price_cny_per_kwh")
NUMERIC_FIELDS = HEADERS[1:]
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_PERIODS = 50_000
MAX_NUMERIC_CHARS = 128
MAX_VALUE = Decimal("1000000000000")
TOL = Decimal("0.00001")


class DataError(ValueError):
    """Chart inputs do not match a validated energy series."""


@dataclass(frozen=True)
class EnergyRecord:
    timestamp: str
    load_kw: Decimal
    pv_kw: Decimal
    price_cny_per_kwh: Decimal


@dataclass(frozen=True)
class QualityIssue:
    code: str
    line: int
    field: str
    detail: str


@dataclass(frozen=True)
class QualityReport:
    source: str
    ready: bool
    row_count: int
    step_hours: str
    missing_counts: dict[str, int]
    issues: tuple[QualityIssue, ...]


@dataclass(frozen=True)
class ImportResult:
    records: tuple[EnergyRecord, ...]
    report: QualityReport


def _decimal(value, where, *, max_value=MAX_VALUE):
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise DataError(f"{where}: expected finite nonnegative number")
    token = str(value).strip()
    if len(token) > MAX_NUMERIC_CHARS:
        raise DataError(f"{where}: numeric token exceeds {MAX_NUMERIC_CHARS} characters")
    try:
        number = Decimal(token)
    except InvalidOperation as exc:
        raise DataError(f"{where}: expected finite nonnegative number") from exc
    if not number.is_finite() or number < 0 or number > max_value:
        raise DataError(f"{where}: expected finite nonnegative number at most {max_value}")
    return number


def _step(value):
    step = _decimal(value, "step_hours", max_value=Decimal(24))
    if step == 0:
        raise DataError("step_hours: must be positive")
    return step


def import_energy_csv(path: Path, step_hours) -> ImportResult:
    """Return a quality report and complete records only when every check passes."""
    path = Path(path)
    missing = {name: 0 for name in HEADERS}
    issues = []
    records = []
    row_count = 0

    def finish():
        report = QualityReport(str(path), not issues, row_count,
                               str(step) if step is not None else str(step_hours),
                               missing, tuple(issues))
        return ImportResult(tuple(records) if report.ready else (), report)

    try:
        step = _step(step_hours)
    except DataError as exc:
        step = None
        issues.append(QualityIssue("invalid_step", 0, "step_hours", str(exc)))
        return finish()
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        issues.append(QualityIssue("read_error", 0, "file", f"{path}: {exc}"))
        return finish()
    if len(data) > MAX_FILE_BYTES:
        issues.append(QualityIssue("file_too_large", 0, "file", f"exceeds {MAX_FILE_BYTES} bytes"))
        return finish()
    for line_no, line in enumerate(data.splitlines(keepends=True), 1):
        if len(line) > MAX_LINE_BYTES:
            issues.append(QualityIssue("line_too_long", line_no, "file",
                                       f"exceeds {MAX_LINE_BYTES} bytes"))
            return finish()
        if not line.strip():
            issues.append(QualityIssue("blank_line", line_no, "file", "blank physical line"))
            return finish()
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeError as exc:
        issues.append(QualityIssue("encoding", 0, "file", f"invalid UTF-8: {exc}"))
        return finish()
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            issues.append(QualityIssue("empty_file", 1, "header", "CSV is empty"))
            return finish()
        if len(header) != len(set(header)):
            issues.append(QualityIssue("duplicate_header", 1, "header", "duplicate column name"))
            return finish()
        if tuple(header) != HEADERS:
            issues.append(QualityIssue("header", 1, "header",
                                       f"expected {','.join(HEADERS)}; units are encoded in names"))
            return finish()
        previous = None
        seen = set()
        for values in reader:
            row_count += 1
            line = reader.line_num
            if row_count > MAX_PERIODS:
                issues.append(QualityIssue("too_many_periods", line, "file",
                                           f"exceeds {MAX_PERIODS} periods"))
                break
            if len(values) != len(HEADERS):
                issues.append(QualityIssue("field_count", line, "row",
                                           f"expected {len(HEADERS)} fields, got {len(values)}"))
            row = {name: values[index].strip() if index < len(values) else ""
                   for index, name in enumerate(HEADERS)}
            for name, value in row.items():
                if value == "":
                    missing[name] += 1
                    issues.append(QualityIssue("missing", line, name, "missing value"))
            stamp = None
            if row["timestamp"]:
                try:
                    stamp = datetime.fromisoformat(row["timestamp"])
                    if stamp.tzinfo is None or stamp.utcoffset() is None:
                        raise ValueError("UTC offset required")
                except ValueError:
                    issues.append(QualityIssue("timestamp", line, "timestamp",
                                               "expected ISO 8601 timestamp with UTC offset"))
                    stamp = None
            numbers = {}
            for name in NUMERIC_FIELDS:
                if row[name]:
                    try:
                        numbers[name] = _decimal(row[name], f"line {line}.{name}")
                    except DataError as exc:
                        issues.append(QualityIssue("number", line, name, str(exc)))
            if stamp is not None:
                if stamp in seen:
                    issues.append(QualityIssue("duplicate_timestamp", line, "timestamp",
                                               "same instant appears more than once"))
                seen.add(stamp)
                if previous is not None:
                    elapsed = Decimal(str((stamp - previous).total_seconds())) / Decimal(3600)
                    if elapsed < 0:
                        issues.append(QualityIssue("out_of_order", line, "timestamp",
                                                   "timestamp precedes previous valid timestamp"))
                    elif elapsed > 0 and elapsed != step:
                        issues.append(QualityIssue("time_gap", line, "timestamp",
                                                   f"expected {step} h, got {elapsed} h"))
                previous = stamp
            if stamp is not None and len(numbers) == len(NUMERIC_FIELDS):
                records.append(EnergyRecord(row["timestamp"], *(numbers[name] for name in NUMERIC_FIELDS)))
    except csv.Error as exc:
        issues.append(QualityIssue("csv_error", reader.line_num, "file", str(exc)))
    if row_count == 0:
        issues.append(QualityIssue("empty_series", reader.line_num or 1, "file", "no data rows"))
    return finish()


def _export_number(row, name, where):
    if not isinstance(row, dict) or name not in row:
        raise DataError(f"{where}.{name}: missing")
    return _decimal(row[name], f"{where}.{name}")


def prepare_visualization_data(imported: ImportResult, summary: dict) -> dict:
    """Align existing export rows and calculate distinct cost, energy and peak metrics."""
    if not imported.report.ready or not imported.records:
        raise DataError("input CSV is not ready for visualization")
    if not isinstance(summary, dict):
        raise DataError("summary: expected object")
    step = _step(imported.report.step_hours)
    series = {name: [] for name in ("load_kw", "pv_kw", "optimized_grid_kw",
              "baseline_grid_kw", "optimized_soc_kwh", "optimized_period_cost_cny",
              "baseline_period_cost_cny", "optimized_cumulative_cost_cny",
              "baseline_cumulative_cost_cny")}
    metrics = {}
    timestamps = [record.timestamp for record in imported.records]
    for kind in ("optimized", "baseline"):
        result = summary.get(kind)
        if not isinstance(result, dict) or result.get("status") != "optimal":
            raise DataError(f"summary.{kind}: missing optimal result")
        if result.get("with_battery") is not (kind == "optimized"):
            raise DataError(f"summary.{kind}.with_battery: mismatch")
        rows = result.get("rows")
        if not isinstance(rows, list) or len(rows) != len(imported.records):
            raise DataError(f"summary.{kind}.rows: row count mismatch")
        costs, grids, socs = [], [], []
        for index, (source, row) in enumerate(zip(imported.records, rows, strict=True), 1):
            where = f"summary.{kind}.rows[{index}]"
            if not isinstance(row, dict) or row.get("timestamp") != source.timestamp:
                raise DataError(f"{where}.timestamp: differs from CSV input")
            for export_name, expected in (("load_kw", source.load_kw),
                                          ("pv_available_kw", source.pv_kw),
                                          ("price_cny_per_kwh", source.price_cny_per_kwh)):
                if abs(_export_number(row, export_name, where) - expected) > TOL:
                    raise DataError(f"{where}.{export_name}: differs from CSV input")
            grid = _export_number(row, "grid_kw", where)
            soc = _export_number(row, "soc_kwh", where)
            grids.append(grid)
            socs.append(soc)
            costs.append(grid * source.price_cny_per_kwh * step)
        total = sum(costs, Decimal(0))
        energy = sum((grid * step for grid in grids), Decimal(0))
        peak = max(grids)
        for name, actual in (("total_cost_cny", total), ("grid_energy_kwh", energy),
                             ("peak_grid_kw", peak)):
            if abs(_export_number(result, name, f"summary.{kind}") - actual) > TOL:
                raise DataError(f"summary.{kind}.{name}: differs from recalculated rows")
        metrics[kind] = {"cost_cny": float(total), "grid_energy_kwh": float(energy),
                         "peak_grid_kw": float(peak)}
        series[f"{kind}_grid_kw"] = [float(value) for value in grids]
        series[f"{kind}_period_cost_cny"] = [float(value) for value in costs]
        running = Decimal(0)
        cumulative = []
        for value in costs:
            running += value
            cumulative.append(float(running))
        series[f"{kind}_cumulative_cost_cny"] = cumulative
        if kind == "optimized":
            series["optimized_soc_kwh"] = [float(value) for value in socs]
    series["load_kw"] = [float(record.load_kw) for record in imported.records]
    series["pv_kw"] = [float(record.pv_kw) for record in imported.records]
    return {"timestamps": timestamps,
            "units": {"power": "kW", "energy": "kWh", "price": "CNY/kWh",
                      "cost": "CNY", "step": "h"},
            "step_hours": float(step), "series": series, "metrics": metrics,
            "delta_optimized_minus_baseline": {
                key: metrics["optimized"][key] - metrics["baseline"][key]
                for key in ("cost_cny", "grid_energy_kwh", "peak_grid_kw")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect offline energy CSV quality")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--step-hours", required=True)
    args = parser.parse_args(argv)
    result = import_energy_csv(args.csv_path, args.step_hours)
    print(json.dumps(asdict(result.report), ensure_ascii=True, indent=2))
    return 0 if result.report.ready else 2


if __name__ == "__main__":
    sys.exit(main())
