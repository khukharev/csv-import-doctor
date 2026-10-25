import csv
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import config
import csv_core
from config import MAX_COLUMNS, MAX_FIELD_LENGTH, Settings
from csv_core import CSVOptions, DoctorError, decode, main, parse, read_csv

EXAMPLES = Path(__file__).resolve().parent / "examples"


@pytest.fixture(autouse=True)
def isolate_cli_settings(monkeypatch):
    for key in (
        "APP_NAME",
        "APP_AUTHOR",
        "APP_LOG_LEVEL",
        "CSV_MAX_BYTES",
        "CSV_MAX_ROWS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        csv_core, "load_settings", lambda: config.load_settings(read_env_file=False)
    )


def test_strings_are_never_converted():
    table = read_csv((EXAMPLES / "valid.csv").read_bytes())
    assert table.header.values == ("id", "name", "note")
    assert table.row_count == 3
    assert table.column_count == 3
    assert [r.values[0] for r in table.records] == ["00123", "123", "1234567890123456"]
    assert table.records[1].values[2] == "Comma, inside"
    assert table.records[2].values[2] == 'Says "hello"'
    assert all(type(v) is str for r in (table.header, *table.records) for v in r.values)


def test_source_hash_and_size():
    raw = b"id\r\n001\r\n"
    table = read_csv(raw)
    assert table.source.sha256 == hashlib.sha256(raw).hexdigest()
    assert table.source.size_bytes == len(raw)
    assert table.source.had_utf8_bom is False


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_line_endings_and_multiline_coordinates(newline):
    raw = (
        f'id,note{newline}001,"First{newline}Second"{newline}002,End{newline}'.encode()
    )
    table = read_csv(raw)
    assert (table.header.line_start, table.header.line_end) == (1, 1)
    first, second = table.records
    assert (first.record_number, first.line_start, first.line_end) == (2, 2, 3)
    assert first.values == ("001", f"First{newline}Second")
    assert (second.record_number, second.line_start, second.line_end) == (3, 4, 4)


def test_bom_removed_only_at_start():
    raw = b"\xef\xbb\xbfid,name\n001,\xef\xbb\xbfAlice\n"
    table = read_csv(raw)
    assert table.source.had_utf8_bom is True
    assert table.header.values[0] == "id"
    assert table.records[0].values[1] == "\ufeffAlice"
    assert table.source.sha256 == hashlib.sha256(raw).hexdigest()


def test_cp1251_semicolon():
    raw = (EXAMPLES / "cp1251_semicolon.csv").read_bytes()
    table = read_csv(raw, options=CSVOptions(encoding="cp1251", delimiter="semicolon"))
    assert table.records[0].values == ("001", "\u041c\u043e\u0441\u043a\u0432\u0430")
    assert table.records[1].values == ("002", "\u041a\u0430\u0437\u0430\u043d\u044c")
    assert table.source.had_utf8_bom is False
    with pytest.raises(DoctorError) as error:
        read_csv(raw)
    assert error.value.code == "DECODE_ERROR"


def test_tab_delimiter():
    table = read_csv(b"id\tname\n001\tAlice\n", options=CSVOptions(delimiter="tab"))
    assert table.records[0].values == ("001", "Alice")


@pytest.mark.parametrize(
    "encoding,raw", [("utf-8", b"id\n\xff"), ("cp1251", b"id\n\x98")]
)
def test_strict_decoding(encoding, raw):
    with pytest.raises(DoctorError) as error:
        decode(raw, encoding)
    assert error.value.code == "DECODE_ERROR"
    assert error.value.details["byte_start"] == 3


def test_bad_utf8_offset_accounts_for_bom():
    with pytest.raises(DoctorError) as error:
        decode(b"\xef\xbb\xbfid\n\xff")
    assert error.value.details["byte_start"] == 6


@pytest.mark.parametrize(
    "char", ["\x00", "\x01", "\x0b", "\x0c", "\x1f", "\x7f", "\x85", "\x9f"]
)
def test_forbidden_controls(char):
    with pytest.raises(DoctorError) as error:
        read_csv(f"id\n{char}SECRET\n".encode())
    assert error.value.code == "INVALID_TEXT"
    assert error.value.details["code_point"] == f"U+{ord(char):04X}"
    assert "SECRET" not in str(error.value.as_dict())


def test_parse_directly_also_rejects_controls():
    with pytest.raises(DoctorError) as error:
        parse("id\n\x00\n")
    assert error.value.code == "INVALID_TEXT"


def test_spaces_tabs_nbsp_empty_literals_and_formulas_are_preserved():
    raw = (
        " id ,note\n 001 ,\t Alice \t\n002,\u00a0Bob\u00a0\n"
        "003,NA\n004,NULL\n005,None\n006,0\n007,\n008,=1+1\n"
    ).encode()
    table = read_csv(raw)
    assert table.header.values == (" id ", "note")
    assert table.records[0].values == (" 001 ", "\t Alice \t")
    assert table.records[1].values[1] == "\u00a0Bob\u00a0"
    assert [r.values[1] for r in table.records[2:]] == [
        "NA",
        "NULL",
        "None",
        "0",
        "",
        "=1+1",
    ]


def test_no_header_guessing_or_delimiter_guessing():
    table = read_csv(b"001;Alice\n002;Bob\n")
    assert table.header.values == ("001;Alice",)
    assert table.column_count == 1
    assert table.row_count == 1


def test_empty_headers_and_duplicate_headers_deferred():
    table = read_csv(b" ,id,id,\n1,2,3,4\n")
    assert table.header.values == (" ", "id", "id", "")


def test_unequal_width_deferred_not_repaired():
    table = read_csv((EXAMPLES / "bad_width.csv").read_bytes())
    assert [len(r.values) for r in table.records] == [2, 1, 3]


def test_blank_record_is_retained_and_trailing_newline_is_not_an_extra_record():
    table = read_csv((EXAMPLES / "blank_record.csv").read_bytes())
    assert table.row_count == 3
    assert table.records[1].values == ()
    assert (table.records[1].line_start, table.records[1].line_end) == (3, 3)
    assert read_csv(b"id\n001\n").row_count == 1
    assert read_csv(b"id\n001\n\n").records[-1].values == ()


def test_header_only_is_parseable():
    table = read_csv(b"id,name\n")
    assert table.row_count == 0


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "EMPTY_FILE"),
        (b"\xef\xbb\xbf", "MISSING_HEADER"),
        (b"\n", "MISSING_HEADER"),
        (b"\n001\n", "MISSING_HEADER"),
        (b'id,note\n001,"Unclosed\n', "CSV_PARSE_ERROR"),
        (b'id\n"value"unexpected\n', "CSV_PARSE_ERROR"),
    ],
)
def test_expected_input_errors(raw, code):
    with pytest.raises(DoctorError) as error:
        read_csv(raw)
    assert error.value.code == code


def test_parser_error_coordinates_and_no_input_disclosure():
    with pytest.raises(DoctorError) as error:
        read_csv(b'id,note\n001,"PRIVATE_MARKER\n')
    assert error.value.details == {"record_number": 2, "line_start": 2, "line_end": 2}
    assert "PRIVATE_MARKER" not in json.dumps(error.value.as_dict())
    assert error.value.__suppress_context__ is True


def test_byte_limit_at_boundary():
    raw = b"id\n1\n"
    assert read_csv(raw, settings=Settings(csv_max_bytes=len(raw))).row_count == 1
    with pytest.raises(DoctorError) as error:
        read_csv(raw, settings=Settings(csv_max_bytes=len(raw) - 1))
    assert error.value.code == "FILE_TOO_LARGE"


def test_row_limit_excludes_header_but_counts_blank_records():
    settings = Settings(csv_max_rows=1)
    assert read_csv(b"id\n1\n", settings=settings).row_count == 1
    for raw in (b"id\n1\n2\n", b"id\n1\n\n"):
        with pytest.raises(DoctorError) as error:
            read_csv(raw, settings=settings)
        assert error.value.code == "ROW_LIMIT_EXCEEDED"
        assert error.value.details == {"limit": 1, "record_number": 3}


@pytest.mark.parametrize("is_header", [True, False])
def test_column_limit_at_boundary(is_header):
    wide = ",".join(["v"] * MAX_COLUMNS).encode()
    assert read_csv(wide + b"\n").column_count == MAX_COLUMNS
    raw = wide + b",extra\n"
    if not is_header:
        raw = b"id\n" + raw
    with pytest.raises(DoctorError) as error:
        read_csv(raw)
    assert error.value.code == "COLUMN_LIMIT_EXCEEDED"
    assert error.value.details["record_number"] == (1 if is_header else 2)


@pytest.mark.parametrize("length", [MAX_FIELD_LENGTH + 1, 150_000])
def test_field_limit_including_csv_native_limit(length):
    with pytest.raises(DoctorError) as error:
        read_csv(b"id\n" + b"x" * length + b"\n")
    assert error.value.code == "FIELD_LIMIT_EXCEEDED"
    assert error.value.details["limit"] == MAX_FIELD_LENGTH


def test_field_limit_counts_unicode_characters_not_bytes():
    raw = ("id\n" + "\u00e9" * MAX_FIELD_LENGTH + "\n").encode()
    assert len(read_csv(raw).records[0].values[0]) == MAX_FIELD_LENGTH


def test_native_csv_limit_is_not_modified():
    before = csv.field_size_limit()
    read_csv(b"id\n001\n")
    assert csv.field_size_limit() == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"encoding": "latin-1"},
        {"encoding": "UTF-8"},
        {"encoding": None},
        {"delimiter": ","},
        {"delimiter": "pipe"},
        {"delimiter": []},
    ],
)
def test_invalid_options(kwargs):
    with pytest.raises(DoctorError) as error:
        CSVOptions(**kwargs)
    assert error.value.code == "INVALID_OPTIONS"


def test_table_and_records_are_immutable():
    table = read_csv(b"id\n001\n")
    with pytest.raises(FrozenInstanceError):
        table.header = table.records[0]
    with pytest.raises(FrozenInstanceError):
        table.records[0].line_start = 0
    assert isinstance(table.header.values, tuple)
    assert isinstance(table.records, tuple)


def test_direct_core_does_not_read_environment_or_files(monkeypatch):
    monkeypatch.setenv("CSV_MAX_BYTES", "1")
    monkeypatch.setenv("CSV_MAX_ROWS", "bad")
    assert read_csv(b"id\n001\n").row_count == 1


def test_programming_errors_are_not_misreported_as_input_errors():
    with pytest.raises(TypeError):
        decode("id\n")
    with pytest.raises(TypeError):
        parse(b"id\n")
    with pytest.raises(ValueError):
        decode(b"id\n", max_bytes=True)
    with pytest.raises(ValueError):
        parse("id\n", max_rows=0)


@pytest.mark.parametrize(
    "name,rows,columns",
    [
        ("valid.csv", 3, 3),
        ("needs_trim.csv", 2, 3),
        ("duplicate_id.csv", 2, 2),
        ("duplicate_rows.csv", 2, 2),
        ("bad_width.csv", 3, 2),
        ("blank_record.csv", 3, 2),
        ("multiline.csv", 2, 2),
        ("utf8_bom.csv", 1, 2),
        ("nbsp_edges.csv", 1, 2),
    ],
)
def test_example_contract(name, rows, columns):
    table = read_csv((EXAMPLES / name).read_bytes())
    assert (table.row_count, table.column_count) == (rows, columns)


def test_cli_success_is_not_validation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(EXAMPLES / "valid.csv")]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["parsed"] is True
    assert body["validated"] is False
    assert (body["rows"], body["columns"]) == (3, 3)
    assert "Alice" not in json.dumps(body)


def test_cli_cp1251(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                str(EXAMPLES / "cp1251_semicolon.csv"),
                "--encoding",
                "cp1251",
                "--delimiter",
                "semicolon",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["rows"] == 2


def test_cli_read_error_does_not_disclose_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(tmp_path / "PRIVATE_PATH.csv")]) == 2
    text = capsys.readouterr().out
    assert json.loads(text)["error"]["code"] == "FILE_READ_ERROR"
    assert "PRIVATE_PATH" not in text


def test_cli_parsing_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(EXAMPLES / "broken_quotes.csv")]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CSV_PARSE_ERROR"


def test_cli_byte_limit_from_environment(monkeypatch, capsys):
    monkeypatch.setenv("CSV_MAX_BYTES", "1")
    assert main([str(EXAMPLES / "valid.csv")]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "FILE_TOO_LARGE"


def test_cli_invalid_settings(monkeypatch, capsys):
    monkeypatch.setenv("CSV_MAX_ROWS", "PRIVATE_INVALID_VALUE")
    assert main([str(EXAMPLES / "valid.csv")]) == 2
    text = capsys.readouterr().out
    assert json.loads(text)["error"]["code"] == "INVALID_SETTINGS"
    assert "PRIVATE_INVALID_VALUE" not in text


def test_decode_error_does_not_expose_original_bytes():
    with pytest.raises(DoctorError) as error:
        decode(b"id\nPRIVATE_MARKER\xff")
    assert "PRIVATE_MARKER" not in str(error.value)
    assert "PRIVATE_MARKER" not in json.dumps(error.value.as_dict())
    assert error.value.__suppress_context__ is True


def test_ascii_files_are_valid_under_both_selected_encodings():
    raw = b"id,name\n001,Alice\n"
    assert (
        read_csv(raw).records
        == read_csv(raw, options=CSVOptions(encoding="cp1251")).records
    )


def test_needs_trim_fixture_has_exactly_four_changed_values():
    table = read_csv((EXAMPLES / "needs_trim.csv").read_bytes())
    values = [v for record in (table.header, *table.records) for v in record.values]
    assert sum(v != v.strip(" \t") for v in values) == 4


def test_core_defaults_match_static_profile():
    from csv_profile import load_profile

    defaults = load_profile()["defaults"]
    assert CSVOptions().encoding == defaults["encoding"]
    assert CSVOptions().delimiter == defaults["delimiter"]


@pytest.mark.parametrize("delimiter", ["comma", "semicolon", "tab"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_writer_reader_round_trip_for_tricky_strings(delimiter, newline):
    import io

    from csv_core import DELIMITERS

    expected = [
        ["id", "comma", "quotes", "line", "space", "empty", "nbsp"],
        [
            "0001",
            "a,b;c\td",
            '"quoted"',
            "first\r\nsecond\nthird\rfourth",
            "  a  ",
            "",
            "\u00a0",
        ],
        ["1234567890123456", ",", '"', "\n", "\t", "NULL", "\u00e9"],
    ]
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter=DELIMITERS[delimiter],
        quoting=csv.QUOTE_ALL,
        lineterminator=newline,
    )
    writer.writerows(expected)
    table = read_csv(
        output.getvalue().encode(), options=CSVOptions(delimiter=delimiter)
    )
    actual = [list(r.values) for r in (table.header, *table.records)]
    assert actual == expected


def test_cli_does_not_change_input_or_write_files(tmp_path, capsys):
    path = tmp_path / "sample.csv"
    raw = b" id,name\n 001,Alice \n"
    path.write_bytes(raw)
    before = set(tmp_path.iterdir())
    assert main([str(path)]) == 0
    assert path.read_bytes() == raw
    assert set(tmp_path.iterdir()) == before
    capsys.readouterr()
