"""Path containment and scope key tests."""

import pytest

from sandboxd.paths import InvalidPathError, scope_key, validate_relative_path


@pytest.mark.parametrize(
    "value",
    [
        "a.txt",
        "inputs/photo.png",
        "work/sub/dir/file.py",
        "a/./b.txt",
        "outputs/result.json",
    ],
)
def test_valid_relative_paths(value):
    safe = validate_relative_path(value)
    assert safe.value == value or safe.value.endswith("b.txt") or safe.value == value.replace("./", "")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/etc/passwd",
        "../escape",
        "a/../../escape",
        "a\\..\\escape",
        "work\\..\\..\\etc",
        "with\x00nul",
        "..",
        ".",
    ],
)
def test_invalid_paths(value):
    with pytest.raises(InvalidPathError):
        validate_relative_path(value)


def test_allow_dot():
    assert validate_relative_path(".", allow_dot=True).value == "."
    with pytest.raises(InvalidPathError):
        validate_relative_path(".", allow_dot=False)


def test_scope_key_is_canonical():
    assert scope_key("d", "s") == "d|s"
    assert scope_key("d", "s") == scope_key("d", "s")
    assert scope_key("d", "s") != scope_key("d", "other")
