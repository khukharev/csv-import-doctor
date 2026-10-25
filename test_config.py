"""Tests for settings loading. They do not need FastAPI or a running server."""

import os
from dataclasses import FrozenInstanceError

import pytest

import config
from config import Settings, load_settings


def test_settings_defaults_are_isolated():
    assert load_settings(environ={}, read_env_file=False) == Settings()


def test_settings_precedence_and_no_environment_mutation(tmp_path, monkeypatch):
    monkeypatch.delenv("CSV_DOCTOR_FILE_ONLY", raising=False)
    before = dict(os.environ)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'APP_NAME="From file"\nAPP_AUTHOR="File Author"\n'
        "APP_LOG_LEVEL=DEBUG\nCSV_DOCTOR_FILE_ONLY=private\n",
        encoding="utf-8",
    )
    settings = load_settings(environ={"APP_NAME": "From process"}, env_file=env_file)
    assert settings == Settings("From process", "File Author", "DEBUG")
    assert dict(os.environ) == before


def test_dotenv_does_not_expand_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('APP_NAME="${HOME}"\n', encoding="utf-8")
    assert load_settings(environ={}, env_file=env_file).app_name == "${HOME}"


def test_default_env_path_does_not_depend_on_working_directory(tmp_path, monkeypatch):
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
