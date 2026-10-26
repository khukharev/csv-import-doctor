import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

APP_VERSION = "0.4"
REPORT_SCHEMA_VERSION = "1.0"
MAX_HEADER_LENGTH = 128
MAX_ISSUE_DETAILS = 1_000
MAX_CHANGES = 10_000
PROJECT_ROOT = Path(__file__).resolve().parent
PROFILE_PATH = PROJECT_ROOT / "profile.json"
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ROWS = 10_000
MAX_ALLOWED_BYTES = 8 * 1024 * 1024
MAX_ALLOWED_ROWS = 50_000
MAX_COLUMNS = 200
MAX_FIELD_LENGTH = 10_000
DEFAULT_NAME = "CSV Import Doctor"
DEFAULT_AUTHOR = "Student"
ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


@dataclass(frozen=True)
class Settings:
    app_name: str = DEFAULT_NAME
    app_author: str = DEFAULT_AUTHOR
    log_level: str = "INFO"
    csv_max_bytes: int = DEFAULT_MAX_BYTES
    csv_max_rows: int = DEFAULT_MAX_ROWS
    csv_trim_nbsp: bool = False

    def __post_init__(self) -> None:
        if type(self.csv_trim_nbsp) is not bool:
            raise ValueError("CSV_TRIM_NBSP: must be true or false")
        for key, value in (
            ("APP_NAME", self.app_name),
            ("APP_AUTHOR", self.app_author),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(
                    f"{key}: must be a non-empty string of at most 128 characters"
                )
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError(f"{key}: control characters are not allowed")
        if self.log_level not in ALLOWED_LOG_LEVELS:
            raise ValueError(
                "APP_LOG_LEVEL: allowed values are DEBUG, INFO, WARNING, ERROR"
            )

        for key, value, upper in (
            ("CSV_MAX_BYTES", self.csv_max_bytes, MAX_ALLOWED_BYTES),
            ("CSV_MAX_ROWS", self.csv_max_rows, MAX_ALLOWED_ROWS),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"{key}: must be an integer from 1 to {upper}")


def load_settings(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    read_env_file: bool = True,
) -> Settings:
    """Build settings. Priority: process environment, then .env, then defaults."""
    file_values: Mapping[str, str | None] = {}
    if read_env_file:
        path = PROJECT_ROOT / ".env" if env_file is None else Path(env_file)
        file_values = dotenv_values(path, encoding="utf-8", interpolate=False)
    process_values = os.environ if environ is None else environ

    def value(key: str, default: str) -> str:
        if key in process_values:
            result = process_values[key]
        elif key in file_values:
            result = file_values[key]
        else:
            result = default
        if result is None:
            raise ValueError(f"{key}: value is not set")
        return result

    def integer(key: str, default: int) -> int:
        text = value(key, str(default))
        if not text or any(char not in "0123456789" for char in text):
            raise ValueError(f"{key}: must contain only decimal digits")
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{key}: invalid integer") from None

    def boolean(key: str, default: bool) -> bool:
        text = value(key, "true" if default else "false")
        if text not in {"true", "false"}:
            raise ValueError(f"{key}: must be true or false (lowercase)")
        return text == "true"

    return Settings(
        app_name=value("APP_NAME", DEFAULT_NAME),
        app_author=value("APP_AUTHOR", DEFAULT_AUTHOR),
        log_level=value("APP_LOG_LEVEL", "INFO"),
        csv_max_bytes=integer("CSV_MAX_BYTES", DEFAULT_MAX_BYTES),
        csv_max_rows=integer("CSV_MAX_ROWS", DEFAULT_MAX_ROWS),
        csv_trim_nbsp=boolean("CSV_TRIM_NBSP", False),
    )
