"""The agent loop."""

import time

from va_agent.core import config
from va_agent.core import llm
from va_agent.harness import snapshots
from va_agent.harness.permissions import approve, checkpoint
from va_agent.tools import registry
from va_agent.ui.console import console, describe_call

# The editing instructions follow config.EDIT_MODE so the prompt never
# names a tool the model was not given.
EDIT_PROMPTS = {
    "write": ("Call Write to create or change a file.",
              "- When changing a file, call Write with the COMPLETE new "
              "content —\n  never print the file's content as text, and "
              "never send a diff."),
    "edit": ("Call Edit to change a file.",
             "- To change a file, call Edit with an exact, unique old_string "
             "copied\n  from the Read output (without the line-number "
             "prefix) and its\n  replacement. Edit only changes existing "
             "files. Never print a file's\n  content as text."),
    "both": ("Call Edit for small changes to an existing file, or Write to "
             "create a\nfile or replace it completely.",
             "- With Edit, copy an exact, unique old_string from the Read "
             "output\n  (without the line-number prefix). With Write, send "
             "the COMPLETE new\n  content, never a diff. Never print a "
             "file's content as text."),
}

SYSTEM = """You are a coding assistant working inside a project folder.

You are running fully autonomously. There is no human operator who will run
commands or create files for you — the ONLY way anything happens is if YOU
call a tool. Never paste a code block or a shell command as text expecting
someone else to run it; that accomplishes nothing. {edit_how}
Call Bash to run anything, including tests.

Rules:
- Use Grep or Glob to find code before reading files.
- Before editing a file, Read it first.
{edit_rule}
- Run tests or the program yourself with Bash to check your work.
- Call exactly ONE tool per reply, then stop and wait for its real result.
- Never invent or describe a tool result yourself — you have not seen one
  until it comes back to you.
- When the task is genuinely finished (files written, tests run and
  passing), reply with a short summary and no tool call."""


def system_prompt():
    """The system prompt for the current EDIT_MODE."""
    edit_how, edit_rule = EDIT_PROMPTS[registry.edit_mode()]
    return SYSTEM.format(edit_how=edit_how, edit_rule=edit_rule)


def _new_stats(task):
    return {"task": task, "steps": 0, "tool_calls": 0,
            "tool_failures": 0, "seconds": 0.0, "finished": False,
            # Phase 4 experiment outputs.
            "edit_attempts": 0, "edit_failures": 0, "peak_tokens": 0}


def run(task, verbose=True):
    """Run one task to completion. Returns a stats dict."""
    checkpoint(task)

    # Built per run so VA_EDIT_MODE / VA_TODOS changes take effect.
    tools = registry.build_registry()
    schemas = registry.build_schemas()

    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": task}]
    stats = _new_stats(task)
    started = time.time()

    for step in range(config.MAX_STEPS):
        stats["steps"] = step + 1
        snapshots.next_step()
        # Rough size of what the model sees: ~4 characters per token.
        stats["peak_tokens"] = max(stats["peak_tokens"],
                                   len(str(messages)) // 4)

        try:
            reply = llm.chat(messages, tools=schemas)
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

            if name not in tools:
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

            is_edit = name in registry.EDIT_TOOLS
            if is_edit:
                stats["edit_attempts"] += 1
            try:
                result = tools[name](**arguments)
            except Exception as error:
                stats["tool_failures"] += 1
                if is_edit:
                    stats["edit_failures"] += 1
                result = f"ERROR: {type(error).__name__}: {error}"

            messages.append({"role": "tool", "content": str(result)})
    else:
        if verbose:
            console.print(
                f"[yellow]Stopped: hit the {config.MAX_STEPS} step limit."
                f"[/yellow]")

    stats["seconds"] = round(time.time() - started, 1)
    return stats