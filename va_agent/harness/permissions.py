"""Permission decisions, approval prompts and undo checkpoints.

Modelled on Claude Code: working-directory scoping, four modes and
allow/deny rules (optionally from ``.claude/settings.json``).

Everything reads ``config.NAME`` at call time, never at import time, so the
evaluation harness can change SANDBOX, AUTO_APPROVE and the rules between
runs.
"""

import fnmatch
import json
import os
import re
import subprocess

from va_agent.core import config
from va_agent.ui.console import console, describe_call

MODES = ("default", "acceptEdits", "plan", "bypassPermissions")

READ_ONLY = {"list_files", "read_file", "grep"}
MUTATING = {"write_file", "run_command"}
EDIT_TOOLS = {"write_file"}
PATH_TOOLS = {"list_files", "read_file", "write_file", "grep"}

# Claude Code tool names each project tool also answers to in rules.
ALIASES = {
    "read_file": {"Read"},
    "write_file": {"Write", "Edit"},
    "run_command": {"Bash"},
    "list_files": {"Glob", "LS"},
    "grep": {"Grep"},
}

_RULE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*$", re.DOTALL)
_CHAIN = re.compile(r"&&|\|\||;|\|")

# Tools the user answered [a]lways for during this session.
_approved = set()


# ---------------------------------------------------------------- paths

def _roots():
    roots = [config.SANDBOX, *config.WORKING_DIRS]
    return [os.path.realpath(root) for root in roots]


def _resolve(path):
    root = os.path.realpath(config.SANDBOX)
    return os.path.realpath(os.path.join(root, os.path.expanduser(str(path))))


def classify_path(path):
    """Return "inside" if *path* resolves under SANDBOX or a WORKING_DIRS
    entry, else "outside". Uses realpath so a symlink cannot escape.
    Never raises: anything unresolvable counts as outside."""
    try:
        full = _resolve(path)
        for root in _roots():
            if full == root or full.startswith(root.rstrip(os.sep) + os.sep):
                return "inside"
    except Exception:
        pass
    return "outside"


# ---------------------------------------------------------------- rules

def _parse_rule(rule):
    """Split "Tool(pattern)" into (tool, pattern); pattern is None if bare."""
    match = _RULE.match(str(rule))
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _tool_matches(rule_tool, name):
    return rule_tool == name or rule_tool in ALIASES.get(name, set())


def _segments(command):
    """Split a shell command on &&, ||, ; and | into its parts."""
    return [part.strip() for part in _CHAIN.split(command) if part.strip()]


def _command_matches(pattern, segment):
    pattern = pattern.strip()
    if pattern.endswith(":*"):
        prefix = pattern[:-2].strip()
        return segment == prefix or segment.startswith(prefix + " ")
    return fnmatch.fnmatchcase(segment, pattern)


def _strip_dot_slash(path):
    # Not lstrip("./"): that strips a character set and turns ".env" into
    # "env", so dotfile rules would silently never fire.
    while path.startswith("./"):
        path = path[2:]
    return path


def _path_candidates(path):
    """The forms of *path* a pattern is tried against."""
    given = _strip_dot_slash(str(path))
    found = {given, os.path.basename(given.rstrip("/"))}
    full = _resolve(path)
    root = os.path.realpath(config.SANDBOX)
    if full.startswith(root + os.sep):
        found.add(os.path.relpath(full, root))
    found.add(full)
    found.discard("")
    return found


def _path_matches(pattern, path):
    pattern = _strip_dot_slash(pattern.strip())
    patterns = [pattern]
    # fnmatch has no "**": also try without a leading "**/" so that
    # "**/.env*" matches a .env at the project root.
    if pattern.startswith("**/"):
        patterns.append(pattern[3:])

    for candidate in _path_candidates(path):
        for pat in patterns:
            if pat.endswith("/**"):
                prefix = pat[:-3]
                if candidate == prefix or candidate.startswith(prefix + "/"):
                    return True
            if fnmatch.fnmatchcase(candidate, pat):
                return True
    return False


def _rule_hits(rule, name, arguments, segment=None):
    """Does *rule* match this call (or this one command segment)?"""
    rule_tool, pattern = _parse_rule(rule)
    if rule_tool is None or not _tool_matches(rule_tool, name):
        return False
    if pattern is None or pattern.strip() in ("", "*"):
        return True
    if name == "run_command":
        return _command_matches(pattern, segment or "")
    if name in PATH_TOOLS:
        return _path_matches(pattern, arguments.get("path", "."))
    return False


def _denied_by_rule(name, arguments):
    rules = config.DENY_RULES
    if name == "run_command":
        # Any one denied segment denies the whole chain.
        for segment in _segments(str(arguments.get("command", ""))) or [""]:
            for rule in rules:
                if _rule_hits(rule, name, arguments, segment):
                    return rule
        return None
    for rule in rules:
        if _rule_hits(rule, name, arguments):
            return rule
    return None


def _allowed_by_rule(name, arguments):
    rules = config.ALLOW_RULES
    if name == "run_command":
        # Every segment must be allowed, or "pytest && curl x" slips through.
        segments = _segments(str(arguments.get("command", "")))
        if not segments:
            return None
        hits = []
        for segment in segments:
            rule = next((r for r in rules
                         if _rule_hits(r, name, arguments, segment)), None)
            if rule is None:
                return None
            hits.append(rule)
        return hits[0]
    for rule in rules:
        if _rule_hits(rule, name, arguments):
            return rule
    return None


# ---------------------------------------------------------------- decision

def check(name, arguments):
    """Decide a tool call. Returns (decision, reason) where decision is
    "allow", "deny" or "ask". Order matters; first match wins."""
    arguments = arguments or {}
    unattended = config.AUTO_APPROVE or config.MODE == "bypassPermissions"

    rule = _denied_by_rule(name, arguments)
    if rule:
        return "deny", f"deny rule {rule}"

    if config.MODE == "plan" and name in MUTATING:
        return "deny", "plan mode is read-only"

    if name in PATH_TOOLS:
        path = arguments.get("path", ".")
        if classify_path(path) == "outside":
            if unattended:
                # Unattended runs must never reach outside their workdir.
                return "deny", f"{path} is outside the working directories"
            return "ask", f"{path} is outside the working directories"

    rule = _allowed_by_rule(name, arguments)
    if rule:
        return "allow", f"allow rule {rule}"

    if unattended:
        return "allow", ("auto-approve" if config.AUTO_APPROVE
                         else "bypassPermissions mode")
    if name in READ_ONLY:
        return "allow", "read-only tool"
    if config.MODE == "acceptEdits" and name in EDIT_TOOLS:
        return "allow", "acceptEdits mode"

    if name in _approved:
        return "allow", "approved for this session"

    return "ask", f"{name} needs approval"


def approve(name, arguments):
    """Return True if this tool call may proceed, prompting if needed."""
    decision, reason = check(name, arguments)
    if decision == "allow":
        return True
    if decision == "deny":
        console.print(f"[yellow]  denied: {reason}[/yellow]")
        return False

    console.print("")
    console.print(describe_call(name, arguments))
    console.print(f"[dim]  {reason}[/dim]")
    try:
        answer = input("  Allow? [y]es / [a]lways / [N]o ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer in {"a", "always"}:
        _approved.add(name)
        return True
    return answer in {"y", "yes"}


def reset_session():
    """Forget every [a]lways approval. Called before each benchmark run."""
    _approved.clear()


# ---------------------------------------------------------------- settings

def _warn(message):
    console.print(f"[yellow]  settings: {message}[/yellow]")


def _string_list(value, key):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        _warn(f"'{key}' must be a list of strings; ignored")
        return []
    return list(value)


def load_settings(project_dir=None):
    """Read <project_dir or SANDBOX>/.claude/settings.json and apply its
    "permissions" block to config. Missing file returns {} silently;
    malformed JSON warns and returns {}. Never raises."""
    base = project_dir or config.SANDBOX
    path = os.path.join(base, ".claude", "settings.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, ValueError) as error:
        _warn(f"could not read {path}: {error}")
        return {}
    if not isinstance(settings, dict):
        _warn(f"{path} is not a JSON object; ignored")
        return {}

    block = settings.get("permissions") or {}
    if not isinstance(block, dict):
        _warn("'permissions' must be an object; ignored")
        return settings

    mode = block.get("defaultMode")
    if mode is not None:
        if mode in MODES:
            config.MODE = mode
        else:
            _warn(f"unknown defaultMode {mode!r}; keeping {config.MODE}")

    config.ALLOW_RULES = _string_list(block.get("allow"), "allow")
    config.DENY_RULES = _string_list(block.get("deny"), "deny")
    config.WORKING_DIRS = [
        os.path.realpath(os.path.join(base, os.path.expanduser(d)))
        for d in _string_list(block.get("additionalDirectories"),
                              "additionalDirectories")]
    return settings


# ---------------------------------------------------------------- undo

def checkpoint(label):
    """Commit the sandbox so the run can be undone with git."""
    if not os.path.isdir(os.path.join(config.SANDBOX, ".git")):
        return "no git repo in sandbox"
    subprocess.run(["git", "add", "-A"],
                   cwd=config.SANDBOX, capture_output=True)
    subprocess.run(["git",
                    "-c", "user.email=va@local",
                    "-c", "user.name=va-agent",
                    "commit", "-q", "-m", f"before: {label[:60]}"],
                   cwd=config.SANDBOX, capture_output=True)
    return "checkpoint saved"
