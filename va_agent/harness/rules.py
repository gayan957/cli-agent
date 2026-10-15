"""Path-scoped rules from ``.claude/rules/``, as in Claude Code.

A rule is a markdown file. With no ``paths:``/``globs:`` frontmatter it is
global and goes into the system prompt. With patterns it is held back and
injected only when the agent first touches a matching file, which saves a
small model's context.

A rule with broken frontmatter is not loaded at all, rather than falling
back to global: otherwise a rule meant for one folder would silently apply
to every file. Broken rules are listed by /rules.

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

# patterns_of() result for a rule whose frontmatter or patterns are wrong.
BROKEN = object()

_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)",
                          re.DOTALL | re.MULTILINE)


@dataclass
class Rule:
    name: str                   # path relative to its rules dir
    patterns: list | None       # None means global
    body: str
    source: str                 # absolute path of the .md file


def parse(text):
    """Split a rule file into (meta, body). meta is {} when there is no
    frontmatter, and None when there is frontmatter but it is malformed
    or not a mapping. Never raises."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    try:
        meta = yaml.safe_load(match.group(1))
    except Exception:
        return None, body
    if meta is None:
        return {}, body              # an empty --- --- block
    return (meta if isinstance(meta, dict) else None), body


def _strip_quotes(entry):
    entry = entry.strip()
    while len(entry) >= 2 and entry[0] == entry[-1] and entry[0] in "\"'":
        entry = entry[1:-1].strip()
    return entry


def _entries(value):
    """Non-empty patterns from a string, comma-separated string or list."""
    items = value if isinstance(value, list) else [value]
    found = []
    for item in items:
        if item is None:
            continue
        for part in str(item).split(","):
            part = _strip_quotes(part)
            if part:
                found.append(part)
    return found


def patterns_of(meta):
    """A scoped rule's patterns, None for a global rule, or BROKEN when the
    frontmatter failed to parse or names paths/globs with nothing in them.
    Reads ``paths`` first, then ``globs``."""
    if meta is None:
        return BROKEN
    for key in ("paths", "globs"):
        if key in meta:
            found = _entries(meta[key])
            if found:
                return found
    if "paths" in meta or "globs" in meta:
        return BROKEN
    return None


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
    """(name, Rule or (name, reason)) for every rule file under one
    directory, recursively. A missing directory gives []."""
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
            patterns = patterns_of(meta)
            if patterns is BROKEN:
                reason = ("frontmatter failed to parse" if meta is None
                          else "paths:/globs: has no non-empty pattern")
                found.append((name, (name, reason)))
            else:
                found.append((name, Rule(name=name, patterns=patterns,
                                         body=body.strip(), source=source)))
    return found


def discover(project_dir=None):
    """Find project and user rules, store the valid ones in config.RULES
    and the broken ones in config.RULES_BROKEN, and return the valid ones.
    A project rule (even a broken one) shadows a user rule of the same
    name. With rules disabled nothing is scanned."""
    config.RULES = []
    config.RULES_BROKEN = []
    if not config.RULES_ENABLED:
        return []

    project = os.path.join(project_dir or config.SANDBOX, RULES_SUBDIR)
    by_name = dict(_scan(USER_RULES_DIR))
    by_name.update(_scan(project))

    for name in sorted(by_name):
        entry = by_name[name]
        if isinstance(entry, Rule):
            config.RULES.append(entry)
        else:
            config.RULES_BROKEN.append(entry)
            console.print(f"[yellow]  rules: {name} not loaded: {entry[1]}. "
                          "Quote patterns that start with *, e.g. "
                          "paths: \"**/*.py\"[/yellow]")
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
