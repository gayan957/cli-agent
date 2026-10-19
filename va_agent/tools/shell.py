"""A persistent Bash shell, so ``cd`` and ``export`` carry between calls.

One long-lived ``/bin/bash`` reads commands on stdin. After each command we
print a unique marker followed by ``$?``; output is everything before the
marker. A reader thread feeds a queue so a hung command can time out.

Which commands may run is policy, not this module's job: see the
block-dangerous.sh PreToolUse hook and harness/permissions.py.
"""

import os
import queue
import signal
import subprocess
import threading
import uuid

from va_agent.core import config

MAX_OUTPUT_CHARS = 30_000
MAX_TIMEOUT = 600

_proc = None
_lines = None
_started_in = None


def _reader(stream, sink):
    for line in iter(stream.readline, ""):
        sink.put(line)
    sink.put(None)                      # EOF: the shell has exited


def _start():
    global _proc, _lines, _started_in
    _started_in = config.SANDBOX
    _lines = queue.Queue()
    _proc = subprocess.Popen(
        ["/bin/bash"], cwd=config.SANDBOX,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
        start_new_session=True,         # own process group, for clean kills
        env={**os.environ, "PS1": "", "PS2": "", "TERM": "dumb",
             "PAGER": "cat", "GIT_PAGER": "cat"})
    threading.Thread(target=_reader, args=(_proc.stdout, _lines),
                     daemon=True).start()


def reset():
    """Kill the shell. The next Bash call starts a fresh one in SANDBOX."""
    global _proc, _lines, _started_in
    if _proc is not None and _proc.poll() is None:
        try:
            os.killpg(_proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            _proc.kill()
        _proc.wait()
    _proc = _lines = _started_in = None


def _ensure():
    # Restart if the shell died, or if SANDBOX moved (a new benchmark run).
    if _proc is None or _proc.poll() is not None or _started_in != config.SANDBOX:
        reset()
        _start()


def Bash(command, timeout=120):
    """Run *command* in the persistent shell. Returns ``exit_code=N`` and
    the combined stdout/stderr, capped at 30,000 characters."""
    try:
        timeout = min(max(float(timeout), 1), MAX_TIMEOUT)
    except (TypeError, ValueError):
        timeout = 120

    _ensure()
    marker = f"__VA_{uuid.uuid4().hex}__"
    # The braces run in the current shell (so cd persists) while
    # < /dev/null stops a command from swallowing our marker line.
    _proc.stdin.write(f"{{ {command}\n}} < /dev/null\n"
                      f"printf '{marker}%s\\n' $?\n")
    _proc.stdin.flush()

    output, code = [], None
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
    timer.start()
    try:
        while not deadline.is_set():
            try:
                line = _lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                reset()
                text = "".join(output)[:MAX_OUTPUT_CHARS]
                return f"exit_code=-1\n{text}\n[shell exited; restarted]"
            if marker in line:
                before, _, after = line.partition(marker)
                output.append(before)
                code = after.strip()
                break
            output.append(line)
    finally:
        timer.cancel()

    text = "".join(output)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n...[TRUNCATED]"

    if code is None:
        reset()
        return (f"exit_code=-1\n{text}\n[TIMEOUT after {timeout:g}s: the "
                "shell was killed and restarted, so cd/export state is lost]")
    return f"exit_code={code}\n{text}"
