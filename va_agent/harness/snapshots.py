"""File snapshots taken before every Write or Edit, and /rewind.

Layout: ``<SANDBOX>/.va/snapshots/<SESSION_ID>/<step>/<relative path>`` plus a
``manifest.json`` per step recording each file's original path and whether
it existed (a file that did not exist is deleted on rewind).

Steps are numbered across the whole session, not per task, so two tasks in
one REPL session never share a snapshot folder.
"""

import json
import os
import shutil
import time
import uuid

from va_agent.core import config

_step = 0


def new_session():
    """Start a new snapshot session and return its id."""
    global _step
    config.SESSION_ID = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    _step = 0
    return config.SESSION_ID


def next_step():
    """Advance the session step counter. Called once per agent step."""
    global _step
    _step += 1
    return _step


def _base():
    return os.path.join(config.SANDBOX, ".va", "snapshots")


def _session_dir(session=None):
    return os.path.join(_base(), session or config.SESSION_ID)


def _manifest(step_dir):
    path = os.path.join(step_dir, "manifest.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def save(full_path):
    """Copy *full_path* aside before it is changed. A file already saved
    in this step keeps its first (pre-step) copy."""
    if not config.SESSION_ID:
        new_session()
    root = os.path.realpath(config.SANDBOX)
    full = os.path.realpath(full_path)

    # Keep snapshots out of the project's git history.
    va_dir = os.path.join(root, ".va")
    os.makedirs(va_dir, exist_ok=True)
    ignore = os.path.join(va_dir, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as handle:
            handle.write("*\n")

    step_dir = os.path.join(_session_dir(), f"{max(_step, 1):04d}")
    entries = _manifest(step_dir)
    if any(entry["path"] == full for entry in entries):
        return

    if full.startswith(root + os.sep):
        copy = os.path.relpath(full, root)
    else:
        copy = os.path.join("_abs", full.lstrip(os.sep))
    existed = os.path.isfile(full)
    if existed:
        target = os.path.join(step_dir, copy)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(full, target)

    entries.append({"path": full, "copy": copy, "existed": existed})
    os.makedirs(step_dir, exist_ok=True)
    with open(os.path.join(step_dir, "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump(entries, handle, indent=1)


def latest_session():
    """This session if it has snapshots, else the newest one on disk."""
    if config.SESSION_ID and os.path.isdir(_session_dir()):
        return config.SESSION_ID
    try:
        sessions = sorted(os.listdir(_base()))
    except OSError:
        return None
    return sessions[-1] if sessions else None


def list_snapshots(session=None):
    """[(step, [relative paths changed])] for a session, oldest first."""
    session = session or latest_session()
    if not session:
        return []
    folder = _session_dir(session)
    root = os.path.realpath(config.SANDBOX)
    found = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.isdigit():
            paths = [os.path.relpath(e["path"], root)
                     for e in _manifest(os.path.join(folder, name))]
            found.append((int(name), paths))
    return found


def rewind(step, session=None):
    """Restore every file to how it was before *step*. Undoes that step
    and all later ones (newest first), then drops their snapshots.
    Returns a list of what was done."""
    session = session or latest_session()
    if not session:
        return []
    folder = _session_dir(session)
    done = []
    for number, _ in reversed(list_snapshots(session)):
        if number < step:
            continue
        step_dir = os.path.join(folder, f"{number:04d}")
        for entry in reversed(_manifest(step_dir)):
            if entry["existed"]:
                os.makedirs(os.path.dirname(entry["path"]), exist_ok=True)
                shutil.copy2(os.path.join(step_dir, entry["copy"]),
                             entry["path"])
                done.append(f"restored {entry['path']}")
            elif os.path.exists(entry["path"]):
                os.remove(entry["path"])
                done.append(f"removed {entry['path']}")
        shutil.rmtree(step_dir, ignore_errors=True)
    return done
