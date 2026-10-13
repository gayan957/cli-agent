"""Command line entry point."""

import sys

from va_agent.core import config
from va_agent.core.agent import run
from va_agent.harness import permissions
from va_agent.ui.console import banner, console, summary

HELP = """Commands:
  /mode            show the current permission mode
  /mode <name>     switch mode: default | acceptEdits | plan | bypassPermissions
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
    else:
        console.print(f"[yellow]unknown command {parts[0]}; try /help[/yellow]")
    return True


def main(argv=None):
    argv, mode = _consume_flags(list(sys.argv[1:] if argv is None else argv))
    permissions.load_settings()
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
