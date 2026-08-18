"""Approval prompts and undo checkpoints."""

import os
import subprocess

import tools
from ui import console, describe_call

NEEDS_APPROVAL = {"write_file", "run_command"}
AUTO_APPROVE = os.environ.get("VA_AUTO_APPROVE") == "1"


def approve(name, arguments):
    if name not in NEEDS_APPROVAL or AUTO_APPROVE:
        return True
    console.print(f"\n  [yellow]>>> {name}:[/yellow] {describe_call(name, arguments)}")
    answer = console.input("  [bold]Allow? [y/N][/bold] ").strip().lower()
    return answer in {"y", "yes"}


def checkpoint(label):
    """Commit the sandbox so any run can be undone with git."""
    sandbox = tools.SANDBOX
    if not os.path.isdir(os.path.join(sandbox, ".git")):
        return "no git repo in sandbox"
    subprocess.run(["git", "add", "-A"], cwd=sandbox, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"before: {label[:60]}"],
                    cwd=sandbox, capture_output=True)
    return "checkpoint saved"