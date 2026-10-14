"""Tests for the Claude Code tool set. No Ollama, no network."""

import os
import time

import pytest

from va_agent.core import config
from va_agent.harness import permissions, snapshots
from va_agent.tools import fs, registry, shell, todo


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Fresh project folder and session state for every test."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(project))
    monkeypatch.setattr(config, "READ_FILES", set())
    monkeypatch.setattr(config, "EDIT_MODE", "write")
    monkeypatch.setattr(config, "TODOS", True)
    monkeypatch.setattr(config, "SESSION_ID", "")
    monkeypatch.setattr(config, "MODE", "default")
    monkeypatch.setattr(config, "AUTO_APPROVE", False)
    monkeypatch.setattr(config, "ALLOW_RULES", [])
    monkeypatch.setattr(config, "DENY_RULES", [])
    monkeypatch.setattr(config, "WORKING_DIRS", [])
    todo.reset()
    yield project
    shell.reset()
    todo.reset()


def make(project, name, text):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ------------------------------------------------------------------ Read

def test_read_is_line_numbered(sandbox):
    make(sandbox, "a.py", "def add(a, b):\n    return a + b\n")
    assert fs.Read("a.py").splitlines() == [
        "     1\tdef add(a, b):",
        "     2\t    return a + b",
    ]


def test_read_offset_and_limit_page(sandbox):
    make(sandbox, "a.txt", "\n".join(f"line{i}" for i in range(1, 11)))
    out = fs.Read("a.txt", offset=4, limit=3).splitlines()
    assert out[:3] == ["     4\tline4", "     5\tline5", "     6\tline6"]
    assert out[3] == "...[TRUNCATED at line 6 of 10]"


def test_read_truncates_past_cap(sandbox):
    make(sandbox, "big.txt", "\n".join("x" for _ in range(2500)))
    out = fs.Read("big.txt").splitlines()
    assert len(out) == 2001
    assert out[-1] == "...[TRUNCATED at line 2000 of 2500]"


def test_read_cuts_long_lines(sandbox):
    make(sandbox, "wide.txt", "y" * 5000)
    assert len(fs.Read("wide.txt").split("\t", 1)[1]) == 2000


def test_read_populates_read_files(sandbox):
    make(sandbox, "a.py", "x = 1\n")
    fs.Read("./a.py")
    assert os.path.realpath(sandbox / "a.py") in config.READ_FILES


def test_read_missing_file_raises_clearly():
    with pytest.raises(FileNotFoundError, match="File not found: nope.py"):
        fs.Read("nope.py")


# ------------------------------------------------------------------ Edit

def read_then(sandbox, name, text):
    make(sandbox, name, text)
    fs.Read(name)


def test_edit_unique_match_replaces(sandbox):
    read_then(sandbox, "a.py", "x = 1\ny = 2\n")
    result = fs.Edit("a.py", "y = 2", "y = 3")
    assert (sandbox / "a.py").read_text() == "x = 1\ny = 3\n"
    assert "1 occurrence" in result and "line 2" in result


def test_edit_absent_old_string_raises(sandbox):
    read_then(sandbox, "a.py", "x = 1\n")
    with pytest.raises(ValueError, match="old_string not found in file"):
        fs.Edit("a.py", "z = 9", "z = 0")


def test_edit_ambiguous_match_states_count(sandbox):
    read_then(sandbox, "a.py", "v = 1\nv = 1\nv = 1\n")
    with pytest.raises(ValueError, match="3 times") as error:
        fs.Edit("a.py", "v = 1", "v = 2")
    assert "replace_all" in str(error.value)
    assert "surrounding lines" in str(error.value)
    assert (sandbox / "a.py").read_text() == "v = 1\nv = 1\nv = 1\n"


def test_edit_replace_all_replaces_every_occurrence(sandbox):
    read_then(sandbox, "a.py", "v = 1\nw = 0\nv = 1\n")
    result = fs.Edit("a.py", "v = 1", "v = 2", replace_all=True)
    assert (sandbox / "a.py").read_text() == "v = 2\nw = 0\nv = 2\n"
    assert "lines 1, 3" in result


def test_edit_identical_strings_raise(sandbox):
    read_then(sandbox, "a.py", "x = 1\n")
    with pytest.raises(ValueError, match="identical"):
        fs.Edit("a.py", "x = 1", "x = 1")


def test_edit_unread_file_raises(sandbox):
    make(sandbox, "a.py", "x = 1\n")
    with pytest.raises(ValueError, match="must Read this file"):
        fs.Edit("a.py", "x = 1", "x = 2")


def test_write_creates_parent_folders(sandbox):
    assert "11 characters" in fs.Write("pkg/new.py", "print('hi')")
    assert (sandbox / "pkg" / "new.py").read_text() == "print('hi')"


# ------------------------------------------------------------------ Bash

def test_bash_cd_persists(sandbox):
    (sandbox / "sub").mkdir()
    shell.Bash("cd sub")
    out = shell.Bash("pwd")
    assert out.startswith("exit_code=0")
    assert out.strip().endswith(os.sep + "sub")


def test_bash_export_persists():
    shell.Bash("export VA_TEST_VALUE=42")
    assert "42" in shell.Bash("echo $VA_TEST_VALUE")


def test_bash_failure_returns_nonzero():
    assert shell.Bash("false").startswith("exit_code=1")
    assert shell.Bash("ls /definitely/not/here").startswith("exit_code=")
    assert not shell.Bash("ls /definitely/not/here").startswith("exit_code=0")


def test_bash_timeout_kills_and_restarts():
    shell.Bash("export LOST=yes")
    started = time.time()
    out = shell.Bash("sleep 30", timeout=1)
    assert time.time() - started < 10
    assert "TIMEOUT" in out and "restarted" in out
    after = shell.Bash("echo still-here; echo ${LOST:-gone}")
    assert after.startswith("exit_code=0")
    assert "still-here" in after and "gone" in after


def test_bash_stdin_command_does_not_hang():
    assert shell.Bash("cat", timeout=5).startswith("exit_code=0")


# ------------------------------------------------------------------ Glob / Grep

def test_glob_matches_nested(sandbox):
    for name in ["a.py", "pkg/b.py", "pkg/deep/c.py", "pkg/readme.md"]:
        make(sandbox, name, "")
    found = set(fs.Glob("**/*.py").splitlines())
    assert found == {"a.py", "pkg/b.py", "pkg/deep/c.py"}


def test_glob_newest_first(sandbox):
    old = make(sandbox, "old.py", "")
    new = make(sandbox, "new.py", "")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    assert fs.Glob("*.py").splitlines() == ["new.py", "old.py"]


def test_glob_and_grep_skip_directories(sandbox):
    make(sandbox, "keep.py", "needle\n")
    for skipped in [".git", "__pycache__", "node_modules", ".venv", ".va"]:
        make(sandbox, f"{skipped}/x.py", "needle\n")
    assert fs.Glob("**/*.py").splitlines() == ["keep.py"]
    assert fs.Grep("needle", output_mode="files_with_matches") == "keep.py"


def test_grep_glob_filters_files(sandbox):
    make(sandbox, "a.py", "target\n")
    make(sandbox, "pkg/b.txt", "target\n")
    out = fs.Grep("target", glob="*.txt")
    assert out == "pkg/b.txt:1: target"


def test_grep_files_with_matches_returns_paths_only(sandbox):
    make(sandbox, "a.py", "hit\nhit\n")
    make(sandbox, "b.py", "miss\n")
    assert fs.Grep("hit", output_mode="files_with_matches") == "a.py"


# ------------------------------------------------------------------ Registry

def names(schemas):
    return {schema["function"]["name"] for schema in schemas}


def test_edit_mode_write(monkeypatch):
    monkeypatch.setattr(config, "EDIT_MODE", "write")
    assert "Write" in names(registry.build_schemas())
    assert "Edit" not in names(registry.build_schemas())
    assert "Edit" not in registry.build_registry()


def test_edit_mode_edit(monkeypatch):
    monkeypatch.setattr(config, "EDIT_MODE", "edit")
    assert "Edit" in names(registry.build_schemas())
    assert "Write" not in names(registry.build_schemas())
    built = registry.build_registry()
    assert "Write" not in built and "write_file" not in built


def test_edit_mode_both(monkeypatch):
    monkeypatch.setattr(config, "EDIT_MODE", "both")
    assert {"Write", "Edit"} <= names(registry.build_schemas())


def test_todos_off_removes_todowrite(monkeypatch):
    monkeypatch.setattr(config, "TODOS", False)
    assert "TodoWrite" not in names(registry.build_schemas())
    assert "TodoWrite" not in registry.build_registry()


def test_bad_edit_mode_raises(monkeypatch):
    monkeypatch.setattr(config, "EDIT_MODE", "patch")
    with pytest.raises(ValueError, match="VA_EDIT_MODE"):
        registry.build_schemas()


def test_old_names_and_path_argument_still_work(sandbox):
    make(sandbox, "a.py", "x = 1\n")
    tools = registry.build_registry()
    assert "x = 1" in tools["read_file"](path="a.py")
    tools["write_file"](path="b.py", content="y")
    assert (sandbox / "b.py").read_text() == "y"
    assert "a.py" in tools["list_files"]()
    assert tools["run_command"](command="true").startswith("exit_code=0")


# ------------------------------------------------------------------ TodoWrite

def test_todowrite_stores_and_renders():
    out = todo.TodoWrite([
        {"content": "read", "status": "completed"},
        {"content": "edit", "status": "in_progress"},
        {"content": "test", "status": "pending"},
    ])
    assert "☒ read" in out and "▶ edit" in out and "☐ test" in out
    assert [t["status"] for t in todo.current()] == [
        "completed", "in_progress", "pending"]


def test_todowrite_rejects_bad_status():
    with pytest.raises(ValueError, match="status"):
        todo.TodoWrite([{"content": "x", "status": "done"}])


# ------------------------------------------------------------------ Snapshots

def test_snapshot_holds_pre_edit_content(sandbox):
    snapshots.new_session()
    snapshots.next_step()
    read_then(sandbox, "a.py", "before\n")
    fs.Edit("a.py", "before", "after")

    copy = (sandbox / ".va" / "snapshots" / config.SESSION_ID / "0001" / "a.py")
    assert copy.read_text() == "before\n"
    assert (sandbox / "a.py").read_text() == "after\n"


def test_rewind_restores_and_removes_created_files(sandbox):
    snapshots.new_session()
    make(sandbox, "a.py", "v0\n")
    snapshots.next_step()
    fs.Write("a.py", "v1\n")
    snapshots.next_step()
    fs.Write("a.py", "v2\n")
    fs.Write("new.py", "x")

    snapshots.rewind(2)
    assert (sandbox / "a.py").read_text() == "v1\n"
    assert not (sandbox / "new.py").exists()
    snapshots.rewind(1)
    assert (sandbox / "a.py").read_text() == "v0\n"


# ------------------------------------------------------------------ Permissions

def test_edit_is_denied_in_plan_mode(monkeypatch):
    monkeypatch.setattr(config, "MODE", "plan")
    args = {"file_path": "a.py", "old_string": "x", "new_string": "y"}
    assert permissions.check("Edit", args)[0] == "deny"
    assert permissions.check("Bash", {"command": "ls"})[0] == "deny"
    assert permissions.check("Read", {"file_path": "a.py"})[0] == "allow"


def test_edit_allowed_like_write_in_accept_edits(monkeypatch):
    monkeypatch.setattr(config, "MODE", "acceptEdits")
    args = {"file_path": "a.py", "old_string": "x", "new_string": "y"}
    assert permissions.check("Edit", args)[0] == "allow"
    assert permissions.check("Bash", {"command": "ls"})[0] == "ask"


def test_rules_match_file_path_argument(monkeypatch):
    monkeypatch.setattr(config, "MODE", "bypassPermissions")
    monkeypatch.setattr(config, "DENY_RULES", ["Read(**/.env*)", "Edit(*.lock)"])
    assert permissions.check("Read", {"file_path": ".env"})[0] == "deny"
    assert permissions.check("Write", {"file_path": "poetry.lock",
                                       "content": ""})[0] == "deny"


def test_outside_file_path_denied_unattended(monkeypatch):
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    assert permissions.check("Read", {"file_path": "../../etc/passwd"})[0] == "deny"
