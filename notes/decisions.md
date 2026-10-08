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
