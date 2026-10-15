"""Path-scoped rules from ``.claude/rules/``, as in Claude Code.

A rule is a markdown file. With no ``paths:``/``globs:`` frontmatter it is
global and goes into the system prompt. With patterns it is held back and
injected only when the agent first touches a matching file, which saves a
small model's context.

Rules are context only. They never grant or deny permission; that stays in
harness/permissions.py.
"""

import functools
import os
import re
from dataclasses import dataclass

import yaml

from va_agent.core import config
from va_agent.ui.console import console

RULES_SUBDIR = os.path.join(".claude", "rules")

# User-level rules. A module attribute so tests can point it elsewhere.
USER_RULES_DIR = os.path.join(os.path.expanduser("~"), RULES_SUBDIR)

_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)",
                          re.DOTALL | re.MULTILINE)


@dataclass
class Rule:
    name: str                   # path relative to its rules dir
    patterns: list | None       # None means global
    body: str
    source: str                 # absolute path of the .md file


def parse(text):
    """Split a rule file into (frontmatter dict, body). Malformed YAML
    warns and counts as no frontmatter; it never raises."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        first = str(error).splitlines()[0] if str(error) else "invalid YAML"
        console.print(f"[yellow]  rules: bad frontmatter ignored ({first}). "
                      "Quote patterns that start with *, e.g. "
                      "paths: \"**/*.py\"[/yellow]")
        return {}, body
    return (meta if isinstance(meta, dict) else {}), body


def _strip_quotes(entry):
    entry = entry.strip()
    while len(entry) >= 2 and entry[0] == entry[-1] and entry[0] in "\"'":
        entry = entry[1:-1].strip()
    return entry


def patterns_of(meta):
    """The rule's patterns, or None for a global rule. Reads ``paths``,
    then ``globs``; accepts a string, a comma-separated string, or a list."""
    value = meta.get("paths")
    if value in (None, "", []):
        value = meta.get("globs")
    if value in (None, "", []):
        return None

    items = value if isinstance(value, list) else [value]
    found = []
    for item in items:
        if item is None:
            continue
        for part in str(item).split(","):
            part = _strip_quotes(part)
            if part:
                found.append(part)
    return found or None


@functools.lru_cache(maxsize=None)
def _to_regex(pattern):
    """Glob to regex. ``*`` and ``?`` stay within one folder, ``**``
    crosses folders, and ``**/`` also matches zero folders. Not fnmatch:
    its ``*`` crosses "/", so ``tests/*`` would match ``tests/sub/x.py``."""
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i:i + 3] == "**/":
                out.append(r"(?:.*/)?")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
        elif char == "?":
            out.append(r"[^/]")
        elif char in ".^$+{}[]()|\\":
            out.append("\\" + char)
        else:
            out.append(char)
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _strip_dot_slash(path):
    # Not lstrip("./"): that strips a character set, turning ".env" into "env".
    while path.startswith("./"):
        path = path[2:]
    return path


def matches(path, pattern):
    """gitignore-style match: a pattern without "/" matches the basename
    anywhere; a pattern with "/" is anchored to the whole path."""
    path = _strip_dot_slash(str(path).replace(os.sep, "/"))
    pattern = _strip_dot_slash(pattern.strip())
    regex = _to_regex(pattern)
    if regex.match(path):
        return True
    return "/" not in pattern and bool(regex.match(path.rsplit("/", 1)[-1]))


def _scan(rules_dir):
    """Rules under one directory, recursively. Missing dir gives []."""
    if not os.path.isdir(rules_dir):
        return []
    found = []
    for root, dirs, files in os.walk(rules_dir):
        dirs.sort()
        for filename in sorted(files):
            if not filename.endswith(".md"):
                continue
            source = os.path.abspath(os.path.join(root, filename))
            try:
                with open(source, encoding="utf-8") as handle:
                    text = handle.read()
            except OSError as error:
                console.print(f"[yellow]  rules: cannot read {source}: "
                              f"{error}[/yellow]")
                continue
            meta, body = parse(text)
            name = os.path.relpath(source, rules_dir).replace(os.sep, "/")
            found.append(Rule(name=name, patterns=patterns_of(meta),
                              body=body.strip(), source=source))
    return found


def discover(project_dir=None):
    """Find project and user rules, store them in config.RULES and return
    them. A project rule shadows a user rule with the same name."""
    project = os.path.join(project_dir or config.SANDBOX, RULES_SUBDIR)
    by_name = {rule.name: rule for rule in _scan(USER_RULES_DIR)}
    for rule in _scan(project):
        by_name[rule.name] = rule
    config.RULES = sorted(by_name.values(), key=lambda rule: rule.name)
    return list(config.RULES)


# Which path first triggered each injected rule, for /rules.
_matched = {}


def _relative(path):
    """*path* relative to the project root when it is inside it."""
    root = os.path.realpath(config.SANDBOX)
    full = os.path.realpath(os.path.join(root, os.path.expanduser(str(path))))
    if full == root:
        return "."
    if full.startswith(root + os.sep):
        return os.path.relpath(full, root).replace(os.sep, "/")
    return str(path)


def global_rules():
    """Rules with no patterns: always loaded into the system prompt."""
    if not config.RULES_ENABLED:
        return []
    return [rule for rule in config.RULES if rule.patterns is None]


def rules_for(path):
    """Path-scoped rules matching *path* that have not been injected yet."""
    if not config.RULES_ENABLED:
        return []
    target = _relative(path)
    return [rule for rule in config.RULES
            if rule.patterns is not None
            and rule.name not in config.RULES_INJECTED
            and any(matches(target, pattern) for pattern in rule.patterns)]


def mark_injected(rule, path=None):
    """Record that *rule* was injected, so it is not injected again."""
    config.RULES_INJECTED.add(rule.name)
    if path is not None:
        _matched[rule.name] = _relative(path)


def matched_path(rule):
    """The path that first injected *rule* this session, if known."""
    return _matched.get(rule.name)


def reset_session():
    """Make every path-scoped rule injectable again."""
    config.RULES_INJECTED.clear()
    _matched.clear()


def system_text():
    """Global rule bodies, formatted for the end of the system prompt."""
    return "".join(f"\n\n[project rule: {rule.name}]\n\n{rule.body}"
                   for rule in global_rules())
