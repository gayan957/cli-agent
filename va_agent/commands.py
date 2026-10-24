"""Slash commands: one registry, so /help is generated rather than kept by
hand and adding a command is one register() call.

A line that is not a registered command but names a skill runs that skill
(Phase 7). Anything that does not start with "/" is not a command at all
and goes to the agent.
"""

import os
import shlex
import subprocess
import time
from dataclasses import dataclass

from va_agent.core import config
from va_agent.core import context
from va_agent.core import session
from va_agent.harness import hooks, permissions, rules, skills
from va_agent.ui.console import console


@dataclass
class Command:
    name: str
    args: str
    help: str
    handler: object     # called with the text after the command name
    group: str


COMMANDS = {}
GROUP_ORDER = ("Session", "Context", "Project", "Agent")


def register(name: str, help_text: str, handler, args: str = "",
             group: str = "Session") -> None:
    COMMANDS[name] = Command(name=name, args=args, help=help_text,
                             handler=handler, group=group)


def _say(text: str) -> None:
    console.print(text, markup=False, highlight=False)


def help_text() -> str:
    """Every registered command, grouped, plus the current skills."""
    lines = ["Commands:"]
    groups = sorted({c.group for c in COMMANDS.values()},
                    key=lambda g: (GROUP_ORDER.index(g)
                                   if g in GROUP_ORDER else len(GROUP_ORDER), g))
    for group in groups:
        lines.append(f"\n{group}")
        for command in sorted((c for c in COMMANDS.values() if c.group == group),
                              key=lambda c: c.name):
            usage = f"/{command.name} {command.args}".rstrip()
            lines.append(f"  {usage:<26}{command.help}")
    available = [name for name in config.SKILLS if name not in COMMANDS]
    if available:
        lines.append("\nSkills (/<name> [task] loads one and runs the task)")
        lines.append("  " + "  ".join(f"/{name}" for name in available))
    lines.append("\nexit or quit leaves va-agent. Anything else is a task.")
    return "\n".join(lines)


def _suggest(name: str) -> str | None:
    """The known command or skill sharing the longest prefix with *name*."""
    known = list(COMMANDS) + [s for s in config.SKILLS if s not in COMMANDS]
    starts = sorted(k for k in known if k.startswith(name))
    if name and starts:
        return starts[0]

    def shared(other):
        count = 0
        for a, b in zip(name, other):
            if a != b:
                break
            count += 1
        return count

    best = max(known, key=lambda k: (shared(k), -len(k)), default=None)
    return best if best and shared(best) else None


def dispatch(line: str) -> bool:
    """Run *line* if it is a /command (or /skill). Returns False for any
    line that does not start with "/", so it goes to the agent."""
    line = line.strip()
    if not line.startswith("/"):
        return False
    head, _, rest = line.partition(" ")
    name, rest = head[1:], rest.strip()
    command = COMMANDS.get(name)
    if command:
        command.handler(rest)
        return True
    if _run_skill(name, rest):
        return True
    guess = _suggest(name)
    hint = f" Did you mean /{guess}?" if guess else ""
    console.print(f"[yellow]unknown command /{name}.{hint} "
                  f"Run /help to list commands.[/yellow]")
    return True


def _submit(task, extra=None):
    from va_agent import cli         # at call time: cli imports this module
    cli._submit(task, extra)


def _run_skill(name: str, rest: str) -> bool:
    """/name [task]: load a skill directly and run the task with it."""
    skills.maybe_reload()
    if name not in config.SKILLS:
        return False
    body = skills.invoke(name, rest or f"/{name}")
    _submit(rest or f"Follow the {name} skill's instructions.",
            [skills.message(name, body)])
    return True


# ---------------------------------------------------------------- Session

def _help(_args):
    _say(help_text())


def _clear(_args):
    """A fresh start that keeps the CLI running: every sibling reset."""
    permissions.reset_session()
    rules.reset_rules()
    hooks.reset_hooks()
    skills.reset_skills()
    context.reset_context()
    session.reset_session_state()
    context.forget()
    _say(f"cleared: new conversation and session {config.SESSION_ID}")


def _mode(args):
    if not args:
        _say(f"mode: {config.MODE}")
    elif args in permissions.MODES:
        config.MODE = args
        _say(f"mode: {config.MODE}")
    else:
        _say("unknown mode; choose one of: " + ", ".join(permissions.MODES))


def _age(stamp) -> str:
    seconds = max(0, time.time() - (stamp or 0))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def resume_into(data: dict) -> None:
    """Make a saved session the current conversation."""
    messages = session.restore(data)
    context.remember(messages)
    # Rules already in the saved conversation must not inject twice.
    for message in messages:
        content = str(message.get("content") or "")
        if content.startswith(context.RULE_MARK) and "]" in content:
            config.RULES_INJECTED.add(
                content[len(context.RULE_MARK):content.index("]")].strip())
    _say(f"resumed {data['session_id']}: {len(messages)} messages - "
         f"{data.get('first_task') or '(no task yet)'}")


def pick_session():
    """Numbered list of saved sessions; returns the chosen one or None."""
    found = session.list_sessions()
    if not found:
        _say("no saved sessions here")
        return None
    for number, item in enumerate(found, 1):
        _say(f"  {number:>2}. {item.get('first_task') or '(no task)'}  "
             f"({_age(item.get('updated'))}, {item['message_count']} messages)")
    try:
        choice = input("  Resume which? (Enter cancels) ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not choice.isdigit() or not 1 <= int(choice) <= len(found):
        return None
    return session.load(found[int(choice) - 1]["session_id"])


def _resume(args):
    data = session.load(args) if args else pick_session()
    if data:
        resume_into(data)


# ---------------------------------------------------------------- Context

def _context(_args):
    """Tokens in the window by category, against NUM_CTX."""
    from va_agent.core import agent
    messages = context.last_messages or [agent.system_message()]
    counts = context.breakdown(messages)
    total = context.estimate(messages)
    share = round(100 * total / config.NUM_CTX)
    _say(f"Context: {total:,} / {config.NUM_CTX:,} tokens ({share}%)\n")
    rows = [("system prompt", counts["system"]),
            ("CLAUDE.md", counts["claude_md"]),
            ("skills index", counts["skills_index"]),
            (f"rules ({counts['rule_messages']} injected)", counts["rules"]),
            ("conversation", counts["conversation"])]
    for label, tokens in rows:
        _say(f"  {label:<19}{tokens:>6,}")


def _compact(args):
    """Compact the session's conversation now and report the change."""
    if not context.last_messages:
        _say("nothing to compact yet: run a task first")
        return
    before = context.estimate(context.last_messages)
    messages, did = context.compact(context.last_messages, focus=args or None,
                                    trigger="manual")
    if not did:
        _say(f"not compacted ({before:,} tokens): nothing to summarise, or "
             "a PreCompact hook blocked it")
        return
    context.remember(messages)
    session.save(messages)
    _say(f"compacted: {before:,} -> {context.estimate(messages):,} tokens")


def _memory(_args):
    """Open the project CLAUDE.md in $EDITOR, or print where it is."""
    root = config.SANDBOX
    path = os.path.join(root, "CLAUDE.md")
    if not os.path.exists(path) and os.path.exists(
            os.path.join(root, ".claude", "CLAUDE.md")):
        path = os.path.join(root, ".claude", "CLAUDE.md")
    if not os.path.exists(path):
        _say(f"no CLAUDE.md yet at {path}; run /init to create one")
        return
    editor = os.environ.get("EDITOR")
    if not editor:
        _say(f"CLAUDE.md: {path}  (set $EDITOR to open it here)")
        return
    try:
        subprocess.run([*shlex.split(editor), path])
    except OSError as error:
        _say(f"could not run {editor}: {error}")
        return
    context.load_memory()
    _say("CLAUDE.md reloaded")


def _init(_args):
    _say(context.init_memory())
    context.load_memory()


def _rewind(args):
    """List snapshots, then restore files to before the chosen step,
    after confirming."""
    found = context.list_snapshots()
    if not found:
        _say("no snapshots yet")
        return
    step = args
    if not step:
        for number, stamp, files in found:
            _say(f"  step {number}  {stamp}  {', '.join(files)}")
        try:
            step = input("  Rewind to before which step? (Enter cancels) ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        step = step.strip()
        if not step:
            return
    if not step.isdigit():
        _say("step must be a number")
        return
    try:
        answer = input(f"  Restore files to how they were before step {step}? "
                       "This overwrites their current content. [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer.strip().lower() not in {"y", "yes"}:
        _say("cancelled")
        return
    _say(context.restore(int(step)))


# ---------------------------------------------------------------- Project

def _sections(sections, empty):
    sections = [(title, items) for title, items in sections if items]
    if not sections:
        _say(empty)
        return
    for number, (title, items) in enumerate(sections):
        if number:
            _say("")
        _say(title)
        width = max(len(name) for name, _ in items) + 3
        for name, note in items:
            _say(f"  {name.ljust(width)}{note}".rstrip())


def _rules(_args):
    """Global, injected, held and broken rules."""
    if not config.RULES_ENABLED:
        _say("rules are off (VA_RULES=0)")
        return
    every = config.RULES
    _sections([
        ("Global (always loaded):",
         [(r.name, "") for r in every if r.patterns is None]),
        ("Injected this session:",
         [(r.name, f"(matched {rules.matched_path(r)})"
                   if rules.matched_path(r) else "")
          for r in every
          if r.patterns is not None and r.name in config.RULES_INJECTED]),
        ("Held (not yet matched):",
         [(r.name, "paths: " + ", ".join(r.patterns)) for r in every
          if r.patterns is not None and r.name not in config.RULES_INJECTED]),
        ("Broken (not loaded):", list(config.RULES_BROKEN)),
    ], "no rules found in .claude/rules/")


def _skills(_args):
    """Available, loaded and broken skills plus the invocation log."""
    if not config.SKILLS_ENABLED:
        _say("skills are off (VA_SKILLS=0)")
        return
    skills.maybe_reload()

    def short(text, width=60):
        return text if len(text) <= width else text[:width - 3] + "..."

    def where(path):
        root = os.path.realpath(config.SANDBOX)
        real = os.path.realpath(path)
        return (os.path.relpath(real, root)
                if real.startswith(root + os.sep) else path)

    available = list(config.SKILLS.values())
    _sections([
        (f"Available ({len(available)}):",
         [(s.name, f"{s.origin:<9}{short(s.description)}") for s in available]),
        ("Loaded this session:",
         [(name, "") for name in sorted(config.SKILLS_LOADED)]),
        ("Broken (not loaded):",
         [(where(path), reason) for path, reason in config.SKILLS_BROKEN]),
        ("Invocations:",
         [(name, f'<- "{task}"') for name, task in config.SKILL_INVOCATIONS]),
    ], "no skills found in .claude/skills/ or .claude/commands/")


def _hooks(_args):
    """Configured hooks grouped by event: matcher, command, timeout."""
    state = "enabled" if config.HOOKS_ENABLED else "disabled (VA_HOOKS=0)"
    _say(f"Hooks are {state}.")
    if not config.HOOKS:
        _say("no hooks configured in .claude/settings.json")
        return
    for event in hooks.EVENTS:
        entries = config.HOOKS.get(event)
        if not entries:
            continue
        _say(f"\n{event}:")
        for entry in entries:
            for hook in entry["hooks"]:
                limit = hook["timeout"] or config.HOOK_TIMEOUT
                _say(f"  [{entry['matcher'] or '*'}]  {hook['command']}  "
                     f"(timeout {limit:g}s)")


register("help", "list every command (this list)", _help)
register("clear", "new conversation and session; every reset", _clear)
register("resume", "switch to a saved session (pick from a list)", _resume,
         args="[id]")
register("mode", "show or set the permission mode", _mode, args="[name]",
         group="Agent")
register("context", "how full the context window is, by category", _context,
         group="Context")
register("compact", "summarise the conversation now", _compact,
         args="[focus]", group="Context")
register("memory", "open CLAUDE.md in $EDITOR", _memory, group="Context")
register("init", "write a starter CLAUDE.md (never overwrites)", _init,
         group="Context")
register("rewind", "list snapshots or restore files to before a step",
         _rewind, args="[step]", group="Context")
register("rules", "global, injected, held and broken rules", _rules,
         group="Project")
register("skills", "skills, which are loaded, and invocations", _skills,
         group="Project")
register("hooks", "configured hooks by event", _hooks, group="Project")
