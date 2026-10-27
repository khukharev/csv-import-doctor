"""Normalization: trimmed copy, full change log, re-check and a ZIP result.

No dependency on FastAPI. Works on bytes in memory and never writes files.
"""

import csv
import dataclasses
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Any

from checks import make_candidate, read_and_check, run_checks
from config import APP_VERSION, MAX_CHANGES, MAX_OUTPUT_BYTES, Settings
from csv_core import CSVOptions, CSVTable, DoctorError, read_csv

CHANGES_SCHEMA_VERSION = "1.0"
ARCHIVE_MEMBERS = ("cleaned.csv", "report.json", "changes.json")
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_QUOTING = {"all": csv.QUOTE_ALL, "minimal": csv.QUOTE_MINIMAL}
_OUTPUT_OPTIONS = CSVOptions(encoding="utf-8", delimiter="comma")

Rows = tuple[tuple[str, ...], ...]


class NormalizationBlocked(Exception):
    """The source has data errors; carries the full 'before' report."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("Normalization is blocked by data errors.")
        self.report = report


class NormalizationInvariantError(RuntimeError):
    """The written result failed a safety check. This is a program error."""


@dataclass(frozen=True)
class NormalizationResult:
    archive: bytes
    cleaned: bytes
    before: dict[str, Any]
    after: dict[str, Any]
    changes: dict[str, Any]


def build_changes(table: CSVTable, candidate: Rows) -> list[dict[str, Any]]:
    """One entry per changed header or cell, with source coordinates and values."""
    result = []
    for record, values in zip((table.header, *table.records), candidate):
        for column, (before, after) in enumerate(zip(record.values, values), 1):
            if before != after:
                result.append({
                    "code": "TRIM_HEADER" if record.record_number == 1 else "TRIM_CELL",
                    "record_number": record.record_number,
                    "line_start": record.line_start,
                    "line_end": record.line_end,
                    "column_number": column,
                    "before": before,
                    "after": after,
                })
    return result


def serialize_csv(rows: Rows, quoting: str) -> bytes:
    """UTF-8 without BOM, comma, LF. The csv module quotes CR/LF inside values."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=",", lineterminator="\n", quoting=_QUOTING[quoting])
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _output_settings(settings: Settings) -> Settings:
    # The output may be larger than the input; re-read it under the output cap.
    return dataclasses.replace(settings, csv_max_bytes=MAX_OUTPUT_BYTES)


def verify_result(
    cleaned: bytes, source: CSVTable, candidate: Rows, *,
    id_column: str | None, settings: Settings, profile: dict[str, Any],
) -> dict[str, Any]:
    """Re-read the written bytes and check that nothing but the trim changed."""
    try:
        table = read_csv(cleaned, options=_OUTPUT_OPTIONS, settings=_output_settings(settings))
    except DoctorError as exc:
        raise NormalizationInvariantError(f"output is not readable: {exc.code}") from None
    if (table.row_count, table.column_count) != (source.row_count, source.column_count):
        raise NormalizationInvariantError("record or column count changed")
    written = tuple(record.values for record in (table.header, *table.records))
    if written != candidate:
        raise NormalizationInvariantError("written values differ from the trimmed candidate")
    after = run_checks(table, id_column=id_column, settings=settings, profile=profile)
    if not after["valid"] or after["normalization"]["total_changes"] != 0:
        raise NormalizationInvariantError("output still has errors or pending changes")
    if serialize_csv(make_candidate(table, trim_nbsp=settings.csv_trim_nbsp),
                     settings.csv_quoting) != cleaned:
        raise NormalizationInvariantError("normalization is not idempotent")
    return after


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_archive(cleaned: bytes, report: bytes, changes: bytes) -> bytes:
    """Fixed member names, order and timestamps: same members give the same ZIP."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in zip(ARCHIVE_MEMBERS, (cleaned, report, changes)):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_csv(
    raw: bytes, *, options: CSVOptions | None = None, id_column: str | None = None,
    settings: Settings | None = None, profile: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> NormalizationResult:
    """Trim allowed edge characters, re-check the result and pack a ZIP.

    Raises DoctorError for unreadable input or limits, NormalizationBlocked for
    data errors and NormalizationInvariantError if a safety check fails.
    """
    table, before, settings, profile = read_and_check(
        raw, options=options, id_column=id_column, settings=settings, profile=profile,
    )
    if request_id is not None:
        before["request_id"] = request_id
    blockers = before["normalization"]["blockers"]
    if blockers == ["CHANGE_LIMIT_EXCEEDED"]:
        raise DoctorError(
            "CHANGE_LIMIT_EXCEEDED",
            f"Normalization would make more than {MAX_CHANGES} changes.",
            details={"limit": MAX_CHANGES, "actual": before["normalization"]["total_changes"]},
        )
    if blockers:
        raise NormalizationBlocked(before)

    candidate = make_candidate(table, trim_nbsp=settings.csv_trim_nbsp)
    changes = build_changes(table, candidate)
    if len(changes) != before["normalization"]["total_changes"]:
        raise NormalizationInvariantError("change log does not match the plan")
    cleaned = serialize_csv(candidate, settings.csv_quoting)
    if len(cleaned) > MAX_OUTPUT_BYTES:
        raise DoctorError(
            "OUTPUT_TOO_LARGE", "The normalized CSV exceeds the output size limit.",
            details={"limit": MAX_OUTPUT_BYTES},
        )
    after = verify_result(
        cleaned, table, candidate, id_column=id_column, settings=settings, profile=profile,
    )
    after["request_id"] = before["request_id"]

    change_log = {
        "schema_version": CHANGES_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "request_id": before["request_id"],
        "input": {
            "size_bytes": table.source.size_bytes, "sha256": table.source.sha256,
            "had_utf8_bom": table.source.had_utf8_bom,
            "encoding": table.options.encoding, "delimiter": table.options.delimiter,
        },
        "output": {
            "size_bytes": len(cleaned), "sha256": _digest(cleaned),
            "encoding": "utf-8", "delimiter": "comma", "line_terminator": "lf",
            "quoting": settings.csv_quoting,
        },
        "rules": {
            "trim_characters": ["U+0020", "U+0009"] + (["U+00A0"] if settings.csv_trim_nbsp else []),
            "rewritten": True,
        },
        "summary": {
            "changed_headers": before["normalization"]["changed_headers"],
            "changed_cells": before["normalization"]["changed_cells"],
            "total_changes": len(changes),
        },
        "changes": changes,
    }
    archive = build_archive(
        cleaned, _json_bytes({"before": before, "after": after}), _json_bytes(change_log),
    )
    return NormalizationResult(archive, cleaned, before, after, change_log)
