"""The tools the agent is allowed to use.

Every path goes through :func:`_safe`, which reads ``config.SANDBOX`` at
call time so the evaluation harness can redirect the agent between runs.
Access control lives in :mod:`va_agent.harness.permissions`.
"""

import os
import re
import subprocess

from va_agent.core import config

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache",
             ".va"}
BLOCKED = ["rm -rf", "sudo", "shutdown", "mkfs", "dd if=", ":(){", "> /dev/"]

MAX_READ_CHARS = 50_000
MAX_GREP_HITS = 50
MAX_OUTPUT_CHARS = 5_000
COMMAND_TIMEOUT = 60


def _safe(path):
    """Resolve *path* against the sandbox. Never blocks: whether an outside
    path may be used is decided by harness.permissions before a tool runs."""
    root = os.path.realpath(config.SANDBOX)
    return os.path.realpath(os.path.join(root, os.path.expanduser(path)))


def _relative(full):
    return os.path.relpath(full, os.path.realpath(config.SANDBOX))


def list_files(path="."):
    """List every file in the project, relative to the sandbox root."""
    root_dir = _safe(path)
    found = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            found.append(_relative(os.path.join(root, name)))
    return "\n".join(sorted(found)) or "(no files)"


def read_file(path):
    """Return the full contents of one file."""
    with open(_safe(path), encoding="utf-8", errors="ignore") as handle:
        text = handle.read()
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + "\n...[TRUNCATED]"
    return text


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
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
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


def write_file(path, content):
    """Write *content* to *path*, creating parent folders as needed."""
    full = _safe(path)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(content)
    return f"Wrote {len(content)} characters to {path}"


def grep(pattern, path="."):
    """Search for *pattern* across files, returning file:line: text hits."""
    root_dir = _safe(path)
    hits = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            full = os.path.join(root, name)
            try:
                with open(full, encoding="utf-8", errors="ignore") as handle:
                    for number, line in enumerate(handle, 1):
                        if re.search(pattern, line):
                            hits.append(
                                f"{_relative(full)}:{number}: {line.strip()[:200]}")
            except OSError:
                continue
            if len(hits) >= MAX_GREP_HITS:
                break
    return "\n".join(hits[:MAX_GREP_HITS]) or "No matches"


def run_command(command):
    """Run a shell command inside the sandbox and return its output."""
    if any(bad in command for bad in BLOCKED):
        return "BLOCKED: this command is not allowed"
    try:
        done = subprocess.run(
            command, shell=True, cwd=config.SANDBOX,
            capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {COMMAND_TIMEOUT} seconds"
    except OSError as error:
        return f"ERROR: {error}"
    return (f"exit_code={done.returncode}\n"
            f"STDOUT:\n{done.stdout[:MAX_OUTPUT_CHARS]}\n"
            f"STDERR:\n{done.stderr[:MAX_OUTPUT_CHARS]}")


REGISTRY = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "grep": grep,
    "run_command": run_command,
}


def _schema(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object",
                       "properties": properties,
                       "required": required},
    }}


SCHEMAS = [
    _schema("list_files",
            "List every file in the project.",
            {"path": {"type": "string",
                      "description": "Folder to list, default '.'"}},
            []),
    _schema("read_file",
            "Read the full contents of one file.",
            {"path": {"type": "string", "description": "File path"}},
            ["path"]),
    _schema("write_file",
            "Write a file. You MUST provide the COMPLETE new file content. "
            "Never send a diff, a patch, or only the changed lines.",
            {"path": {"type": "string", "description": "File path"},
             "content": {"type": "string",
                         "description": "Complete new file content"}},
            ["path", "content"]),
    _schema("grep",
            "Search text across all files. Use this to locate code before "
            "reading files.",
            {"pattern": {"type": "string",
                         "description": "Regex or plain text to find"},
             "path": {"type": "string",
                      "description": "Folder to search, default '.'"}},
            ["pattern"]),
    _schema("run_command",
            "Run a shell command in the project and see its output.",
            {"command": {"type": "string", "description": "Shell command"}},
            ["command"]),
]