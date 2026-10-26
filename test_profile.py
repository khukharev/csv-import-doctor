"""Tests for profile.json and its validation. They do not need FastAPI."""

import copy
import json

import pytest

import config
from csv_profile import (
    CHECK_IDS,
    IMPLEMENTED_CHECK_IDS,
    ProfileError,
    load_profile,
    validate_profile,
)


def test_project_profile_is_valid():
    profile = load_profile()
    assert profile["id"] == "basic_table_v1"
    assert profile["defaults"] == {"encoding": "utf-8", "delimiter": "comma"}
    assert [check["id"] for check in profile["checks"]] == list(CHECK_IDS)


def test_default_path_does_not_depend_on_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_profile()["id"] == "basic_table_v1"


def test_blocking_and_id_requirements_match_the_design():
    checks = {check["id"]: check for check in load_profile()["checks"]}
    blocking = {cid for cid, check in checks.items() if check["blocks_normalization"]}
    needs_id = {cid for cid, check in checks.items() if check["requires_id_column"]}
    assert blocking == {"C01", "C02", "C03", "C05"}
    assert needs_id == {"C05", "C07"}


def test_nbsp_is_not_trimmed_by_default():
    profile = load_profile()
    assert profile["trim_characters"] == ["U+0020", "U+0009"]
    assert "U+00A0" not in profile["trim_characters"]


def test_implemented_checks_are_known_checks():
    assert set(IMPLEMENTED_CHECK_IDS) <= set(CHECK_IDS)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ProfileError, match="cannot be read"):
        load_profile(tmp_path / "missing.json")


def test_invalid_json_is_rejected_with_position(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{\n  "id": \n', encoding="utf-8")
    with pytest.raises(ProfileError, match="line"):
        load_profile(path)


def test_invalid_utf8_is_rejected(tmp_path):
    path = tmp_path / "profile.json"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ProfileError, match="cannot be read"):
        load_profile(path)


def test_top_level_must_be_an_object():
    with pytest.raises(ProfileError):
        validate_profile([])


def test_valid_profile_survives_a_json_round_trip():
    original = load_profile()
    assert validate_profile(json.loads(json.dumps(original))) == original


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(id="other_profile"), id="wrong-id"),
        pytest.param(lambda d: d.pop("title"), id="missing-key"),
        pytest.param(lambda d: d.update(extra=1), id="unexpected-key"),
        pytest.param(lambda d: d.update(title="   "), id="blank-title"),
        pytest.param(lambda d: d["defaults"].update(encoding="latin-1"), id="default-not-allowed"),
        pytest.param(lambda d: d.update(allowed_encodings=["latin-1"]), id="unsupported-encoding"),
        pytest.param(lambda d: d["allowed_delimiters"].append("comma"), id="duplicate-delimiter"),
        pytest.param(lambda d: d["trim_characters"].append("U+20"), id="bad-code-point"),
        pytest.param(lambda d: d["normalization"]["forbidden_changes"].append("trim_cell_edges"), id="allowed-and-forbidden"),
        pytest.param(lambda d: d["output"].pop("encoding"), id="output-key-missing"),
        pytest.param(lambda d: d["checks"].reverse(), id="checks-out-of-order"),
        pytest.param(lambda d: d["checks"].pop(), id="check-missing"),
        pytest.param(lambda d: d["checks"][0].pop("title"), id="check-key-missing"),
        pytest.param(lambda d: d["checks"][0].update(blocks_normalization="yes"), id="flag-not-boolean"),
        pytest.param(lambda d: d["checks"][0].update(blocks_normalization=1), id="flag-is-integer"),
    ],
)
def test_invalid_profiles_are_rejected(mutate):
    data = copy.deepcopy(load_profile())
    mutate(data)
    with pytest.raises(ProfileError):
        validate_profile(data)


def test_profile_path_constant_points_to_project_file():
    assert config.PROFILE_PATH.name == "profile.json"
    assert config.PROFILE_PATH.parent == config.PROJECT_ROOT


@pytest.mark.parametrize("characters", [["U+000A"], ["U+0020", "U+0009", "U+00A0"]])
def test_profile_cannot_enable_unsafe_or_hidden_trim(characters):
    profile = copy.deepcopy(load_profile())
    profile["trim_characters"] = characters
    with pytest.raises(ProfileError, match="trim_characters"):
        validate_profile(profile)
