
"""Approval prompts and undo checkpoints.

Reads ``config.AUTO_APPROVE`` at call time, not at import time, so the
evaluation harness can switch prompts off for unattended runs.
"""

import os
import subprocess

from va_agent.core import config
from va_agent.ui.console import console, describe_call

NEEDS_APPROVAL = {"write_file", "run_command"}


def approve(name, arguments):
    """Return True if this tool call may proceed."""
    if name not in NEEDS_APPROVAL or config.AUTO_APPROVE:
        return True
    console.print("")
    console.print(describe_call(name, arguments))
    try:
        answer = input("  Allow? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() == "y"


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