"""The agent loop."""

import time

from llm import chat
from safety import approve, checkpoint
from tools import REGISTRY, SCHEMAS

MAX_STEPS = 20

SYSTEM = """You are a coding assistant working inside a project folder.

Rules:
- Use grep to find code before reading files.
- Before editing a file, read it first.
- When writing a file, send the COMPLETE new content, never a diff.
- Run tests or the program to check your work when possible.
- Do one step at a time.
- When the task is finished, reply with a short summary and no tool call."""


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
            reply = chat(messages, tools=SCHEMAS)
        except Exception as error:
            print(f"  LLM error: {error}")
            break

        messages.append({"role": "assistant",
                        "content": reply["content"],
                        "tool_calls": reply["tool_calls"]})

        if not reply["tool_calls"]:
            if verbose:
                print(reply["content"])
            stats["finished"] = True
            break

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
                print(f"  [{step + 1}] {name}({str(arguments)[:120]})")

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
            print(f"Stopped: hit the {MAX_STEPS} step limit.")

    stats["seconds"] = round(time.time() - started, 1)
    return stats