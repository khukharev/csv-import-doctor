import logging
import os
from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app_final import LOGGER, configure_logging, create_app
from config import APP_VERSION, Settings, load_settings


@pytest.fixture
def client():
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
    }
    assert APP_VERSION == "0.1"


def test_docs(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger-ui" in response.text


def test_openapi_contains_only_current_routes(client):
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == {"/health", "/version"}
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


def test_missing_route_and_future_routes_return_404(client):
    for path in ("/", "/profile", "/inspect", "/normalize", "/missing"):
        assert client.get(path).status_code == 404


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
    assert "event=application_started version=0.1" in messages
    assert "event=application_stopped version=0.1" in messages


def test_logger_does_not_duplicate_handlers():
    configure_logging("INFO")
    configure_logging("DEBUG")
    handlers = [h for h in LOGGER.handlers if getattr(h, "csv_doctor_handler", False)]
    assert len(handlers) == 1
    configure_logging("INFO")


def test_settings_defaults_are_isolated():
    assert load_settings(environ={}, read_env_file=False) == Settings()


def test_settings_precedence_and_no_environment_mutation(tmp_path, monkeypatch):
    monkeypatch.delenv("CSV_DOCTOR_FILE_ONLY", raising=False)
    before = dict(os.environ)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'APP_NAME="From file"\nAPP_AUTHOR="File Author"\n'
        'APP_LOG_LEVEL=DEBUG\nCSV_DOCTOR_FILE_ONLY=private\n', encoding="utf-8"
    )
    settings = load_settings(environ={"APP_NAME": "From process"}, env_file=env_file)
    assert settings == Settings("From process", "File Author", "DEBUG")
    assert dict(os.environ) == before


def test_dotenv_does_not_expand_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('APP_NAME="${HOME}"\n', encoding="utf-8")
    assert load_settings(environ={}, env_file=env_file).app_name == "${HOME}"


def test_default_env_path_does_not_depend_on_working_directory(tmp_path, monkeypatch):
    import config

    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    (project / ".env").write_text('APP_NAME="Project file"\n', encoding="utf-8")
    (other / ".env").write_text('APP_NAME="Wrong file"\n', encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", project)
    monkeypatch.chdir(other)
    assert load_settings(environ={}).app_name == "Project file"


@pytest.mark.parametrize(
    "env",
    [
        {"APP_NAME": ""},
        {"APP_NAME": "   "},
        {"APP_NAME": "x" * 129},
        {"APP_NAME": "hello\nworld"},
        {"APP_AUTHOR": ""},
        {"APP_AUTHOR": "bad\tname"},
        {"APP_LOG_LEVEL": "TRACE"},
        {"APP_LOG_LEVEL": ""},
        {"APP_LOG_LEVEL": "info"},
    ],
)
def test_invalid_settings_fail(env):
    with pytest.raises(ValueError, match="APP_"):
        load_settings(environ=env, read_env_file=False)


def test_unknown_environment_variables_are_ignored():
    assert load_settings(environ={"UNRELATED_KEY": "value"}, read_env_file=False) == Settings()


def test_settings_are_immutable():
    settings = Settings()
    with pytest.raises(FrozenInstanceError):
        settings.app_name = "Changed"


def test_invalid_settings_prevent_startup(monkeypatch):
    monkeypatch.setenv("APP_LOG_LEVEL", "INVALID")
    with pytest.raises(ValueError, match="APP_LOG_LEVEL"):
        with TestClient(create_app()):
            pass
