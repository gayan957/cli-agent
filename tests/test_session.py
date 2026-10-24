"""Session persistence and command registry tests. No Ollama."""

import json
import os

import pytest

from va_agent import cli, commands
from va_agent.core import config, context, llm, session
from va_agent.harness import snapshots
from va_agent.tools import todo


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    """A fresh project and a fresh session for every test."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(root))
    monkeypatch.setattr(config, "MODEL", "qwen-coder-32k")
    monkeypatch.setattr(config, "MODE", "default")
    monkeypatch.setattr(config, "HOOKS", {})
    monkeypatch.setattr(config, "RULES_INJECTED", set())
    session.reset_session_state()
    todo.reset()
    context.forget()
    yield root
    todo.reset()
    context.forget()


CONVERSATION = [
    {"role": "system", "content": "SYSTEM"},
    {"role": "user", "content": "add an update method to NoteStore"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "Read", "arguments": {"file_path": "a.py"}}}]},
    {"role": "tool", "content": "x = 1"},
]


# ------------------------------------------------------------ save / load

def test_save_writes_json_at_the_expected_path(project):
    path = session.save(CONVERSATION)
    assert path == str(project / ".va" / "sessions" / f"{config.SESSION_ID}.json")
    data = json.loads(open(path).read())
    assert data["session_id"] == config.SESSION_ID
    assert data["cwd"] == os.path.realpath(project)
    assert data["model"] == "qwen-coder-32k" and data["mode"] == "default"
    assert {"created", "updated", "tokens", "seconds"} <= set(data)


def test_load_round_trips_messages_and_todos():
    todos = [{"content": "write test", "status": "in_progress"}]
    session.save(CONVERSATION, todos=todos)
    data = session.load(config.SESSION_ID)
    assert data["messages"] == CONVERSATION
    assert data["todos"] == todos


def test_list_sessions_newest_first():
    ids = []
    for stamp in (3000.0, 1000.0, 2000.0):          # saved out of order
        path = session.save(CONVERSATION)
        data = json.loads(open(path).read())
        data["updated"] = stamp
        open(path, "w").write(json.dumps(data))
        ids.append((stamp, config.SESSION_ID))
        snapshots.new_session()
    listed = [item["session_id"] for item in session.list_sessions()]
    assert listed == [sid for _, sid in sorted(ids, reverse=True)]
    assert session.list_sessions()[0]["message_count"] == len(CONVERSATION)


def test_most_recent_and_empty(monkeypatch):
    assert session.most_recent() is None
    session.save(CONVERSATION)
    assert session.most_recent()["session_id"] == config.SESSION_ID


def test_corrupt_session_is_skipped_without_raising(project, capsys):
    session.save(CONVERSATION)
    bad = project / ".va" / "sessions" / "20260101-000000-abcdef.json"
    bad.write_text("{not json")
    listed = session.list_sessions()
    assert [item["session_id"] for item in listed] == [config.SESSION_ID]
    assert session.load("20260101-000000-abcdef") is None
    assert "skipping" in capsys.readouterr().out


def test_save_is_atomic(project, monkeypatch):
    path = session.save(CONVERSATION)
    good = open(path).read()

    def half_write(data, handle, **kwargs):
        handle.write('{"session_id": "trunc')
        raise OSError("disk full")

    monkeypatch.setattr(session.json, "dump", half_write)
    with pytest.raises(OSError):
        session.save(CONVERSATION + [{"role": "user", "content": "more"}])
    assert open(path).read() == good
    leftovers = [n for n in os.listdir(os.path.dirname(path)) if n.startswith(".tmp")]
    assert leftovers == []


def test_first_task_is_first_real_user_message_truncated():
    messages = [{"role": "system", "content": "S"},
                {"role": "user", "content": "[project memory]\n\nnot a task"},
                {"role": "user", "content": "refactor " + "the parser " * 20}]
    session.save(messages)
    label = session.load(config.SESSION_ID)["first_task"]
    assert label.startswith("refactor the parser")
    assert len(label) == 80


def test_saved_messages_are_the_capped_ones():
    huge = {"role": "tool", "content": "y" * 200_000}
    session.save([*CONVERSATION, huge])
    stored = session.load(config.SESSION_ID)["messages"][-1]["content"]
    assert len(stored) < 8100
    assert "...[truncated," in stored


# ------------------------------------------------------------ resume

def test_restore_brings_back_model_mode_todos_and_id(monkeypatch):
    config.MODEL, config.MODE = "qwen2.5-coder:7b", "acceptEdits"
    session.save(CONVERSATION, todos=[{"content": "t", "status": "pending"}])
    saved_id = config.SESSION_ID

    session.reset_session_state()
    config.MODEL, config.MODE = "other", "default"
    todo.reset()
    messages = session.restore(session.load(saved_id))

    assert messages == CONVERSATION
    assert config.SESSION_ID == saved_id
    assert (config.MODEL, config.MODE) == ("qwen2.5-coder:7b", "acceptEdits")
    assert todo.current() == [{"content": "t", "status": "pending"}]


def test_resumed_snapshots_continue_numbering(project):
    from va_agent.tools import fs
    (project / "a.py").write_text("v0\n")
    snapshots.next_step()
    fs.Write("a.py", "v1\n")
    saved_id = config.SESSION_ID
    session.save(CONVERSATION)

    session.reset_session_state()
    session.restore(session.load(saved_id))
    assert snapshots.next_step() == 2


def test_continue_flag_resumes_conversation(project, monkeypatch):
    seen = []

    def chat(messages, tools=None):
        seen.append([m["content"] for m in messages if m["role"] == "user"])
        return {"content": "ok", "tool_calls": []}

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    cli._submit("first task")
    cli._submit("second task")
    assert seen[-1] == ["first task", "second task"]

    context.forget()
    session.reset_session_state()
    cli._start_resumed("continue")
    cli._submit("third task")
    assert seen[-1] == ["first task", "second task", "third task"]


def test_continue_without_sessions_starts_fresh(capsys):
    cli._start_resumed("continue")
    assert "starting fresh" in capsys.readouterr().out
    assert context.last_messages == []


def test_flags_parse():
    argv, mode, resume = cli._consume_flags(["--continue", "fix", "it"])
    assert (argv, resume) == (["fix", "it"], "continue")
    _, _, resume = cli._consume_flags(["--resume"])
    assert resume == "pick"
    argv, _, resume = cli._consume_flags(["--resume", "20261024-101010-abcdef", "go"])
    assert (argv, resume) == (["go"], "20261024-101010-abcdef")


# ------------------------------------------------------------ commands

def test_dispatch_commands_and_tasks():
    assert commands.dispatch("/help") is True
    assert commands.dispatch("add a docstring") is False


def test_help_lists_every_registered_command():
    text = commands.help_text()
    for name in commands.COMMANDS:
        assert f"/{name}" in text
    expected = {"help", "mode", "rules", "hooks", "skills", "context", "compact",
                "clear", "memory", "init", "rewind", "model", "cost", "resume",
                "worktree"}
    assert expected <= set(commands.COMMANDS)


def test_unknown_command_suggests_nearest(capsys):
    assert commands.dispatch("/hepl") is True
    assert "Did you mean /help?" in capsys.readouterr().out
    commands.dispatch("/wor")
    assert "Did you mean /worktree?" in capsys.readouterr().out


def test_clear_calls_every_reset(monkeypatch):
    from va_agent.harness import hooks, permissions, rules, skills
    called = []
    for module, name in [(permissions, "reset_session"), (rules, "reset_rules"),
                         (hooks, "reset_hooks"), (skills, "reset_skills"),
                         (context, "reset_context"),
                         (session, "reset_session_state")]:
        monkeypatch.setattr(module, name,
                            lambda name=name: called.append(name))
    commands.dispatch("/clear")
    assert sorted(called) == sorted(["reset_session", "reset_rules", "reset_hooks",
                                     "reset_skills", "reset_context",
                                     "reset_session_state"])


def test_cost_reports_time_tokens_and_turns(capsys, monkeypatch):
    monkeypatch.setattr(config, "SESSION_SECONDS", 484.0)
    monkeypatch.setattr(config, "SESSION_TOKENS", 11240)
    monkeypatch.setattr(config, "SESSION_PEAK", 9812)
    monkeypatch.setattr(config, "SESSION_TURNS", 17)
    commands.dispatch("/cost")
    out = capsys.readouterr().out
    assert "model time     8m 04s" in out
    assert "11,240 (peak 9,812 in one turn)" in out
    assert "turns          17" in out


def test_model_without_ollama_prints_current(monkeypatch, capsys):
    def down():
        raise ConnectionError("no ollama")
    monkeypatch.setattr(commands, "ollama_models", down)
    commands.dispatch("/model")
    out = capsys.readouterr().out
    assert "qwen-coder-32k" in out and "could not list" in out


def test_agent_accumulates_session_cost(monkeypatch):
    monkeypatch.setattr(llm, "chat",
                        lambda messages, tools=None: {"content": "ok",
                                                      "tool_calls": []})
    from va_agent.core.agent import run
    run("one", verbose=False)
    run("two", verbose=False)
    assert config.SESSION_TURNS == 2
    assert config.SESSION_TOKENS > 0
    assert config.SESSION_PEAK > 0
