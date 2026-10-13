"""The tools the agent is allowed to use.

Every path goes through :func:`_safe`, which reads ``config.SANDBOX`` at
call time so the evaluation harness can redirect the agent between runs.
Access control lives in :mod:`va_agent.harness.permissions`.
"""

import os
import re
import subprocess

from va_agent.core import config

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache"}
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