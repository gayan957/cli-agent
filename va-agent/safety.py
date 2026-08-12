"""Approval prompts and undo checkpoints."""

import os
import subprocess

from tools import SANDBOX

NEEDS_APPROVAL = {"write_file", "run_command"}
AUTO_APPROVE = os.environ.get("VA_AUTO_APPROVE") == "1"


def approve(name, arguments):
    if name not in NEEDS_APPROVAL or AUTO_APPROVE:
        return True
    print(f"\n  >>> {name}: {str(arguments)[:300]}")
    return input("  Allow? [y/N] ").strip().lower() == "y"


def checkpoint(label):
    """Commit the sandbox so any run can be undone with git."""
    if not os.path.isdir(os.path.join(SANDBOX, ".git")):
        return "no git repo in sandbox"
    subprocess.run(["git", "add", "-A"], cwd=SANDBOX, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"before: {label[:60]}"],
                   cwd=SANDBOX, capture_output=True)
    return "checkpoint saved"