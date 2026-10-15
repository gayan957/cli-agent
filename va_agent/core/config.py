"""Mutable runtime state for va-agent.

IMPORTANT: always read these as ``config.NAME``.

Never do ``from va_agent.core.config import SANDBOX`` -- that copies the
value at import time, so later changes are not seen. The evaluation
harness depends on being able to change SANDBOX at runtime.

Correct:
    from va_agent.core import config
    path = config.SANDBOX

Wrong:
    from va_agent.core.config import SANDBOX
"""

import os


def _flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Where the agent is allowed to work. The evaluation harness overwrites
# this with a fresh fixture directory before every run.
SANDBOX = os.path.abspath(os.environ.get("VA_SANDBOX", "./workspace"))

# Skip approval prompts. Set True by run_eval.py for unattended runs.
AUTO_APPROVE = _flag("VA_AUTO_APPROVE")

# Permission mode: default | acceptEdits | plan | bypassPermissions.
MODE = os.environ.get("VA_MODE", "default")

# Extra directories the agent may touch besides SANDBOX.
WORKING_DIRS = []

# Permission rules, e.g. ["Bash(pytest:*)"] and ["Read(**/.env*)"].
# Normally filled from .claude/settings.json by permissions.load_settings().
ALLOW_RULES = []
DENY_RULES = []

# Which editing tools the model is offered: write | edit | both.
# This is the Phase 4 experiment; see tools/registry.py.
EDIT_MODE = os.environ.get("VA_EDIT_MODE", "write")

# Offer the TodoWrite tool. Second Phase 4 experiment.
TODOS = os.environ.get("VA_TODOS", "1") != "0"

# Identifies this session's snapshots under .va/snapshots/.
SESSION_ID = ""

# Resolved paths of files Read this session, for read-before-edit.
READ_FILES = set()

# Path-scoped rules from .claude/rules/ (Phase 5). See harness/rules.py.
RULES_ENABLED = os.environ.get("VA_RULES", "1") != "0"
RULES = []              # all discovered rules
RULES_INJECTED = set()  # names already injected this session

# Which Ollama model to use.
MODEL = os.environ.get("VA_MODEL", "qwen-coder-32k")

# Safety ceiling on the agent loop.
MAX_STEPS = int(os.environ.get("VA_MAX_STEPS", "20"))

# Context window of the current model. Used from Phase 8 onward.
NUM_CTX = int(os.environ.get("VA_NUM_CTX", "32768"))


def reset_sandbox(path):
    """Point the agent at a new working directory."""
    global SANDBOX
    SANDBOX = os.path.abspath(path)
    return SANDBOX