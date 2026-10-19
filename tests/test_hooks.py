"""Lifecycle hook tests. No Ollama, no network: hooks are small shell
scripts written to tmp_path."""

import json
import os
import time

import pytest

from va_agent.core import config
from va_agent.harness import hooks


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    """A fresh project dir, no hooks configured, hooks enabled."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(root))
    monkeypatch.setattr(config, "HOOKS", {})
    monkeypatch.setattr(config, "HOOKS_ENABLED", True)
    monkeypatch.setattr(config, "HOOK_TIMEOUT", 60)
    monkeypatch.setattr(config, "SESSION_ID", "test-session")
    hooks.reset_hooks()
    return root


def script(project, name, body):
    """An executable bash hook in the project; returns its path."""
    path = project / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def configure(event, *commands, matcher=None, timeout=None):
    """Add one matcher entry for *event* running *commands*."""
    config.HOOKS.setdefault(event, []).append(
        {"matcher": matcher,
         "hooks": [{"command": c, "timeout": timeout} for c in commands]})


BASH = {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}


# ------------------------------------------------------------------ protocol

def test_exit_0_stdout_becomes_context(project):
    configure("PreToolUse", script(project, "h.sh", "cat >/dev/null; echo hello"))
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision is None
    assert result.context == ["hello"]


def test_exit_2_on_pre_tool_use_denies_with_stderr(project):
    configure("PreToolUse",
              script(project, "h.sh", "cat >/dev/null; echo nope >&2; exit 2"))
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision == "deny"
    assert result.reason == "nope"


def test_exit_2_on_post_tool_use_is_feedback_not_deny(project):
    configure("PostToolUse",
              script(project, "h.sh", "cat >/dev/null; echo 'tests failed' >&2; exit 2"))
    result = hooks.fire("PostToolUse", {**BASH, "tool_response": "ok"})
    assert result.decision is None
    assert result.context == ["tests failed"]


def test_other_exit_code_is_logged_not_deny(project):
    command = script(project, "h.sh", "cat >/dev/null; echo broken >&2; exit 1")
    configure("PreToolUse", command)
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision is None
    assert len(result.context) == 1
    assert "exit 1" in result.context[0] and "broken" in result.context[0]


def json_hook(project, name, data):
    return script(project, name,
                  f"cat >/dev/null; echo '{json.dumps(data)}'")


def test_json_deny_inside_hook_specific_output(project):
    configure("PreToolUse", json_hook(project, "h.sh", {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "prod needs approval"}}))
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision == "deny"
    assert result.reason == "prod needs approval"


def test_top_level_permission_decision_is_ignored(project):
    configure("PreToolUse", json_hook(project, "h.sh", {
        "permissionDecision": "deny", "permissionDecisionReason": "x"}))
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision is None


def test_unparseable_stdout_is_plain_context(project):
    configure("PreToolUse", script(project, "h.sh", "cat >/dev/null; echo '{oops'"))
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision is None
    assert result.context == ["{oops"]


def test_additional_context_is_collected(project):
    configure("PreToolUse", json_hook(project, "h.sh", {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "additionalContext": "note this"}}))
    assert hooks.fire("PreToolUse", BASH).context == ["note this"]


def test_payload_fields_and_project_dir(project):
    dump = project / "payload.json"
    configure("PostToolUse", script(
        project, "h.sh", f'cat > "{dump}"; echo "$CLAUDE_PROJECT_DIR" > "{dump}.env"'))
    hooks.fire("PostToolUse", {**BASH, "tool_response": "exit_code=0"})

    payload = json.loads(dump.read_text())
    assert payload["session_id"] == "test-session"
    assert payload["hook_event_name"] == "PostToolUse"
    assert payload["tool_name"] == "Bash"
    assert payload["tool_input"] == {"command": "pytest -q"}
    assert payload["tool_response"] == "exit_code=0"
    assert payload["cwd"] == str(project.resolve())
    assert (project / "payload.json.env").read_text().strip() == str(project.resolve())


# ------------------------------------------------------------------ runner

def test_bash_matcher_fires_only_for_bash(project):
    configure("PreToolUse", script(project, "h.sh", "cat >/dev/null; echo hit"),
              matcher="Bash")
    assert hooks.fire("PreToolUse", BASH).context == ["hit"]
    assert hooks.fire("PreToolUse", {"tool_name": "Read",
                                     "tool_input": {}}).context == []


def test_regex_matcher_edit_or_write(project):
    configure("PostToolUse", script(project, "h.sh", "cat >/dev/null; echo hit"),
              matcher="Edit|Write")
    for tool in ("Edit", "Write"):
        assert hooks.fire("PostToolUse", {"tool_name": tool}).context == ["hit"]
    assert hooks.fire("PostToolUse", {"tool_name": "Read"}).context == []


def test_no_matcher_fires_for_everything(project):
    configure("PreToolUse", script(project, "h.sh", "cat >/dev/null; echo hit"))
    for tool in ("Bash", "Read", "TodoWrite"):
        assert hooks.fire("PreToolUse", {"tool_name": tool}).context == ["hit"]


def test_same_command_under_two_matchers_runs_once(project):
    counter = project / "count.txt"
    command = script(project, "h.sh", f'cat >/dev/null; echo x >> "{counter}"')
    configure("PreToolUse", command, matcher="Bash")
    configure("PreToolUse", command)
    hooks.fire("PreToolUse", BASH)
    assert counter.read_text().count("x") == 1


def test_hooks_run_in_parallel_and_all_context_is_kept(project):
    configure("PreToolUse",
              script(project, "a.sh", "cat >/dev/null; sleep 1; echo a"),
              script(project, "b.sh", "cat >/dev/null; sleep 1; echo b"),
              script(project, "c.sh", "cat >/dev/null; sleep 1; echo c"))
    started = time.time()
    result = hooks.fire("PreToolUse", BASH)
    assert time.time() - started < 2.5
    assert result.context == ["a", "b", "c"]


def test_timeout_kills_and_reports_without_hanging(project):
    # The hook starts a child that would outlive a kill of the shell alone.
    pid_file = project / "child.pid"
    command = script(project, "slow.sh",
                     f'sleep 10 & echo $! > "{pid_file}"; wait')
    configure("PreToolUse", command, timeout=1)
    started = time.time()
    result = hooks.fire("PreToolUse", BASH)
    assert time.time() - started < 5
    assert result.decision is None
    assert result.context == [f"[hook timed out after 1s] {command}"]

    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)                   # the child is gone too


def test_hooks_disabled_runs_nothing(project, monkeypatch):
    marker = project / "ran"
    configure("PreToolUse", script(project, "h.sh", f'touch "{marker}"; exit 2'))
    monkeypatch.setattr(config, "HOOKS_ENABLED", False)
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision is None and result.context == []
    assert not marker.exists()


def test_old_tool_name_is_matched_as_claude_code_name():
    payload = hooks.tool_payload("run_command", {"command": "ls"})
    assert payload == {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    payload = hooks.tool_payload("write_file", {"path": "a.py", "content": ""})
    assert payload["tool_name"] == "Write"
    assert payload["tool_input"]["file_path"] == "a.py"


# ------------------------------------------------------------------ precedence

def allow_hook(project, name):
    return json_hook(project, name, {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow"}})


def test_one_deny_among_allows_is_deny(project):
    configure("PreToolUse",
              allow_hook(project, "a.sh"),
              script(project, "d.sh", "cat >/dev/null; echo no >&2; exit 2"),
              allow_hook(project, "b.sh"))
    result = hooks.fire("PreToolUse", BASH)
    assert result.decision == "deny" and result.reason == "no"


def test_two_allows_is_allow(project):
    configure("PreToolUse", allow_hook(project, "a.sh"),
              allow_hook(project, "b.sh"))
    assert hooks.fire("PreToolUse", BASH).decision == "allow"


def test_last_non_deny_decision_applies(project):
    configure("PreToolUse", allow_hook(project, "a.sh"),
              json_hook(project, "q.sh", {"hookSpecificOutput": {
                  "hookEventName": "PreToolUse", "permissionDecision": "ask"}}))
    assert hooks.fire("PreToolUse", BASH).decision == "ask"


# ------------------------------------------------------------------ integration

@pytest.fixture
def scripted(monkeypatch):
    """Replace llm.chat with a script; returns the list of message lists
    the model was shown."""
    seen = []

    def install(replies, repeat_last=False):
        from va_agent.core import llm
        queue = list(replies)

        def chat(messages, tools=None):
            seen.append([dict(m) for m in messages])
            if len(queue) > 1 or not repeat_last:
                return queue.pop(0)
            return queue[0]

        monkeypatch.setattr(llm, "chat", chat)
        return seen
    return install


WRITE_CALL = {"content": "", "tool_calls": [{"name": "Write", "arguments": {
    "file_path": "out.py", "content": "x = 1\n"}}]}
DONE = {"content": "Done.", "tool_calls": []}


def test_pre_tool_use_deny_means_tool_never_runs(project, scripted, monkeypatch):
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    configure("PreToolUse",
              script(project, "h.sh", "cat >/dev/null; echo 'no writes' >&2; exit 2"),
              matcher="Write")
    seen = scripted([WRITE_CALL, DONE])
    from va_agent.core.agent import run
    stats = run("write", verbose=False)

    assert not (project / "out.py").exists()
    assert stats["tool_failures"] == 1
    assert any(m["role"] == "tool" and "no writes" in m["content"]
               for m in seen[-1])


def test_pre_tool_use_allow_bypasses_approve(project, scripted, monkeypatch):
    monkeypatch.setattr(config, "AUTO_APPROVE", False)
    monkeypatch.setattr(config, "MODE", "default")

    def must_not_be_called(name, arguments):
        raise AssertionError("approve() should have been bypassed")

    monkeypatch.setattr("va_agent.core.agent.approve", must_not_be_called)
    configure("PreToolUse", allow_hook(project, "a.sh"), matcher="Write")
    scripted([WRITE_CALL, DONE])
    from va_agent.core.agent import run
    run("write", verbose=False)
    assert (project / "out.py").read_text() == "x = 1\n"


def test_post_tool_use_feedback_reaches_the_model(project, scripted, monkeypatch):
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    configure("PostToolUse",
              script(project, "t.sh", "cat >/dev/null; echo '1 failed' >&2; exit 2"),
              matcher="Edit|Write")
    seen = scripted([WRITE_CALL, DONE])
    from va_agent.core.agent import run
    run("write", verbose=False)

    final = seen[-1]
    feedback = [i for i, m in enumerate(final)
                if m["content"] == "[hook: PostToolUse]\n\n1 failed"]
    assert len(feedback) == 1
    assert final[feedback[0]]["role"] == "user"
    assert final[feedback[0] - 1]["role"] == "tool"


def test_stop_deny_continues_then_gives_up_after_three(project, scripted):
    configure("Stop", script(project, "s.sh", "cat >/dev/null; echo 'run the tests' >&2; exit 2"))
    seen = scripted([DONE], repeat_last=True)
    from va_agent.core.agent import run
    stats = run("finish", verbose=False)

    assert len(seen) == 1 + hooks.MAX_STOP_CONTINUATIONS
    assert stats["finished"] is True
    assert stats["stop_hook_limit"] is True
    pushes = [m for m in seen[-1] if m["content"] == "[hook: Stop]\n\nrun the tests"]
    assert len(pushes) == hooks.MAX_STOP_CONTINUATIONS


def test_stop_counter_resets_each_run(project, scripted):
    configure("Stop", script(project, "s.sh", "cat >/dev/null; exit 2"))
    seen = scripted([DONE], repeat_last=True)
    from va_agent.core.agent import run
    run("one", verbose=False)
    run("two", verbose=False)
    assert len(seen) == 2 * (1 + hooks.MAX_STOP_CONTINUATIONS)


def test_session_start_context_goes_into_system_prompt(project, scripted):
    configure("SessionStart", script(project, "s.sh", "cat >/dev/null; echo 'Git branch: main'"))
    hooks.start_session()
    seen = scripted([DONE])
    from va_agent.core.agent import run
    try:
        run("hi", verbose=False)
    finally:
        hooks._session_context.clear()
    assert "[hook: SessionStart]\n\nGit branch: main" in seen[0][0]["content"]


def test_user_prompt_submit_deny_skips_the_model(project, monkeypatch):
    from va_agent import cli
    configure("UserPromptSubmit",
              script(project, "u.sh", "cat >/dev/null; echo 'not today' >&2; exit 2"))
    called = []
    monkeypatch.setattr(cli, "run", lambda *a, **k: called.append(a))
    cli._submit("do something")
    assert called == []


def test_user_prompt_submit_context_follows_the_task(project, monkeypatch):
    from va_agent import cli
    configure("UserPromptSubmit", script(project, "u.sh", "cat >/dev/null; echo 'ticket ABC-1'"))
    called = []
    monkeypatch.setattr(cli, "run",
                        lambda task, **kw: called.append((task, kw)) or
                        {"steps": 0, "tool_calls": 0, "tool_failures": 0,
                         "seconds": 0, "finished": True})
    cli._submit("fix it")
    task, kwargs = called[0]
    assert task == "fix it"
    assert kwargs["context"] == [{"role": "user",
                                  "content": "[hook: UserPromptSubmit]\n\nticket ABC-1"}]


# ------------------------------------------------------------------ config

def write_settings(project, data):
    folder = project / ".claude"
    folder.mkdir(exist_ok=True)
    (folder / "settings.json").write_text(
        data if isinstance(data, str) else json.dumps(data))


def test_project_dir_is_expanded_in_commands(project):
    script(project, "h.sh", "cat >/dev/null; echo expanded")
    write_settings(project, {"hooks": {"PreToolUse": [{"hooks": [
        {"type": "command", "command": "${CLAUDE_PROJECT_DIR}/h.sh"}]}]}})
    hooks.load_hooks()
    assert hooks.fire("PreToolUse", BASH).context == ["expanded"]


def test_load_hooks_populates_config(project):
    write_settings(project, {"hooks": {"PostToolUse": [{
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": "a.sh", "timeout": 5}]}]}})
    loaded = hooks.load_hooks()
    assert loaded is config.HOOKS
    assert config.HOOKS == {"PostToolUse": [{
        "matcher": "Edit|Write", "hooks": [{"command": "a.sh", "timeout": 5}]}]}


def test_malformed_hook_config_is_skipped(project):
    write_settings(project, {"hooks": {
        "PreToolUse": [
            "not an object",
            {"matcher": "([", "hooks": [{"command": "x.sh"}]},
            {"hooks": [{"type": "prompt", "prompt": "p"}, {"command": ""},
                       {"command": "ok.sh", "timeout": "soon"}]},
        ],
        "NotAnEvent": [],
        "Stop": "nope",
    }})
    loaded = hooks.load_hooks()
    assert loaded == {"PreToolUse": [{"matcher": None,
                                      "hooks": [{"command": "ok.sh",
                                                 "timeout": None}]}]}


def test_malformed_settings_json_does_not_raise(project):
    write_settings(project, "{not json")
    assert hooks.load_hooks() == {}


def test_missing_settings_gives_no_hooks():
    assert hooks.load_hooks() == {}
