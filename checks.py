"""Read-only inspection. Business checks have no dependency on FastAPI."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import (
    APP_VERSION, MAX_CHANGES, MAX_HEADER_LENGTH, MAX_ISSUE_DETAILS,
    REPORT_SCHEMA_VERSION, Settings, load_settings,
)
from csv_core import CSVOptions, CSVRecord, CSVTable, DoctorError, read_csv
from csv_profile import CHECK_IDS, load_profile, validate_profile


def trim(value: str, *, trim_nbsp: bool = False) -> str:
    """Strip ONLY edge SPACE/TAB; optionally NBSP. Never strip CR or LF."""
    return value.strip(" \t\u00a0" if trim_nbsp else " \t")


def make_candidate(table: CSVTable, *, trim_nbsp: bool = False) -> tuple[tuple[str, ...], ...]:
    """Independent immutable candidate, including header, without changing table."""
    return tuple(
        tuple(trim(value, trim_nbsp=trim_nbsp) for value in record.values)
        for record in (table.header, *table.records)
    )


class _Issues:
    """Count every issue but retain at most 1,000 details in check order."""

    def __init__(self) -> None:
        self.details: list[dict[str, Any]] = []
        self.counts = {cid: {"error": 0, "warning": 0} for cid in CHECK_IDS}

    def add(
        self, check_id: str, code: str, severity: str, message: str,
        record: CSVRecord | None = None, column_number: int | None = None,
        **extra: Any,
    ) -> None:
        self.counts[check_id][severity] += 1
        if len(self.details) < MAX_ISSUE_DETAILS:
            self.details.append({
                "check_id": check_id, "code": code, "severity": severity,
                "message": message,
                "record_number": None if record is None else record.record_number,
                "line_start": None if record is None else record.line_start,
                "line_end": None if record is None else record.line_end,
                "column_number": column_number,
                **extra,
            })

    def result(self, check_id: str, reason: str | None = None) -> dict[str, Any]:
        counts = self.counts[check_id]
        status = "skipped" if reason else (
            "error" if counts["error"] else "warning" if counts["warning"] else "passed"
        )
        return {
            "id": check_id, "status": status, "reason": reason,
            "error_count": counts["error"], "warning_count": counts["warning"],
        }


def _check_structure(table: CSVTable, issues: _Issues) -> None:
    if not table.records:
        issues.add("C02", "NO_DATA_ROWS", "warning", "The table has no data records.")
    for record in table.records:
        if len(record.values) != table.column_count:
            issues.add(
                "C02", "ROW_WIDTH_MISMATCH", "error",
                "Record width does not match the header.", record,
                expected_columns=table.column_count, actual_columns=len(record.values),
            )


def _check_headers(table: CSVTable, header: tuple[str, ...], issues: _Issues) -> None:
    seen: dict[str, int] = {}
    for column, value in enumerate(header, 1):
        if not value:
            issues.add("C03", "EMPTY_HEADER", "error", "A trimmed header is empty.", table.header, column)
        if len(value) > MAX_HEADER_LENGTH:
            issues.add(
                "C03", "HEADER_TOO_LONG", "error", "A trimmed header is too long.",
                table.header, column, limit=MAX_HEADER_LENGTH, actual_length=len(value),
            )
        if value in seen:
            issues.add(
                "C03", "DUPLICATE_HEADER", "error", "Trimmed headers are not unique.",
                table.header, column, first_column_number=seen[value],
            )
        else:
            seen[value] = column


def _check_edges(
    table: CSVTable, candidate: tuple[tuple[str, ...], ...], issues: _Issues,
) -> tuple[int, int]:
    headers = cells = 0
    for record, values in zip((table.header, *table.records), candidate):
        for column, (before, after) in enumerate(zip(record.values, values), 1):
            if before != after:
                is_header = record.record_number == 1
                headers += is_header
                cells += not is_header
                issues.add(
                    "C04", "TRIM_HEADER" if is_header else "TRIM_CELL", "warning",
                    "Allowed edge whitespace can be removed.", record, column,
                )
    return headers, cells


def _check_id(
    table: CSVTable, rows: tuple[tuple[str, ...], ...], index: int, issues: _Issues,
) -> None:
    seen: dict[str, int] = {}
    for record, row in zip(table.records, rows):
        value = row[index]
        if not value:
            issues.add("C05", "EMPTY_ID", "error", "The trimmed identifier is empty.", record, index + 1)
        elif value in seen:
            issues.add(
                "C05", "DUPLICATE_ID", "error", "The trimmed identifier is not unique.",
                record, index + 1, first_record_number=seen[value],
            )
        else:
            seen[value] = record.record_number


def _check_duplicates(
    table: CSVTable, rows: tuple[tuple[str, ...], ...], issues: _Issues,
) -> None:
    seen: dict[tuple[str, ...], int] = {}
    for record, row in zip(table.records, rows):
        if row in seen:
            issues.add(
                "C06", "DUPLICATE_ROW", "warning", "A row repeats an earlier trimmed row.",
                record, first_record_number=seen[row],
            )
        else:
            seen[row] = record.record_number


def _check_numeric_ids(
    table: CSVTable, rows: tuple[tuple[str, ...], ...], index: int, issues: _Issues,
) -> None:
    for record, row in zip(table.records, rows):
        value = row[index]
        if not (value.isascii() and value.isdecimal()):
            continue
        if len(value) > 1 and value.startswith("0"):
            issues.add(
                "C07", "LEADING_ZERO_ID", "warning", "Keep this identifier as text: it has a leading zero.",
                record, index + 1,
            )
        if len(value) > 15:
            issues.add(
                "C07", "LONG_NUMERIC_ID", "warning", "Keep this identifier as text: it has more than 15 digits.",
                record, index + 1,
            )


def column_stats(rows: tuple[tuple[str, ...], ...], width: int) -> list[dict[str, Any]]:
    """Statistics on trimmed candidate; distinct_count includes empty strings."""
    result = []
    for index in range(width):
        values = [row[index] for row in rows]
        empty = values.count("")
        result.append({
            "column_number": index + 1, "empty_count": empty,
            "empty_fraction": round(empty / len(rows), 6) if rows else None,
            "distinct_count": len(set(values)),
            "max_length": max(map(len, values), default=0),
        })
    return result


def _validate_id_column(id_column: str | None, trim_nbsp: bool) -> None:
    if id_column is not None and (
        not isinstance(id_column, str) or not id_column
        or len(id_column) > MAX_HEADER_LENGTH
        or trim(id_column, trim_nbsp=trim_nbsp) != id_column
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in id_column)
    ):
        raise DoctorError(
            "INVALID_OPTIONS", "id_column must be an exact trimmed header name of 1 to 128 characters."
        )


def run_checks(
    table: CSVTable, *, id_column: str | None = None,
    settings: Settings | None = None, profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect a table from read_csv. C01 passed if read_csv returned successfully."""
    settings = Settings() if settings is None else settings
    profile = load_profile() if profile is None else validate_profile(profile)
    _validate_id_column(id_column, settings.csv_trim_nbsp)
    candidate = make_candidate(table, trim_nbsp=settings.csv_trim_nbsp)
    header, rows = candidate[0], candidate[1:]
    issues = _Issues()
    _check_structure(table, issues)
    _check_headers(table, header, issues)
    changed_headers, changed_cells = _check_edges(table, candidate, issues)

    prerequisite_failed = bool(issues.counts["C02"]["error"] or issues.counts["C03"]["error"])
    reasons: dict[str, str] = {}
    if prerequisite_failed:
        reasons.update({cid: "PREREQUISITE_FAILED" for cid in ("C05", "C06", "C07")})
    else:
        index = None
        if id_column is None:
            reasons.update({cid: "ID_COLUMN_NOT_CONFIGURED" for cid in ("C05", "C07")})
        elif id_column not in header:
            issues.add("C05", "ID_COLUMN_NOT_FOUND", "error", "The configured identifier column is absent.")
            reasons["C07"] = "ID_COLUMN_NOT_FOUND"
        else:
            index = header.index(id_column)
            _check_id(table, rows, index, issues)
        _check_duplicates(table, rows, issues)
        if index is not None:
            _check_numeric_ids(table, rows, index, issues)

    checks = [issues.result(cid, reasons.get(cid)) for cid in CHECK_IDS]
    errors = sum(check["error_count"] for check in checks)
    warnings = sum(check["warning_count"] for check in checks)
    blockers = [check["id"] for check in checks if check["error_count"]]
    changes = changed_headers + changed_cells
    if changes > MAX_CHANGES:
        blockers.append("CHANGE_LIMIT_EXCEEDED")
    # C07 may add two details for the same cell; retain deterministic code order.
    issues.details.sort(key=lambda item: (
        item["check_id"], item["record_number"] or 0,
        item["column_number"] or 0, item["code"],
    ))
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "request_id": str(uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "profile": {"id": profile["id"], "version": profile["version"]},
        "options": {
            "encoding": table.options.encoding, "delimiter": table.options.delimiter,
            "id_column": id_column, "trim_nbsp": settings.csv_trim_nbsp,
        },
        "source": {
            "size_bytes": table.source.size_bytes, "sha256": table.source.sha256,
            "had_utf8_bom": table.source.had_utf8_bom,
        },
        "table": {
            "row_count": table.row_count, "column_count": table.column_count,
            "headers": [
                {"column_number": index, "original": before, "normalized": after}
                for index, (before, after) in enumerate(zip(table.header.values, header), 1)
            ],
        },
        "valid": errors == 0,
        "summary": {
            "error_count": errors, "warning_count": warnings,
            "issue_count": errors + warnings, "shown_issue_count": len(issues.details),
            "omitted_issue_count": errors + warnings - len(issues.details),
        },
        "checks": checks, "issues": issues.details,
        "column_stats": None if prerequisite_failed else column_stats(rows, len(header)),
        "normalization": {
            "implemented": False, "can_normalize": not blockers,
            "blockers": blockers, "changed_headers": changed_headers,
            "changed_cells": changed_cells, "total_changes": changes,
            "change_limit": MAX_CHANGES,
        },
    }


def inspect_csv(
    raw: bytes, *, options: CSVOptions | None = None, id_column: str | None = None,
    settings: Settings | None = None, profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One entry point for future HTTP: raw bytes in, report or DoctorError out."""
    settings = Settings() if settings is None else settings
    profile = load_profile() if profile is None else validate_profile(profile)
    _validate_id_column(id_column, settings.csv_trim_nbsp)
    if options is None:
        options = CSVOptions(**profile["defaults"])
    if (options.encoding not in profile["allowed_encodings"]
            or options.delimiter not in profile["allowed_delimiters"]):
        raise DoctorError("INVALID_OPTIONS", "The selected format is not allowed by the profile.")
    table = read_csv(raw, options=options, settings=settings)
    return run_checks(table, id_column=id_column, settings=settings, profile=profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a CSV without changing or writing it.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--encoding", choices=("utf-8", "cp1251"), default=None)
    parser.add_argument("--delimiter", choices=("comma", "semicolon", "tab"), default=None)
    parser.add_argument("--id-column", default=None)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        profile = load_profile()
        options = CSVOptions(
            args.encoding or profile["defaults"]["encoding"],
            args.delimiter or profile["defaults"]["delimiter"],
        )
        with args.file.open("rb") as source:
            raw = source.read(settings.csv_max_bytes + 1)
        report = inspect_csv(raw, options=options, id_column=args.id_column, settings=settings, profile=profile)
    except DoctorError as exc:
        print(json.dumps({"error": exc.as_dict()}, ensure_ascii=False))
        return 2
    except OSError:
        print(json.dumps({"error": {"code": "FILE_READ_ERROR", "message": "The local file cannot be read."}}))
        return 2
    except ValueError:
        print(json.dumps({"error": {"code": "INVALID_SETTINGS", "message": "Check the settings and CSV profile."}}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
