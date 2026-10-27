"""Normalization core, without HTTP. Expected outputs are written by hand."""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

import normalize
from config import MAX_CHANGES, MAX_OUTPUT_BYTES, Settings
from csv_core import CSVOptions, DoctorError, read_csv
from normalize import (
    ARCHIVE_MEMBERS, NormalizationBlocked, NormalizationInvariantError,
    build_archive, normalize_csv, serialize_csv,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
MINIMAL = Settings(csv_quoting="minimal")


def members(result):
    with zipfile.ZipFile(io.BytesIO(result.archive)) as archive:
        assert archive.namelist() == list(ARCHIVE_MEMBERS)
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def test_needs_trim_example_exact_output_and_log():
    result = normalize_csv((EXAMPLES / "needs_trim.csv").read_bytes(), id_column="id")
    assert result.cleaned == (
        b'"id","name","note"\n"001","Alice","Ready"\n"002","Bob","Done"\n'
    )
    assert [(c["code"], c["record_number"], c["column_number"], c["before"], c["after"])
            for c in result.changes["changes"]] == [
        ("TRIM_HEADER", 1, 1, " id", "id"),
        ("TRIM_CELL", 2, 1, " 001", "001"),
        ("TRIM_CELL", 2, 2, "Alice ", "Alice"),
        ("TRIM_CELL", 3, 3, "Done ", "Done"),
    ]
    assert result.changes["summary"] == {"changed_headers": 1, "changed_cells": 3, "total_changes": 4}


def test_archive_members_match_result():
    raw = (EXAMPLES / "needs_trim.csv").read_bytes()
    result = normalize_csv(raw, id_column="id", request_id="req-1")
    files = members(result)
    assert files["cleaned.csv"] == result.cleaned
    reports = json.loads(files["report.json"])
    assert set(reports) == {"before", "after"}
    assert reports["before"]["request_id"] == reports["after"]["request_id"] == "req-1"
    assert reports["before"]["normalization"]["total_changes"] == 4
    assert reports["after"]["valid"] is True
    assert reports["after"]["normalization"]["total_changes"] == 0
    assert "column_stats" in reports["before"]  # always full, never compact
    log = json.loads(files["changes.json"])
    assert log["request_id"] == "req-1"
    assert log["input"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert log["output"]["sha256"] == hashlib.sha256(result.cleaned).hexdigest()
    assert log["output"]["size_bytes"] == len(result.cleaned)
    assert log["output"]["quoting"] == "all"
    assert len(log["changes"]) == 4


def test_minimal_quoting_exact_output():
    raw = b'id,note\n001,"a, b"\n002,"say ""hi"""\n003,plain\n'
    result = normalize_csv(raw, id_column="id", settings=MINIMAL)
    assert result.cleaned == b'id,note\n001,"a, b"\n002,"say ""hi"""\n003,plain\n'
    assert result.changes["output"]["quoting"] == "minimal"


def test_ids_stay_text():
    result = normalize_csv((EXAMPLES / "valid.csv").read_bytes(), id_column="id", settings=MINIMAL)
    lines = result.cleaned.decode().splitlines()
    assert lines[1].startswith("00123,")
    assert lines[3].startswith("1234567890123456,")
    assert result.changes["changes"] == []


def test_internal_whitespace_and_line_breaks_are_preserved():
    raw = b'id,note\n1," a  b\tc "\n2,"x\r\ny"\n3,"p\nq"\n4,"r\rs"\n'
    for settings in (Settings(), MINIMAL):
        result = normalize_csv(raw, settings=settings)
        table = read_csv(result.cleaned)
        assert [r.values for r in table.records] == [
            ("1", "a  b\tc"), ("2", "x\r\ny"), ("3", "p\nq"), ("4", "r\rs"),
        ]


def test_edge_line_breaks_are_not_trimmed():
    result = normalize_csv(b'id,note\n1,"\nx\n"\n', settings=MINIMAL)
    assert read_csv(result.cleaned).records[0].values == ("1", "\nx\n")
    assert result.changes["changes"] == []


def test_duplicate_rows_and_order_are_kept():
    raw = b"id,name\n2,b\n1,a\n1,a\n"
    result = normalize_csv(raw, settings=MINIMAL)  # no id_column: C06 is a warning only
    assert result.cleaned == b"id,name\n2,b\n1,a\n1,a\n"


def test_nbsp_kept_by_default_and_trimmed_when_enabled():
    raw = (EXAMPLES / "nbsp_edges.csv").read_bytes()
    kept = normalize_csv(raw, id_column="id", settings=MINIMAL)
    assert read_csv(kept.cleaned).records[0].values == ("001", "\u00a0Alice\u00a0")
    assert kept.changes["changes"] == []
    trimmed = normalize_csv(raw, id_column="id", settings=Settings(csv_quoting="minimal", csv_trim_nbsp=True))
    assert read_csv(trimmed.cleaned).records[0].values == ("001", "Alice")
    assert trimmed.changes["rules"]["trim_characters"] == ["U+0020", "U+0009", "U+00A0"]


def test_bom_cp1251_semicolon_and_tab_are_rewritten_as_utf8_comma():
    bom = normalize_csv((EXAMPLES / "utf8_bom.csv").read_bytes(), id_column="id", settings=MINIMAL)
    assert bom.cleaned == b"id,name\n001,Alice\n"
    assert bom.changes["input"]["had_utf8_bom"] is True

    cp = normalize_csv(
        (EXAMPLES / "cp1251_semicolon.csv").read_bytes(), id_column="id", settings=MINIMAL,
        options=CSVOptions("cp1251", "semicolon"),
    )
    assert cp.cleaned.decode("utf-8") == "id,city\n001,Москва\n002,Казань\n"
    assert cp.changes["input"]["encoding"] == "cp1251"

    tab = normalize_csv(b"id\tname\n1\ta,b\n", settings=MINIMAL, options=CSVOptions("utf-8", "tab"))
    assert tab.cleaned == b'id,name\n1,"a,b"\n'


def test_single_column_with_empty_value_round_trips():
    for settings in (Settings(), MINIMAL):
        result = normalize_csv(b'note\n""\nx\n', settings=settings)
        assert [r.values for r in read_csv(result.cleaned).records] == [("",), ("x",)]


def test_whitespace_only_cell_becomes_empty_and_is_logged():
    result = normalize_csv(b"id,note\n1,   \n", settings=MINIMAL)
    assert result.cleaned == b"id,note\n1,\n"
    assert result.changes["changes"][0]["before"] == "   "
    assert result.changes["changes"][0]["after"] == ""


def test_header_only_table():
    result = normalize_csv(b" id ,name\n", settings=MINIMAL)
    assert result.cleaned == b"id,name\n"
    assert result.after["table"]["row_count"] == 0


def test_multiline_coordinates_refer_to_source():
    raw = b'id,note\n1,"first\nsecond "\n2, x\n'
    changes = normalize_csv(raw).changes["changes"]
    assert [(c["record_number"], c["line_start"], c["line_end"]) for c in changes] == [(2, 2, 3), (3, 4, 4)]


@pytest.mark.parametrize("settings", [Settings(), MINIMAL, Settings(csv_trim_nbsp=True)])
@pytest.mark.parametrize("name", ["valid.csv", "needs_trim.csv", "multiline.csv", "nbsp_edges.csv", "utf8_bom.csv"])
def test_normalizing_the_result_changes_nothing(name, settings):
    first = normalize_csv((EXAMPLES / name).read_bytes(), settings=settings)
    second = normalize_csv(first.cleaned, settings=settings)
    assert second.cleaned == first.cleaned
    assert second.changes["changes"] == []


@pytest.mark.parametrize("name,blocker", [
    ("duplicate_id.csv", "C05"), ("bad_width.csv", "C02"), ("blank_record.csv", "C02"),
])
def test_data_errors_block_with_full_report(name, blocker):
    with pytest.raises(NormalizationBlocked) as caught:
        normalize_csv((EXAMPLES / name).read_bytes(), id_column="id")
    report = caught.value.report
    assert report["valid"] is False
    assert blocker in report["normalization"]["blockers"]
    assert "column_stats" in report


def test_duplicate_header_blocks():
    with pytest.raises(NormalizationBlocked) as caught:
        normalize_csv(b"id, id\n1,2\n")
    assert caught.value.report["normalization"]["blockers"] == ["C03"]


@pytest.mark.parametrize("name,code", [
    ("broken_quotes.csv", "CSV_PARSE_ERROR"),
])
def test_unreadable_input_raises_doctor_error(name, code):
    with pytest.raises(DoctorError) as caught:
        normalize_csv((EXAMPLES / name).read_bytes())
    assert caught.value.code == code


def test_change_limit_boundary():
    # Two changes per record keeps the row count (5,000) under the row limit.
    at_limit = b"a,b\n" + b" x , y \n" * (MAX_CHANGES // 2)
    assert normalize_csv(at_limit).changes["summary"]["total_changes"] == MAX_CHANGES
    over = b" a,b\n" + b" x , y \n" * (MAX_CHANGES // 2)  # plus one header change
    with pytest.raises(DoctorError) as caught:
        normalize_csv(over)
    assert caught.value.code == "CHANGE_LIMIT_EXCEEDED"
    assert caught.value.details == {"limit": MAX_CHANGES, "actual": MAX_CHANGES + 1}


def test_change_limit_with_data_errors_is_blocked_not_limit():
    raw = b"id,b\n" + b"".join(b"%d , y \n" % (i % 3) for i in range(6000))
    with pytest.raises(NormalizationBlocked) as caught:
        normalize_csv(raw, id_column="id")
    assert caught.value.report["normalization"]["blockers"] == ["C05", "CHANGE_LIMIT_EXCEEDED"]


def test_output_larger_than_input_limit_is_still_verified():
    # QUOTE_ALL adds 4 bytes per row; output exceeds the 64-byte input cap.
    settings = Settings(csv_max_bytes=64)
    raw = b"a,b\n" + b"1,2\n" * 15
    assert len(raw) <= 64
    result = normalize_csv(raw, settings=settings)
    assert len(result.cleaned) > 64


def test_output_size_limit(monkeypatch):
    monkeypatch.setattr(normalize, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(DoctorError) as caught:
        normalize_csv(b"a,b\n1,2\n")
    assert caught.value.code == "OUTPUT_TOO_LARGE"
    assert MAX_OUTPUT_BYTES == 8 * 1024 * 1024


@pytest.mark.parametrize("broken", [
    lambda data: data.replace(b'"Alice"', b'"Alicia"'),        # value changed
    lambda data: data + b'"003","Eve","x"\n',                   # record added
    lambda data: data.replace(b'\n"002"', b'\n"001"'),          # duplicate ID introduced
])
def test_invariant_violation_produces_no_archive(monkeypatch, broken):
    original = normalize.serialize_csv
    calls = []

    def tampered(rows, quoting):
        calls.append(1)
        data = original(rows, quoting)
        return broken(data) if len(calls) == 1 else data

    monkeypatch.setattr(normalize, "serialize_csv", tampered)
    with pytest.raises(NormalizationInvariantError):
        normalize_csv((EXAMPLES / "needs_trim.csv").read_bytes(), id_column="id")


def test_invariant_error_message_has_no_cell_values(monkeypatch):
    monkeypatch.setattr(normalize, "serialize_csv", lambda rows, quoting: b"id\nSECRET\n")
    with pytest.raises(NormalizationInvariantError) as caught:
        normalize_csv(b"id,name\n1,SECRET\n")
    assert "SECRET" not in str(caught.value)


def test_serializer_round_trips_tricky_values_in_both_modes():
    rows = (("h1", "h2"), ("", "a,b"), ('"q"', "x\ry"), ("\n", " lead"), ("=1+1", "tail\t"))
    for quoting in ("all", "minimal"):
        data = serialize_csv(rows, quoting)
        parsed = tuple(tuple(r) for r in csv.reader(io.StringIO(data.decode(), newline="")))
        assert parsed == rows
        assert not data.startswith(b"\xef\xbb\xbf")


def test_archive_is_deterministic_for_same_members():
    first = build_archive(b"a\n", b"{}", b"[]")
    second = build_archive(b"a\n", b"{}", b"[]")
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert [i.date_time for i in archive.infolist()] == [(1980, 1, 1, 0, 0, 0)] * 3


def test_cleaned_csv_is_deterministic_across_requests():
    raw = (EXAMPLES / "needs_trim.csv").read_bytes()
    assert normalize_csv(raw).cleaned == normalize_csv(raw).cleaned


def test_source_bytes_are_not_modified():
    raw = bytearray((EXAMPLES / "needs_trim.csv").read_bytes())
    snapshot = bytes(raw)
    normalize_csv(bytes(raw))
    assert bytes(raw) == snapshot == (EXAMPLES / "needs_trim.csv").read_bytes()
