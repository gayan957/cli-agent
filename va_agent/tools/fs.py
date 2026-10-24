"""File tools: Read, Write, Edit, Glob and Grep. Bash is in shell.py,
TodoWrite in todo.py; registry.py decides which ones the model gets.

Every path goes through :func:`_safe`, which reads ``config.SANDBOX`` at
call time so the evaluation harness can redirect the agent between runs.
Access control lives in :mod:`va_agent.harness.permissions`.
"""

import os
import re

from va_agent.core import config
from va_agent.harness import snapshots

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache",
             ".va"}

MAX_GREP_HITS = 50


def _safe(path):
    """Resolve *path* against the sandbox. Never blocks: whether an outside
    path may be used is decided by harness.permissions before a tool runs."""
    root = os.path.realpath(config.SANDBOX)
    return os.path.realpath(os.path.join(root, os.path.expanduser(path)))


def _relative(full):
    return os.path.relpath(full, os.path.realpath(config.SANDBOX))


READ_LINE_CAP = 2000
READ_LINE_CHARS = 2000


def Read(file_path, offset=None, limit=None):
    """Return a file's lines numbered like ``cat -n``: a 6-wide line number,
    a tab, then the line. ``offset`` is the 1-based first line, ``limit``
    the number of lines (default 2000)."""
    full = _safe(file_path)
    if not os.path.exists(full):
        raise FileNotFoundError(
            f"File not found: {file_path}. Use Glob to find the right path.")
    if os.path.isdir(full):
        raise IsADirectoryError(
            f"{file_path} is a directory. Use Glob to list its files.")

    with open(full, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    total = len(lines)

    start = max(int(offset or 1), 1)
    count = int(limit) if limit else READ_LINE_CAP
    if total and start > total:
        raise ValueError(f"offset {start} is past the end of {file_path} "
                         f"({total} lines). Use a smaller offset.")

    shown = lines[start - 1:start - 1 + count]
    out = [f"{number:>6}\t{line[:READ_LINE_CHARS]}"
           for number, line in enumerate(shown, start)]
    last = start - 1 + len(shown)
    if last < total:
        out.append(f"...[TRUNCATED at line {last} of {total}]")

    config.READ_FILES.add(full)
    return "\n".join(out) if out else "(empty file)"


def Write(file_path, content):
    """Write a whole file, creating parent folders as needed."""
    full = _safe(file_path)
    snapshots.save(full)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(content)
    # The model has just supplied the full content, so it may Edit it next.
    config.READ_FILES.add(full)
    return f"Wrote {len(content)} characters to {file_path}"


def _flag(value):
    """Small models send booleans as strings; accept both."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def Edit(file_path, old_string, new_string, replace_all=False):
    """Replace an exact string in a file. ``old_string`` must be unique
    unless ``replace_all`` is set. The file must have been Read first."""
    full = _safe(file_path)
    if full not in config.READ_FILES:
        raise ValueError("You must Read this file before editing it. "
                         f"Call Read on {file_path} first.")
    if old_string == new_string:
        raise ValueError("old_string and new_string are identical, so "
                         "nothing would change. Provide the new text in "
                         "new_string.")
    if old_string == "":
        raise ValueError("old_string is empty. Copy the exact text to "
                         "replace from the Read output.")

    with open(full, encoding="utf-8") as handle:
        text = handle.read()

    found = text.count(old_string)
    if found == 0:
        raise ValueError("old_string not found in file. Read the file again "
                         "and copy the exact text, including whitespace and "
                         "indentation, without the line-number prefix.")
    if found > 1 and not _flag(replace_all):
        raise ValueError(f"old_string appears {found} times in the file; it "
                         "must be unique. Add surrounding lines to make it "
                         "unique, or set replace_all to true to replace "
                         "every occurrence.")

    # Rebuild the text so we know where each replacement landed.
    lines, cursor, built = [], 0, ""
    while True:
        index = text.find(old_string, cursor)
        if index == -1:
            break
        built += text[cursor:index]
        lines.append(built.count("\n") + 1)
        built += new_string
        cursor = index + len(old_string)
    built += text[cursor:]

    snapshots.save(full)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(built)

    where = ", ".join(str(n) for n in lines)
    plural = "s" if len(lines) > 1 else ""
    return (f"Replaced {len(lines)} occurrence{plural} in {file_path} "
            f"at line{plural} {where}.")


GLOB_CAP = 100


def _glob_regex(pattern):
    """Translate a glob with ``**`` support into a regex over "/" paths.
    ``**/`` spans zero or more folders, ``*`` stays within one."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _walk(root_dir):
    """Yield every file under *root_dir*, skipping SKIP_DIRS."""
    if os.path.isfile(root_dir):
        yield root_dir
        return
    for root, dirs, files in os.walk(root_dir):
        # .claude/worktrees/ holds full copies of the repo (Phase 9).
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not (d == "worktrees"
                            and os.path.basename(root) == ".claude")]
        for name in files:
            yield os.path.join(root, name)


def Glob(pattern, path="."):
    """Files under *path* matching *pattern* (e.g. ``**/*.py``), newest
    first, at most 100. Paths are relative to the project root."""
    root_dir = _safe(path)
    regex = _glob_regex(pattern.strip().lstrip("/"))
    found = []
    for full in _walk(root_dir):
        if regex.match(os.path.relpath(full, root_dir).replace(os.sep, "/")):
            try:
                found.append((os.path.getmtime(full), full))
            except OSError:
                continue
    if not found:
        return "No files found"
    found.sort(key=lambda item: item[0], reverse=True)
    shown = [_relative(full) for _, full in found[:GLOB_CAP]]
    if len(found) > GLOB_CAP:
        shown.append(f"...[showing {GLOB_CAP} of {len(found)} files]")
    return "\n".join(shown)


def Grep(pattern, path=".", glob=None, output_mode="content"):
    """Search file contents for a regex. ``glob`` limits which files are
    searched; ``output_mode`` is "content" (file:line: text) or
    "files_with_matches" (paths only)."""
    if output_mode not in {"content", "files_with_matches"}:
        raise ValueError('output_mode must be "content" or '
                         '"files_with_matches"')
    try:
        regex = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"Invalid regex {pattern!r}: {error}. Escape special "
                         "characters such as ( [ . * with a backslash.")
    root_dir = _safe(path)
    file_filter = _glob_regex(glob.strip()) if glob else None

    hits, files = [], []
    for full in _walk(root_dir):
        if file_filter:
            rel = os.path.relpath(full, root_dir).replace(os.sep, "/")
            if not (file_filter.match(rel)
                    or file_filter.match(os.path.basename(full))):
                continue
        try:
            with open(full, encoding="utf-8", errors="ignore") as handle:
                for number, line in enumerate(handle, 1):
                    if regex.search(line):
                        if output_mode == "files_with_matches":
                            files.append(_relative(full))
                            break
                        hits.append(f"{_relative(full)}:{number}: "
                                    f"{line.strip()[:200]}")
        except OSError:
            continue
        if len(hits) >= MAX_GREP_HITS:
            break

    if output_mode == "files_with_matches":
        return "\n".join(sorted(files)) or "No matches"
    return "\n".join(hits[:MAX_GREP_HITS]) or "No matches"
