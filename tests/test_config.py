import pytest

from rnix.config import (
    base_hash,
    lifetime,
    parse_duration,
    project_key,
    sanitize,
    validate_session_name,
)
from rnix.errors import RnixError


@pytest.mark.parametrize(
    ("value", "seconds"),
    [(None, None), (90, 90), ("90", 90), ("30m", 1800), ("24h", 86400), ("30d", 2592000)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def test_parse_duration_rejects_garbage():
    with pytest.raises(RnixError):
        parse_duration("soon")


def test_lifetime_enforces_modal_limit():
    with pytest.raises(RnixError):
        lifetime("25h")


def test_sanitize():
    assert sanitize("My_Repo.git") == "my-repo-git"
    assert sanitize("___") == "repo"


@pytest.mark.parametrize("name", ["feature-1", "abc1234-ff00", "a.b_c"])
def test_valid_session_names(name):
    assert validate_session_name(name) == name


@pytest.mark.parametrize("name", ["", "-x", "UP", "a b", "a..b", "x.lock", "a/b"])
def test_invalid_session_names(name):
    with pytest.raises(RnixError):
        validate_session_name(name)


def test_project_key_depends_on_shell_and_base():
    key = project_key("shell", "base")
    assert key != project_key("other", "base")
    assert key != project_key("shell", "other")


def test_base_hash_depends_on_each_input():
    value = base_hash("ref", "top", "script")
    assert value != base_hash("other", "top", "script")
    assert value != base_hash("ref", "other", "script")
    assert value != base_hash("ref", "top", "other")
