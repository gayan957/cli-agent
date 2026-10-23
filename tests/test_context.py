"""Context management tests. No Ollama: llm.chat is patched for both the
agent and the compaction summary."""

import os

import pytest

from va_agent.core import config, context, llm
from va_agent.harness import hooks, snapshots
from va_agent.tools import fs


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    """A fresh project and default context settings for every test."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(root))
    monkeypatch.setattr(config, "NUM_CTX", 32768)
    monkeypatch.setattr(config, "COMPACT_THRESHOLD", 0.75)
    monkeypatch.setattr(config, "COMPACT_ENABLED", True)
    monkeypatch.setattr(config, "KEEP_RECENT", 4)
    monkeypatch.setattr(config, "TOOL_RESULT_CAP", 8000)
    monkeypatch.setattr(config, "MAX_MESSAGE_SHARE", 0.25)
    monkeypatch.setattr(config, "CLAUDE_MD", "")
    monkeypatch.setattr(config, "COMPACT_ATTEMPTS", 0)
    monkeypatch.setattr(config, "PEAK_TOKENS", 0)
    monkeypatch.setattr(config, "HOOKS", {})
    monkeypatch.setattr(config, "HOOKS_ENABLED", True)
    monkeypatch.setattr(config, "RULES_INJECTED", set())
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    monkeypatch.setattr(context, "_memory_dir", None)
    monkeypatch.setattr(context, "USER_MEMORY", str(tmp_path / "home" / "CLAUDE.md"))
    context.forget()
    yield root
    context.forget()


@pytest.fixture
def summariser(monkeypatch):
    """Patch llm.chat to return a fixed summary; returns the call log."""
    calls = []

    def install(text="- task: refactor the parser"):
        def chat(messages, tools=None):
            calls.append({"messages": messages, "tools": tools})
            return {"content": text, "tool_calls": []}
        monkeypatch.setattr(llm, "chat", chat)
        return calls
    return install


def long_conversation(pairs=12, size=8000):
    """System, task, then Read call/result pairs: ~24,000 tokens at 12."""
    messages = [{"role": "system", "content": "SYSTEM PROMPT"},
                {"role": "user", "content": "refactor the parser"}]
    for number in range(pairs):
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "Read",
                          "arguments": {"file_path": f"f{number}.py"}}}]})
        messages.append({"role": "tool", "content": "x" * size})
    return messages


# ------------------------------------------------------------ estimation

def test_estimate_of_nothing_is_zero():
    assert context.estimate([]) == 0


def test_estimate_scales_with_content():
    small = context.estimate([{"role": "user", "content": "a" * 400}])
    large = context.estimate([{"role": "user", "content": "a" * 4000}])
    assert small == 100 and large == 1000


def test_estimate_counts_tool_call_arguments():
    write = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "Write",
                      "arguments": {"file_path": "a.py", "content": "w" * 4000}}}]}
    assert context.estimate([write]) >= 1000


def test_breakdown_attributes_by_prefix():
    system = ("BASE" * 100
              + "\n\n[project rule: style.md]\n\n" + "r" * 400
              + "\n\nAvailable skills (call the Skill tool to load one):\n- a: b"
              + "\n\n[project memory]\n\n" + "m" * 800)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "the task"},
        {"role": "user", "content": "[project rule: tests.md]\n\n" + "t" * 400},
        {"role": "user", "content": "[skill: review]\n\n" + "s" * 400},
        {"role": "user", "content": "[hook: PostToolUse]\n\n1 failed"},
    ]
    counts = context.breakdown(messages)
    assert counts["claude_md"] >= 200
    assert counts["rules"] >= 200
    assert counts["skills_index"] >= 100
    assert counts["system"] >= 100
    assert counts["rule_messages"] == 1
    assert counts["conversation"] < 20


# ------------------------------------------------------------ capping

def test_tool_result_over_cap_is_truncated_with_marker():
    out = context.cap("y" * 10000)
    assert out.startswith("y" * 8000)
    assert out.endswith("\n...[truncated, 2000 chars omitted]")


def test_tool_result_under_cap_is_untouched():
    assert context.cap("short result") == "short result"


def test_agent_caps_tool_results(project, monkeypatch):
    (project / "big.py").write_text("line of code\n" * 20000)
    seen = []
    script = iter([
        {"content": "", "tool_calls": [{"name": "Read",
                                        "arguments": {"file_path": "big.py"}}]},
        {"content": "Done.", "tool_calls": []},
    ])

    def chat(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return next(script)

    monkeypatch.setattr(llm, "chat", chat)
    from va_agent.core.agent import run
    run("read it", verbose=False)
    result = next(m for m in seen[-1] if m["role"] == "tool")
    assert "...[truncated," in result["content"]
    assert len(result["content"]) < 8100


# ------------------------------------------------------------ clamping

def test_oversized_message_is_clamped():
    limit = int(0.25 * 32768 * 4)
    out = context.clamp({"role": "tool", "content": "z" * (limit + 5000)})
    assert out["content"].endswith("\n...[message truncated]")
    assert len(out["content"]) <= limit + len("\n...[message truncated]")


def test_message_under_limit_is_unchanged():
    message = {"role": "user", "content": "fine"}
    assert context.clamp(message) == message


def test_clamp_covers_tool_call_arguments():
    huge = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "Write",
                      "arguments": {"file_path": "a.py", "content": "w" * 200000}}}]}
    out = context.clamp(huge)
    content = out["tool_calls"][0]["function"]["arguments"]["content"]
    assert content.endswith("\n...[message truncated]")
    assert out["tool_calls"][0]["function"]["arguments"]["file_path"] == "a.py"


def test_verified_loop_case_falls_below_threshold_after_clamping():
    messages = [{"role": "system", "content": "s" * 4000},
                {"role": "user", "content": "task"},
                {"role": "tool", "content": "x" * 120000},
                *[{"role": "user", "content": "u" * 2000}] * 4]
    assert context.needs_compaction(messages)
    clamped = [context.clamp(m) for m in messages]
    assert not context.needs_compaction(clamped)


# ------------------------------------------------------------ compaction

def test_triggers_above_threshold_not_below():
    assert not context.needs_compaction(long_conversation(pairs=11))
    assert context.needs_compaction(long_conversation(pairs=13))


def test_compaction_keeps_system_summary_and_recent(summariser):
    calls = summariser()
    messages = long_conversation()
    new, did = context.compact(messages)
    assert did is True
    assert new[0] == messages[0]
    assert new[1] == {"role": "user", "content":
                      "[summary of earlier work]\n\n- task: refactor the parser"}
    assert new[-4:] == messages[-4:]
    assert context.estimate(new) < context.estimate(messages) / 2
    assert len(calls) == 1
    assert calls[0]["tools"] is None


def test_kept_window_never_starts_with_an_orphaned_tool_result(summariser, monkeypatch):
    summariser()
    monkeypatch.setattr(config, "KEEP_RECENT", 3)
    new, _ = context.compact(long_conversation())
    kept = [m for m in new if not str(m["content"]).startswith("[summary")][1:]
    assert kept[0]["role"] == "assistant"


def test_one_reply_with_many_calls_still_compacts(summariser):
    """Seen live: the model asked for 13 Reads in one reply. Keeping every
    result of that reply made compaction nearly useless."""
    summariser()
    calls = [{"function": {"name": "Read", "arguments": {"file_path": f"f{n}.py"}}}
             for n in range(13)]
    messages = [{"role": "system", "content": "SYSTEM"},
                {"role": "user", "content": "read them all"},
                {"role": "assistant", "content": "", "tool_calls": calls},
                *[{"role": "tool", "content": "x" * 8000} for _ in range(13)]]
    new, did = context.compact(messages)
    assert did is True
    # Stepping back toward the call stops after KEEP_RECENT more messages.
    assert len(new) <= 2 + 2 * config.KEEP_RECENT
    assert not context.needs_compaction(new)
    assert config.COMPACT_ATTEMPTS == 0             # counted as effective


def test_claude_md_reread_from_disk_after_compaction(summariser, project):
    summariser()
    (project / "CLAUDE.md").write_text("Old memory.")
    context.load_memory()
    messages = long_conversation()
    messages[0] = {"role": "system", "content": "SYSTEM" + context.memory_text()}
    (project / "CLAUDE.md").write_text("Fresh memory.")

    new, _ = context.compact(messages)
    joined = "\n".join(str(m["content"]) for m in new)
    assert "[project memory]\n\nFresh memory." in joined
    assert "Old memory." not in joined
    assert new[0]["content"] == "SYSTEM"


def test_compaction_clears_injected_rules(summariser):
    summariser()
    config.RULES_INJECTED.update({"testing.md", "agent-core.md"})
    context.compact(long_conversation())
    assert config.RULES_INJECTED == set()


def test_nothing_to_summarise_returns_unchanged(summariser):
    calls = summariser()
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "task"},
                {"role": "assistant", "content": "ok"}]
    new, did = context.compact(messages)
    assert did is False and new is messages
    assert calls == []


def test_pre_compact_deny_returns_unchanged(summariser, project):
    calls = summariser()
    hook = project / "deny.sh"
    hook.write_text("#!/bin/bash\ncat >/dev/null\necho 'saving state' >&2\nexit 2\n")
    hook.chmod(0o755)
    config.HOOKS["PreCompact"] = [{"matcher": None,
                                   "hooks": [{"command": str(hook), "timeout": None}]}]
    messages = long_conversation()
    new, did = context.compact(messages)
    assert did is False and new is messages
    assert calls == []


def test_focus_reaches_the_summary_prompt(summariser):
    calls = summariser()
    context.compact(long_conversation(), focus="the tool layer")
    prompt = calls[0]["messages"][-1]["content"]
    assert "the tool layer" in prompt
    assert "original task" in prompt


def test_summary_failure_does_not_raise(monkeypatch):
    def broken(messages, tools=None):
        raise ConnectionError("ollama down")
    monkeypatch.setattr(llm, "chat", broken)
    messages = long_conversation()
    new, did = context.compact(messages)
    assert did is False and new is messages
    assert config.COMPACT_ATTEMPTS == 1


# ------------------------------------------------------------ attempt counter

def test_ineffective_compaction_increments_attempts(summariser):
    summariser(text="S" * 100000)            # a "summary" bigger than the history
    context.compact(long_conversation())
    assert config.COMPACT_ATTEMPTS == 1


def test_effective_compaction_resets_attempts(summariser):
    summariser()
    config.COMPACT_ATTEMPTS = 1
    context.compact(long_conversation())
    assert config.COMPACT_ATTEMPTS == 0


def test_loop_stops_compacting_after_two_ineffective_attempts(project, monkeypatch):
    """The runaway case: every compaction is useless. It must be tried
    exactly twice, and the run must still reach its step limit."""
    monkeypatch.setattr(config, "NUM_CTX", 8000)
    monkeypatch.setattr(config, "MAX_STEPS", 12)
    (project / "big.py").write_text("x = 1\n" * 4000)
    summaries = []

    def chat(messages, tools=None):
        if tools is None:
            summaries.append(1)
            return {"content": "S" * 100000, "tool_calls": []}
        return {"content": "", "tool_calls": [
            {"name": "Read", "arguments": {"file_path": "big.py"}}]}

    monkeypatch.setattr(llm, "chat", chat)
    from va_agent.core.agent import run
    stats = run("read it again and again", verbose=False)

    assert len(summaries) == 2
    assert config.COMPACT_ATTEMPTS == 2
    assert stats["steps"] == 12


def test_peak_tokens_reported_in_stats(project, monkeypatch):
    monkeypatch.setattr(llm, "chat",
                        lambda messages, tools=None: {"content": "Done.",
                                                      "tool_calls": []})
    from va_agent.core.agent import run
    stats = run("hello", verbose=False)
    assert stats["peak_tokens"] == config.PEAK_TOKENS > 0


def test_reset_context_clears_counters():
    config.PEAK_TOKENS, config.COMPACT_ATTEMPTS = 900, 2
    context.reset_context()
    assert (config.PEAK_TOKENS, config.COMPACT_ATTEMPTS) == (0, 0)


# ------------------------------------------------------------ memory

def test_memory_is_user_then_project(project, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "CLAUDE.md").write_text("USER prefs")
    (project / "CLAUDE.md").write_text("PROJECT facts")
    assert context.load_memory() == "USER prefs\n\nPROJECT facts"
    assert config.CLAUDE_MD == "USER prefs\n\nPROJECT facts"


def test_memory_falls_back_to_dot_claude(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "CLAUDE.md").write_text("hidden memory")
    assert context.load_memory() == "hidden memory"


def test_missing_memory_is_empty():
    assert context.load_memory() == ""


def test_init_refuses_to_overwrite(project):
    (project / "CLAUDE.md").write_text("mine")
    message = context.init_memory()
    assert "not overwriting" in message
    assert (project / "CLAUDE.md").read_text() == "mine"


def test_init_describes_the_repo(project):
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "pkg").mkdir()
    (project / "pkg" / "a.py").write_text("x = 1\n")
    assert context.init_memory().startswith("Created")
    text = (project / "CLAUDE.md").read_text()
    assert "Language: Python" in text
    assert "`pytest -q`" in text
    assert "- `pkg/`" in text


# ------------------------------------------------------------ snapshots

def test_list_snapshots_in_step_order(project):
    snapshots.new_session()
    (project / "a.py").write_text("v0\n")
    for version in ("v1", "v2", "v3"):
        snapshots.next_step()
        fs.Write("a.py", f"{version}\n")
    assert [step for step, _, _ in context.list_snapshots()] == [1, 2, 3]
    assert context.list_snapshots()[0][2] == ["a.py"]


def test_restore_puts_pre_edit_content_back(project):
    snapshots.new_session()
    (project / "a.py").write_text("original\n")
    snapshots.next_step()
    fs.Write("a.py", "edited\n")
    assert "restored" in context.restore(1)
    assert (project / "a.py").read_text() == "original\n"
