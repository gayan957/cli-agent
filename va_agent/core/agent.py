"""The agent loop."""

import time

from va_agent.core import config
from va_agent.core import llm
from va_agent.harness.permissions import approve, checkpoint
from va_agent.tools.fs import REGISTRY, SCHEMAS
from va_agent.ui.console import console, describe_call

SYSTEM = """You are a coding assistant working inside a project folder.

You are running fully autonomously. There is no human operator who will run
commands or create files for you — the ONLY way anything happens is if YOU
call a tool. Never paste a code block or a shell command as text expecting
someone else to run it; that accomplishes nothing. Call write_file to create
or change a file. Call run_command to run anything, including tests.

Rules:
- Use grep to find code before reading files.
- Before editing a file, read it first.
- When writing a file, call write_file with the COMPLETE new content —
  never print the file's content as text, and never send a diff.
- Run tests or the program yourself with run_command to check your work.
- Call exactly ONE tool per reply, then stop and wait for its real result.
- Never invent or describe a tool result yourself — you have not seen one
  until it comes back to you.
- When the task is genuinely finished (files written, tests run and
  passing), reply with a short summary and no tool call."""


def _new_stats(task):
    return {"task": task, "steps": 0, "tool_calls": 0,
            "tool_failures": 0, "seconds": 0.0, "finished": False}


def run(task, verbose=True):
    """Run one task to completion. Returns a stats dict."""
    checkpoint(task)

    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": task}]
    stats = _new_stats(task)
    started = time.time()

    for step in range(config.MAX_STEPS):
        stats["steps"] = step + 1

        try:
            reply = llm.chat(messages, tools=SCHEMAS)
        except Exception as error:
            if verbose:
                console.print(f"[red]  LLM error: {error}[/red]")
            stats["tool_failures"] += 1
            break

        # Ollama re-validates history and requires each tool call as
        # {"function": {"name": ..., "arguments": ...}}. Sending our
        # normalised {"name", "arguments"} shape fails on the next step.
        messages.append({"role": "assistant",
                         "content": reply["content"],
                         "tool_calls": [
                             {"function": {"name": c["name"],
                                           "arguments": c["arguments"]}}
                             for c in reply["tool_calls"]] or None})

        if not reply["tool_calls"]:
            if reply["content"].strip():
                if verbose:
                    console.print(reply["content"])
                stats["finished"] = True
                break
            # A blank reply (no text, no tool call) is never a real finish.
            messages.append({"role": "user",
                             "content": "You replied with nothing and called "
                                        "no tool. Call a tool now to make "
                                        "progress, or explain why the task "
                                        "is already done."})
            continue

        for call in reply["tool_calls"]:
            name = call["name"]
            arguments = call["arguments"]
            stats["tool_calls"] += 1

            if name not in REGISTRY:
                stats["tool_failures"] += 1
                messages.append({"role": "tool",
                                 "content": f"ERROR: no tool named '{name}'"})
                continue

            if verbose:
                console.print(describe_call(name, arguments, step + 1))

            if not approve(name, arguments):
                messages.append({"role": "tool",
                                 "content": "User denied this action."})
                continue

            try:
                result = REGISTRY[name](**arguments)
            except Exception as error:
                stats["tool_failures"] += 1
                result = f"ERROR: {type(error).__name__}: {error}"

            messages.append({"role": "tool", "content": str(result)})
    else:
        if verbose:
            console.print(
                f"[yellow]Stopped: hit the {config.MAX_STEPS} step limit."
                f"[/yellow]")

    stats["seconds"] = round(time.time() - started, 1)
    return stats