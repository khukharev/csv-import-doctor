import json
import re
from pathlib import Path
from typing import Any, NoReturn

import config

PROFILE_ID = "basic_table_v1"
CHECK_IDS = ("C01", "C02", "C03", "C04", "C05", "C06", "C07")
SUPPORTED_ENCODINGS = ("utf-8", "cp1251")
SUPPORTED_DELIMITERS = ("comma", "semicolon", "tab")
# All checks are available in the local core. HTTP inspection is a later stage.
IMPLEMENTED_CHECK_IDS: tuple[str, ...] = CHECK_IDS

_TOP_LEVEL_KEYS = frozenset(
    {
        "id",
        "version",
        "title",
        "description",
        "defaults",
        "allowed_encodings",
        "allowed_delimiters",
        "trim_characters",
        "normalization",
        "output",
        "checks",
    }
)
_DEFAULTS_KEYS = frozenset({"encoding", "delimiter"})
_NORMALIZATION_KEYS = frozenset({"allowed_changes", "forbidden_changes"})
_OUTPUT_KEYS = frozenset({"encoding", "delimiter", "line_terminator"})
_CHECK_KEYS = frozenset(
    {"id", "title", "description", "blocks_normalization", "requires_id_column"}
)
_CODE_POINT = re.compile(r"U\+[0-9A-F]{4}")


class ProfileError(ValueError):
    """Load or CSV profile error."""


def _fail(where: str, message: str) -> NoReturn:
    raise ProfileError(f"{where}: {message}")


def _object(value: Any, where: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(where, "must be a JSON object")
    actual = set(value)
    if actual != keys:
        _fail(
            where,
            f"missing keys {sorted(keys - actual)}, unexpected keys {sorted(actual - keys)}",
        )
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(where, "must be a non-empty string")
    return value


def _text_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _fail(where, "must be a non-empty list")
    items = [_text(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if len(set(items)) != len(items):
        _fail(where, "must not contain duplicates")
    return items


def _choice_list(value: Any, where: str, supported: tuple[str, ...]) -> list[str]:
    items = _text_list(value, where)
    unsupported = [item for item in items if item not in supported]
    if unsupported:
        _fail(where, f"unsupported values {unsupported}")
    return items


def validate_profile(data: Any) -> dict[str, Any]:
    profile = _object(data, "profile", _TOP_LEVEL_KEYS)

    if profile["id"] != PROFILE_ID:
        _fail("id", f"must be {PROFILE_ID!r}")
    _text(profile["version"], "version")
    _text(profile["title"], "title")
    _text(profile["description"], "description")

    encodings = _choice_list(profile["allowed_encodings"], "allowed_encodings", SUPPORTED_ENCODINGS)
    delimiters = _choice_list(
        profile["allowed_delimiters"], "allowed_delimiters", SUPPORTED_DELIMITERS
    )
    defaults = _object(profile["defaults"], "defaults", _DEFAULTS_KEYS)
    if defaults["encoding"] not in encodings:
        _fail("defaults.encoding", "must be one of allowed_encodings")
    if defaults["delimiter"] not in delimiters:
        _fail("defaults.delimiter", "must be one of allowed_delimiters")

    for index, code_point in enumerate(_text_list(profile["trim_characters"], "trim_characters")):
        if not _CODE_POINT.fullmatch(code_point):
            _fail(f"trim_characters[{index}]", "must look like U+0020")

    if profile["trim_characters"] != ["U+0020", "U+0009"]:
        _fail("trim_characters", "this profile supports only SPACE and TAB; use CSV_TRIM_NBSP for NBSP")

    normalization = _object(profile["normalization"], "normalization", _NORMALIZATION_KEYS)
    allowed = _text_list(normalization["allowed_changes"], "normalization.allowed_changes")
    forbidden = _text_list(normalization["forbidden_changes"], "normalization.forbidden_changes")
    if set(allowed) & set(forbidden):
        _fail("normalization", "a change cannot be both allowed and forbidden")

    output = _object(profile["output"], "output", _OUTPUT_KEYS)
    for key in sorted(_OUTPUT_KEYS):
        _text(output[key], f"output.{key}")

    checks = profile["checks"]
    if not isinstance(checks, list) or len(checks) != len(CHECK_IDS):
        _fail("checks", f"must be a list of exactly {len(CHECK_IDS)} checks")
    for expected_id, check in zip(CHECK_IDS, checks):
        where = f"checks[{expected_id}]"
        check = _object(check, where, _CHECK_KEYS)
        if check["id"] != expected_id:
            _fail(where, f"id must be {expected_id!r}, in the order {list(CHECK_IDS)}")
        _text(check["title"], f"{where}.title")
        _text(check["description"], f"{where}.description")
        for key in ("blocks_normalization", "requires_id_column"):
            if not isinstance(check[key], bool):
                _fail(f"{where}.{key}", "must be true or false")

    return profile


def load_profile(path: Path | None = None) -> dict[str, Any]:
    profile_path = Path(path) if path is not None else config.PROFILE_PATH
    try:
        text = profile_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileError(f"profile file cannot be read: {profile_path.name}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"profile is not valid JSON: line {exc.lineno}, column {exc.colno}"
        ) from exc
    return validate_profile(data)
