"""Runs every task N times and writes results/results.csv."""
import csv, json, os, subprocess, sys, time

os.environ["VA_AUTO_APPROVE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "va-agent"))

from agent import run          # noqa: E402
from tools import SANDBOX      # noqa: E402

RUNS = 3
OUT = os.path.join(os.path.dirname(__file__), "..", "results", "results.csv")
SYSTEM_ID = os.environ.get("VA_SYSTEM", "A")
MODEL = os.environ.get("VA_MODEL", "qwen-coder-32k")


def reset():
    subprocess.run(["git", "checkout", "."], cwd=SANDBOX, capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=SANDBOX, capture_output=True)


def verify(command):
    if not command:
        return ""
    done = subprocess.run(command, shell=True, cwd=SANDBOX,
                        capture_output=True, text=True, timeout=120)
    return 2 if done.returncode == 0 else 0


def main():
    tasks = json.load(open(os.path.join(os.path.dirname(__file__), "tasks.json")))
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["timestamp", "system", "model", "task_id",
                            "difficulty", "run", "success", "steps",
                            "tool_calls", "tool_failures", "seconds"])
        for task in tasks:
            for attempt in range(1, RUNS + 1):
                reset()
                print(f"\n=== {task['id']} run {attempt} ===")
                stats = run(task["prompt"], verbose=False)
                score = verify(task.get("verify"))
                writer.writerow([time.strftime("%Y-%m-%d %H:%M"), SYSTEM_ID,
                                MODEL, task["id"], task["difficulty"], attempt,
                                score, stats["steps"], stats["tool_calls"],
                                stats["tool_failures"], stats["seconds"]])
                handle.flush()
                print(f"    success={score} steps={stats['steps']} "
                    f"failures={stats['tool_failures']} time={stats['seconds']}s")
    reset()


if __name__ == "__main__":
    main()