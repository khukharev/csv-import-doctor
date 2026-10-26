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
    assert (
        load_settings(environ={"UNRELATED_KEY": "value"}, read_env_file=False)
        == Settings()
    )


def test_settings_are_immutable():
    settings = Settings()
    with pytest.raises(FrozenInstanceError):
        settings.app_name = "Changed"


def test_csv_limit_defaults():
    settings = load_settings(environ={}, read_env_file=False)
    assert settings.csv_max_bytes == 2_097_152
    assert settings.csv_max_rows == 10_000


def test_csv_limits_environment_overrides_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CSV_MAX_BYTES=200\nCSV_MAX_ROWS=4\n", encoding="utf-8")
    settings = load_settings(environ={"CSV_MAX_BYTES": "300"}, env_file=env_file)
    assert settings.csv_max_bytes == 300
    assert settings.csv_max_rows == 4


@pytest.mark.parametrize(
    "key,maximum", [("CSV_MAX_BYTES", 8_388_608), ("CSV_MAX_ROWS", 50_000)]
)
@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "zero",
        "negative",
        "float",
        "space",
        "plus",
        "letters",
        "unicode-digit",
        "unset",
        "too-large",
    ],
)
def test_invalid_csv_limits_fail(key, maximum, kind):
    values = {
        "empty": "",
        "zero": "0",
        "negative": "-1",
        "float": "1.5",
        "space": " 1",
        "plus": "+1",
        "letters": "abc",
        "unicode-digit": "\u0661",
        "unset": None,
        "too-large": str(maximum + 1),
    }
    with pytest.raises(ValueError, match=key):
        load_settings(environ={key: values[kind]}, read_env_file=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"csv_max_bytes": True},
        {"csv_max_rows": False},
        {"csv_max_bytes": 0},
        {"csv_max_rows": 1.0},
    ],
)
def test_direct_settings_reject_invalid_limit_types(kwargs):
    with pytest.raises(ValueError, match="CSV_"):
        Settings(**kwargs)


def test_csv_limit_upper_boundaries():
    settings = load_settings(
        environ={"CSV_MAX_BYTES": "8388608", "CSV_MAX_ROWS": "50000"},
        read_env_file=False,
    )
    assert (settings.csv_max_bytes, settings.csv_max_rows) == (8_388_608, 50_000)


def test_nbsp_default_is_false():
    assert load_settings(environ={}, read_env_file=False).csv_trim_nbsp is False


@pytest.mark.parametrize("text,expected", [("true", True), ("false", False)])
def test_nbsp_env_values(text, expected):
    assert load_settings(environ={"CSV_TRIM_NBSP": text}, read_env_file=False).csv_trim_nbsp is expected


@pytest.mark.parametrize("value", ["", "TRUE", "False", "yes", "0", "1", " true", None])
def test_nbsp_invalid_env_fails(value):
    with pytest.raises(ValueError, match="CSV_TRIM_NBSP"):
        load_settings(environ={"CSV_TRIM_NBSP": value}, read_env_file=False)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_nbsp_direct_type_is_strict(value):
    with pytest.raises(ValueError, match="CSV_TRIM_NBSP"):
        Settings(csv_trim_nbsp=value)


def test_nbsp_process_overrides_dotenv_without_modifying_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CSV_TRIM_NBSP=true\n", encoding="utf-8")
    before = dict(os.environ)
    assert load_settings(environ={}, env_file=env_file).csv_trim_nbsp is True
    assert load_settings(environ={"CSV_TRIM_NBSP": "false"}, env_file=env_file).csv_trim_nbsp is False
    assert dict(os.environ) == before
