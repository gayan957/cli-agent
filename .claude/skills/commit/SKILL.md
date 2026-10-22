---
name: commit
description: Stages changes and writes a conventional commit message. Use when the user asks to commit, or to save their work to git.
---

# Commit

1. Run `git status` and `git diff` to see what changed.
2. Run `pytest -q`. Do not commit with failing tests — say so and stop.
3. Stage only files relevant to one logical change.
4. Write the message as `type: summary`, where type is feat, fix, docs, test,
   refactor or chore. Keep the summary under 60 characters, imperative mood.
5. Show the message and wait for approval before committing.
