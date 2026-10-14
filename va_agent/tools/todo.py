"""TodoWrite: the model's own task list for the current session."""

import json

from va_agent.ui import console as ui

STATUSES = ("pending", "in_progress", "completed")

_todos = []


def current():
    """The session's todo list (a copy)."""
    return [dict(item) for item in _todos]


def reset():
    """Clear the list. Called between benchmark runs."""
    _todos.clear()


def _validate(todos):
    if isinstance(todos, str):
        # Small models often send the list as a JSON string.
        try:
            todos = json.loads(todos)
        except ValueError:
            raise ValueError("todos must be a list of {content, status} "
                             "objects, not plain text.")
    if not isinstance(todos, list):
        raise ValueError("todos must be a list of {content, status} objects.")

    clean = []
    for number, item in enumerate(todos, 1):
        if not isinstance(item, dict) or not str(item.get("content", "")).strip():
            raise ValueError(f"todo {number} needs a non-empty 'content'.")
        status = item.get("status", "pending")
        if status not in STATUSES:
            raise ValueError(f"todo {number} has status {status!r}; use one "
                             "of: " + ", ".join(STATUSES) + ".")
        clean.append({"content": str(item["content"]).strip(),
                      "status": status})
    return clean


def TodoWrite(todos):
    """Replace the session's todo list and show it."""
    _todos[:] = _validate(todos)
    ui.render_todos(_todos)
    done = sum(1 for t in _todos if t["status"] == "completed")
    return (f"Todo list updated ({done}/{len(_todos)} completed):\n"
            + ui.todo_lines(_todos))
