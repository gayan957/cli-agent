"""The agent loop."""

import time

from va_agent.core import config
from va_agent.core import llm
from va_agent.harness import hooks, rules, snapshots
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


# Tools whose file path can trigger a path-scoped rule: Read, Write, Edit
# (and the old names for the same tools). Not Glob or Grep: their path is a
# folder or a pattern, not a file.
FILE_TOOLS = {"Read", "Write", "Edit", "read_file", "write_file"}


def _touched_path(name, arguments):
    """The file a successful Read/Write/Edit touched, or None."""
    if name not in FILE_TOOLS:
        return None
    path = arguments.get("file_path", arguments.get("path"))
    return path if isinstance(path, str) and path else None


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

    messages = [{"role": "system",
                 "content": system_prompt() + rules.system_text()},
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

            # PreToolUse hooks sit in front of the permission system: deny
            # stops the call, allow skips approve(), anything else falls
            # through to it unchanged.
            hook_tool = hooks.tool_payload(name, arguments)
            pre = hooks.fire("PreToolUse", hook_tool)
            if pre.decision == "deny":
                stats["tool_failures"] += 1
                messages.append({"role": "tool",
                                 "content": f"Blocked by hook: {pre.reason}"})
                messages.extend(hooks.context_messages("PreToolUse", pre))
                if verbose:
                    console.print(f"[yellow]  blocked by hook: "
                                  f"{pre.reason}[/yellow]")
                continue
            if pre.decision != "allow" and not approve(name, arguments):
                messages.append({"role": "tool",
                                 "content": "User denied this action."})
                messages.extend(hooks.context_messages("PreToolUse", pre))
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
                messages.extend(hooks.context_messages("PreToolUse", pre))
                continue

            messages.append({"role": "tool", "content": str(result)})
            messages.extend(hooks.context_messages("PreToolUse", pre))

            # Path-scoped rules arrive right after the file they apply to.
            # role "user": several local models ignore mid-chat system turns.
            path = _touched_path(name, arguments)
            for rule in rules.rules_for(path) if path else []:
                messages.append({"role": "user",
                                 "content": f"[project rule: {rule.name}]"
                                            f"\n\n{rule.body}"})
                rules.mark_injected(rule, path)
                if verbose:
                    console.print(f"[dim]  rule loaded: {rule.name}[/dim]")

            # PostToolUse: e.g. selftest.sh exits 2 and its failing test
            # output reaches the model here without the model asking.
            post = hooks.fire("PostToolUse",
                              {**hook_tool, "tool_response": str(result)})
            messages.extend(hooks.context_messages("PostToolUse", post))
            if verbose and post.context:
                console.print(f"[dim]  hook feedback: {len(post.context)} "
                              f"message(s)[/dim]")
    else:
        if verbose:
            console.print(
                f"[yellow]Stopped: hit the {config.MAX_STEPS} step limit."
                f"[/yellow]")

    stats["seconds"] = round(time.time() - started, 1)
    return stats