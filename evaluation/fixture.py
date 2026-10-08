"""Builds a clean, git-initialised copy of the fixture repo for each task."""

import os
import shutil
import subprocess
import tempfile

FIXTURE_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixture_repo")


def build():
    """Create a fresh temp copy of the fixture. Returns its path."""
    target = tempfile.mkdtemp(prefix="va_eval_")

    shutil.copytree(FIXTURE_SRC, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__",
                                                  ".pytest_cache", "*.pyc"))

    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "add", "-A"], cwd=target, check=True)
    subprocess.run(["git",
                    "-c", "user.email=eval@local",
                    "-c", "user.name=eval",
                    "commit", "-q", "-m", "fixture"],
                   cwd=target, check=True)
    return target


def destroy(path):
    """Remove a fixture directory."""
    if path and path.startswith(tempfile.gettempdir()):
        shutil.rmtree(path, ignore_errors=True)