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

from va_agent.core import config
from va_agent.ui.console import console

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "PreCompact", "Stop", "SessionEnd")
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}


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
