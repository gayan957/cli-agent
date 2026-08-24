"""The agent loop."""

import time

from llm import chat
from safety import approve, checkpoint
from tools import REGISTRY, SCHEMAS
import ui
from ui import console

MAX_STEPS = 20

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


def run(task, verbose=True):
    checkpoint(task)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": task}]

    stats = {"task": task, "steps": 0, "tool_calls": 0,
            "tool_failures": 0, "seconds": 0.0, "finished": False}
    started = time.time()

    for step in range(MAX_STEPS):
        stats["steps"] = step + 1
        try:
            if verbose:
                with console.status("[cyan]thinking...[/cyan]", spinner="dots"):
                    reply = chat(messages, tools=SCHEMAS)
            else:
                reply = chat(messages, tools=SCHEMAS)
        except Exception as error:
            if verbose:
                ui.llm_error(error)
            break

        # Ollama's client re-validates outgoing messages, expecting each tool
        # call as {"function": {"name": ..., "arguments": ...}} — reshape our
        # normalised {"name": ..., "arguments": ...} calls back into that
        # before they go into history and get sent on the next turn.
        messages.append({"role": "assistant",
                        "content": reply["content"],
                        "tool_calls": [
                            {"function": {"name": call["name"], "arguments": call["arguments"]}}
                            for call in reply["tool_calls"]
                        ] or None})

        if not reply["tool_calls"]:
            if reply["content"].strip():
                if verbose:
                    ui.final_reply(reply["content"])
                stats["finished"] = True
                break
            # A genuinely blank reply — no text, no tool call — is never a
            # real finish. Nudge instead of silently ending the task.
            if verbose:
                ui.empty_reply()
            messages.append({"role": "user",
                            "content": "You replied with nothing and called no tool. "
                                        "Call a tool now to make progress, or explain "
                                        "why the task is already done."})
            continue

        for call in reply["tool_calls"]:
            name = call["name"]
            arguments = call["arguments"]
            stats["tool_calls"] += 1

            if name not in REGISTRY:
                stats["tool_failures"] += 1
                if verbose:
                    ui.unknown_tool(name)
                messages.append({"role": "tool",
                                "content": f"ERROR: no tool named '{name}'"})
                continue

            if verbose:
                ui.tool_call(step + 1, name, arguments)

            if not approve(name, arguments):
                if verbose:
                    ui.tool_denied()
                messages.append({"role": "tool",
                                "content": "User denied this action."})
                continue

            try:
                result = REGISTRY[name](**arguments)
            except Exception as error:
                stats["tool_failures"] += 1
                result = f"ERROR: {type(error).__name__}: {error}"
                if verbose:
                    ui.tool_error(result)

            messages.append({"role": "tool", "content": str(result)})
    else:
        if verbose:
            ui.step_limit(MAX_STEPS)

    stats["seconds"] = round(time.time() - started, 1)
    return stats