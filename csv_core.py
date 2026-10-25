import argparse
import csv
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_ROWS,
    MAX_ALLOWED_BYTES,
    MAX_ALLOWED_ROWS,
    MAX_COLUMNS,
    MAX_FIELD_LENGTH,
    Settings,
    load_settings,
)
from csv_profile import SUPPORTED_ENCODINGS

DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t"}


class DoctorError(ValueError):
    """An expected input error; its message and details do not contain cell values."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else dict(details)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class CSVOptions:
    encoding: str = "utf-8"
    delimiter: str = "comma"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.encoding, str)
            or self.encoding not in SUPPORTED_ENCODINGS
        ):
            raise DoctorError("INVALID_OPTIONS", "Encoding must be utf-8 or cp1251.")
        if not isinstance(self.delimiter, str) or self.delimiter not in DELIMITERS:
            raise DoctorError(
                "INVALID_OPTIONS", "Delimiter must be comma, semicolon or tab."
            )


@dataclass(frozen=True)
class SourceInfo:
    size_bytes: int
    sha256: str
    had_utf8_bom: bool


@dataclass(frozen=True)
class DecodedText:
    text: str
    source: SourceInfo


@dataclass(frozen=True)
class CSVRecord:
    record_number: int
    line_start: int
    line_end: int
    values: tuple[str, ...]


@dataclass(frozen=True)
class CSVTable:
    source: SourceInfo
    options: CSVOptions
    header: CSVRecord
    records: tuple[CSVRecord, ...]

    @property
    def row_count(self) -> int:
        return len(self.records)

    @property
    def column_count(self) -> int:
        return len(self.header.values)


def _positive_limit(value: int, maximum: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name}: must be an integer from 1 to {maximum}")


def decode(
    raw: bytes, encoding: str = "utf-8", *, max_bytes: int = DEFAULT_MAX_BYTES
) -> DecodedText:
    options = CSVOptions(encoding=encoding)
    _positive_limit(max_bytes, MAX_ALLOWED_BYTES, "max_bytes")
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > max_bytes:
        raise DoctorError(
            "FILE_TOO_LARGE",
            "The file exceeds the byte limit.",
            details={"limit": max_bytes, "actual": len(raw)},
        )
    if not raw:
        raise DoctorError("EMPTY_FILE", "The input file is empty.")

    codec = "utf-8-sig" if options.encoding == "utf-8" else options.encoding
    try:
        text = raw.decode(codec, errors="strict")
    except UnicodeDecodeError as exc:
        offset = 3 if codec == "utf-8-sig" and raw.startswith(b"\xef\xbb\xbf") else 0
        raise DoctorError(
            "DECODE_ERROR",
            "The file cannot be decoded with the selected encoding.",
            details={
                "encoding": encoding,
                "byte_start": exc.start + offset,
                "byte_end": exc.end + offset,
            },
        ) from None

    _validate_text(text)
    return DecodedText(
        text=text,
        source=SourceInfo(
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            had_utf8_bom=encoding == "utf-8" and raw.startswith(b"\xef\xbb\xbf"),
        ),
    )


def _validate_text(text: str) -> None:
    for index, char in enumerate(text):
        code = ord(char)
        if (code < 32 and char not in "\t\r\n") or 127 <= code <= 159:
            raise DoctorError(
                "INVALID_TEXT",
                "The text contains a forbidden control character.",
                details={"character_offset": index, "code_point": f"U+{code:04X}"},
            )


def parse(
    text: str, delimiter: str = "comma", *, max_rows: int = DEFAULT_MAX_ROWS
) -> tuple[CSVRecord, ...]:
    """Parse logical records. Keep blank data records and unequal row widths."""
    options = CSVOptions(delimiter=delimiter)
    _positive_limit(max_rows, MAX_ALLOWED_ROWS, "max_rows")
    if not isinstance(text, str):
        raise TypeError("text must be str")
    _validate_text(text)
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=DELIMITERS[options.delimiter],
        strict=True,
        skipinitialspace=False,
    )
    records: list[CSVRecord] = []
    while True:
        line_start = reader.line_num + 1
        record_number = len(records) + 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            # Leave csv.field_size_limit() (a process-global setting) untouched.
            too_long = str(exc).startswith("field larger than field limit")
            raise DoctorError(
                "FIELD_LIMIT_EXCEEDED" if too_long else "CSV_PARSE_ERROR",
                "A field exceeds the character limit."
                if too_long
                else "The CSV parser rejected a record.",
                details={
                    "record_number": record_number,
                    "line_start": line_start,
                    "line_end": reader.line_num,
                    **({"limit": MAX_FIELD_LENGTH} if too_long else {}),
                },
            ) from None

        if record_number == 1 and not fields:
            raise DoctorError(
                "MISSING_HEADER",
                "The first CSV record must contain a header.",
                details={
                    "record_number": 1,
                    "line_start": line_start,
                    "line_end": reader.line_num,
                },
            )
        if record_number > 1 and len(records) > max_rows:
            raise DoctorError(
                "ROW_LIMIT_EXCEEDED",
                "The file exceeds the data record limit.",
                details={"limit": max_rows, "record_number": record_number},
            )
        if len(fields) > MAX_COLUMNS:
            raise DoctorError(
                "COLUMN_LIMIT_EXCEEDED",
                "A record exceeds the column limit.",
                details={
                    "limit": MAX_COLUMNS,
                    "actual": len(fields),
                    "record_number": record_number,
                },
            )
        for column_number, value in enumerate(fields, start=1):
            if len(value) > MAX_FIELD_LENGTH:
                raise DoctorError(
                    "FIELD_LIMIT_EXCEEDED",
                    "A field exceeds the character limit.",
                    details={
                        "limit": MAX_FIELD_LENGTH,
                        "actual": len(value),
                        "record_number": record_number,
                        "column_number": column_number,
                    },
                )
        records.append(
            CSVRecord(record_number, line_start, reader.line_num, tuple(fields))
        )

    if not records:
        raise DoctorError("MISSING_HEADER", "The input has no CSV header record.")
    return tuple(records)


def read_csv(
    raw: bytes, *, options: CSVOptions | None = None, settings: Settings | None = None
) -> CSVTable:
    options = CSVOptions() if options is None else options
    settings = Settings() if settings is None else settings
    decoded = decode(raw, options.encoding, max_bytes=settings.csv_max_bytes)
    records = parse(decoded.text, options.delimiter, max_rows=settings.csv_max_rows)
    return CSVTable(decoded.source, options, records[0], records[1:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse a CSV without modifying it.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--encoding", choices=SUPPORTED_ENCODINGS, default="utf-8")
    parser.add_argument("--delimiter", choices=tuple(DELIMITERS), default="comma")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        with args.file.open("rb") as source:
            raw = source.read(settings.csv_max_bytes + 1)
        table = read_csv(
            raw, options=CSVOptions(args.encoding, args.delimiter), settings=settings
        )
    except DoctorError as exc:
        print(json.dumps({"error": exc.as_dict()}, ensure_ascii=False))
        return 2
    except OSError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "FILE_READ_ERROR",
                        "message": "The local file cannot be read.",
                    }
                }
            )
        )
        return 2
    except ValueError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "INVALID_SETTINGS",
                        "message": "Check the local environment settings.",
                    }
                }
            )
        )
        return 2
    print(
        json.dumps(
            {
                "parsed": True,
                "validated": False,
                "rows": table.row_count,
                "columns": table.column_count,
                "size_bytes": table.source.size_bytes,
                "sha256": table.source.sha256,
                "had_utf8_bom": table.source.had_utf8_bom,
                "encoding": table.options.encoding,
                "delimiter": table.options.delimiter,
                "note": "Parsing is not validation. Headers, row widths and IDs are not checked yet.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
