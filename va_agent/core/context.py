"""Context management for a small window.

Ollama does not error when ``num_ctx`` is exceeded: it silently drops the
oldest messages, so the model loses the task and the failure looks like
model incompetence. Three layers keep the conversation inside the window:

1. cap each tool result as it is appended (the main defence),
2. clamp any single message to a share of the window (so compaction can
   always shrink what it keeps),
3. compact old history into a summary, with an attempt counter so an
   ineffective compaction can never loop.
"""

from va_agent.core import config

# Prefixes that mark injected content, for breakdown().
RULE_MARK = "[project rule:"
SKILL_MARK = "[skill:"
MEMORY_MARK = "[project memory]"
HOOK_MARK = "[hook:"
SKILLS_INDEX_MARK = "Available skills (call the Skill tool"

CATEGORIES = ("system", "claude_md", "skills_index", "rules", "conversation")


def _size(message: dict) -> int:
    """Characters a message puts in the window: its content plus the
    arguments of any tool calls it carries (a Write call's whole file
    stays in history too)."""
    size = len(str(message.get("content") or ""))
    for call in message.get("tool_calls") or []:
        function = call.get("function", call) if isinstance(call, dict) else {}
        size += len(str(function.get("arguments", "")))
    return size


def estimate(messages: list) -> int:
    """Rough token count: characters // 4. No tokeniser, on purpose."""
    return sum(_size(message) for message in messages) // 4


def _split_system(text: str) -> dict:
    """Characters of a system prompt per category, by marker."""
    marks = {RULE_MARK: "rules", SKILLS_INDEX_MARK: "skills_index",
             MEMORY_MARK: "claude_md", HOOK_MARK: "system"}
    cuts = sorted((index, category)
                  for mark, category in marks.items()
                  for index in _all(text, mark))
    counts = dict.fromkeys(CATEGORIES, 0)
    start, category = 0, "system"
    for index, next_category in cuts:
        counts[category] += index - start
        start, category = index, next_category
    counts[category] += len(text) - start
    return counts


def _all(text: str, mark: str):
    index = text.find(mark)
    while index != -1:
        yield index
        index = text.find(mark, index + 1)


def breakdown(messages: list) -> dict:
    """Tokens per category: system, claude_md, skills_index, rules,
    conversation. Injected content is recognised by its prefix marker;
    rule_messages counts injected path-scoped rules."""
    chars = dict.fromkeys(CATEGORIES, 0)
    rule_messages = 0
    for number, message in enumerate(messages):
        content = str(message.get("content") or "")
        if number == 0 and message.get("role") == "system":
            for category, count in _split_system(content).items():
                chars[category] += count
            continue
        size = _size(message)
        if content.startswith(RULE_MARK):
            chars["rules"] += size
            rule_messages += 1
        elif content.startswith(MEMORY_MARK):
            chars["claude_md"] += size
        elif content.startswith(SKILL_MARK):
            chars["skills_index"] += size
        else:
            chars["conversation"] += size
    tokens = {category: count // 4 for category, count in chars.items()}
    tokens["rule_messages"] = rule_messages
    return tokens


# ---------------------------------------------------------------- limits

def cap(text: str) -> str:
    """Layer 1: cut a tool result to TOOL_RESULT_CAP characters. The marker
    tells the model the output was partial, so it can page with
    Read(offset, limit) if it needs more."""
    text = str(text)
    limit = config.TOOL_RESULT_CAP
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated, {len(text) - limit} chars omitted]"


def message_limit() -> int:
    """Most characters any one message may hold."""
    return int(config.MAX_MESSAGE_SHARE * config.NUM_CTX * 4)


def _clamp_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n...[message truncated]"


def clamp(message: dict) -> dict:
    """Layer 2: no message larger than MAX_MESSAGE_SHARE of the window,
    including long tool-call arguments. Without this an oversized message
    in compaction's kept window survives every compaction, and the agent
    compacts forever. Returns a new dict; the input is not changed."""
    limit = message_limit()
    content = message.get("content")
    clamped = dict(message)
    if isinstance(content, str) and len(content) > limit:
        clamped["content"] = _clamp_text(content, limit)
    calls = message.get("tool_calls")
    if calls:
        new_calls = []
        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            arguments = function.get("arguments") if function else None
            if isinstance(arguments, dict) and any(
                    isinstance(v, str) and len(v) > limit
                    for v in arguments.values()):
                arguments = {key: _clamp_text(value, limit)
                             if isinstance(value, str) else value
                             for key, value in arguments.items()}
                call = {**call, "function": {**function,
                                             "arguments": arguments}}
            new_calls.append(call)
        clamped["tool_calls"] = new_calls
    return clamped
