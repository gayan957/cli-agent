"""Command line entry point: flags, startup, and the input loop. Slash
commands live in commands.py."""

import re
import sys

from va_agent import commands
from va_agent.core import config
from va_agent.core import context
from va_agent.core import session
from va_agent.core.agent import run
from va_agent.harness import hooks, permissions, rules, skills, worktrees
from va_agent.ui.console import banner, console, summary

SESSION_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


def _consume_flags(argv):
    """Handle flags that must take effect before anything runs. Returns
    (remaining argv, mode from --mode or None, resume) where resume is
    None, "continue", "pick" or a session id."""
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

    resume = None
    if "--continue" in argv:
        argv.remove("--continue")
        resume = "continue"
    if "--resume" in argv:
        index = argv.index("--resume")
        following = argv[index + 1] if index + 1 < len(argv) else ""
        if SESSION_ID_PATTERN.match(following):
            resume = following
            del argv[index:index + 2]
        else:
            resume = "pick"
            del argv[index]
    return argv, mode, resume


def _start_resumed(resume):
    """Apply --continue / --resume before the first turn."""
    if resume == "continue":
        data = session.most_recent()
        if data is None:
            console.print("no saved session to continue; starting fresh")
    elif resume == "pick":
        data = commands.pick_session()
    else:
        data = session.load(resume)
        if data is None:
            console.print("starting fresh")
    if data:
        commands.resume_into(data)


def _slash_command(text):
    """Handle a /command or /skill line. Returns True if it was one."""
    return commands.dispatch(text)


def _submit(task, extra=None):
    """Run *task* unless a UserPromptSubmit hook denies it. *extra* is
    context to follow the task, e.g. a skill loaded with /name."""
    check = hooks.fire("UserPromptSubmit", {"prompt": task})
    if check.decision == "deny":
        console.print(f"[yellow]prompt blocked by hook: {check.reason}"
                      "[/yellow]")
        return
    summary(run(task, context=(extra or [])
                + hooks.context_messages("UserPromptSubmit", check),
                history=list(context.last_messages)))
    # After every turn, so an uncleanly closed terminal loses nothing.
    session.save(context.last_messages)


def main(argv=None):
    argv, mode, resume = _consume_flags(
        list(sys.argv[1:] if argv is None else argv))
    permissions.load_settings()
    hooks.load_hooks()
    rules.discover()
    rules.reset_rules()
    skills.discover()
    skills.reset_skills()
    context.load_memory()
    context.reset_context()
    session.reset_session_state()   # new SESSION_ID, shared with snapshots
    if resume:
        _start_resumed(resume)
    if mode:
        config.MODE = mode          # the flag beats settings.json
    banner(config.SANDBOX, config.MODEL, config.MODE,
           worktrees.banner_branch())
    hooks.start_session()
    try:
        return _session(argv)
    finally:
        hooks.fire("SessionEnd", {"reason": "exit"})


def _session(argv):
    if argv:
        text = " ".join(argv)
        if not commands.dispatch(text):
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
        if task and not commands.dispatch(task):
            _submit(task)


if __name__ == "__main__":
    sys.exit(main())
