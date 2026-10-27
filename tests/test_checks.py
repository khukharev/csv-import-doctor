"""Synthetic fixtures only. No real exports, networks or developer .env needed."""

import copy
import csv
import io
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from checks import inspect_csv, make_candidate, run_checks, trim
from config import APP_VERSION, MAX_ISSUE_DETAILS, Settings
from csv_core import CSVOptions, DoctorError, read_csv
from csv_profile import CHECK_IDS, load_profile

ROOT = Path(__file__).resolve().parents[1]


def report(text, *, id_column=None, nbsp=False):
    return inspect_csv(text.encode("utf-8"), id_column=id_column, settings=Settings(csv_trim_nbsp=nbsp))


def check(body, cid):
    return next(item for item in body["checks"] if item["id"] == cid)


def codes(body, cid=None):
    return [i["code"] for i in body["issues"] if cid is None or i["check_id"] == cid]


def test_clean_report_schema():
    body = report("id,name\nA,Alice\nB,Bob\n", id_column="id")
    assert body["valid"] is True
    assert [c["id"] for c in body["checks"]] == list(CHECK_IDS)
    assert all(c["status"] == "passed" for c in body["checks"])
    assert body["schema_version"] == "1.0"
    assert body["app_version"] == APP_VERSION == "0.6"
    assert UUID(body["request_id"]).version == 4
    assert datetime.fromisoformat(body["generated_at"]).utcoffset().total_seconds() == 0
    assert body["summary"] == {"error_count": 0, "warning_count": 0, "issue_count": 0,
                               "shown_issue_count": 0, "omitted_issue_count": 0}
    assert body["normalization"]["can_normalize"] is True
    assert body["normalization"]["implemented"] is True
    assert body["table"]["row_count"] == 2
    assert json.loads(json.dumps(body, ensure_ascii=False)) == body


@pytest.mark.parametrize("raw,expected", [
    (b"", "EMPTY_FILE"), (b"\xff", "DECODE_ERROR"),
    (b"id\n\x00\n", "INVALID_TEXT"), (b'"id\n', "CSV_PARSE_ERROR"),
    (b"\n", "MISSING_HEADER"),
])
def test_unreadable_inputs_raise_not_partial_report(raw, expected):
    with pytest.raises(DoctorError) as exc:
        inspect_csv(raw)
    assert exc.value.code == expected


def test_no_data_is_warning_not_invalid():
    body = report("id,name\n", id_column="id")
    assert body["valid"] is True
    assert codes(body) == ["NO_DATA_ROWS"]
    assert check(body, "C02")["status"] == "warning"
    assert body["column_stats"] == [
        {"column_number": i, "empty_count": 0, "empty_fraction": None,
         "distinct_count": 0, "max_length": 0} for i in (1, 2)
    ]


@pytest.mark.parametrize("row,width", [("A", 1), ("A,B,C", 3), ("", 0)])
def test_bad_width_and_blank_record(row, width):
    body = report("id,name\n" + row + "\n", id_column="id")
    assert not body["valid"]
    issue = body["issues"][0]
    assert issue["code"] == "ROW_WIDTH_MISMATCH"
    assert issue["expected_columns"] == 2 and issue["actual_columns"] == width
    assert issue["record_number"] == 2
    assert body["column_stats"] is None
    for cid in ("C05", "C06", "C07"):
        assert check(body, cid)["status"] == "skipped"
        assert check(body, cid)["reason"] == "PREREQUISITE_FAILED"
    assert body["normalization"]["blockers"] == ["C02"]


@pytest.mark.parametrize("header,expected", [
    ("id,", "EMPTY_HEADER"), ('id," \t "', "EMPTY_HEADER"),
    ("id, id ", "DUPLICATE_HEADER"), ("id," + "x" * 129, "HEADER_TOO_LONG"),
])
def test_invalid_header_blocks_downstream(header, expected):
    body = report(header + "\nA,B\n", id_column="id")
    assert expected in codes(body, "C03")
    assert check(body, "C03")["status"] == "error"
    assert body["column_stats"] is None
    assert check(body, "C06")["reason"] == "PREREQUISITE_FAILED"
    assert "C03" in body["normalization"]["blockers"]


def test_header_length_uses_trimmed_value_and_boundary():
    body = report('id," ' + "x" * 128 + ' "\nA,B\n')
    assert check(body, "C03")["status"] == "passed"
    assert check(body, "C04")["warning_count"] == 1


def test_header_case_sensitive_and_no_renaming():
    body = report("id,ID\nA,B\n", id_column="ID")
    assert body["valid"]
    assert [h["normalized"] for h in body["table"]["headers"]] == ["id", "ID"]


@pytest.mark.parametrize("value,expected", [
    (" \tA\t ", "A"), ("A B", "A B"), ("A\tB", "A\tB"),
    (" \nA\r\n ", "\nA\r\n"), ("\u00a0A\u00a0", "\u00a0A\u00a0"),
    ("NA", "NA"), ("NULL", "NULL"), ("None", "None"), ("0", "0"),
])
def test_trim_exact_characters(value, expected):
    assert trim(value) == expected


def test_nbsp_optional_only_at_edges():
    assert trim(" \u00a0A\u00a0B\u00a0 ", trim_nbsp=True) == "A\u00a0B"
    assert trim("\n\u00a0A\n", trim_nbsp=True) == "\n\u00a0A\n"


def test_candidate_is_independent_and_original_unchanged():
    table = read_csv(b' id ,name\n001," A "\n')
    before = copy.deepcopy(table)
    candidate = make_candidate(table)
    assert candidate == (("id", "name"), ("001", "A"))
    assert table == before
    assert table.header.values[0] == " id "
    with pytest.raises(FrozenInstanceError):
        table.header.values = ("changed",)


def test_exact_change_counts():
    body = report(" id ,name\n A , Bob \nB, Carol \n", id_column="id")
    plan = body["normalization"]
    assert (plan["changed_headers"], plan["changed_cells"], plan["total_changes"]) == (1, 3, 4)
    assert check(body, "C04")["warning_count"] == 4
    assert body["valid"] and plan["can_normalize"]


def test_c04_runs_even_on_bad_width_and_headers():
    body = report(" ,x\n A \n")
    assert codes(body, "C04") == ["TRIM_HEADER", "TRIM_CELL"]
    assert not body["valid"]


def test_id_checks_skip_when_not_configured():
    body = report("id\nA\nA\n")
    assert body["valid"]
    for cid in ("C05", "C07"):
        assert check(body, cid)["status"] == "skipped"
        assert check(body, cid)["reason"] == "ID_COLUMN_NOT_CONFIGURED"
    assert codes(body, "C06") == ["DUPLICATE_ROW"]


def test_missing_id_column_still_checks_duplicate_rows():
    body = report("id\nA\nA\n", id_column="missing")
    assert not body["valid"]
    assert codes(body, "C05") == ["ID_COLUMN_NOT_FOUND"]
    assert check(body, "C07")["reason"] == "ID_COLUMN_NOT_FOUND"
    assert codes(body, "C06") == ["DUPLICATE_ROW"]
    assert body["column_stats"] is not None


@pytest.mark.parametrize("name", ["", " id", "id ", "x" * 129, "id\n", "id\x7f", 123, False])
def test_invalid_id_option(name):
    with pytest.raises(DoctorError) as exc:
        report("id\nA\n", id_column=name)
    assert exc.value.code == "INVALID_OPTIONS"


def test_empty_and_duplicate_ids_after_trim():
    body = report('id,x\n A ,one\nA,two\n" \t ",three\n"",four\n', id_column="id")
    assert codes(body, "C05") == ["DUPLICATE_ID", "EMPTY_ID", "EMPTY_ID"]
    duplicate = next(i for i in body["issues"] if i["code"] == "DUPLICATE_ID")
    assert duplicate["first_record_number"] == 2
    assert duplicate["record_number"] == 3
    assert check(body, "C05")["error_count"] == 3


def test_zero_and_leading_zero_remain_different_ids():
    body = report("id\n0\n00\n00123\n123\nNA\nNULL\nNone\n", id_column="id")
    assert body["valid"]
    assert check(body, "C05")["status"] == "passed"
    assert check(body, "C07")["warning_count"] == 2


def test_id_is_case_sensitive():
    assert report("id\nA\na\n", id_column="id")["valid"]


def test_duplicate_rows_use_trimmed_values_and_keep_all_records():
    raw = 'id,x\n A , B \nA,B\nA,B\n'
    table = read_csv(raw.encode())
    body = run_checks(table)
    details = [i for i in body["issues"] if i["check_id"] == "C06"]
    assert [i["record_number"] for i in details] == [3, 4]
    assert [i["first_record_number"] for i in details] == [2, 2]
    assert table.row_count == body["table"]["row_count"] == 3
    assert body["normalization"]["can_normalize"]


@pytest.mark.parametrize("value,expected", [
    ("0", []), ("01", ["LEADING_ZERO_ID"]),
    ("123456789012345", []), ("1234567890123456", ["LONG_NUMERIC_ID"]),
    ("0123456789012345", ["LEADING_ZERO_ID", "LONG_NUMERIC_ID"]),
    ("+001", []), ("1e16", []), ("1.0", []), ("-01", []),
    ("\u0660\u0661", []), ("\u00b2", []),
])
def test_numeric_risk_is_ascii_only(value, expected):
    body = report(f"id\n{value}\n", id_column="id")
    assert codes(body, "C07") == expected
    assert body["valid"]


def test_numeric_warnings_still_run_with_duplicate_id_errors():
    body = report("id\n001\n001\n", id_column="id")
    assert check(body, "C05")["status"] == "error"
    assert check(body, "C07")["warning_count"] == 2


def test_column_stats_on_candidate_including_empty():
    body = report('id,x\nA," "\n B ,NA\nB,NULL\n')
    assert body["column_stats"] == [
        {"column_number": 1, "empty_count": 0, "empty_fraction": 0.0,
         "distinct_count": 2, "max_length": 1},
        {"column_number": 2, "empty_count": 1, "empty_fraction": 0.333333,
         "distinct_count": 3, "max_length": 4},
    ]


def test_nbsp_setting_changes_checks_but_not_input():
    text = "id,name\n\u00a0,name\n"
    keep = report(text, id_column="id")
    remove = report(text, id_column="id", nbsp=True)
    assert keep["valid"] and not remove["valid"]
    assert keep["normalization"]["total_changes"] == 0
    assert remove["normalization"]["total_changes"] == 1
    assert "EMPTY_ID" in codes(remove)
    assert remove["options"]["trim_nbsp"] is True


def test_multiline_issue_coordinates():
    body = report('id,name\nA,"line1\nline2"\nA,again\n', id_column="id")
    issue = next(i for i in body["issues"] if i["code"] == "DUPLICATE_ID")
    assert (issue["record_number"], issue["line_start"], issue["line_end"]) == (3, 4, 4)
    body = report('id,name\nA," line1\nline2 "\n')
    issue = next(i for i in body["issues"] if i["code"] == "TRIM_CELL")
    assert (issue["record_number"], issue["line_start"], issue["line_end"]) == (2, 2, 3)


def test_issues_no_cell_values_no_logging(caplog):
    secret = "PRIVATE_CELL_DO_NOT_EXPOSE"
    body = report(f"id,x\n{secret}, A \n{secret}, A \n", id_column="id")
    assert secret not in json.dumps(body, ensure_ascii=False)
    assert secret not in caplog.text
    assert all("before" not in i and "after" not in i for i in body["issues"])


def test_issue_details_bounded_but_full_error_counts_and_stats():
    body = report("id\n" + "same\n" * 1101, id_column="id")
    assert len(body["issues"]) == MAX_ISSUE_DETAILS == 1000
    assert body["summary"] == {
        "error_count": 1100, "warning_count": 1100, "issue_count": 2200,
        "shown_issue_count": 1000, "omitted_issue_count": 1200,
    }
    assert check(body, "C06")["warning_count"] == 1100
    assert body["column_stats"][0]["distinct_count"] == 1


def test_error_after_display_limit_still_blocks_normalization():
    body = report("id,x\n" + " A ,v\n" * 1001, id_column="id")
    assert not body["valid"]
    assert check(body, "C05")["error_count"] == 1000
    assert all(i["check_id"] == "C04" for i in body["issues"])
    assert "C05" in body["normalization"]["blockers"]


def test_full_change_count_even_after_issue_limit():
    body = report("id,x\n" + " A , B \n" * 6000)
    plan = body["normalization"]
    assert body["valid"]
    assert (plan["changed_cells"], plan["total_changes"]) == (12000, 12000)
    assert plan["can_normalize"] is False
    assert plan["blockers"] == ["CHANGE_LIMIT_EXCEEDED"]
    assert body["summary"]["omitted_issue_count"] > 10000


def test_change_limit_exactly_allowed():
    body = report("id,x\n" + " A , B \n" * 5000)
    assert body["normalization"]["total_changes"] == 10000
    assert body["normalization"]["can_normalize"]


def test_deterministic_report_order_except_request_metadata():
    text = "id,x\n 001 , A \n001,A\n"
    one, two = report(text, id_column="id"), report(text, id_column="id")
    for body in (one, two):
        body.pop("request_id")
        body.pop("generated_at")
    assert one == two
    keys = [(i["check_id"], i["record_number"] or 0, i["column_number"] or 0, i["code"])
            for i in one["issues"]]
    assert keys == sorted(keys)


def test_profile_defaults_and_cp1251():
    profile = copy.deepcopy(load_profile())
    profile["defaults"] = {"encoding": "cp1251", "delimiter": "semicolon"}
    body = inspect_csv("id;имя\nA;Анна\n".encode("cp1251"), profile=profile, id_column="id")
    assert body["valid"]
    assert body["options"]["encoding"] == "cp1251"
    assert body["table"]["column_count"] == 2


def test_options_restricted_by_profile():
    profile = copy.deepcopy(load_profile())
    profile["allowed_delimiters"] = ["comma"]
    with pytest.raises(DoctorError) as exc:
        inspect_csv(b"id\nA\n", options=CSVOptions(delimiter="tab"), profile=profile)
    assert exc.value.code == "INVALID_OPTIONS"


def test_bom_and_source_hash_are_from_original_bytes():
    raw = b"\xef\xbb\xbfid\n A \n"
    body = inspect_csv(raw)
    assert body["source"]["had_utf8_bom"] is True
    assert body["source"] == {
        "size_bytes": len(raw), "sha256": read_csv(raw).source.sha256, "had_utf8_bom": True,
    }


@pytest.mark.parametrize("delimiter", ["comma", "semicolon", "tab"])
def test_read_only_candidate_invariants_across_csv_dialects(delimiter):
    # Deliberately fabricated edge cases, not a real-data simulation.
    values = ["", " x ", "\tA\t", "001", "1234567890123456", 'a"b', "a,b", "a;b",
              "a\tb", "\nline\r\n", "\u00a0x\u00a0", "A B", "NULL", "=1+1", "Москва"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter={"comma": ",", "semicolon": ";", "tab": "\t"}[delimiter])
    writer.writerow(["id", "value"])
    for index, value in enumerate(values):
        writer.writerow([str(index), value])
    raw = stream.getvalue().encode()
    table = read_csv(raw, options=CSVOptions(delimiter=delimiter))
    original = copy.deepcopy(table)
    for nbsp in (False, True):
        body = run_checks(table, id_column="id", settings=Settings(csv_trim_nbsp=nbsp))
        candidate = make_candidate(table, trim_nbsp=nbsp)
        assert table == original
        assert len(candidate) == len(table.records) + 1
        assert all(len(row) == 2 for row in candidate)
        assert [row[1] for row in candidate[1:]] == [trim(v, trim_nbsp=nbsp) for v in values]
        assert body["valid"]


@pytest.mark.parametrize("filename,args,exit_code,expected", [
    ("valid.csv", ["--id-column", "id"], 0, None),
    ("needs_trim.csv", ["--id-column", "id"], 0, None),
    ("duplicate_id.csv", ["--id-column", "id"], 1, None),
    ("duplicate_id.csv", [], 0, None),
    ("bad_width.csv", [], 1, None),
    ("broken_quotes.csv", [], 2, "CSV_PARSE_ERROR"),
    ("missing.csv", [], 2, "FILE_READ_ERROR"),
    ("valid.csv", ["--id-column", ""], 2, "INVALID_OPTIONS"),
    ("cp1251_semicolon.csv", ["--encoding", "cp1251", "--delimiter", "semicolon"], 0, None),
])
def test_cli_from_another_working_directory(tmp_path, filename, args, exit_code, expected):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APP_", "CSV_"))}
    env["PYTHONPATH"] = str(ROOT)
    # Explicit process settings override any real .env.
    env.update(APP_NAME="Test", APP_AUTHOR="Test", APP_LOG_LEVEL="INFO",
               CSV_MAX_BYTES="2097152", CSV_MAX_ROWS="10000", CSV_TRIM_NBSP="false")
    result = subprocess.run(
        [sys.executable, "-m", "checks", str(ROOT / "examples" / filename), *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == exit_code, result.stderr
    body = json.loads(result.stdout)
    if expected:
        assert body["error"]["code"] == expected
    else:
        assert body["valid"] == (exit_code == 0)
    assert result.stderr == ""


@pytest.mark.parametrize("filename,errors,warnings,changes", [
    ("valid.csv", 0, 2, 0), ("needs_trim.csv", 0, 6, 4),
    ("duplicate_id.csv", 1, 2, 0), ("duplicate_rows.csv", 1, 3, 0),
    ("bad_width.csv", 2, 0, 0), ("blank_record.csv", 1, 0, 0),
    ("multiline.csv", 0, 2, 0), ("utf8_bom.csv", 0, 1, 0),
    ("nbsp_edges.csv", 0, 1, 0), ("cp1251_semicolon.csv", 0, 2, 0),
])
def test_existing_fixture_exact_expected_report(filename, errors, warnings, changes):
    path = ROOT / "examples" / filename
    original = path.read_bytes()
    options = CSVOptions("cp1251", "semicolon") if filename.startswith("cp1251") else CSVOptions()
    body = inspect_csv(original, options=options, id_column="id")
    assert body["summary"]["error_count"] == errors
    assert body["summary"]["warning_count"] == warnings
    assert body["normalization"]["total_changes"] == changes
    assert path.read_bytes() == original


@pytest.mark.parametrize("raw,settings,code", [
    (b"id\nA\n", Settings(csv_max_bytes=4), "FILE_TOO_LARGE"),
    (b"id\nA\nB\n", Settings(csv_max_rows=1), "ROW_LIMIT_EXCEEDED"),
    (("h\n" + "x" * 10001).encode(), Settings(), "FIELD_LIMIT_EXCEEDED"),
    ((",".join(["x"] * 201) + "\n").encode(), Settings(), "COLUMN_LIMIT_EXCEEDED"),
])
def test_inspection_keeps_parser_resource_guards(raw, settings, code):
    with pytest.raises(DoctorError) as exc:
        inspect_csv(raw, settings=settings)
    assert exc.value.code == code


def test_inspection_does_not_mutate_profile_or_table():
    profile = load_profile()
    profile_copy = copy.deepcopy(profile)
    table = read_csv(b" id ,name\n 001 , A \n")
    table_copy = copy.deepcopy(table)
    run_checks(table, profile=profile, id_column="id", settings=Settings(csv_trim_nbsp=True))
    assert profile == profile_copy
    assert table == table_copy


def test_issues_only_numeric_coordinates_not_header_or_cell_values():
    body = report("PRIVATE_HEADER,PRIVATE_HEADER\n A , B \n")
    assert "PRIVATE_HEADER" not in json.dumps(body["issues"])
    assert "PRIVATE_HEADER" in json.dumps(body["table"]["headers"])


def test_prerequisites_take_precedence_over_absent_id_option():
    body = report("id,x\nA\n")
    assert check(body, "C05")["reason"] == "PREREQUISITE_FAILED"
    assert check(body, "C07")["reason"] == "PREREQUISITE_FAILED"


def test_report_count_consistency_for_mixed_issues():
    body = report(' id ,name\n 001 , A \n001,A\n', id_column="id")
    assert body["summary"]["error_count"] == sum(c["error_count"] for c in body["checks"])
    assert body["summary"]["warning_count"] == sum(c["warning_count"] for c in body["checks"])
    assert body["summary"]["shown_issue_count"] + body["summary"]["omitted_issue_count"] == body["summary"]["issue_count"]
    assert set(body["normalization"]["blockers"]) == {"C05"}
