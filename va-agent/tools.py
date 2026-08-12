"""The tools the agent is allowed to use. Everything is sandboxed."""

import os
import re
import subprocess

SANDBOX = os.path.abspath(os.environ.get("VA_SANDBOX", "./workspace"))
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
BLOCKED = ["rm -rf", "sudo", "shutdown", "mkfs", "dd if=", ":(){", "> /dev/"]

def _safe(path):
    """Reject any path that escapes the sandbox."""
    full = os.path.abspath(os.path.join(SANDBOX, path))
    if full != SANDBOX and not full.startswith(SANDBOX + os.sep):
        raise ValueError(f"Path '{path}' is outside the sandbox")
    return full

def list_files(path="."):
    root_dir = _safe(path)
    found = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            found.append(os.path.relpath(os.path.join(root, name), SANDBOX))
    return "\n".join(sorted(found)) or "(no files)"


def read_file(path):
    with open(_safe(path), encoding="utf-8", errors="ignore") as handle:
        text = handle.read()
    if len(text) > 50000:
        return text[:50000] + "\n...[TRUNCATED]"
    return text

def write_file(path, content):
    full = _safe(path)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(content)
    return f"Wrote {len(content)} characters to {path}"

def grep(pattern, path="."):
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
                            rel = os.path.relpath(full, SANDBOX)
                            hits.append(f"{rel}:{number}: {line.strip()[:200]}")
            except OSError:
                continue
    return "\n".join(hits[:50]) or "No matches"

def run_command(command):
    if any(bad in command for bad in BLOCKED):
        return "BLOCKED: this command is not allowed"
    try:
        done = subprocess.run(
            command, shell=True, cwd=SANDBOX,
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT after 60 seconds"
    return (f"exit_code={done.returncode}\n"
            f"STDOUT:\n{done.stdout[:5000]}\n"
            f"STDERR:\n{done.stderr[:5000]}")

REGISTRY = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "grep": grep,
    "run_command": run_command,
}

def _schema(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required},
    }}


SCHEMAS = [
      _schema("list_files", "List every file in the project.",
              {"path": {"type": "string", "description": "Folder, default '.'"}}, []),
      _schema("read_file", "Read the full contents of one file.",
              {"path": {"type": "string", "description": "File path"}}, ["path"]),
      _schema("write_file",
              "Write a file. You MUST provide the COMPLETE new file content. "
              "Never send a diff, a patch, or only the changed lines.",
              {"path": {"type": "string", "description": "File path"},
               "content": {"type": "string", "description": "Complete file content"}},
              ["path", "content"]),
      _schema("grep", "Search text across all files. Use this to locate code "
                      "before reading files.",
              {"pattern": {"type": "string", "description": "Regex or plain text"},
               "path": {"type": "string", "description": "Folder, default '.'"}},
              ["pattern"]),
      _schema("run_command", "Run a shell command in the project and see its output.",
              {"command": {"type": "string", "description": "Shell command"}},
              ["command"]),
  ]