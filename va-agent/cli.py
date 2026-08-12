"""Command line entry point."""

import sys

from agent import run
from tools import SANDBOX


def show(stats):
    print(f"\n  steps={stats['steps']}  tool_calls={stats['tool_calls']}  "
        f"failures={stats['tool_failures']}  time={stats['seconds']}s")


def main():
    print(f"va-agent  |  sandbox: {SANDBOX}")
    if len(sys.argv) > 1:
        show(run(" ".join(sys.argv[1:])))
        return
    while True:
        try:
            task = input("\nva> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if task in {"exit", "quit"}:
            return
        if task:
            show(run(task))


if __name__ == "__main__":
    main()