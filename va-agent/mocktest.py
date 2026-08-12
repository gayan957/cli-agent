"""Verifies the agent logic with a fake model. No Ollama needed."""
import os, shutil

os.environ["VA_AUTO_APPROVE"] = "1"
os.environ["VA_SANDBOX"] = os.path.abspath("./testbox")

shutil.rmtree("./testbox", ignore_errors=True)
os.makedirs("./testbox")
open("./testbox/main.py", "w").write("def add(a, b):\n    return a + b\n")

import llm

SCRIPT = [
    {"content": "", "tool_calls": [{"name": "list_files", "arguments": {}}]},
    {"content": "", "tool_calls": [{"name": "grep", "arguments": {"pattern": "def add"}}]},
    {"content": "", "tool_calls": [{"name": "read_file", "arguments": {"path": "main.py"}}]},
    {"content": "", "tool_calls": [{"name": "write_file", "arguments": {
        "path": "main.py",
        "content": '"""Math helpers."""\n\n\ndef add(a, b):\n    return a + b\n'}}]},
    {"content": "", "tool_calls": [{"name": "run_command", "arguments": {
        "command": "python3 -c \"import main; print(main.add(2,3))\""}}]},
    # deliberate failures to check error handling
    {"content": "", "tool_calls": [{"name": "nonexistent_tool", "arguments": {}}]},
    {"content": "", "tool_calls": [{"name": "read_file", "arguments": {"path": "../../etc/passwd"}}]},
    {"content": "", "tool_calls": [{"name": "read_file", "arguments": {"wrong_arg": "x"}}]},
    {"content": "", "tool_calls": [{"name": "run_command", "arguments": {"command": "rm -rf /"}}]},
    {"content": "Done: added a docstring and verified add(2,3)=5.", "tool_calls": []},
]
step = iter(SCRIPT)
llm.chat = lambda messages, tools=None: next(step)

from agent import run
stats = run("Add a docstring to main.py and test it")

print("\nSTATS:", stats)
assert stats["finished"] is True, "agent did not finish"
assert stats["tool_failures"] == 3, f"expected 3 failures, got {stats['tool_failures']}"
assert "Math helpers" in open("./testbox/main.py").read()
print("ALL CHECKS PASSED")