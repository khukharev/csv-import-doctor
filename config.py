import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

APP_VERSION = "0.1"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_NAME = "CSV Import Doctor"
DEFAULT_AUTHOR = "Student"
ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


@dataclass(frozen=True)
class Settings:

    app_name: str = DEFAULT_NAME
    app_author: str = DEFAULT_AUTHOR
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for key, value in (("APP_NAME", self.app_name), ("APP_AUTHOR", self.app_author)):
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{key}: non empty string no longer than 128 characters")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError(f"{key}: special symbols are prohibited")
        if self.log_level not in ALLOWED_LOG_LEVELS:
            raise ValueError("APP_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR")


def load_settings(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    read_env_file: bool = True,
) -> Settings:
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
            raise ValueError(f"{key}: Value doesn't exist")
        return result

    return Settings(
        app_name=value("APP_NAME", DEFAULT_NAME),
        app_author=value("APP_AUTHOR", DEFAULT_AUTHOR),
        log_level=value("APP_LOG_LEVEL", "INFO"),
    )
