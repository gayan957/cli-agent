# va-agent — Project Report (context for Claude)

This file gives an AI assistant the full context of this repository: what it
is, how every file fits together, how data flows through it, and the known
quirks and gaps. Snapshot as of commit `f7f07d2` ("Modify CLI interface").
When this file and the code disagree, trust the code.

---

## 1. What the project is

**va-agent** is a small, local-first autonomous coding agent (a research
project, "virtusa-local-llm-assistant"). A user types a task in plain English;
the agent runs a ReAct-style loop against an LLM served by **Ollama**, calling
five sandboxed tools (list / read / write / grep / run a shell command) until
the model replies with plain text and no tool call.

Goals visible in the code:

- Run fully offline on a local open-weight model (default: `qwen-coder-32k`,
  i.e. `qwen2.5-coder:7b` with a 32k context, see `Modelfile`).
- Be robust to weak local models that don't emit structured tool calls
  (text-parsing fallbacks in `llm.py`).
- Keep the human in control: approval prompts for writes/commands, git
  checkpoints per task, a sandbox root, a 20-step cap.
- Measure performance with a small benchmark harness (`evaluation/`).

Optional: Ollama Cloud models (`*-cloud`, `*:cloud`) can be selected or pulled
from the CLI too.

## 2. Tech stack

| Area | Choice |
|---|---|
| Language | Python 3.14 (`.venv/lib/python3.14`) |
| LLM runtime | `ollama` Python client 0.6.2 talking to a local Ollama daemon |
| Terminal UI | `rich` 15 |
| Evaluation / charts | `pandas`, `matplotlib` (listed in requirements; not yet imported by any script) |
| Packaging | None — plain scripts, flat imports, `requirements.txt` only |
| Tests | `va-agent/mocktest.py` (a script with asserts, not pytest) |

## 3. Repository layout

```
virtusa-local-llm-assistant/
├── Modelfile                 # Ollama model def: FROM qwen2.5-coder:7b, num_ctx 32768
├── requirements.txt          # pinned deps
├── .gitignore                # .venv/, workspace/, testbox*/, __pycache__/, *.pyc
├── va-agent/                 # the application (run scripts from inside this dir)
│   ├── cli.py                # entry point: banner, workspace/model pickers, REPL
│   ├── agent.py              # the agent loop: run(task, verbose) -> stats
│   ├── llm.py                # Ollama wrapper + tool-call fallback parsers
│   ├── tools.py              # 5 sandboxed tools, REGISTRY, JSON SCHEMAS
│   ├── safety.py             # approval gate + git checkpoint
│   ├── ui.py                 # all rich-console output helpers
│   ├── mocktest.py           # offline end-to-end test with a scripted fake LLM
│   ├── workspace/            # default sandbox (git-ignored, currently empty)
│   └── testbox/              # created by mocktest.py (git-ignored)
├── evaluation/
│   ├── run_eval.py           # benchmark harness -> results/results.csv
│   └── tasks.json            # benchmark tasks (currently 1: "E1")
├── results/
│   ├── results.csv           # appended benchmark rows
│   └── charts/               # empty — intended for matplotlib output
├── docs/                     # agentic-loop.md, va-agent-overview.md, this file
├── notes/                    # empty
└── .github/workflows/        # empty — no CI configured yet
```

Size: ~800 lines of Python total. The largest files are `llm.py` (165),
`cli.py` (156), `tools.py` (129), `agent.py` (119), `ui.py` (116).

## 4. Module dependency graph

```
cli.py ──► agent.py ──► llm.py      (chat)
   │          ├──────► tools.py    (REGISTRY, SCHEMAS)
   │          ├──────► safety.py ─► tools.py (tools.SANDBOX), ui.py
   │          └──────► ui.py
   ├──► llm.py   (list_models, pull_model, set_model, MODEL, CLOUD_MODELS)
   ├──► tools.py (SANDBOX, set_sandbox)
   └──► ui.py

evaluation/run_eval.py ──(sys.path insert va-agent/)──► agent.run, tools.SANDBOX
mocktest.py ──► monkeypatches llm.chat, then imports agent.run
```

Imports are **flat** (`from llm import chat`), so scripts must run with
`va-agent/` as the working directory or on `sys.path`. `ollama` is imported
lazily inside `llm.py` functions, so modules import fine without the package.

## 5. File-by-file

### 5.1 `va-agent/agent.py` — the loop

- `MAX_STEPS = 20`; `SYSTEM` prompt tells the model it is autonomous, must act
  only through tools, grep before read, read before edit, write COMPLETE file
  content (never diffs), run tests itself, make exactly ONE tool call per
  reply, never invent tool results, and finish by replying with text and no
  tool call.
- `run(task, verbose=True) -> stats`:
  1. `safety.checkpoint(task)` (git commit in sandbox if it is a repo).
  2. `messages = [system, user(task)]`.
  3. For up to 20 steps: `reply = llm.chat(messages, tools=SCHEMAS)`
     (spinner if verbose). Any exception from `chat` → print and `break`.
  4. Append the assistant turn, **reshaping** tool calls from the normalized
     `{"name","arguments"}` to Ollama's `{"function": {...}}` shape, because
     the Ollama client re-validates history on the next request.
  5. No tool calls + non-empty text → finished (`stats["finished"]=True`).
     No tool calls + empty text → append a nudge `user` message and continue.
  6. For each tool call: unknown name → failure + `ERROR: no tool named`;
     `safety.approve()` denied → tool message "User denied this action.";
     else call `REGISTRY[name](**arguments)`, catching any exception into an
     `ERROR: <Type>: <msg>` string (counted as a failure). Result is appended
     as a `role: "tool"` message.
  7. `for ... else` prints "hit the step limit" if 20 steps pass without a
     break.
- Returns `stats = {task, steps, tool_calls, tool_failures, seconds, finished}`.

Note: although the prompt asks for one call per reply, the loop executes
**all** tool calls in a reply.

### 5.2 `va-agent/llm.py` — model access and robustness

- `MODEL` = env `VA_MODEL` or `"qwen-coder-32k"`; `set_model(name)` mutates
  the module global. Because `agent.py` calls `chat`, which reads `MODEL` at
  call time, switching models takes effect immediately.
- `CLOUD_MODELS` — hint list for the picker; `is_cloud_model(name)`.
- `list_models()` — sorted names from `ollama.list()`; `pull_model(name)`.
- `chat(messages, tools=None)` → `{"content": str, "tool_calls": [{"name","arguments"}]}`:
  - Calls `ollama.chat(..., think=False)` — forcing "thinking" models to put
    output in `content`, otherwise they could return empty and look finished.
  - Normalizes structured tool calls; string arguments are `json.loads`'d
    (falls back to `{}` on error).
  - If no structured calls, tries two text fallbacks, in order:
    - `_extract_fallback_call(content)` — walks every `{` and uses
      `json.JSONDecoder.raw_decode` to find the first object shaped
      `{"name": str, "arguments": dict}` (handles `<tool_call>{...}</tool_call>`
      and hallucinated output after the call).
    - `_extract_call_expr(content, tools)` — finds `tool_name(` at a word
      boundary, matches parens, parses with `ast.parse(mode="eval")` and
      `ast.literal_eval` (never `eval`); positional args are mapped to
      parameter names via schema order (`_param_order`).
  - When a fallback matches, `content` is cleared so hallucinated text is
    discarded and the loop treats the turn as a tool call.

### 5.3 `va-agent/tools.py` — sandboxed capabilities

- `SANDBOX` = env `VA_SANDBOX` or `va-agent/workspace` (next to the file, not
  cwd); created on import. `set_sandbox(path)` changes it at runtime —
  **callers must read `tools.SANDBOX`**, not `from tools import SANDBOX`.
- `_safe(path)` — `abspath(join(SANDBOX, path))` must equal `SANDBOX` or start
  with `SANDBOX + os.sep`, else `ValueError`.
- Tools:

| Tool | Signature | Behavior / limits |
|---|---|---|
| `list_files` | `(path=".")` | recursive walk, skips `.git`, `__pycache__`, `node_modules`, `.venv`; returns sorted relative paths or `(no files)` |
| `read_file` | `(path)` | UTF-8, errors ignored; truncated at 50,000 chars |
| `write_file` | `(path, content)` | creates parent dirs, overwrites whole file |
| `grep` | `(pattern, path=".")` | `re.search` per line, first 50 hits, `file:line: text` (200-char lines) |
| `run_command` | `(command)` | substring blocklist (`rm -rf`, `sudo`, `shutdown`, `mkfs`, `dd if=`, `:(){`, `> /dev/`) → returns `"BLOCKED: ..."`; else `shell=True`, `cwd=SANDBOX`, 60 s timeout; returns exit code + first 5,000 chars of stdout/stderr |

- `REGISTRY` maps names → functions; `SCHEMAS` are OpenAI/Ollama-style
  function schemas built by `_schema()`. **Adding a tool means updating
  `REGISTRY`, `SCHEMAS`, and optionally `ui.TOOL_ICONS` and
  `safety.NEEDS_APPROVAL`.**

### 5.4 `va-agent/safety.py`

- `NEEDS_APPROVAL = {"write_file", "run_command"}`.
- `AUTO_APPROVE` is read from env `VA_AUTO_APPROVE == "1"` **at import time**.
- `approve(name, arguments)` — prints a one-line description
  (`ui.describe_call`, which never dumps file content) and asks `Allow? [y/N]`.
- `checkpoint(label)` — only if `<sandbox>/.git` exists: `git add -A` and
  `git commit -m "before: <label[:60]>"` (output captured, errors ignored).

### 5.5 `va-agent/cli.py` — entry point

`main()`: `ui.banner()` → `pick_workspace()` → `pick_model()` → then either
runs the task from `sys.argv` once, or starts `repl()`.

- `pick_workspace()` — Enter keeps current; rejects files; offers to create
  missing dirs; calls `tools.set_sandbox`; warns if not a git repo.
- `pick_model()` — lists Ollama models (tags cloud ones, marks default); `c`
  opens `pick_cloud_model()` which pulls a cloud model (needs `ollama signin`).
  If Ollama is unreachable, keeps the default instead of blocking.
- `repl()` — commands `help`, `workspace`, `model`, `exit`/`quit`; anything
  else is a task. Ctrl+C during a task returns to the prompt; Ctrl+C/EOF at
  the prompt exits. Prints `ui.summary(stats)` after each task.

### 5.6 `va-agent/ui.py`

Single shared `rich.Console`. Helpers: `banner`, `help_table`, `prompt_task`
(`➜ va>`), `announce_task`, `tool_call` (with per-tool emoji from
`TOOL_ICONS`), `tool_denied`, `tool_error`, `unknown_tool`, `empty_reply`,
`llm_error`, `step_limit`, `final_reply` (green panel), `summary` (colored by
`tool_failures`, not by `finished`), and `describe_call`. All presentation
logic lives here so `agent.py`/`cli.py` stay logic-only.

### 5.7 `va-agent/mocktest.py` — offline test

Sets `VA_AUTO_APPROVE=1` and `VA_SANDBOX=./testbox` **before** importing
modules, writes `testbox/main.py`, replaces `llm.chat` with an iterator over a
10-step script, **then** imports `agent.run` (so `agent`'s `from llm import
chat` binds to the fake). The script exercises the happy path plus four
deliberate failures: unknown tool, path traversal (`ValueError`), bad
argument (`TypeError`), and `rm -rf /` (returns `BLOCKED`, which is *not*
counted as a failure). Asserts: finished, exactly 3 failures, and the new
docstring was written.

Run: `cd va-agent && python mocktest.py` → prints `ALL CHECKS PASSED`.

### 5.8 `evaluation/`

- `tasks.json` — list of `{id, difficulty, prompt, verify}`; `verify` is a
  shell command run in the sandbox (exit 0 = success). Currently one task
  (`E1`: add docstrings to every function in `main.py`).
- `run_eval.py` — forces auto-approve, runs each task `RUNS = 3` times with
  `verbose=False`; before each run `reset()` does `git checkout .` and
  `git clean -fd` in the sandbox; scores success as `2` (pass), `0` (fail) or
  `""` (no verify). Appends to `results/results.csv` with columns
  `timestamp, system, model, task_id, difficulty, run, success, steps,
  tool_calls, tool_failures, seconds`. `VA_SYSTEM` (default `A`) labels the
  configuration being compared; `VA_MODEL` labels the model.

### 5.9 Existing results

`results/results.csv` has 3 rows (2026-08-12, system A, `qwen-coder-32k`,
task E1): all `success=0`, each 2 steps, 1 tool call, 0 failures, 7–12 s.
The agent stopped after one tool call and a text reply — see §8 for the
likely cause.

## 6. Configuration (environment variables)

| Variable | Used in | Effect |
|---|---|---|
| `VA_MODEL` | `llm.py`, `run_eval.py` | Ollama model name (default `qwen-coder-32k`) |
| `VA_SANDBOX` | `tools.py` | Sandbox root (default `va-agent/workspace`) |
| `VA_AUTO_APPROVE` | `safety.py` | `"1"` skips approval prompts (read at import) |
| `VA_SYSTEM` | `run_eval.py` | Label for the system variant in results CSV |

## 7. How to run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama create qwen-coder-32k -f Modelfile     # one-time, needs qwen2.5-coder:7b pulled

cd va-agent
python cli.py                          # interactive REPL
python cli.py "add a README"           # one-shot (still asks workspace/model first)
python mocktest.py                     # offline test, no Ollama needed

cd .. && python evaluation/run_eval.py # benchmark (needs a prepared git sandbox)
```

## 8. Known issues, risks and gaps

These are observations from reading the code; verify before acting on them.

1. **Eval sandbox is not set up.** `va-agent/workspace/` is empty and is not
   its own git repo (it is git-ignored inside the outer repo). So
   `checkpoint()` does nothing, `reset()`'s `git checkout`/`git clean` act on
   the outer repo (and `git clean -fd` won't touch ignored files), and E1's
   `main.py` doesn't exist — which explains the all-zero results. The
   harness needs a fixture step: create `workspace/`, `git init`, add
   `main.py`, commit.
2. **Shell commands are not sandboxed.** `_safe()` only guards the file tools.
   `run_command` uses `shell=True` with `cwd=SANDBOX`, so `cd ..`, absolute
   paths, or `cat /etc/passwd` all work. The blocklist is plain substring
   matching and easily bypassed (`rm -r -f`, `rm -fr`, `python -c ...`).
   Human approval is the real safeguard; with `VA_AUTO_APPROVE=1` there is
   effectively none.
3. **Symlinks escape `_safe`.** It uses `abspath`, not `realpath`, so a
   symlink inside the sandbox can point outside it.
4. **Multiple tool calls per turn are executed** even though the prompt asks
   for one; approval happens per call.
5. **Context growth is unbounded** — whole-file reads (up to 50k chars) are
   appended to history each step; long tasks may exceed the 32k context.
6. **Fallback parser false positives** — any JSON-looking `{"name", "arguments"}`
   or `tool_name(...)` in prose is treated as a real call.
7. **One-shot CLI mode still prompts** for workspace and model, so it isn't
   scriptable non-interactively.
8. `ui.summary` shows green when there were no tool failures even if the task
   did not finish (hit step limit or LLM error).
9. `pandas`/`matplotlib` are dependencies, but no chart/report script exists
   yet; `results/charts/`, `notes/` and `.github/workflows/` are empty.
10. No pytest suite, no CI, no `README.md`. `docs/` is currently untracked in git.
11. Project lives under `~/Documents` (iCloud-synced on macOS); first reads of
    evicted files can raise `TimeoutError: [Errno 60]` — a sync artifact, not
    a code bug.

## 9. Conventions to follow when changing this code

- Keep presentation in `ui.py`; keep `agent.py`/`cli.py` free of formatting.
- Convert failures into tool-result strings the model can react to rather
  than raising out of the loop; only LLM-layer failures break the loop.
- Keep `run(task, verbose)` usable both interactively and quietly (eval).
- Read `tools.SANDBOX` / `llm.MODEL` via the module, not `from x import Y`,
  since both can change at runtime.
- Comment style: short docstrings plus "why" comments on non-obvious
  workarounds (see the Ollama reshape in `agent.py`, `think=False` in `llm.py`).
- After changing the loop or tools, run `python mocktest.py` and update its
  expected failure count if needed.
- Commit messages use a light conventional style (`feat:`, `chore:`).

## 10. Related docs

- `docs/agentic-loop.md` — detailed walkthrough of `agent.run`.
- `docs/va-agent-overview.md` — higher-level technical overview with a
  sequence diagram.
