"""Context management for a small window.

Ollama does not error when ``num_ctx`` is exceeded: it silently drops the
oldest messages, so the model loses the task and the failure looks like
model incompetence. Three layers keep the conversation inside the window:

1. cap each tool result as it is appended (the main defence),
2. clamp any single message to a share of the window (so compaction can
   always shrink what it keeps),
3. compact old history into a summary, with an attempt counter so an
   ineffective compaction can never loop.
"""

import os
import subprocess
import time

from va_agent.core import config
from va_agent.core import llm
from va_agent.harness import hooks, rules, skills, snapshots
from va_agent.ui.console import console

# Prefixes that mark injected content, for breakdown().
RULE_MARK = "[project rule:"
SKILL_MARK = "[skill:"
MEMORY_MARK = "[project memory]"
HOOK_MARK = "[hook:"
SKILLS_INDEX_MARK = "Available skills (call the Skill tool"

CATEGORIES = ("system", "claude_md", "skills_index", "rules", "conversation")


def _size(message: dict) -> int:
    """Characters a message puts in the window: its content plus the
    arguments of any tool calls it carries (a Write call's whole file
    stays in history too)."""
    size = len(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        function = call.get("function", call) if isinstance(call, dict) else {}
        size += len(str(function.get("arguments", "")))
    return size


def estimate(messages: list) -> int:
    """Rough token count: characters // 4. No tokeniser, on purpose."""
    return sum(_size(message) for message in messages) // 4


def _split_system(text: str) -> dict:
    """Characters of a system prompt per category, by marker."""
    marks = {RULE_MARK: "rules", SKILLS_INDEX_MARK: "skills_index",
             MEMORY_MARK: "claude_md", HOOK_MARK: "system"}
    cuts = sorted((index, category)
                  for mark, category in marks.items()
                  for index in _all(text, mark))
    counts = dict.fromkeys(CATEGORIES, 0)
    start, category = 0, "system"
    for index, next_category in cuts:
        counts[category] += index - start
        start, category = index, next_category
    counts[category] += len(text) - start
    return counts


def _all(text: str, mark: str):
    index = text.find(mark)
    while index != -1:
        yield index
        index = text.find(mark, index + 1)


def breakdown(messages: list) -> dict:
    """Tokens per category: system, claude_md, skills_index, rules,
    conversation. Injected content is recognised by its prefix marker;
    rule_messages counts injected path-scoped rules."""
    chars = dict.fromkeys(CATEGORIES, 0)
    rule_messages = 0
    for number, message in enumerate(messages):
        content = str(message.get("content") or "")
        if number == 0 and message.get("role") == "system":
            for category, count in _split_system(content).items():
                chars[category] += count
            continue
        size = _size(message)
        if content.startswith(RULE_MARK):
            chars["rules"] += size
            rule_messages += 1
        elif content.startswith(MEMORY_MARK):
            chars["claude_md"] += size
        elif content.startswith(SKILL_MARK):
            chars["skills_index"] += size
        else:
            chars["conversation"] += size
    tokens = {category: count // 4 for category, count in chars.items()}
    tokens["rule_messages"] = rule_messages
    return tokens


# ---------------------------------------------------------------- limits

def cap(text: str) -> str:
    """Layer 1: cut a tool result to TOOL_RESULT_CAP characters. The marker
    tells the model the output was partial, so it can page with
    Read(offset, limit) if it needs more."""
    text = str(text)
    limit = config.TOOL_RESULT_CAP
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated, {len(text) - limit} chars omitted]"


def message_limit() -> int:
    """Most characters any one message may hold."""
    return int(config.MAX_MESSAGE_SHARE * config.NUM_CTX * 4)


def _clamp_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n...[message truncated]"


def clamp(message: dict) -> dict:
    """Layer 2: no message larger than MAX_MESSAGE_SHARE of the window,
    including long tool-call arguments. Without this an oversized message
    in compaction's kept window survives every compaction, and the agent
    compacts forever. Returns a new dict; the input is not changed."""
    limit = message_limit()
    content = message.get("content")
    clamped = dict(message)
    if isinstance(content, str) and len(content) > limit:
        clamped["content"] = _clamp_text(content, limit)
    calls = message.get("tool_calls")
    if calls:
        new_calls = []
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            arguments = function.get("arguments") if function else None
            if isinstance(arguments, dict) and any(
                    isinstance(v, str) and len(v) > limit
                    for v in arguments.values()):
                arguments = {key: _clamp_text(value, limit)
                             if isinstance(value, str) else value
                             for key, value in arguments.items()}
                call = {**call, "function": {**function,
                                             "arguments": arguments}}
            new_calls.append(call)
        clamped["tool_calls"] = new_calls
    return clamped


# ---------------------------------------------------------------- memory

_memory_dir = None      # set by load_memory(); None means never loaded

# User-level memory. A module attribute so tests can point it elsewhere.
USER_MEMORY = os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def load_memory(project_dir: str | None = None) -> str:
    """~/.claude/CLAUDE.md, then <project>/CLAUDE.md (else
    <project>/.claude/CLAUDE.md), joined. Missing files are fine."""
    global _memory_dir
    _memory_dir = project_dir or config.SANDBOX
    project = _read(os.path.join(_memory_dir, "CLAUDE.md")) \
        or _read(os.path.join(_memory_dir, ".claude", "CLAUDE.md"))
    config.CLAUDE_MD = "\n\n".join(part for part in (_read(USER_MEMORY), project)
                                   if part)
    return config.CLAUDE_MD


def _reload_memory() -> str:
    """Fresh CLAUDE.md for compaction. Only re-read if this session loaded
    memory at all, so benchmark runs never pick up ~/.claude/CLAUDE.md."""
    if _memory_dir is None:
        return config.CLAUDE_MD
    return load_memory(_memory_dir)


def memory_text() -> str:
    """CLAUDE.md formatted for the end of the system prompt."""
    return f"\n\n{MEMORY_MARK}\n\n{config.CLAUDE_MD}" if config.CLAUDE_MD else ""


# ---------------------------------------------------------------- compaction

MAX_ATTEMPTS = 2        # consecutive ineffective compactions before giving up
MIN_REDUCTION = 0.10    # a compaction must cut at least this share

SUMMARY_SYSTEM = ("You summarise the earlier part of a coding agent's "
                  "session so it can continue without the full history.")

SUMMARY_PROMPT = """Summarise the conversation below for the agent that will
continue the work. Preserve, as short bullet points:
- the original task, in full
- files read or modified, and what changed in each
- decisions made and why
- errors encountered and whether they were fixed
- what remains to be done
Do not invent anything that is not in the conversation."""


def needs_compaction(messages: list) -> bool:
    """True once the estimate passes COMPACT_THRESHOLD of the window."""
    return estimate(messages) > config.NUM_CTX * config.COMPACT_THRESHOLD


def _transcript(messages: list) -> str:
    """The messages to summarise as plain text, cut in the middle if it
    would not fit in half the window."""
    lines = []
    for message in messages:
        text = str(message.get("content") or "")
        for call in message.get("tool_calls") or []:
            function = call.get("function", call)
            arguments = str(function.get("arguments", ""))[:300]
            text += f"\n(called {function.get('name')} {arguments})"
        lines.append(f"{message.get('role', '?')}: {text}")
    text = "\n\n".join(lines)
    room = int(config.NUM_CTX * 4 * 0.5)
    if len(text) > room:
        text = (text[:room // 2] + "\n\n...[middle of transcript omitted]...\n\n"
                + text[-room // 2:])
    return text


def _without_memory(system: dict) -> dict:
    """The system message minus its CLAUDE.md section (re-added fresh)."""
    content = str(system.get("content") or "")
    cut = content.find(f"\n\n{MEMORY_MARK}")
    return system if cut == -1 else {**system, "content": content[:cut]}


def _ineffective(reason: str) -> None:
    config.COMPACT_ATTEMPTS += 1
    if config.COMPACT_ATTEMPTS >= MAX_ATTEMPTS:
        console.print(f"[yellow]  context: compaction is not helping "
                      f"({reason}); continuing without it[/yellow]")


def compact(messages: list, focus: str | None = None,
            trigger: str = "manual") -> tuple:
    """Summarise everything between the system prompt and the last
    KEEP_RECENT messages with exactly one model call. Returns
    (messages, True) when compacted, else (unchanged messages, False)."""
    if not messages:
        return messages, False
    start = max(1, len(messages) - config.KEEP_RECENT)
    # Prefer not to start the kept window on a tool result whose call was
    # summarised away: step back to the call, but by at most KEEP_RECENT
    # more messages. A reply that made a dozen calls would otherwise drag
    # every one of its results into the kept window (seen live: 13 Reads
    # in one reply made compaction keep ~85% of the conversation).
    floor = max(1, start - config.KEEP_RECENT)
    while start > floor and messages[start].get("role") == "tool":
        start -= 1
    middle = [m for m in messages[1:start]
              if not str(m.get("content") or "").startswith(MEMORY_MARK)]
    if not middle:
        return messages, False

    pre = hooks.fire("PreCompact", {"trigger": trigger,
                                    "custom_instructions": focus or ""})
    if pre.decision == "deny":
        return messages, False

    before = estimate(messages)
    prompt = SUMMARY_PROMPT
    if focus:
        prompt += f"\nGive priority to anything about: {focus}"
    prompt += f"\n\nConversation:\n\n{_transcript(middle)}"
    try:
        reply = llm.chat([{"role": "system", "content": SUMMARY_SYSTEM},
                          {"role": "user", "content": prompt}])
        summary = str(reply.get("content") or "").strip()
    except Exception as error:
        _ineffective(f"summary failed: {error}")
        return messages, False
    if not summary:
        _ineffective("empty summary")
        return messages, False

    memory = _reload_memory()
    rebuilt = [_without_memory(messages[0])]
    if memory:
        rebuilt.append({"role": "user", "content": f"{MEMORY_MARK}\n\n{memory}"})
    rebuilt.append({"role": "user",
                    "content": f"[summary of earlier work]\n\n{summary}"})
    rebuilt += hooks.context_messages("PreCompact", pre)
    rebuilt += messages[start:]

    # Rules can re-fire when their file is touched again; skills whose body
    # was summarised away can be loaded again.
    rules.reset_rules()
    skills.sync_context(rebuilt)

    after = estimate(rebuilt)
    if after > before * (1 - MIN_REDUCTION):
        _ineffective(f"{before} -> {after} tokens")
    else:
        config.COMPACT_ATTEMPTS = 0
    return rebuilt, True


def reset_context() -> None:
    """Per-run maximum and attempt counter back to zero. (Not
    reset_session/rules/hooks/skills: sibling modules own those.)"""
    config.PEAK_TOKENS = 0
    config.COMPACT_ATTEMPTS = 0


# ---------------------------------------------------------------- /init

_LANGUAGES = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
              ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
              ".c": "C", ".cpp": "C++", ".cs": "C#", ".php": "PHP"}
_SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".va",
         ".pytest_cache", "dist", "build"}


def _language(root: str) -> str:
    counts, seen = {}, 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP
                   and not d.endswith(".egg-info")]
        for name in files:
            language = _LANGUAGES.get(os.path.splitext(name)[1])
            if language:
                counts[language] = counts.get(language, 0) + 1
            seen += 1
        if seen > 5000:
            break
    return max(counts, key=counts.get) if counts else "unknown"


def _test_command(root: str) -> str:
    if os.path.isfile(os.path.join(root, "pytest.ini")):
        return "pytest -q"
    pyproject = _read(os.path.join(root, "pyproject.toml"))
    if "pytest" in pyproject:
        return "pytest -q"
    if '"test"' in _read(os.path.join(root, "package.json")):
        return "npm test"
    return "unknown: add the test command here"


def _branch(root: str) -> str:
    try:
        done = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                              cwd=root, capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def _layout(root: str) -> list:
    entries = []
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name in _SKIP or name.endswith(".egg-info"):
            continue
        entries.append(f"{name}/" if os.path.isdir(os.path.join(root, name))
                       else name)
    return entries


def init_memory(project_dir: str | None = None) -> str:
    """Write a starter <project>/CLAUDE.md from what the repo looks like.
    Never overwrites an existing file. Returns a message saying what
    happened."""
    root = project_dir or config.SANDBOX
    path = os.path.join(root, "CLAUDE.md")
    if os.path.exists(path):
        return f"{path} already exists; not overwriting it."
    layout = "\n".join(f"- `{entry}`" for entry in _layout(root)) \
        or "- (empty)"
    text = f"""# CLAUDE.md

Project memory for coding agents. Keep it short: it is sent on every turn.

## Overview
- Language: {_language(root)}
- Git branch when this file was generated: {_branch(root)}

## Commands
- Run tests: `{_test_command(root)}`

## Layout
{layout}

## Conventions
- (add this project's conventions here)
"""
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as error:
        return f"could not write {path}: {error}"
    return f"Created {path}"


# ---------------------------------------------------------------- snapshots

def list_snapshots() -> list:
    """(step, time, [files]) for this session's snapshots, oldest first."""
    found = []
    for step, files in snapshots.list_snapshots():
        try:
            stamp = time.strftime("%H:%M:%S", time.localtime(
                os.path.getmtime(snapshots.step_dir(step))))
        except OSError:
            stamp = "?"
        found.append((step, stamp, files))
    return found


def restore(step: int) -> str:
    """Put files back as they were before *step* (undoing that step and
    every later one). Returns what happened."""
    done = snapshots.rewind(int(step))
    if not done:
        return f"nothing to restore at step {step}"
    return "\n".join(done + [f"restored {len(done)} file change(s)"])


# ---------------------------------------------------------------- session

# The most recent run's conversation, for /context and /compact. Each task
# starts a fresh conversation, so this is what those commands look at.
last_messages = []


def remember(messages: list) -> None:
    last_messages[:] = list(messages)


def forget() -> None:
    last_messages.clear()
