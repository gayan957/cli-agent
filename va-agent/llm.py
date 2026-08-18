"""Talks to the local model via Ollama and normalises the reply."""

import ast
import json
import os

MODEL = os.environ.get("VA_MODEL", "qwen-coder-32k")

# A few well-known names from ollama.com/cloud, offered as hints in the
# picker — not exhaustive, and any "*-cloud" / "*:cloud" name can be typed
# in directly (Ollama's cloud catalog changes over time).
CLOUD_MODELS = [
    "gpt-oss:20b-cloud",
    "gpt-oss:120b-cloud",
    "qwen3-coder:480b-cloud",
    "deepseek-v3.1:671b-cloud",
    "glm-4.6:cloud",
    "minimax-m2:cloud",
    "kimi-k2:1t-cloud",
]


def is_cloud_model(name):
    return name.endswith("-cloud") or name.endswith(":cloud")


def set_model(name):
    """Switch models for the rest of the process (e.g. after a CLI picker)."""
    global MODEL
    MODEL = name


def list_models():
    """Names of models currently registered with the local Ollama daemon —
    both fully local ones and any Ollama Cloud models already pulled."""
    import ollama
    return sorted(m["model"] for m in ollama.list()["models"])


def pull_model(name):
    """Register a model with the local daemon — for a cloud model this just
    links the name to ollama.com's hosted weights (needs `ollama signin`),
    it does not download anything sizeable."""
    import ollama
    ollama.pull(name)

# Some small models don't reliably use Ollama's structured tool_calls field —
# this model's own chat template tells it to emit "<tool_call>{json}</tool_call>"
# as plain text instead, and it sometimes also hallucinates a fake result
# right after the call (e.g. "{...}\n\n# Output:\n...") in the same message.
# Scan the whole text for the first valid {"name": ..., "arguments": {...}}
# object — using a real JSON decoder (not regex) so nested braces in
# arguments don't break matching — execute that for real, and discard
# whatever the model invented around it.
_decoder = json.JSONDecoder()


def _extract_fallback_call(content):
    """Recover a {"name": ..., "arguments": ...} call embedded anywhere in text."""
    idx = content.find("{")
    while idx != -1:
        try:
            data, end = _decoder.raw_decode(content, idx)
        except json.JSONDecodeError:
            idx = content.find("{", idx + 1)
            continue
        if (isinstance(data, dict) and isinstance(data.get("name"), str)
                and isinstance(data.get("arguments"), dict)):
            return {"name": data["name"], "arguments": data["arguments"]}
        idx = content.find("{", max(end, idx + 1))
    return None


def _matching_paren(content, open_pos):
    depth = 0
    for i in range(open_pos, len(content)):
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _param_order(tools):
    """tool name -> its parameters in declared order, from the schemas
    passed into chat(). Lets us map positional args below back to names."""
    order = {}
    for schema in tools or []:
        fn = schema.get("function", {})
        name = fn.get("name")
        if name:
            order[name] = list(fn.get("parameters", {}).get("properties", {}))
    return order


def _extract_call_expr(content, tools):
    """Recover pseudo-code like write_file("a.py", "text") or
    run_command(command="ls") — some models fall back to a literal Python
    call instead of the expected JSON tool-call format. Parsed with ast,
    never eval'd, so this can't execute arbitrary code."""
    for name, params in _param_order(tools).items():
        token = name + "("
        start = 0
        while True:
            pos = content.find(token, start)
            if pos == -1:
                break
            start = pos + 1
            is_boundary = pos == 0 or not (content[pos - 1].isalnum() or content[pos - 1] == "_")
            if not is_boundary:
                continue
            end = _matching_paren(content, pos + len(name))
            if end is None:
                continue
            try:
                node = ast.parse(content[pos:end + 1], mode="eval").body
                if not isinstance(node, ast.Call):
                    continue
                args = [ast.literal_eval(a) for a in node.args]
                kwargs = {kw.arg: ast.literal_eval(kw.value)
                        for kw in node.keywords if kw.arg is not None}
            except (SyntaxError, ValueError, TypeError):
                continue
            for param, value in zip(params, args):
                kwargs.setdefault(param, value)
            return {"name": name, "arguments": kwargs}
    return None


def chat(messages, tools=None):
    """Send messages to the model. Returns a plain dict:
       {"content": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}
    """

    import ollama

    # think=False: "thinking" models (e.g. qwen3.5) otherwise spend their
    # whole turn on the internal <thinking> field and stop before producing
    # any content or tool call at all, which the agent loop then reads as
    # "task finished" with nothing done. Forcing the answer straight into
    # content keeps every model on the same code path below.
    response = ollama.chat(model=MODEL, messages=messages, tools=tools, think=False)
    msg = response["message"]

    calls = []
    for call in (msg.get("tool_calls") or []):
        fn = call["function"]
        args = fn["arguments"]
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        calls.append({"name": fn["name"], "arguments": args})

    content = msg.get("content") or ""
    if not calls:
        fallback = _extract_fallback_call(content) or _extract_call_expr(content, tools)
        if fallback:
            calls.append(fallback)
            content = ""

    return {"content": content, "tool_calls": calls}
