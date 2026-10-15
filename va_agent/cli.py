"""Command line entry point."""

import sys

from va_agent.core import config
from va_agent.core.agent import run
from va_agent.harness import permissions, rules, snapshots
from va_agent.ui.console import banner, console, summary

HELP = """Commands:
  /mode            show the current permission mode
  /mode <name>     switch mode: default | acceptEdits | plan | bypassPermissions
  /rewind          list file snapshots and restore to before a step
  /rewind <step>   restore files to how they were before that step
  /rules           show global, injected and held project rules
  /clear           forget which rules were injected this session
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
        rules.reset_rules()
        console.print("cleared: path-scoped rules can inject again")
    elif parts[0] == "/rewind":
        _rewind(parts[1] if len(parts) > 1 else None)
    else:
        console.print(f"[yellow]unknown command {parts[0]}; try /help[/yellow]")
    return True


def _show_rules():
    """Global, injected and held rules. A rule that never fires is this
    feature's main failure mode, so this is how to check."""
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
    ]
    width = max((len(name) for _, items in sections for name, _ in items),
                default=0) + 2
    for number, (title, items) in enumerate(sections):
        if number:
            console.print("")
        console.print(title, markup=False)
        for name, note in items or [("(none)", "")]:
            console.print(f"  {name.ljust(width)}{note}".rstrip(),
                          markup=False, highlight=False)


def _rewind(step=None):
    """List snapshots, then restore files to before the chosen step."""
    found = snapshots.list_snapshots()
    if not found:
        console.print("no snapshots yet")
        return
    if step is None:
        for number, paths in found:
            console.print(f"  step {number}: {', '.join(paths)}")
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
    done = snapshots.rewind(int(step))
    for line in done:
        console.print(f"  {line}")
    console.print(f"[green]rewound {len(done)} file change(s)[/green]")


def main(argv=None):
    argv, mode = _consume_flags(list(sys.argv[1:] if argv is None else argv))
    permissions.load_settings()
    rules.discover()
    rules.reset_rules()
    snapshots.new_session()
    if mode:
        config.MODE = mode          # the flag beats settings.json
    banner(config.SANDBOX, config.MODEL, config.MODE)

    if argv:
        summary(run(" ".join(argv)))
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
            summary(run(task))


if __name__ == "__main__":
    sys.exit(main())
