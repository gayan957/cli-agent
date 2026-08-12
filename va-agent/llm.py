"""Talks to the local model via Ollama and normalises the reply."""

import os

MODEL = os.environ.get("VA_MODEL", "qwen-coder-32k") 

def chat(messages, tools=None):
    """Send messages to the model. Returns a plain dict:
       {"content": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}
    """

    import ollama

    response = ollama.chat(model=MODEL, messages=messages, tools=tools)
    msg = response["message"]

    calls = []
    for call in (msg.get("tool_calls") or []):
        fn = call["function"]
        args = fn["arguments"]
        if isinstance(args, str):
            import json
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        calls.append({"name": msg.get("content") or "", "tool_calls": calls})

    return {"content": msg.get("content") or "", "tool_calls": calls}