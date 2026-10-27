"""HTTP-level tests for the endpoints that exist in the current version."""

import json
import logging
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import config
from app_final import LOGGER, configure_logging, create_app
from config import APP_VERSION, Settings
from csv_profile import CHECK_IDS, ProfileError


@pytest.fixture
def client():
    # Do not read the real .env or the environment of the developer's machine.
    with TestClient(create_app(Settings())) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {
        "name": "CSV Import Doctor",
        "author": "Student",
        "version": APP_VERSION,
        "profile_id": "basic_table_v1",
        "profile_version": "1.0",
    }
    assert APP_VERSION == "0.7"


def test_profile_endpoint(client):
    response = client.get("/profile")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"profile", "implemented_check_ids", "limits", "effective_settings"}
    assert body["profile"]["id"] == "basic_table_v1"
    assert [check["id"] for check in body["profile"]["checks"]] == list(CHECK_IDS)
    assert set(body["implemented_check_ids"]) <= set(CHECK_IDS)


def test_profile_endpoint_matches_profile_file(client):
    expected = json.loads(config.PROFILE_PATH.read_text(encoding="utf-8"))
    assert client.get("/profile").json()["profile"] == expected


def test_profile_method_is_get_only(client):
    assert client.post("/profile").status_code == 405


def test_docs(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text


def test_openapi_contains_only_current_routes(client):
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/health", "/version", "/profile", "/inspect", "/normalize"}
    assert schema["info"]["version"] == APP_VERSION


def test_environment_name_and_author_are_used(monkeypatch):
    monkeypatch.setenv("APP_NAME", "CSV Doctor Test")
    monkeypatch.setenv("APP_AUTHOR", "Test Author")
    monkeypatch.setenv("APP_LOG_LEVEL", "INFO")
    with TestClient(create_app()) as client:
        body = client.get("/version").json()
        schema = client.get("/openapi.json").json()
    assert body["name"] == "CSV Doctor Test"
    assert body["author"] == "Test Author"
    assert schema["info"]["title"] == "CSV Doctor Test"


def test_health_method_is_get_only(client):
    assert client.post("/health").status_code == 405


def test_missing_routes_return_404_and_normalize_is_post_only(client):
    for path in ("/", "/missing"):
        assert client.get(path).status_code == 404
    assert client.get("/normalize").status_code == 405


def test_request_headers_and_unique_server_ids(client):
    first = client.get("/health", headers={"X-Request-ID": "client-supplied"})
    second = client.get("/health")
    assert first.headers["cache-control"] == "no-store"
    assert UUID(first.headers["x-request-id"]).version == 4
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_logs_include_result_not_query_or_client_id(client, caplog):
    marker = "PRIVATE_TEST_MARKER"
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        response = client.get(f"/health?note={marker}", headers={"X-Request-ID": marker})
        client.get(f"/{marker}")
    messages = "\n".join(
        record.getMessage() for record in caplog.records if record.name == "csv_import_doctor"
    )
    assert "event=request_completed" in messages
    assert "path=/health" in messages
    assert "status=200" in messages
    assert response.headers["x-request-id"] in messages
    assert marker not in messages


def test_startup_and_shutdown_are_logged(caplog):
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        with TestClient(create_app(Settings())) as client:
            assert client.get("/health").status_code == 200
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=application_started version=0.7" in messages
    assert "profile_id=basic_table_v1 profile_version=1.0" in messages
    assert "event=application_stopped version=0.7" in messages


def test_logger_does_not_duplicate_handlers():
    configure_logging("INFO")
    configure_logging("DEBUG")
    handlers = [h for h in LOGGER.handlers if getattr(h, "csv_doctor_handler", False)]
    assert len(handlers) == 1
    configure_logging("INFO")


def test_invalid_settings_prevent_startup(monkeypatch):
    monkeypatch.setenv("APP_LOG_LEVEL", "INVALID")
    with pytest.raises(ValueError, match="APP_LOG_LEVEL"):
        with TestClient(create_app()):
            pass


def test_invalid_profile_prevents_startup_and_is_logged(tmp_path, caplog):
    broken = tmp_path / "profile.json"
    broken.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="csv_import_doctor"):
        with pytest.raises(ProfileError):
            with TestClient(create_app(Settings(), profile_path=broken)):
                pass
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=profile_load_failed" in messages
    assert "event=application_started" not in messages


def test_missing_profile_prevents_startup(tmp_path):
    with pytest.raises(ProfileError):
        with TestClient(create_app(Settings(), profile_path=tmp_path / "missing.json")):
            pass


def test_all_checks_are_available_in_local_core(client):
    assert client.get("/profile").json()["implemented_check_ids"] == list(CHECK_IDS)


def test_invalid_csv_limits_prevent_startup(monkeypatch):
    monkeypatch.setenv("CSV_MAX_BYTES", "0")
    with pytest.raises(ValueError, match="CSV_MAX_BYTES"):
        with TestClient(create_app()):
            pass
