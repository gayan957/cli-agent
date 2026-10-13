# Decision Log

Format: date — decision — why — alternatives rejected

## 2026-10-05 — Keep existing code, restructure rather than rewrite
~800 lines already work. llm.py's text-parsing fallbacks solve a real
problem with weak local models that would need re-solving.

## 2026-10-05 — Moved project out of ~/Documents
iCloud sync caused TimeoutError on evicted files (see report §8.11).
Silent failures during unattended benchmark runs would invalidate results.

## 2026-10-08 — Archived pre-fixture results, started a fresh results.csv
The 3 old rows (E1, success=0) were invalid: workspace/ was empty and not a
git repo, so the test file never existed (report §8.1). They also used the
old 11-column schema (no `files`/`finished`), so appending new rows would
misalign. Moved to results/results_invalid_pre_fixture.csv; run_eval.py
writes a new results.csv with the 13-column header.

## 2026-10-08 — Fixed §8.7 and §8.8
- cli.py `--print`: one-shot, no prompts (sets VA_AUTO_APPROVE before
  safety.py is imported, skips workspace/model pickers).
- ui.summary: shows finished/unfinished, yellow unless finished with zero
  tool failures.

## Baseline (System A, qwen-coder-32k) — TODO after Step 8

## 2026-10-08 — Baseline run interrupted by a branch switch; resumed from M1
`git checkout develop` during the run replaced results.csv and
baseline_log.txt on disk. The running process kept appending to the old,
deleted files, so rows 22–60 were silently lost (exit code 0). The 21 easy
rows (E1–E7) already on disk are valid and kept. M1–H6 were rerun the same
day with identical code, model and tasks, appended to the same file.
Lesson: never touch git in this checkout while a benchmark is running.
Second attempt (12:39) was also invalid: Ollama was not running, so all 39
runs failed instantly on an LLM error (hidden by verbose=False) and still
wrote rows. Those rows were deleted. run_eval.py now has a preflight() that
exits before writing anything if Ollama or the model is unavailable.

## 2026-10-08 — Task M6 excluded (broken verify, task left unedited)
M6's verify passes on the untouched fixture: json.load raises
json.JSONDecodeError, which is a subclass of ValueError, so
`pytest.raises(ValueError, NoteStore(p).load)` succeeds with zero work.
Found when M6 scored success=2 in runs where the agent made no calls.
Per the frozen-task rule, tasks.json is not edited. M6 rows are still
recorded but excluded from reported success rates (report on 19 tasks).

## 2026-10-12 — Task E1 excluded (no-op task, task left unedited)
All four functions in notes/search.py already have docstrings in the
fixture, so E1's verify passes with zero work; it only fails if the agent
breaks the file. Found when a run with Ollama down scored success=2.
With M6, 2 of 20 tasks are excluded: report on 18 tasks (6 easy, 6 medium,
6 hard). A scan of all 20 verifies against the untouched fixture found no
others.

## 2026-10-12 — Fixed regression from va_agent/ package restructure
The restructure (a) appended normalised {"name","arguments"} tool calls to
history, which Ollama rejects (needs {"function": {...}}), so every task
crashed on step 2; (b) shortened the system prompt, dropping the lines that
stop the model pasting code as text; (c) treated a blank reply as finished.
All three restored to pre-restructure behaviour; regression test added in
tests/test_agent.py. Root `pytest` now limited to tests/ via pyproject.toml
(fixture_repo tests must be run from inside that folder).

The 21 easy rows from 2026-10-08 were produced by the pre-restructure code.
