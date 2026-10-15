"""Path-scoped rules from ``.claude/rules/``, as in Claude Code.

A rule is a markdown file. With no ``paths:``/``globs:`` frontmatter it is
global and goes into the system prompt. With patterns it is held back and
injected only when the agent first touches a matching file, which saves a
small model's context.

Rules are context only. They never grant or deny permission; that stays in
harness/permissions.py.
"""

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
