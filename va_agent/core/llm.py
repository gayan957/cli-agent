"""Talks to the local model via Ollama and normalises the reply.

Every caller gets the same shape back::

    {"content": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}

Ollama's own reply shape varies between library versions, and small models
sometimes emit a tool call as plain text instead of a structured one, so all
of that mess is handled here and nowhere else.
"""

import json
import re

from va_agent.core import config

# Matches a fenced ```json ... ``` block, or a bare {...} object.
_FENCE = re.compile(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", re.DOTALL)
_BARE = re.compile(r"(\{(?:[^{}]|\{[^{}]*\})*\})", re.DOTALL)


def _coerce_arguments(raw):
    """Arguments may arrive as a dict or as a JSON string."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _structured_calls(message):
    """Pull tool calls out of a well-formed Ollama reply."""
    calls = []
    for call in (message.get("tool_calls") or []):
        function = call.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        calls.append({"name": name,
                      "arguments": _coerce_arguments(function.get("arguments"))})
    return calls


def _fallback_calls(text, known_tools):
    """Recover a tool call that the model wrote as plain text.

    Small models often ignore the tool-calling format and emit something
    like ``{"name": "read_file", "arguments": {"path": "main.py"}}`` inside
    a code fence. Without this, those turns look like a refusal to act.
    """
    if not text or not known_tools:
        return []

    candidates = _FENCE.findall(text) or _BARE.findall(text)
    calls = []
    for blob in candidates:
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        name = data.get("name") or data.get("tool") or data.get("function")
        if isinstance(name, dict):
            name = name.get("name")
        if name not in known_tools:
            continue

        args = (data.get("arguments") if "arguments" in data
                else data.get("parameters", data.get("input", {})))
        calls.append({"name": name, "arguments": _coerce_arguments(args)})

    return calls


def chat(messages, tools=None):
    """Send messages to the model and return a normalised reply."""
    import ollama

    response = ollama.chat(model=config.MODEL, messages=messages, tools=tools)
    message = response["message"]

    content = message.get("content") or ""
    calls = _structured_calls(message)

    if not calls and tools:
        known = {t["function"]["name"] for t in tools}
        calls = _fallback_calls(content, known)
        if calls:
            # The text was really a tool call, not an answer.
            content = ""

    return {"content": content, "tool_calls": calls}