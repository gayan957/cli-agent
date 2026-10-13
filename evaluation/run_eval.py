"""Runs every task N times and appends to results/results.csv.

Run from the project root:
    VA_SYSTEM=A VA_RUNS=3 python evaluation/run_eval.py
"""

import csv
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixture                                  # noqa: E402

from va_agent.core import config                # noqa: E402
from va_agent.core.agent import run             # noqa: E402

config.AUTO_APPROVE = True

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("VA_OUT",
                     os.path.join(HERE, "..", "results", "results.csv"))
TASKS = os.environ.get("VA_TASKS", os.path.join(HERE, "tasks.json"))

RUNS = int(os.environ.get("VA_RUNS", "3"))
SYSTEM_ID = os.environ.get("VA_SYSTEM", "A")

COLUMNS = ["timestamp", "system", "model", "task_id", "difficulty", "files",
           "run", "success", "steps", "tool_calls", "tool_failures",
           "seconds", "finished"]


def verify(command, cwd):
    """Run a task's verify command. 2 = passed, 0 = failed."""
    if not command:
        return 0
    try:
        done = subprocess.run(command, shell=True, cwd=cwd,
                              capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return 0
    return 2 if done.returncode == 0 else 0


def preflight():
    """Refuse to start unless Ollama is up and has the model. Without this,
    every run fails instantly on an LLM error and still writes a row."""
    import ollama
    try:
        models = [m["model"] for m in ollama.list()["models"]]
    except Exception as error:
        sys.exit(f"Ollama is not reachable ({error}). Start it first.")
    if not any(m.split(":")[0] == config.MODEL.split(":")[0] for m in models):
        sys.exit(f"Model {config.MODEL!r} not found in Ollama: {models}")


def main():
    preflight()
    with open(TASKS) as handle:
        tasks = json.load(handle)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    is_new = not os.path.exists(OUT)

    with open(OUT, "a", newline="") as out_handle:
        writer = csv.writer(out_handle)
        if is_new:
            writer.writerow(COLUMNS)

        for task in tasks:
            for attempt in range(1, RUNS + 1):
                workdir = fixture.build()
                config.SANDBOX = workdir
                os.chdir(workdir)

                print(f"\n=== {task['id']} run {attempt} ===", flush=True)
                try:
                    stats = run(task["prompt"], verbose=False)
                except Exception as error:
                    print(f"    agent crashed: {error}")
                    stats = {"steps": 0, "tool_calls": 0, "tool_failures": 1,
                             "seconds": 0.0, "finished": False}

                score = verify(task.get("verify"), workdir)

                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M"), SYSTEM_ID, config.MODEL,
                    task["id"], task["difficulty"], task.get("files", 1),
                    attempt, score, stats["steps"], stats["tool_calls"],
                    stats["tool_failures"], stats["seconds"],
                    int(stats.get("finished", False)),
                ])
                out_handle.flush()

                print(f"    success={score} steps={stats['steps']} "
                      f"failures={stats['tool_failures']} "
                      f"time={stats['seconds']}s", flush=True)

                os.chdir(HERE)
                fixture.destroy(workdir)


if __name__ == "__main__":
    main()