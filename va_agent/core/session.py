"""Session persistence: ``<SANDBOX>/.va/sessions/<SESSION_ID>.json``.

Saved after every turn, not only at exit, because a terminal can close
uncleanly. Each write goes to a temp file first and is moved into place with
os.replace(), so a crash mid-write can never corrupt a saved session.

Saved messages are the capped and clamped ones (Phase 8): saving originals
would make a resumed long session blow past the window on its first call.
"""

import json
import os
import tempfile
import time

from va_agent.core import config
from va_agent.core import context
from va_agent.harness import snapshots
from va_agent.tools import todo
from va_agent.ui.console import console

FIRST_TASK_CHARS = 80

# Content the agent injects itself; never a user's task.
_INJECTED = ("[project rule:", "[skill:", "[project memory]", "[hook:",
             "[summary of earlier work]")


def _directory() -> str:
    return os.path.join(config.SANDBOX, config.SESSION_DIR)


def path_for(session_id: str) -> str:
    return os.path.join(_directory(), f"{session_id}.json")


def first_task(messages: list) -> str:
    """The first real user message, cut to FIRST_TASK_CHARS, as a label."""
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "user" and content.strip() \
                and not content.startswith(_INJECTED):
            text = " ".join(content.split())
            return text[:FIRST_TASK_CHARS]
    return ""


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        console.print(f"[yellow]  sessions: skipping {path}: {error}[/yellow]")
        return None
    if not isinstance(data, dict) or "session_id" not in data:
        console.print(f"[yellow]  sessions: skipping {path}: not a "
                      f"session file[/yellow]")
        return None
    return data


def _stored(message: dict) -> dict:
    """A message as saved: tool results capped, every message clamped."""
    if message.get("role") == "tool":
        message = {**message, "content": context.cap(message.get("content", ""))}
    return context.clamp(message)


def save(messages: list, todos: list | None = None) -> str:
    """Write this session atomically and return the file path."""
    if not config.SESSION_ID:
        reset_session_state()
    folder = _directory()
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(config.SANDBOX, ".va", ".gitignore")
    if not os.path.exists(ignore):          # keep sessions out of git
        with open(ignore, "w", encoding="utf-8") as handle:
            handle.write("*\n")
    path = path_for(config.SESSION_ID)
    previous = _read(path) if os.path.exists(path) else None

    stored = [_stored(message) for message in messages]
    now = time.time()
    data = {
        "session_id": config.SESSION_ID,
        "created": (previous or {}).get("created") or config.SESSION_STARTED
                   or now,
        "updated": now,
        "cwd": os.path.realpath(config.SANDBOX),
        "model": config.MODEL,
        "mode": config.MODE,
        "first_task": first_task(stored) or (previous or {}).get("first_task", ""),
        "messages": stored,
        "todos": todo.current() if todos is None else list(todos),
        "tokens": config.SESSION_TOKENS,
        "seconds": round(config.SESSION_SECONDS, 1),
        "turns": config.SESSION_TURNS,
        "peak": config.SESSION_PEAK,
    }

    handle = tempfile.NamedTemporaryFile("w", dir=folder, prefix=".tmp-",
                                         suffix=".json", delete=False,
                                         encoding="utf-8")
    try:
        with handle:
            json.dump(data, handle, indent=1, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return path


def load(session_id: str):
    """The saved session as a dict, or None if it is missing or unreadable
    (which warns rather than raising)."""
    path = path_for(session_id)
    if not os.path.exists(path):
        console.print(f"[yellow]  sessions: no session {session_id}[/yellow]")
        return None
    return _read(path)


def list_sessions(limit: int = 20) -> list:
    """Saved sessions, newest first, without their messages. Unreadable
    files are skipped with a warning."""
    folder = _directory()
    try:
        names = [name for name in os.listdir(folder)
                 if name.endswith(".json") and not name.startswith(".")]
    except OSError:
        return []
    found = []
    for name in names:
        data = _read(os.path.join(folder, name))
        if data is None:
            continue
        summary = {key: value for key, value in data.items()
                   if key != "messages"}
        summary["message_count"] = len(data.get("messages") or [])
        found.append(summary)
    found.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    return found[:limit]


def most_recent():
    """The newest saved session in full, or None when there are none."""
    newest = list_sessions(limit=1)
    return load(newest[0]["session_id"]) if newest else None


def reset_session_state() -> None:
    """A brand-new session: new SESSION_ID (snapshots follow it) and the
    /cost counters back to zero. (Not reset_session: permissions.py owns
    that name.)"""
    snapshots.new_session()
    config.SESSION_STARTED = time.time()
    config.SESSION_TOKENS = 0
    config.SESSION_SECONDS = 0.0
    config.SESSION_TURNS = 0
    config.SESSION_PEAK = 0


def restore(data: dict) -> list:
    """Make *data* the current session: same SESSION_ID (so snapshots stay
    together), its model, mode, todos and counters. Returns its messages."""
    snapshots.continue_session(data["session_id"])
    config.SESSION_STARTED = time.time()
    config.MODEL = data.get("model") or config.MODEL
    if data.get("mode"):
        config.MODE = data["mode"]
    config.SESSION_TOKENS = int(data.get("tokens") or 0)
    config.SESSION_SECONDS = float(data.get("seconds") or 0.0)
    config.SESSION_TURNS = int(data.get("turns") or 0)
    config.SESSION_PEAK = int(data.get("peak") or 0)
    todo.restore(data.get("todos"))
    return list(data.get("messages") or [])
