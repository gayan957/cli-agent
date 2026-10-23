"""Command line entry point."""

import os
import shlex
import subprocess
import sys

from va_agent.core import config
from va_agent.core import context
from va_agent.core import agent
from va_agent.core.agent import run
from va_agent.harness import hooks, permissions, rules, skills, snapshots
from va_agent.ui.console import banner, console, summary

HELP = """Commands:
  /mode            show the current permission mode
  /mode <name>     switch mode: default | acceptEdits | plan | bypassPermissions
  /rewind          list file snapshots and restore to before a step
  /rewind <step>   restore files to how they were before that step
  /rules           show global, injected and held project rules
  /skills          list skills, which are loaded, and invocations
  /context         show how full the context window is, by category
  /compact [focus] summarise the last task's conversation now
  /clear           fresh start: reset rules, skills, hooks, approvals, context
  /memory          open CLAUDE.md in $EDITOR (or print its path)
  /hooks           list configured hooks by event
  /<skill> [task]  load a skill (or .claude/commands file) and run the task
  /init            write a starter CLAUDE.md (never overwrites)
  /help            show this list
  exit, quit       leave va-agent
Anything else is sent to the agent as a task."""


def _consume_flags(argv):
    """Handle flags that must take effect before anything runs.
    Returns (remaining argv, mode from --mode or None)."""
    if "--print" in argv:
        argv.remove("--print")
        config.AUTO_APPROVE = True

    mode = None
    if "--mode" in argv:
        index = argv.index("--mode")
        if index + 1 >= len(argv) or argv[index + 1] not in permissions.MODES:
            raise SystemExit("--mode needs one of: "
                             + ", ".join(permissions.MODES))
        mode = argv[index + 1]
        del argv[index:index + 2]
    return argv, mode


def _slash_command(text):
    """Handle a /command. Returns True if *text* was one."""
    parts = text.split()
    if parts[0] == "/help":
        console.print(HELP)
    elif parts[0] == "/mode":
        if len(parts) == 1:
            console.print(f"mode: {config.MODE}")
        elif parts[1] in permissions.MODES:
            config.MODE = parts[1]
            console.print(f"[green]mode: {config.MODE}[/green]")
        else:
            console.print("[yellow]unknown mode; choose one of: "
                          + ", ".join(permissions.MODES) + "[/yellow]")
    elif parts[0] == "/rules":
        _show_rules()
    elif parts[0] == "/clear":
        permissions.reset_session()
        rules.reset_rules()
        hooks.reset_hooks()
        skills.reset_skills()
        context.reset_context()
        context.forget()
        console.print("cleared: conversation, approvals, injected rules, "
                      "loaded skills and context counters reset")
    elif parts[0] == "/context":
        _show_context()
    elif parts[0] == "/compact":
        _compact(text.partition(" ")[2].strip() or None)
    elif parts[0] == "/memory":
        _memory()
    elif parts[0] == "/skills":
        _show_skills()
    elif parts[0] == "/init":
        console.print(context.init_memory(), markup=False)
        context.load_memory()
    elif parts[0] == "/hooks":
        _show_hooks()
    elif parts[0] == "/rewind":
        _rewind(parts[1] if len(parts) > 1 else None)
    elif _skill_command(text):
        pass
    else:
        console.print(f"[yellow]unknown command {parts[0]}; try /help[/yellow]")
    return True


def _show_rules():
    """Global, injected, held and broken rules. A rule that never fires is
    this feature's main failure mode, so this is how to check. Empty
    sections are left out."""
    if not config.RULES_ENABLED:
        console.print("[yellow]rules are off (VA_RULES=0)[/yellow]")
        return
    every = config.RULES
    sections = [
        ("Global (always loaded):",
         [(r.name, "") for r in every if r.patterns is None]),
        ("Injected this session:",
         [(r.name, f"(matched {rules.matched_path(r)})"
                   if rules.matched_path(r) else "")
          for r in every
          if r.patterns is not None and r.name in config.RULES_INJECTED]),
        ("Held (not yet matched):",
         [(r.name, "paths: " + ", ".join(r.patterns))
          for r in every
          if r.patterns is not None and r.name not in config.RULES_INJECTED]),
        ("Broken (not loaded):",
         list(config.RULES_BROKEN)),
    ]
    sections = [(title, items) for title, items in sections if items]
    if not sections:
        console.print("no rules found in .claude/rules/")
        return
    width = max(len(name) for _, items in sections for name, _ in items) + 2
    for number, (title, items) in enumerate(sections):
        if number:
            console.print("")
        console.print(title, markup=False)
        for name, note in items:
            console.print(f"  {name.ljust(width)}{note}".rstrip(),
                          markup=False, highlight=False)


def _show_skills() -> None:
    """Available, loaded and broken skills plus the invocation log.
    Empty sections are left out; a broken skill always shows."""
    if not config.SKILLS_ENABLED:
        console.print("[yellow]skills are off (VA_SKILLS=0)[/yellow]")
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
    sections = [
        (f"Available ({len(available)}):",
         [(s.name, f"{s.origin:<9}{short(s.description)}")
          for s in available]),
        ("Loaded this session:",
         [(name, "") for name in sorted(config.SKILLS_LOADED)]),
        ("Broken (not loaded):",
         [(where(path), reason) for path, reason in config.SKILLS_BROKEN]),
        ("Invocations:",
         [(name, f'<- "{task}"') for name, task in config.SKILL_INVOCATIONS]),
    ]
    sections = [(title, items) for title, items in sections if items]
    if not sections:
        console.print("no skills found in .claude/skills/ or "
                      ".claude/commands/")
        return
    for number, (title, items) in enumerate(sections):
        if number:
            console.print("")
        console.print(title, markup=False)
        width = max(len(name) for name, _ in items) + 3
        for name, note in items:
            console.print(f"  {name.ljust(width)}{note}".rstrip(),
                          markup=False, highlight=False)


def _show_context() -> None:
    """Tokens in the window by category, against NUM_CTX."""
    messages = context.last_messages or [agent.system_message()]
    counts = context.breakdown(messages)
    total = context.estimate(messages)
    share = round(100 * total / config.NUM_CTX)
    console.print(f"Context: {total:,} / {config.NUM_CTX:,} tokens ({share}%)\n",
                  markup=False)
    rows = [("system prompt", counts["system"]),
            ("CLAUDE.md", counts["claude_md"]),
            ("skills index", counts["skills_index"]),
            (f"rules ({counts['rule_messages']} injected)", counts["rules"]),
            ("conversation", counts["conversation"])]
    for label, tokens in rows:
        console.print(f"  {label:<19}{tokens:>6,}", markup=False,
                      highlight=False)
    console.print("\n  (" + ("last task's conversation"
                             if context.last_messages
                             else "a new task's opening prompt") + ")",
                  markup=False)


def _compact(focus=None) -> None:
    """Compact the last task's conversation now and report the change."""
    if not context.last_messages:
        console.print("nothing to compact yet: run a task first")
        return
    before = context.estimate(context.last_messages)
    messages, did = context.compact(context.last_messages, focus=focus,
                                    trigger="manual")
    if not did:
        console.print(f"not compacted ({before:,} tokens): nothing to "
                      "summarise, or a PreCompact hook blocked it")
        return
    context.remember(messages)
    console.print(f"compacted: {before:,} -> {context.estimate(messages):,} "
                  "tokens", markup=False)


def _memory() -> None:
    """Open the project CLAUDE.md in $EDITOR, or print where it is."""
    root = config.SANDBOX
    path = os.path.join(root, "CLAUDE.md")
    if not os.path.exists(path) and os.path.exists(
            os.path.join(root, ".claude", "CLAUDE.md")):
        path = os.path.join(root, ".claude", "CLAUDE.md")
    editor = os.environ.get("EDITOR")
    if not os.path.exists(path):
        console.print(f"no CLAUDE.md yet at {path}; run /init to create one",
                      markup=False)
        return
    if not editor:
        console.print(f"CLAUDE.md: {path}  (set $EDITOR to open it here)",
                      markup=False)
        return
    try:
        subprocess.run([*shlex.split(editor), path])
    except OSError as error:
        console.print(f"[yellow]could not run {editor}: {error}[/yellow]")
        return
    context.load_memory()
    console.print("CLAUDE.md reloaded")


def _show_hooks() -> None:
    """Configured hooks grouped by event: matcher, command, timeout."""
    state = "enabled" if config.HOOKS_ENABLED else "disabled (VA_HOOKS=0)"
    console.print(f"Hooks are {state}.", markup=False)
    if not config.HOOKS:
        console.print("no hooks configured in .claude/settings.json",
                      markup=False)
        return
    for event in hooks.EVENTS:
        entries = config.HOOKS.get(event)
        if not entries:
            continue
        console.print(f"\n{event}:", markup=False)
        for entry in entries:
            matcher = entry["matcher"] or "*"
            for hook in entry["hooks"]:
                limit = hook["timeout"] or config.HOOK_TIMEOUT
                console.print(f"  [{matcher}]  {hook['command']}  "
                              f"(timeout {limit:g}s)",
                              markup=False, highlight=False)


def _rewind(step=None):
    """List snapshots, then restore files to before the chosen step,
    after confirming."""
    found = context.list_snapshots()
    if not found:
        console.print("no snapshots yet")
        return
    if step is None:
        for number, stamp, files in found:
            console.print(f"  step {number}  {stamp}  {', '.join(files)}",
                          markup=False)
        try:
            step = input("  Rewind to before which step? (Enter cancels) ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        step = step.strip()
        if not step:
            return
    if not step.isdigit():
        console.print("[yellow]step must be a number[/yellow]")
        return
    try:
        answer = input(f"  Restore files to how they were before step {step}? "
                       "This overwrites their current content. [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer.strip().lower() not in {"y", "yes"}:
        console.print("cancelled")
        return
    console.print(context.restore(int(step)), markup=False)


def _skill_command(text):
    """/name [task]: load skill *name* directly and run the rest of the
    line as the task. Returns False if no skill has that name."""
    skills.maybe_reload()
    head, _, rest = text.strip().partition(" ")
    name, rest = head[1:], rest.strip()
    if name not in config.SKILLS:
        return False
    body = skills.invoke(name, rest or head)
    _submit(rest or f"Follow the {name} skill's instructions.",
            [skills.message(name, body)])
    return True


def _submit(task, extra=None):
    """Run *task* unless a UserPromptSubmit hook denies it. *extra* is
    context to follow the task, e.g. a skill loaded with /name."""
    check = hooks.fire("UserPromptSubmit", {"prompt": task})
    if check.decision == "deny":
        console.print(f"[yellow]prompt blocked by hook: {check.reason}"
                      "[/yellow]")
        return
    summary(run(task, context=(extra or [])
                + hooks.context_messages("UserPromptSubmit", check)))


def main(argv=None):
    argv, mode = _consume_flags(list(sys.argv[1:] if argv is None else argv))
    permissions.load_settings()
    hooks.load_hooks()
    rules.discover()
    rules.reset_rules()
    skills.discover()
    skills.reset_skills()
    context.load_memory()
    context.reset_context()
    snapshots.new_session()
    if mode:
        config.MODE = mode          # the flag beats settings.json
    banner(config.SANDBOX, config.MODEL, config.MODE)
    hooks.start_session()
    try:
        return _session(argv)
    finally:
        hooks.fire("SessionEnd", {"reason": "exit"})


def _session(argv):
    if argv:
        text = " ".join(argv)
        if text.startswith("/"):
            _slash_command(text)
        else:
            _submit(text)
        return 0

    while True:
        try:
            task = input("\nva> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if task in {"exit", "quit"}:
            return 0
        if task.startswith("/"):
            _slash_command(task)
        elif task:
            _submit(task)


if __name__ == "__main__":
    sys.exit(main())
