"""The agent loop."""

import time

from va_agent.core import config
# Imported as ctx: run() has a parameter named context (Phase 6).
from va_agent.core import context as ctx
from va_agent.core import llm
from va_agent.harness import hooks, rules, skills, snapshots
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


def _skill_aliases(reply, tools):
    """Small models often call a skill as if its name were a tool, e.g.
    {"name": "code-review", "arguments": {}}, as text or as a real call.
    Turn those into Skill(name) so the right skill still loads."""
    if "Skill" not in tools:
        return reply
    calls = reply["tool_calls"]
    if not calls and reply["content"]:
        # Text calls naming a skill: llm's own parser, with skill names.
        calls = llm._fallback_calls(reply["content"], set(config.SKILLS))
        if not calls:
            return reply
        reply = {**reply, "content": ""}
    return {**reply, "tool_calls": [
        {"name": "Skill", "arguments": {"name": call["name"]}}
        if call["name"] in config.SKILLS and call["name"] not in tools
        else call
        for call in calls]}


def _skills_text():
    block = skills.prompt_block()
    return f"\n\n{block}" if block else ""


def _earlier(history):
    """Earlier turns to continue from: without the old system prompt and
    CLAUDE.md copies, since a fresh system message carries current ones."""
    if not history:
        return []
    return [message for message in history
            if message.get("role") != "system"
            and not str(message.get("content") or "").startswith(
                ctx.MEMORY_MARK)]


def system_message():
    """The system message a new run starts with."""
    return {"role": "system",
            "content": system_prompt() + rules.system_text()
                       + hooks.system_text() + _skills_text()
                       # Last, so compaction can swap in a fresh copy.
                       + ctx.memory_text()}


def _new_stats(task):
    return {"task": task, "steps": 0, "tool_calls": 0,
            "tool_failures": 0, "seconds": 0.0, "finished": False,
            # Phase 4 experiment outputs.
            "edit_attempts": 0, "edit_failures": 0, "peak_tokens": 0,
            "compactions": 0}


def run(task, verbose=True, context=None, history=None):
    """Run one task to completion. Returns a stats dict. *context* is extra
    messages to follow the task, e.g. UserPromptSubmit hook output.
    *history* is the session's earlier conversation (interactive and resumed
    sessions); benchmark runs pass none and start fresh."""
    checkpoint(task)
    hooks.reset_hooks()
    ctx.reset_context()             # per-run token peak and attempt counter
    skills.maybe_reload()           # an edited SKILL.md needs no restart
    earlier = _earlier(history)
    skills.begin_task(task, earlier + list(context or []))

    # Built per run so VA_EDIT_MODE / VA_TODOS changes take effect.
    tools = registry.build_registry()
    schemas = registry.build_schemas()

    messages = [system_message(), *earlier,
                {"role": "user", "content": task}, *(context or [])]
    stats = _new_stats(task)
    started = time.time()

    for step in range(config.MAX_STEPS):
        stats["steps"] = step + 1
        snapshots.next_step()
        # Layer 2: no single message may take over the window.
        messages = [ctx.clamp(message) for message in messages]
        # Layer 3: summarise old history near the limit. The attempt
        # counter stops an ineffective compaction from looping.
        if (config.COMPACT_ENABLED
                and config.COMPACT_ATTEMPTS < ctx.MAX_ATTEMPTS
                and ctx.needs_compaction(messages)):
            before = ctx.estimate(messages)
            messages, did = ctx.compact(messages, trigger="auto")
            if did:
                stats["compactions"] += 1
                if verbose:
                    console.print(f"[dim]  compacted context: {before:,} -> "
                                  f"{ctx.estimate(messages):,} tokens[/dim]")
        config.PEAK_TOKENS = max(config.PEAK_TOKENS, ctx.estimate(messages))
        stats["peak_tokens"] = config.PEAK_TOKENS

        try:
            reply = _skill_aliases(llm.chat(messages, tools=schemas), tools)
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
                # A Stop hook can refuse the finish and send the model back
                # to work, at most MAX_STOP_CONTINUATIONS times per run.
                stop = hooks.fire("Stop", {"stop_hook_active":
                                           hooks.stop_hook_active()})
                if stop.decision == "deny":
                    if hooks.stop_may_continue():
                        messages.append({"role": "user",
                                         "content": f"[hook: Stop]\n\n"
                                                    f"{stop.reason}"})
                        if verbose:
                            console.print(f"[yellow]  Stop hook: continuing "
                                          f"({stop.reason})[/yellow]")
                        continue
                    stats["stop_hook_limit"] = True
                    if verbose:
                        console.print(
                            f"[yellow]  Stop hook still objecting after "
                            f"{hooks.MAX_STOP_CONTINUATIONS} continuations; "
                            f"finishing anyway.[/yellow]")
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
                messages.append({"role": "tool",
                                 "content": ctx.cap(result)})
                messages.extend(hooks.context_messages("PreToolUse", pre))
                continue

            # A newly loaded skill's body arrives as a user message; the
            # tool result just says so, so the body is not sent twice.
            loaded = skills.take_pending() if name == "Skill" else None
            if loaded:
                result = f"Loaded skill {loaded[0]!r}; its instructions follow."
            # Layer 1 of context management: cap every tool result.
            messages.append({"role": "tool", "content": ctx.cap(result)})
            if loaded:
                messages.append(skills.message(*loaded))
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

    ctx.remember(messages)          # for /context and /compact
    stats["seconds"] = round(time.time() - started, 1)
    return stats