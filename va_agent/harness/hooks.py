"""Lifecycle hooks, configured in ``.claude/settings.json`` as in Claude Code.

A hook is a shell command run at a fixed point in the session. It gets one
line of JSON on stdin and answers through its exit code and stdout. Hooks
never involve the model: they are deterministic checks around it.

Hooks run with the user's full permissions. A settings file from an
untrusted repository can run anything.
"""

import json
import os
import re
import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from va_agent.core import config
from va_agent.ui.console import console

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "PreCompact", "Stop", "SessionEnd")
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}
BLOCKING = {"UserPromptSubmit", "PreToolUse", "PreCompact", "Stop"}
DECISIONS = ("allow", "deny", "ask")


@dataclass
class HookResult:
    decision: str | None = None     # allow | deny | ask | None
    reason: str = ""
    context: list = field(default_factory=list)


def _warn(message: str) -> None:
    console.print(f"[yellow]  hooks: {message}[/yellow]")


def _timeout_of(value, where: str):
    """A positive per-hook timeout, or None to use the default."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value <= 0:
        _warn(f"{where}: timeout must be a positive number; using default")
        return None
    return value


def _entry(event: str, raw, where: str):
    """Validate one matcher entry. Returns it normalised, or None."""
    if not isinstance(raw, dict):
        _warn(f"{where}: entry must be an object; skipped")
        return None

    matcher = raw.get("matcher")
    if matcher in ("", "*"):
        matcher = None
    if matcher is not None:
        if not isinstance(matcher, str):
            _warn(f"{where}: matcher must be a string; skipped")
            return None
        try:
            re.compile(matcher)
        except re.error as error:
            _warn(f"{where}: bad matcher regex {matcher!r} ({error}); skipped")
            return None

    commands = []
    items = raw.get("hooks")
    if not isinstance(items, list):
        _warn(f"{where}: 'hooks' must be a list; skipped")
        return None
    for number, hook in enumerate(items):
        spot = f"{where}.hooks[{number}]"
        if not isinstance(hook, dict):
            _warn(f"{spot}: must be an object; skipped")
            continue
        if hook.get("type", "command") != "command":
            _warn(f"{spot}: type {hook.get('type')!r} is not supported, only "
                  "'command'; ignored")
            continue
        command = hook.get("command")
        if not isinstance(command, str) or not command.strip():
            _warn(f"{spot}: 'command' must be a non-empty string; skipped")
            continue
        commands.append({"command": command,
                         "timeout": _timeout_of(hook.get("timeout"), spot)})

    if not commands:
        return None
    return {"matcher": matcher, "hooks": commands}


def load_hooks(project_dir: str | None = None) -> dict:
    """Read the "hooks" block of <project_dir or SANDBOX>/.claude/settings.json
    (the same file permissions.load_settings() reads) into config.HOOKS.
    Bad entries warn and are skipped; nothing here raises."""
    config.HOOKS = {}
    base = project_dir or config.SANDBOX
    path = os.path.join(base, ".claude", "settings.json")
    if not os.path.isfile(path):
        return config.HOOKS
    try:
        with open(path, encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, ValueError) as error:
        _warn(f"could not read {path}: {error}")
        return config.HOOKS

    block = settings.get("hooks") if isinstance(settings, dict) else None
    if block is None:
        return config.HOOKS
    if not isinstance(block, dict):
        _warn("'hooks' must be an object; ignored")
        return config.HOOKS

    for event, entries in block.items():
        if event not in EVENTS:
            _warn(f"unknown event {event!r}; skipped")
            continue
        if not isinstance(entries, list):
            _warn(f"{event}: must be a list; skipped")
            continue
        valid = [entry for number, raw in enumerate(entries)
                 if (entry := _entry(event, raw, f"{event}[{number}]"))]
        if valid:
            config.HOOKS[event] = valid
    return config.HOOKS


def _project_dir() -> str:
    return os.path.realpath(config.SANDBOX)


def _expand(command: str, project: str) -> str:
    return command.replace("${CLAUDE_PROJECT_DIR}", project)


def _selected(event: str, payload: dict, default_timeout) -> list:
    """(command, timeout) pairs to run: matching entries, duplicates
    removed, in configuration order."""
    tool = payload.get("tool_name", "")
    project = _project_dir()
    chosen, seen = [], set()
    for entry in config.HOOKS.get(event, []):
        matcher = entry.get("matcher")
        if event in TOOL_EVENTS and matcher is not None:
            try:
                if not re.fullmatch(matcher, tool):
                    continue
            except re.error:
                continue
        for hook in entry.get("hooks", []):
            command = _expand(hook["command"], project)
            if command in seen:
                continue
            seen.add(command)
            chosen.append((command, hook.get("timeout") or default_timeout))
    return chosen


def _run(command: str, stdin: str, timeout: float, project: str) -> dict:
    """Run one hook. Own process group, so a timeout kills everything it
    started instead of waiting on a child that holds the pipes open."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": project}
    try:
        proc = subprocess.Popen(command, shell=True, cwd=project, env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except OSError as error:
        return {"code": None, "stdout": "", "stderr": str(error),
                "timed_out": False}
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
        return {"code": proc.returncode, "stdout": out, "stderr": err,
                "timed_out": False}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate()
        return {"code": None, "stdout": "", "stderr": "", "timed_out": True}


def _structured(stdout: str):
    """hookSpecificOutput from stdout, {} for JSON without it, or None
    when stdout is not a JSON object (then it is plain text)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    inner = data.get("hookSpecificOutput")
    return inner if isinstance(inner, dict) else {}


def fire(event: str, payload: dict | None = None,
         timeout: float | None = None) -> HookResult:
    """Run every hook matching *event* in parallel and combine the answers.
    A deny wins; otherwise the last allow/ask applies; context from every
    hook is kept in order. Never raises and never waits past a timeout."""
    result = HookResult()
    if not config.HOOKS_ENABLED or event not in EVENTS:
        return result

    payload = {"session_id": config.SESSION_ID, "cwd": _project_dir(),
               "hook_event_name": event, **(payload or {})}
    chosen = _selected(event, payload,
                       timeout if timeout is not None else config.HOOK_TIMEOUT)
    if not chosen:
        return result

    stdin = json.dumps(payload, default=str) + "\n"
    project = _project_dir()
    with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
        runs = list(pool.map(lambda item: _run(item[0], stdin, item[1], project),
                             chosen))

    blocking = event in BLOCKING
    denials = []
    for (command, limit), run in zip(chosen, runs):
        out, err = run["stdout"].strip(), run["stderr"].strip()
        if run["timed_out"]:
            result.context.append(f"[hook timed out after {limit:g}s] {command}")
            continue
        if run["code"] == 0:
            inner = _structured(out) if out else {}
            if inner is None:
                result.context.append(out)
                continue
            extra = inner.get("additionalContext")
            if isinstance(extra, str) and extra.strip():
                result.context.append(extra.strip())
            decision = inner.get("permissionDecision")
            if blocking and decision in DECISIONS:
                reason = str(inner.get("permissionDecisionReason") or "")
                if decision == "deny":
                    denials.append(reason or f"denied by hook: {command}")
                else:
                    result.decision, result.reason = decision, reason
            continue
        if run["code"] == 2:
            if blocking:
                denials.append(err or f"blocked by hook: {command}")
            elif err or out:
                result.context.append(err or out)
            continue
        code = run["code"] if run["code"] is not None else "not started"
        result.context.append(f"[hook error: exit {code}] {command}"
                              + (f"\n{err}" if err else ""))

    if denials:
        result.decision, result.reason = "deny", "\n".join(denials)
    return result


# Old tool names the model may still call, mapped to the Claude Code names
# hooks match on, so a "Bash" matcher also guards run_command.
CANONICAL = {"read_file": "Read", "write_file": "Write", "run_command": "Bash",
             "grep": "Grep", "list_files": "Glob"}
FILE_PATH_TOOLS = {"Read", "Write", "Edit"}


def tool_payload(name: str, arguments: dict) -> dict:
    """tool_name and tool_input as a Claude Code hook expects them."""
    tool = CANONICAL.get(name, name)
    tool_input = dict(arguments or {})
    if tool in FILE_PATH_TOOLS and "file_path" not in tool_input \
            and "path" in tool_input:
        tool_input["file_path"] = tool_input.pop("path")
    return {"tool_name": tool, "tool_input": tool_input}


def context_messages(event: str, result: HookResult) -> list:
    """Hook context as conversation messages. role "user": several local
    models ignore system messages that arrive mid-conversation."""
    return [{"role": "user", "content": f"[hook: {event}]\n\n{text}"}
            for text in result.context]


MAX_STOP_CONTINUATIONS = 3

_stop_continuations = 0
_session_context = []


def reset_hooks() -> None:
    """Reset the Stop-hook continuation counter. Called at the start of
    every run. (Not reset_session/reset_rules: sibling modules own those.)"""
    global _stop_continuations
    _stop_continuations = 0


def stop_may_continue() -> bool:
    """Count one Stop-forced continuation; False once the limit is used."""
    global _stop_continuations
    if _stop_continuations >= MAX_STOP_CONTINUATIONS:
        return False
    _stop_continuations += 1
    return True


def stop_hook_active() -> bool:
    """True once a Stop hook has already forced a continuation this run."""
    return _stop_continuations > 0


def start_session(source: str = "startup") -> HookResult:
    """Fire SessionStart and keep its context for the system prompt."""
    result = fire("SessionStart", {"source": source})
    _session_context[:] = result.context
    return result


def system_text() -> str:
    """SessionStart context, formatted for the end of the system prompt."""
    return "".join(f"\n\n[hook: SessionStart]\n\n{text}"
                   for text in _session_context)
