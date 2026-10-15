"""Unit tests for the pure sandbox toolkit modules (diff engine + classifiers).

Run with:  uv run --locked --extra test pytest tests/unit/test_sandbox_toolkit.py
"""

from __future__ import annotations

import shlex

import pytest

from app.services.sandbox_authz import (
    classify_destructive_git,
    classify_destructive_shell,
)
from app.services.sandbox_diff import (
    DiffApplyError,
    DiffParseError,
    apply_hunks,
    parse_unified_diff,
)


class TestParseUnifiedDiff:
    def test_parse_simple_modify(self) -> None:
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+LINE2\n"
            " line3\n"
        )
        files = parse_unified_diff(patch)
        assert len(files) == 1
        file = files[0]
        assert file.old_path == "work/a.py"
        assert file.new_path == "work/a.py"
        assert not file.is_create and not file.is_delete
        assert len(file.hunks) == 1
        hunk = file.hunks[0]
        assert hunk.old_start == 1 and hunk.old_count == 3
        assert hunk.new_count == 3
        assert hunk.lines == (" line1", "-line2", "+LINE2", " line3")

    def test_parse_git_diff_with_metadata(self) -> None:
        patch = (
            "diff --git a/work/x.txt b/work/x.txt\n"
            "index 1111111..2222222 100644\n"
            "--- a/work/x.txt\n"
            "+++ b/work/x.txt\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        files = parse_unified_diff(patch)
        assert len(files) == 1
        assert files[0].old_path == "work/x.txt"

    def test_parse_create_and_delete(self) -> None:
        create = (
            "diff --git a/work/new.py b/work/new.py\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "--- /dev/null\n"
            "+++ b/work/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def hello():\n"
            "+    return 42\n"
        )
        files = parse_unified_diff(create)
        assert len(files) == 1
        assert files[0].is_create
        delete = (
            "diff --git a/work/old.py b/work/old.py\n"
            "deleted file mode 100644\n"
            "--- a/work/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-print(1)\n"
            "-print(2)\n"
        )
        files = parse_unified_diff(delete)
        assert len(files) == 1
        assert files[0].is_delete

    def test_parse_hunk_count_mismatch_raises(self) -> None:
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-a\n"
            "+b\n"
            "-c\n"
        )
        with pytest.raises(DiffParseError):
            parse_unified_diff(patch)


class TestApplyHunks:
    def test_apply_modify(self) -> None:
        content = "line1\nline2\nline3\n"
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+LINE2\n"
            " line3\n"
        )
        file = parse_unified_diff(patch)[0]
        updated = apply_hunks(content, file.hunks)
        assert updated == "line1\nLINE2\nline3\n"

    def test_apply_insert_hunk(self) -> None:
        # git convention: "@@ -K,0 +M,N @@" inserts the N lines BEFORE old
        # line K (0-based index K-1).
        content = "a\nb\nc\n"
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -2,0 +3,1 @@\n"
            "+inserted\n"
        )
        file = parse_unified_diff(patch)[0]
        updated = apply_hunks(content, file.hunks)
        assert updated == "a\ninserted\nb\nc\n"

    def test_fuzzy_context_shift(self) -> None:
        # The hunk declares line 1 but the actual position is line 4; the
        # fuzz scan must still find the unique context match.
        content = "other\nother2\nother3\ntarget_a\ntarget_b\ntarget_c\n"
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -1,3 +1,3 @@\n"
            " target_a\n"
            "-target_b\n"
            "+TARGET_B\n"
            " target_c\n"
        )
        file = parse_unified_diff(patch)[0]
        updated = apply_hunks(content, file.hunks)
        assert updated == "other\nother2\nother3\ntarget_a\nTARGET_B\ntarget_c\n"

    def test_apply_no_match_raises(self) -> None:
        content = "x\ny\nz\n"
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-missing\n"
            "+added\n"
        )
        file = parse_unified_diff(patch)[0]
        with pytest.raises(DiffApplyError):
            apply_hunks(content, file.hunks, fuzz=0)

    def test_apply_multi_hunk(self) -> None:
        content = "h1\na\nh2\nb\n"
        patch = (
            "--- a/work/a.py\n"
            "+++ b/work/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-h1\n"
            "+H1\n"
            " a\n"
            "@@ -3,2 +3,2 @@\n"
            "-h2\n"
            "+H2\n"
            " b\n"
        )
        file = parse_unified_diff(patch)[0]
        updated = apply_hunks(content, file.hunks)
        assert updated == "H1\na\nH2\nb\n"


class TestClassifyDestructiveShell:
    def test_rm_work_path_requires_auth(self) -> None:
        intent = classify_destructive_shell("rm -rf work/tmp.txt")
        assert intent is not None
        assert intent["hard_blocked"] is False
        assert intent["paths"] == ["work/tmp.txt"]

    def test_rm_absolute_path_hard_blocked(self) -> None:
        intent = classify_destructive_shell("rm -rf /etc/passwd")
        assert intent is not None
        assert intent["hard_blocked"] is True

    def test_rm_non_work_path_hard_blocked(self) -> None:
        intent = classify_destructive_shell("rm -rf inputs/x.png")
        assert intent is not None
        assert intent["hard_blocked"] is True

    def test_rm_without_path_hard_blocked(self) -> None:
        intent = classify_destructive_shell("rm -rf")
        assert intent is not None
        assert intent["hard_blocked"] is True

    def test_safe_command_no_intent(self) -> None:
        assert classify_destructive_shell("ls -la && python work/main.py") is None
        assert classify_destructive_shell("echo hello > work/out.txt") is None

    def test_pipeline_rm_detected(self) -> None:
        intent = classify_destructive_shell("cat work/a.txt | rm work/b.txt")
        assert intent is not None
        assert intent["paths"] == ["work/b.txt"]

    def test_quote_break_returns_none(self) -> None:
        assert classify_destructive_shell("echo 'unclosed") is None


class TestClassifyDestructiveGit:
    def test_git_rm_work_path_requires_auth(self) -> None:
        intent = classify_destructive_git(("git", "rm", "work/a.txt"))
        assert intent is not None
        assert intent["hard_blocked"] is False
        assert intent["paths"] == ["work/a.txt"]

    def test_git_status_no_intent(self) -> None:
        assert classify_destructive_git(("git", "status")) is None
        assert classify_destructive_git(("git", "log", "--oneline")) is None

    def test_git_clean_hard_blocked(self) -> None:
        intent = classify_destructive_git(("git", "clean", "-fd"))
        assert intent is not None
        assert intent["hard_blocked"] is True

    def test_git_reset_no_path_hard_blocked(self) -> None:
        intent = classify_destructive_git(("git", "reset", "--hard"))
        assert intent is not None
        assert intent["hard_blocked"] is True

    def test_git_checkout_path_requires_auth(self) -> None:
        intent = classify_destructive_git(("git", "checkout", "--", "work/a.txt"))
        assert intent is not None
        assert intent["hard_blocked"] is False


class TestShellShlexParity:
    def test_shlex_roundtrip(self) -> None:
        tokens = shlex.split("bash -lc 'echo hi && rm work/x.txt'")
        assert tokens == ["bash", "-lc", "echo hi && rm work/x.txt"]
