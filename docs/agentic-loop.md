# How the Agentic Loop Works

This explains `va-agent/agent.py` — the core loop that turns a plain-English
task into a sequence of real file edits and shell commands. Everything
else in the project (`cli.py`, `ui.py`, `safety.py`, `tools.py`) exists to
support this one function: `run(task)`.

## 1. The pattern: think → act → observe → repeat

The loop is a classic **ReAct-style agent loop**. On every iteration it:

1. Sends the full conversation so far to the model and asks "what next?"
2. The model replies with either a **tool call** (an action to take) or
   **plain text** (its final answer).
3. If it's a tool call, the loop executes it for real and appends the
   result to the conversation as a new message.
4. Go back to step 1 — now the model can see what actually happened and
   decide the next step.

The model never "imagines" a result. It proposes one action, the code
executes it, and the *real* output is what gets fed back. This is why the
system prompt (below) explicitly forbids the model from describing tool
results itself — it hasn't seen one until the loop hands it back.

## 2. The system prompt sets the contract

```python
SYSTEM = """You are a coding assistant working inside a project folder.

You are running fully autonomously. ...
Rules:
- Use grep to find code before reading files.
- Before editing a file, read it first.
- When writing a file, call write_file with the COMPLETE new content —
  never print the file's content as text, and never send a diff.
- Run tests or the program yourself with run_command to check your work.
- Call exactly ONE tool per reply, then stop and wait for its real result.
- Never invent or describe a tool result yourself...
- When the task is genuinely finished ..., reply with a short summary and
  no tool call."""
```

Each rule maps directly to a failure mode local models are prone to:

| Rule | Prevents |
|---|---|
| "grep before reading" | Wasting steps reading whole files blindly |
| "read before editing" | Overwriting a file based on guessed content |
| "COMPLETE content, never a diff" | `write_file` replaces the whole file — a partial/diff write would silently corrupt it |
| "run tests yourself" | The model claiming success without verifying |
| "exactly ONE tool per reply" | Multi-call replies that are hard to approve/execute safely one at a time |
| "never invent a result" | Hallucinated tool output driving the next decision |
| "reply with no tool call = finished" | This is literally how the loop detects the task is done (see §4) |

## 3. The conversation is a plain message list

`run()` starts the conversation exactly like any chat API:

```python
messages = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": task}]
```

Every step appends to this same list — an `assistant` message for what
the model decided, and a `tool` message for what actually happened — so
each new call to the model sees the entire history and can build on it.

## 4. Walking through one iteration of the loop

```python
for step in range(MAX_STEPS):          # MAX_STEPS = 20, a hard ceiling
    reply = chat(messages, tools=SCHEMAS)   # ask the model what to do next

    messages.append({                       # record what it said
        "role": "assistant",
        "content": reply["content"],
        "tool_calls": [...] or None,
    })

    if not reply["tool_calls"]:
        if reply["content"].strip():
            ...                              # finished — plain text, no tool call
            break
        ...                                  # blank reply — nudge and retry
        continue

    for call in reply["tool_calls"]:
        ...                                  # execute the requested tool(s)
```

Breaking that down:

### a) Ask the model (`llm.chat`)

`chat(messages, tools=SCHEMAS)` sends the conversation plus the JSON
schemas of the 5 available tools (`tools.SCHEMAS`) to Ollama and returns
a normalized `{"content": str, "tool_calls": [...]}` — regardless of
whether the underlying model used structured tool-calling or one of
`llm.py`'s text-based fallbacks (see the earlier overview doc for that
part). `agent.py` doesn't need to know which path was used.

### b) Record the assistant's turn — with a reshape

```python
messages.append({"role": "assistant",
                "content": reply["content"],
                "tool_calls": [
                    {"function": {"name": call["name"], "arguments": call["arguments"]}}
                    for call in reply["tool_calls"]
                ] or None})
```

`llm.py` normalizes tool calls to a flat `{"name", "arguments"}` shape for
convenience. But Ollama's client **re-validates outgoing message history**
on the *next* request, and it expects tool calls nested as
`{"function": {"name": ..., "arguments": ...}}` — the OpenAI-style shape.
So before this message goes back into history, it's reshaped to match
what Ollama will accept when this same list is sent again next step.

### c) Decide: finished, stuck, or acting?

There are three possible outcomes once the model has replied:

- **No tool call + non-empty text** → the task is genuinely done. The
  text is shown to the user as the final answer, `stats["finished"] = True`,
  and the loop breaks.
- **No tool call + empty text** → treated as a non-answer, *not* a finish.
  The loop appends a synthetic `user` message telling the model it replied
  with nothing and must either act or explain, then `continue`s to the
  next iteration. This stops a blank reply from silently ending a task.
- **One or more tool calls** → proceed to execute them (below).

### d) Execute each tool call

For every call the model made in this turn:

```python
if name not in REGISTRY:
    stats["tool_failures"] += 1
    ...
    messages.append({"role": "tool", "content": f"ERROR: no tool named '{name}'"})
    continue
```

Unknown tool names (a hallucinated function) are reported back to the
model as an error rather than crashing the loop — the model gets a
chance to correct itself on the next turn.

```python
if not approve(name, arguments):
    ...
    messages.append({"role": "tool", "content": "User denied this action."})
    continue
```

`write_file` and `run_command` are routed through `safety.approve()`,
which prints what's about to happen and asks the human for `y/N`. A
denial is fed back to the model as a normal tool result ("User denied
this action") so it can adapt — try something else, ask a different way,
or give up gracefully — rather than the process just halting.

```python
try:
    result = REGISTRY[name](**arguments)
except Exception as error:
    stats["tool_failures"] += 1
    result = f"ERROR: {type(error).__name__}: {error}"
    ...

messages.append({"role": "tool", "content": str(result)})
```

The actual tool function runs (from `tools.REGISTRY`). Any exception —
bad path, bad arguments, a failing shell command — is caught, turned into
an `ERROR: ...` string, counted in `stats["tool_failures"]`, and fed back
as the tool result. The model sees its own mistake on the next turn
instead of the whole run crashing.

### e) Loop back

Control returns to the top of the `for` loop. The model now has one more
real observation in its context and gets asked again what to do next.

## 5. How the loop ends

There are four distinct exits:

| Exit | Trigger | Result |
|---|---|---|
| **Finished** | Model replies with text and no tool call | `stats["finished"] = True`, final answer shown |
| **Step limit** | 20 iterations pass without finishing | `for...else` clause fires, "stopped: hit the step limit" |
| **LLM error** | The call to `chat()` itself raises (e.g. Ollama unreachable) | Loop breaks immediately, no further steps |
| **Interrupted** | User presses Ctrl+C mid-run | Propagates up to `cli.py`, which catches it and returns to the prompt |

The `for...else` on line 42/114 is deliberate Python: `else` on a `for`
loop runs only if the loop completed all 20 iterations without hitting a
`break` — i.e. exactly the "ran out of steps" case, distinct from
finishing early or erroring out.

## 6. What surrounds the loop

- **Before**: `safety.checkpoint(task)` commits the sandbox's current
  state to git (if it's a repo), so this run's changes are always
  separable and revertible.
- **After**: `stats` — `steps`, `tool_calls`, `tool_failures`, `seconds`,
  `finished` — is returned to the caller (`cli.py` for interactive use,
  `evaluation/run_eval.py` for benchmarking) and printed as a one-line
  summary.

## 7. Why this design

- **One tool call per turn, real result before continuing** — keeps every
  action reviewable and approvable individually, and stops error
  cascades (a bad assumption in step 3 can't silently corrupt steps 4–10
  before anyone notices).
- **Errors become conversation, not exceptions** — nearly every failure
  mode (unknown tool, denied action, exception, blank reply) is converted
  into a message the *model* sees and can react to, rather than crashing
  the process. The loop itself only breaks on an LLM-layer failure or the
  step cap.
- **`verbose` flag threads through everything** — the same `run()` powers
  both the interactive CLI (verbose, prints each step) and the evaluation
  harness (quiet, just wants the final `stats`), with no duplicated logic.
