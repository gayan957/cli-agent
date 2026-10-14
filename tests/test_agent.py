"""Agent loop tests using a scripted fake model."""

import os
import shutil

import pytest

from va_agent.core import config


@pytest.fixture
def workdir(tmp_path):
    """A clean sandbox for one test."""
    config.SANDBOX = str(tmp_path)
    config.AUTO_APPROVE = True
    (tmp_path / "main.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


def fake_model(script):
    """Replace llm.chat with a scripted sequence of replies."""
    from va_agent.core import llm
    step = iter(script)
    llm.chat = lambda messages, tools=None: next(step)


def test_agent_completes_a_task(workdir):
    fake_model([
        {"content": "", "tool_calls": [
            {"name": "read_file", "arguments": {"path": "main.py"}}]},
        {"content": "", "tool_calls": [
            {"name": "write_file", "arguments": {
                "path": "main.py",
                "content": '"""Math."""\n\n\ndef add(a, b):\n    return a + b\n'}}]},
        {"content": "Done.", "tool_calls": []},
    ])
    from va_agent.core.agent import run
    stats = run("add a docstring", verbose=False)

    assert stats["finished"] is True
    assert "Math" in (workdir / "main.py").read_text()


def test_sandbox_escape_is_blocked(workdir):
    fake_model([
        {"content": "", "tool_calls": [
            {"name": "read_file", "arguments": {"path": "../../etc/passwd"}}]},
        {"content": "Blocked.", "tool_calls": []},
    ])
    from va_agent.core.agent import run
    stats = run("read passwd", verbose=False)

    # Phase 3: an outside path is denied by the permission layer before the
    # tool runs (AUTO_APPROVE is on), so it is not a tool failure any more.
    assert stats["tool_failures"] == 0


def test_unknown_tool_is_counted(workdir):
    fake_model([
        {"content": "", "tool_calls": [{"name": "nope", "arguments": {}}]},
        {"content": "Done.", "tool_calls": []},
    ])
    from va_agent.core.agent import run
    assert run("x", verbose=False)["tool_failures"] == 1


def test_step_limit(workdir):
    from va_agent.core import llm
    llm.chat = lambda messages, tools=None: {
        "content": "", "tool_calls": [{"name": "list_files", "arguments": {}}]}

    from va_agent.core.agent import run
    stats = run("loop", verbose=False)

    assert stats["finished"] is False
    assert stats["steps"] == config.MAX_STEPS

def test_history_uses_ollama_tool_call_shape(workdir):
    """Ollama rejects history unless tool calls are {"function": {...}}."""
    seen = []
    script = iter([
        {"content": "", "tool_calls": [
            {"name": "list_files", "arguments": {}}]},
        {"content": "Done.", "tool_calls": []},
    ])

    from va_agent.core import llm
    def chat(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return next(script)
    llm.chat = chat

    from va_agent.core.agent import run
    run("x", verbose=False)

    from ollama import Message
    for message in seen[-1]:
        Message(**message)          # raises if the shape is wrong


def test_edit_stats_are_recorded(workdir, monkeypatch):
    """The Phase 4 experiment columns: edit attempts, failures, tokens."""
    monkeypatch.setattr(config, "EDIT_MODE", "edit")
    monkeypatch.setattr(config, "READ_FILES", set())
    fake_model([
        # Edit before Read: fails read-before-edit.
        {"content": "", "tool_calls": [{"name": "Edit", "arguments": {
            "file_path": "main.py", "old_string": "a + b",
            "new_string": "b + a"}}]},
        {"content": "", "tool_calls": [{"name": "Read", "arguments": {
            "file_path": "main.py"}}]},
        {"content": "", "tool_calls": [{"name": "Edit", "arguments": {
            "file_path": "main.py", "old_string": "a + b",
            "new_string": "b + a"}}]},
        # Write is not offered in edit mode, so it is an unknown tool.
        {"content": "", "tool_calls": [{"name": "Write", "arguments": {
            "file_path": "x.py", "content": ""}}]},
        {"content": "Done.", "tool_calls": []},
    ])
    from va_agent.core.agent import run
    stats = run("swap", verbose=False)

    assert stats["edit_attempts"] == 2
    assert stats["edit_failures"] == 1
    assert stats["peak_tokens"] > 0
    assert "b + a" in (workdir / "main.py").read_text()
    assert not (workdir / "x.py").exists()
