"""POST /normalize through the real FastAPI app; ZIP contents are checked."""

import hashlib
import io
import json
import logging
import zipfile
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import app_final
import normalize
from app_final import create_app
from config import MAX_CHANGES, Settings
from csv_core import read_csv

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SECRET = "SECRET_CELL_VALUE"


@pytest.fixture
def client():
    with TestClient(create_app(settings=Settings())) as result:
        yield result


def post(client, raw, **params):
    return client.post("/normalize", params=params, files={"file": ("x.csv", raw, "text/csv")})


def unzip(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="csv-import-doctor-result.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["cleaned.csv", "report.json", "changes.json"]
        return {name: archive.read(name) for name in archive.namelist()}


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["x-request-id"]
    return error


def capture_uploads(monkeypatch):
    from starlette.datastructures import UploadFile
    opened = []
    original = UploadFile.__init__

    def capture(self, file, **kwargs):
        opened.append(file)
        original(self, file, **kwargs)

    monkeypatch.setattr(UploadFile, "__init__", capture)
    return opened


def test_download_zip_with_expected_contents(client):
    raw = (EXAMPLES / "needs_trim.csv").read_bytes()
    response = post(client, raw, id_column="id")
    files = unzip(response)
    assert files["cleaned.csv"] == b'"id","name","note"\n"001","Alice","Ready"\n"002","Bob","Done"\n'
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    reports = json.loads(files["report.json"])
    assert reports["before"]["request_id"] == reports["after"]["request_id"] == request_id
    assert reports["after"]["valid"] is True
    log = json.loads(files["changes.json"])
    assert log["request_id"] == request_id
    assert log["input"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert log["output"]["sha256"] == hashlib.sha256(files["cleaned.csv"]).hexdigest()
    assert [(c["record_number"], c["column_number"], c["after"]) for c in log["changes"]] == [
        (1, 1, "id"), (2, 1, "001"), (2, 2, "Alice"), (3, 3, "Done"),
    ]
    assert response.headers["cache-control"] == "no-store"


def test_downloaded_csv_reads_back_and_renormalizes_without_changes(client):
    first = unzip(post(client, (EXAMPLES / "multiline.csv").read_bytes()))
    table = read_csv(first["cleaned.csv"])
    assert table.records[0].values == ("001", "First line\nSecond line")
    second = unzip(post(client, first["cleaned.csv"]))
    assert second["cleaned.csv"] == first["cleaned.csv"]
    assert json.loads(second["changes.json"])["changes"] == []


def test_query_options_cp1251_semicolon(client):
    files = unzip(post(
        client, (EXAMPLES / "cp1251_semicolon.csv").read_bytes(),
        encoding="cp1251", delimiter="semicolon", id_column="id",
    ))
    assert files["cleaned.csv"].decode("utf-8") == '"id","city"\n"001","Москва"\n"002","Казань"\n'


def test_settings_control_quoting_and_nbsp():
    settings = Settings(csv_quoting="minimal", csv_trim_nbsp=True)
    with TestClient(create_app(settings=settings)) as client:
        files = unzip(post(client, (EXAMPLES / "nbsp_edges.csv").read_bytes()))
    assert files["cleaned.csv"] == b"id,name\n001,Alice\n"


@pytest.mark.parametrize("name,blocker", [
    ("duplicate_id.csv", "C05"), ("bad_width.csv", "C02"),
])
def test_data_errors_return_409_with_report(client, name, blocker):
    response = post(client, (EXAMPLES / name).read_bytes(), id_column="id")
    error = assert_error(response, 409, "NORMALIZATION_BLOCKED")
    assert blocker in error["details"]["blockers"]
    report = error["report"]
    assert report["valid"] is False
    assert report["request_id"] == response.headers["x-request-id"]
    assert "column_stats" in report  # default view is full


def test_409_report_view_query_and_setting(client):
    raw = (EXAMPLES / "duplicate_id.csv").read_bytes()
    compact = assert_error(post(client, raw, id_column="id", report_view="compact"), 409, "NORMALIZATION_BLOCKED")
    assert compact["report"]["view"] == "compact"
    assert "column_stats" not in compact["report"]
    with TestClient(create_app(settings=Settings(csv_report_view="compact"))) as other:
        by_setting = assert_error(post(other, raw, id_column="id"), 409, "NORMALIZATION_BLOCKED")
        assert by_setting["report"]["view"] == "compact"
        overridden = assert_error(post(other, raw, id_column="id", report_view="full"), 409, "NORMALIZATION_BLOCKED")
        assert "column_stats" in overridden["report"]


def test_zip_reports_are_full_even_with_compact_setting():
    with TestClient(create_app(settings=Settings(csv_report_view="compact"))) as client:
        files = unzip(post(client, (EXAMPLES / "needs_trim.csv").read_bytes(), report_view="compact"))
    reports = json.loads(files["report.json"])
    assert "column_stats" in reports["before"] and "view" not in reports["before"]


def test_unreadable_csv_is_422(client):
    error = assert_error(post(client, (EXAMPLES / "broken_quotes.csv").read_bytes()), 422, "CSV_PARSE_ERROR")
    assert error["report"] is None


def test_invalid_parameters_are_422(client):
    assert_error(post(client, b"id\n1\n", report_view="short"), 422, "INVALID_REQUEST")
    assert_error(post(client, b"id\n1\n", encoding="latin-1"), 422, "INVALID_REQUEST")
    assert_error(client.post("/normalize"), 422, "INVALID_REQUEST")


def test_change_limit_is_413(client):
    raw = b" a,b\n" + b" x , y \n" * (MAX_CHANGES // 2)
    error = assert_error(post(client, raw), 413, "CHANGE_LIMIT_EXCEEDED")
    assert error["details"] == {"limit": MAX_CHANGES, "actual": MAX_CHANGES + 1}


def test_input_size_limit_is_413():
    with TestClient(create_app(settings=Settings(csv_max_bytes=8))) as client:
        assert_error(post(client, b"id\n" + b"a" * 100), 413, "FILE_TOO_LARGE")


def test_output_size_limit_is_413(monkeypatch, client):
    monkeypatch.setattr(normalize, "MAX_OUTPUT_BYTES", 10)
    assert_error(post(client, b"a,b\n1,2\n"), 413, "OUTPUT_TOO_LARGE")


def test_invariant_failure_is_500_without_values(monkeypatch, client, caplog):
    monkeypatch.setattr(normalize, "serialize_csv", lambda rows, quoting: b"id\nOTHER\n")
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        response = post(client, f"id,name\n1,{SECRET}\n".encode())
    assert_error(response, 500, "INTERNAL_NORMALIZATION_ERROR")
    assert response.headers["content-type"].startswith("application/json")
    assert "event=normalization_invariant_failed" in caplog.text
    assert SECRET not in response.text and SECRET not in caplog.text


@pytest.mark.parametrize("raw,status", [
    ((EXAMPLES / "needs_trim.csv").read_bytes(), 200),
    (b"", 422),
    ((EXAMPLES / "duplicate_id.csv").read_bytes(), 409),
])
def test_upload_is_closed(monkeypatch, client, raw, status):
    opened = capture_uploads(monkeypatch)
    assert post(client, raw, id_column="id").status_code == status
    assert opened and all(handle.closed for handle in opened)


def test_logs_have_counts_but_no_values(client, caplog):
    raw = f"id,name\n1, {SECRET} \n".encode()
    with caplog.at_level(logging.DEBUG, logger="csv_import_doctor"):
        response = post(client, raw, id_column="id")
    files = unzip(response)
    # Values belong only in changes.json, never in logs.
    assert json.loads(files["changes.json"])["changes"][0]["after"] == SECRET
    completed = [r.getMessage() for r in caplog.records if "event=normalization_completed" in r.getMessage()]
    assert completed == [
        f"event=normalization_completed request_id={response.headers['x-request-id']} "
        f"rows=1 changes=1 output_bytes={len(files['cleaned.csv'])}"
    ]
    assert SECRET not in caplog.text
    assert "id_column" not in caplog.text


def test_409_body_has_no_cell_values(client):
    response = post(client, f"id,name\n1,{SECRET}\n1,b\n".encode(), id_column="id")
    assert_error(response, 409, "NORMALIZATION_BLOCKED")
    assert SECRET not in response.text


def test_normalization_does_not_write_files(tmp_path, monkeypatch, client):
    monkeypatch.chdir(tmp_path)
    unzip(post(client, (EXAMPLES / "needs_trim.csv").read_bytes()))
    assert list(tmp_path.iterdir()) == []


def test_filename_is_not_used(client):
    response = client.post("/normalize", files={"file": ("../../evil.csv", b"id\n1\n", "text/plain")})
    files = unzip(response)
    assert b"evil" not in response.content
    assert files["cleaned.csv"] == b'"id"\n"1"\n'


def test_normalize_uses_core_with_read_cap(monkeypatch):
    reads = []
    original = app_final.normalize_csv

    def capture(raw, **kwargs):
        reads.append(raw)
        return original(raw, **kwargs)

    monkeypatch.setattr(app_final, "normalize_csv", capture)
    with TestClient(create_app(settings=Settings(csv_max_bytes=8))) as client:
        assert_error(post(client, b"id\n" + b"a" * 100), 413, "FILE_TOO_LARGE")
    assert list(map(len, reads)) == [9]


def test_openapi_documents_normalize(client):
    operation = client.get("/openapi.json").json()["paths"]["/normalize"]["post"]
    assert {p["name"] for p in operation["parameters"]} == {"encoding", "delimiter", "id_column", "report_view"}
    assert {"200", "409", "413", "422", "500"} <= set(operation["responses"])
    assert "application/zip" in operation["responses"]["200"]["content"]
