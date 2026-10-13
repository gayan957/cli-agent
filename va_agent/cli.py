"""Command line entry point."""

import sys

from va_agent.core import config
from va_agent.core.agent import run
from va_agent.ui.console import banner, console, summary


def _consume_flags(argv):
    """Handle flags that must take effect before anything runs."""
    if "--print" in argv:
        argv.remove("--print")
        config.AUTO_APPROVE = True
    return argv


def main(argv=None):
    argv = _consume_flags(list(sys.argv[1:] if argv is None else argv))
    banner(config.SANDBOX, config.MODEL)

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
        if task:
            summary(run(task))


if __name__ == "__main__":
    sys.exit(main())