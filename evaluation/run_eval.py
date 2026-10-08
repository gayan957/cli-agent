"""Runs every task N times and appends to results/results.csv."""

import csv
import json
import os
import subprocess
import sys
import time

os.environ["VA_AUTO_APPROVE"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "va-agent"))

import fixture                      # noqa: E402
import tools                        # noqa: E402
from agent import run               # noqa: E402

RUNS = int(os.environ.get("VA_RUNS", "3"))
SYSTEM_ID = os.environ.get("VA_SYSTEM", "A")
MODEL = os.environ.get("VA_MODEL", "qwen-coder-32k")
OUT = os.environ.get("VA_OUT",
                     os.path.join(HERE, "..", "results", "results.csv"))
TASKS = os.environ.get("VA_TASKS", os.path.join(HERE, "tasks.json"))

COLUMNS = ["timestamp", "system", "model", "task_id", "difficulty", "files",
           "run", "success", "steps", "tool_calls", "tool_failures",
           "seconds", "finished"]


def verify(command, cwd):
    if not command:
        return 0
    try:
        done = subprocess.run(command, shell=True, cwd=cwd,
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 0
    return 2 if done.returncode == 0 else 0


def main():
    tasks = json.load(open(TASKS))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    is_new = not os.path.exists(OUT)

    with open(OUT, "a", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(COLUMNS)

        for task in tasks:
            for attempt in range(1, RUNS + 1):
                workdir = fixture.build()
                tools.SANDBOX = workdir          # module attribute, not import
                os.chdir(workdir)

                print(f"\n=== {task['id']} run {attempt} ===", flush=True)
                try:
                    stats = run(task["prompt"], verbose=False)
                except Exception as error:
                    print(f"    agent crashed: {error}")
                    stats = {"steps": 0, "tool_calls": 0, "tool_failures": 1,
                             "seconds": 0, "finished": False}

                score = verify(task.get("verify"), workdir)

                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M"), SYSTEM_ID, MODEL,
                    task["id"], task["difficulty"], task.get("files", 1),
                    attempt, score, stats["steps"], stats["tool_calls"],
                    stats["tool_failures"], stats["seconds"],
                    int(stats.get("finished", False)),
                ])
                handle.flush()

                print(f"    success={score} steps={stats['steps']} "
                      f"failures={stats['tool_failures']} "
                      f"time={stats['seconds']}s", flush=True)

                os.chdir(HERE)
                fixture.destroy(workdir)


if __name__ == "__main__":
    main()