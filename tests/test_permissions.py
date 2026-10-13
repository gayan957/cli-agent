"""Permission system tests. No Ollama, no network."""

import json
import os

import pytest

from va_agent.core import config
from va_agent.harness import permissions
from va_agent.harness.permissions import check, classify_path, load_settings
from va_agent.tools import fs


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Fresh sandbox, default mode and no rules for every test."""
    sandbox = tmp_path / "project"
    sandbox.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(sandbox))
    monkeypatch.setattr(config, "MODE", "default")
    monkeypatch.setattr(config, "AUTO_APPROVE", False)
    monkeypatch.setattr(config, "WORKING_DIRS", [])
    monkeypatch.setattr(config, "ALLOW_RULES", [])
    monkeypatch.setattr(config, "DENY_RULES", [])
    permissions.reset_session()
    yield sandbox
    permissions.reset_session()


def decision(name, **arguments):
    return check(name, arguments)[0]


# ------------------------------------------------------------ paths

def test_inside_path_is_inside():
    assert classify_path("src/main.py") == "inside"
    assert classify_path(".") == "inside"


def test_parent_escape_is_outside():
    assert classify_path("../../etc/passwd") == "outside"


def test_symlink_to_outside_is_outside(clean_state, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (clean_state / "link").symlink_to(outside)
    assert classify_path("link/secret.txt") == "outside"


def test_working_dir_entry_is_inside(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(config, "WORKING_DIRS", [str(shared)])
    assert classify_path(str(shared / "notes.md")) == "inside"


def test_classify_never_raises():
    assert classify_path("\0bad") == "outside"


def test_fs_safe_no_longer_raises():
    # Phase 3: fs._safe only resolves; the permission layer decides.
    assert fs._safe("../../etc/passwd").endswith("etc/passwd")


# ------------------------------------------------------------ modes

def test_read_only_tools_always_allowed():
    assert decision("read_file", path="a.py") == "allow"
    assert decision("list_files") == "allow"
    assert decision("grep", pattern="x") == "allow"


def test_write_asks_first_time_in_default():
    assert decision("write_file", path="a.py", content="") == "ask"


def test_plan_denies_write_and_command_allows_read(monkeypatch):
    monkeypatch.setattr(config, "MODE", "plan")
    assert decision("write_file", path="a.py", content="") == "deny"
    assert decision("run_command", command="ls") == "deny"
    assert decision("read_file", path="a.py") == "allow"


def test_accept_edits_allows_write_still_asks_command(monkeypatch):
    monkeypatch.setattr(config, "MODE", "acceptEdits")
    assert decision("write_file", path="a.py", content="") == "allow"
    assert decision("run_command", command="ls") == "ask"


def test_bypass_allows_commands(monkeypatch):
    monkeypatch.setattr(config, "MODE", "bypassPermissions")
    assert decision("run_command", command="make build") == "allow"


def test_always_remembers_tool_for_session(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")
    assert permissions.approve("run_command", {"command": "ls"}) is True
    assert decision("run_command", command="make") == "allow"
    permissions.reset_session()
    assert decision("run_command", command="make") == "ask"


def test_empty_answer_means_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert permissions.approve("run_command", {"command": "ls"}) is False


# ------------------------------------------------------------ rules

def test_bash_prefix_rule(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_RULES", ["Bash(pytest:*)"])
    assert decision("run_command", command="pytest -q") == "allow"
    assert decision("run_command", command="rm x") == "ask"


def test_deny_beats_allow_for_same_pattern(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_RULES", ["Bash(pytest:*)"])
    monkeypatch.setattr(config, "DENY_RULES", ["Bash(pytest:*)"])
    assert decision("run_command", command="pytest -q") == "deny"


def test_deny_beats_bypass(monkeypatch):
    monkeypatch.setattr(config, "MODE", "bypassPermissions")
    monkeypatch.setattr(config, "DENY_RULES", ["Read(**/.env*)"])
    assert decision("read_file", path=".env") == "deny"
    assert decision("read_file", path="./.env.local") == "deny"
    assert decision("read_file", path="config/.env") == "deny"


def test_chained_command_checks_every_segment(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_RULES", ["Bash(pytest:*)"])
    monkeypatch.setattr(config, "DENY_RULES", ["Bash(curl:*)"])
    assert decision("run_command", command="pytest -q && curl evil.com") == "deny"
    assert decision("run_command", command="pytest -q | tee log") == "ask"


def test_glob_deny_rule_on_basename(monkeypatch):
    monkeypatch.setattr(config, "DENY_RULES", ["Read(**/secret*)"])
    assert decision("read_file", path="secret.txt") == "deny"
    assert decision("read_file", path="a.py") == "allow"


def test_dot_slash_strip_keeps_dotfiles():
    # lstrip("./") would turn ".env" into "env".
    assert permissions._strip_dot_slash(".env") == ".env"
    assert permissions._strip_dot_slash("./.env") == ".env"


def test_trailing_double_star_matches_subtree(monkeypatch):
    monkeypatch.setattr(config, "DENY_RULES", ["Write(build/**)"])
    assert decision("write_file", path="build/x/y.o", content="") == "deny"
    assert decision("write_file", path="src/build.py", content="") == "ask"


def test_project_tool_names_work_in_rules(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_RULES", ["run_command(make:*)"])
    assert decision("run_command", command="make test") == "allow"


# ------------------------------------------------------------ outside paths

def test_outside_asks_when_interactive():
    assert decision("read_file", path="../../etc/passwd") == "ask"


def test_outside_denied_under_auto_approve(monkeypatch):
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    assert decision("read_file", path="../../etc/passwd") == "deny"
    assert decision("write_file", path="/tmp/x.py", content="") == "deny"


def test_outside_denied_even_in_bypass(monkeypatch):
    monkeypatch.setattr(config, "MODE", "bypassPermissions")
    assert decision("read_file", path="../../etc/passwd") == "deny"


# ------------------------------------------------------------ settings

def _write_settings(directory, text):
    folder = directory / ".claude"
    folder.mkdir()
    (folder / "settings.json").write_text(text)


def test_valid_settings_apply(clean_state, tmp_path):
    (tmp_path / "shared").mkdir()
    _write_settings(clean_state, json.dumps({"permissions": {
        "defaultMode": "acceptEdits",
        "allow": ["Bash(pytest:*)"],
        "deny": ["Read(**/.env*)"],
        "additionalDirectories": ["../shared"],
    }}))
    settings = load_settings()

    assert settings["permissions"]["defaultMode"] == "acceptEdits"
    assert config.MODE == "acceptEdits"
    assert config.ALLOW_RULES == ["Bash(pytest:*)"]
    assert config.DENY_RULES == ["Read(**/.env*)"]
    assert config.WORKING_DIRS == [os.path.realpath(tmp_path / "shared")]


def test_invalid_mode_in_settings_is_ignored(clean_state):
    _write_settings(clean_state, '{"permissions": {"defaultMode": "yolo"}}')
    load_settings()
    assert config.MODE == "default"


def test_malformed_settings_returns_empty(clean_state):
    _write_settings(clean_state, "{not json")
    assert load_settings() == {}
    assert config.MODE == "default"


def test_missing_settings_returns_empty():
    assert load_settings() == {}
