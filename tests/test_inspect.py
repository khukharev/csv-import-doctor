"""HTTP contract and privacy tests for POST /inspect; no real user data."""

import json
import logging
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import app_final
from app_final import create_app
from checks import inspect_csv
from config import MAX_COLUMNS, MAX_FIELD_LENGTH, MAX_ISSUE_DETAILS, Settings
from csv_core import CSVOptions
from csv_profile import CHECK_IDS, load_profile

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def client():
    with TestClient(create_app(settings=Settings())) as result:
        yield result


def upload(client, raw=b"id,name\nA,Alice\n", **kwargs):
    return client.post("/inspect", files={"file": ("test.csv", raw, "text/csv")}, **kwargs)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "request_id", "report", "details"}
    assert error["code"] == code
    assert error["report"] is None
    assert UUID(error["request_id"]).version == 4
    assert error["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert isinstance(error["details"], dict)
    return error


def test_valid_report_identity_and_seven_checks(client):
    response = upload(client, params={"id_column": "id"})
    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is True
    assert report["app_version"] == "0.7"
    assert report["request_id"] == response.headers["x-request-id"]
    assert UUID(report["request_id"]).version == 4
    assert [item["id"] for item in report["checks"]] == list(CHECK_IDS)
    assert report["normalization"]["implemented"] is True
    assert report["source"]["size_bytes"] == len(b"id,name\nA,Alice\n")


@pytest.mark.parametrize("name", [
    "valid.csv", "needs_trim.csv", "duplicate_id.csv", "duplicate_rows.csv",
    "bad_width.csv", "blank_record.csv", "multiline.csv", "nbsp_edges.csv", "utf8_bom.csv",
])
def test_http_and_core_report_agree(client, name):
    raw = (EXAMPLES / name).read_bytes()
    expected = inspect_csv(raw, id_column="id", settings=Settings())
    response = upload(client, raw, params={"id_column": "id"})
    assert response.status_code == 200
    actual = response.json()
    for key in ("request_id", "generated_at"):
        expected.pop(key)
        actual.pop(key)
    assert actual == expected
    assert (EXAMPLES / name).read_bytes() == raw


@pytest.mark.parametrize("delimiter,separator", [("comma", ","), ("semicolon", ";"), ("tab", "\t")])
@pytest.mark.parametrize("encoding", ["utf-8", "cp1251"])
def test_supported_formats(client, delimiter, separator, encoding):
    raw = f"id{separator}name\nA{separator}Анна\n".encode(encoding)
    response = upload(client, raw, params={"delimiter": delimiter, "encoding": encoding})
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["table"]["column_count"] == 2
    assert response.json()["options"]["encoding"] == encoding


def test_cp1251_fixture(client):
    response = upload(client, (EXAMPLES / "cp1251_semicolon.csv").read_bytes(),
                      params={"encoding": "cp1251", "delimiter": "semicolon"})
    assert response.status_code == 200
    assert response.json()["table"]["row_count"] == 2


def test_defaults_come_from_loaded_profile(tmp_path):
    profile = deepcopy(load_profile())
    profile["defaults"] = {"encoding": "cp1251", "delimiter": "semicolon"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    with TestClient(create_app(settings=Settings(), profile_path=path)) as client:
        response = upload(client, "id;name\nA;Анна\n".encode("cp1251"))
        assert response.status_code == 200
        assert response.json()["options"]["encoding"] == "cp1251"
        assert response.json()["table"]["column_count"] == 2
        # An explicit choice overrides the default.
        assert upload(client, params={"encoding": "utf-8", "delimiter": "comma"}).json()["valid"]


@pytest.mark.parametrize("params", [
    {"encoding": "latin-1"}, {"encoding": "UTF-8"}, {"encoding": ""},
    {"delimiter": ","}, {"delimiter": "pipe"}, {"delimiter": ""},
    {"id_column": ""}, {"id_column": "x" * 129},
    {"id_column": " id"}, {"id_column": "id "},
    {"id_column": "id\n"}, {"id_column": "\x00id"},
])
def test_invalid_parameters_are_safe_422(client, params):
    assert_error(upload(client, params=params), 422, "INVALID_REQUEST")


def test_missing_and_wrong_file_field(client):
    assert_error(client.post("/inspect"), 422, "INVALID_REQUEST")
    assert_error(client.post("/inspect", files={"other": ("a.csv", b"id\nA\n")}), 422, "INVALID_REQUEST")
    assert_error(client.post("/inspect", data={"file": "private text"}), 422, "INVALID_REQUEST")
    assert_error(client.post("/inspect", json={"file": "private text"}), 422, "INVALID_REQUEST")


@pytest.mark.parametrize("raw,code", [
    (b"", "EMPTY_FILE"), (b"\xef\xbb\xbf", "MISSING_HEADER"),
    (b"\nA\n", "MISSING_HEADER"), (b"id\n\xff\n", "DECODE_ERROR"),
    (b"id\n\x00\n", "INVALID_TEXT"), (b'id\n"unfinished', "CSV_PARSE_ERROR"),
])
def test_unreadable_files_are_422(client, raw, code):
    assert_error(upload(client, raw), 422, code)


@pytest.mark.parametrize("settings,raw,code", [
    (Settings(csv_max_bytes=8), b"id\n12345\n", "FILE_TOO_LARGE"),
    (Settings(csv_max_rows=1), b"id\nA\nB\n", "ROW_LIMIT_EXCEEDED"),
    (Settings(), ((",".join(["a"] * (MAX_COLUMNS + 1))) + "\n").encode(), "COLUMN_LIMIT_EXCEEDED"),
    (Settings(), ("id\n" + "A" * (MAX_FIELD_LENGTH + 1) + "\n").encode(), "FIELD_LIMIT_EXCEEDED"),
])
def test_size_limits_are_413(settings, raw, code):
    with TestClient(create_app(settings=settings)) as client:
        assert_error(upload(client, raw), 413, code)


def test_byte_and_row_limits_allow_exact_boundary():
    raw = b"id\nA\n"
    with TestClient(create_app(settings=Settings(csv_max_bytes=len(raw), csv_max_rows=1))) as client:
        assert upload(client, raw).status_code == 200


def test_large_valid_upload_can_spool(client):
    # More than the default 1 MiB spool threshold, below 2 MiB and 10k records.
    raw = ("name\n" + ("x" * 200 + "\n") * 6000).encode()
    response = upload(client, raw)
    assert response.status_code == 200
    assert response.json()["table"]["row_count"] == 6000
    assert response.json()["summary"]["warning_count"] == 5999
    assert len(response.json()["issues"]) == MAX_ISSUE_DETAILS


def test_errors_in_data_are_200_and_valid_false(client):
    raw = b"id,name\nA,Alice\nA,Bob\n"
    configured = upload(client, raw, params={"id_column": "id"})
    assert configured.status_code == 200
    assert configured.json()["valid"] is False
    assert any(issue["code"] == "DUPLICATE_ID" for issue in configured.json()["issues"])
    no_id = upload(client, raw)
    assert no_id.status_code == 200
    assert no_id.json()["valid"] is True
    assert no_id.json()["checks"][4]["status"] == "skipped"


def test_missing_declared_id_is_data_error(client):
    response = upload(client, params={"id_column": "not_present"})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["checks"][4]["status"] == "error"


def test_whitespace_and_nbsp_configuration():
    raw = "id,name\nA,\u00a0Alice\u00a0\n".encode()
    for enabled, expected in [(False, 0), (True, 1)]:
        with TestClient(create_app(settings=Settings(csv_trim_nbsp=enabled))) as client:
            report = upload(client, raw).json()
            assert report["normalization"]["total_changes"] == expected
            assert report["options"]["trim_nbsp"] is enabled
            assert client.get("/profile").json()["effective_settings"]["trim_nbsp"] is enabled


def test_profile_reports_effective_limits(client):
    body = client.get("/profile").json()
    assert body["limits"]["max_bytes"] == Settings().csv_max_bytes
    assert body["limits"]["max_issue_details"] == MAX_ISSUE_DETAILS
    assert body["effective_settings"]["normalization_implemented"] is True
    assert body["effective_settings"]["output_quoting"] == "all"
    assert body["effective_settings"]["report_view"] == "full"


def test_filename_and_mime_are_not_used(client):
    response = client.post("/inspect", files={"file": ("../../private.exe", b"id\nA\n", "application/octet-stream")})
    assert response.status_code == 200
    assert "private.exe" not in response.text


def test_inspection_does_not_write_files(tmp_path, monkeypatch, client):
    monkeypatch.chdir(tmp_path)
    assert upload(client).status_code == 200
    assert list(tmp_path.iterdir()) == []


def test_server_generates_distinct_ids(client):
    first = upload(client, headers={"X-Request-ID": "not-trusted"})
    second = upload(client)
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    assert first.json()["request_id"] != "not-trusted"


@pytest.mark.parametrize("method,path,status,code", [
    ("get", "/inspect", 405, "METHOD_NOT_ALLOWED"),
    ("post", "/health", 405, "METHOD_NOT_ALLOWED"),
    ("get", "/normalize", 405, "METHOD_NOT_ALLOWED"),
    ("get", "/not-found", 404, "NOT_FOUND"),
])
def test_http_errors_share_format(client, method, path, status, code):
    response = getattr(client, method)(path)
    assert_error(response, status, code)
    if status == 405:
        assert "allow" in response.headers


def test_malformed_multipart_is_safe_400(client):
    response = client.post("/inspect", content=b"sensitive body", headers={"Content-Type": "multipart/form-data"})
    assert_error(response, 400, "INVALID_REQUEST")
    assert "sensitive body" not in response.text


def test_logs_and_errors_do_not_leak_inputs(client, caplog):
    secret = "PRIVATE_MARKER_9f27"
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        success = client.post("/inspect", params={"id_column": secret},
                              files={"file": (secret + ".csv", f"{secret}\n{secret}\n{secret}\n".encode())})
        invalid = upload(client, params={"encoding": secret})
    assert success.status_code == 200
    assert_error(invalid, 422, "INVALID_REQUEST")
    messages = "\n".join(r.getMessage() for r in caplog.records if r.name == "csv_import_doctor")
    assert secret not in messages
    assert secret not in invalid.text
    assert all(secret not in json.dumps(issue) for issue in success.json()["issues"])
    assert "event=inspection_completed" in messages
    assert success.headers["x-request-id"] in messages
    assert "event=request_rejected" in messages


def test_internal_error_is_safe_and_upload_is_closed(monkeypatch, client, caplog):
    from starlette.datastructures import UploadFile

    opened = []
    original = UploadFile.__init__
    def capture(self, file, **kwargs):
        opened.append(file)
        original(self, file, **kwargs)
    monkeypatch.setattr(UploadFile, "__init__", capture)
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_EXCEPTION_TEXT")
    monkeypatch.setattr(app_final, "inspect_csv", broken)
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        response = upload(client)
    assert_error(response, 500, "INTERNAL_ERROR")
    assert opened and all(handle.closed for handle in opened)
    assert "PRIVATE_EXCEPTION_TEXT" not in response.text
    assert "PRIVATE_EXCEPTION_TEXT" not in caplog.text
    assert "event=unexpected_error" in caplog.text
    assert client.get("/health").status_code == 200


def test_upload_closed_on_domain_error(monkeypatch, client):
    from starlette.datastructures import UploadFile
    opened = []
    original = UploadFile.__init__
    def capture(self, file, **kwargs):
        opened.append(file)
        original(self, file, **kwargs)
    monkeypatch.setattr(UploadFile, "__init__", capture)
    assert_error(upload(client, b""), 422, "EMPTY_FILE")
    assert opened and all(handle.closed for handle in opened)


def test_openapi_file_query_and_error_documentation(client):
    operation = client.get("/openapi.json").json()["paths"]["/inspect"]["post"]
    assert operation["requestBody"]["required"] is True
    assert "multipart/form-data" in operation["requestBody"]["content"]
    assert {p["name"] for p in operation["parameters"]} == {"encoding", "delimiter", "id_column", "report_view"}
    assert all(p["in"] == "query" for p in operation["parameters"])
    assert {"200", "400", "413", "422", "500"} <= set(operation["responses"])


def test_upload_reads_at_most_configured_limit_plus_one(monkeypatch):
    reads = []
    original = app_final.inspect_csv
    def capture(raw, **kwargs):
        reads.append(raw)
        return original(raw, **kwargs)
    monkeypatch.setattr(app_final, "inspect_csv", capture)
    with TestClient(create_app(settings=Settings(csv_max_bytes=8))) as client:
        assert_error(upload(client, b"id\n" + b"a" * 100), 413, "FILE_TOO_LARGE")
    assert list(map(len, reads)) == [9]


def test_id_in_form_body_is_not_a_query_parameter(client):
    response = client.post("/inspect", data={"id_column": "id"},
                           files={"file": ("test.csv", b"id\nA\nA\n")})
    assert response.status_code == 200
    assert response.json()["options"]["id_column"] is None
    assert response.json()["checks"][4]["reason"] == "ID_COLUMN_NOT_CONFIGURED"


def test_change_limit_does_not_block_inspection(client):
    # 6k rows x 2 cells = 12k changes, still inside the inspection row/byte caps.
    raw = b"a,b\n" + b" x , y \n" * 6000
    response = upload(client, raw)
    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is True
    assert report["normalization"]["total_changes"] == 12000
    assert report["normalization"]["can_normalize"] is False
    assert "CHANGE_LIMIT_EXCEEDED" in report["normalization"]["blockers"]


def test_request_completion_logs_error_status(client, caplog):
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        response = upload(client, b"", params={"id_column": "id"})
    completed = [r.getMessage() for r in caplog.records
                 if "event=request_completed" in r.getMessage()
                 and response.headers["x-request-id"] in r.getMessage()]
    assert len(completed) == 1
    assert "status=422" in completed[0]
    assert "path=/inspect" in completed[0]


@pytest.mark.parametrize("params", [{"delimiter": "pipe"}, {"id_column": ""}])
def test_framework_closes_upload_on_parameter_validation_error(monkeypatch, client, params):
    from starlette.datastructures import UploadFile
    opened = []
    original = UploadFile.__init__
    def capture(self, file, **kwargs):
        opened.append(file)
        original(self, file, **kwargs)
    monkeypatch.setattr(UploadFile, "__init__", capture)
    assert_error(upload(client, params=params), 422, "INVALID_REQUEST")
    assert opened and all(handle.closed for handle in opened)


def test_invalid_file_parameter_does_not_echo_secret(client):
    secret = "PRIVATE_FORM_VALUE_05"
    response = client.post("/inspect", data={"file": secret})
    assert_error(response, 422, "INVALID_REQUEST")
    assert secret not in response.text
