---
name: code-review
description: Reviews uncommitted changes for bugs and style issues. Use when the user asks for a review, asks to check their work, or mentions a pull request.
---

# Code Review

1. Run `git diff` to see uncommitted changes. If empty, try `git diff --staged`.
2. Read each changed file for context before judging the diff.
3. Report findings grouped by severity: Bug, Risk, Style.
4. For each finding give the file, the line, and a concrete fix.
5. Run `pytest -q`. Never approve changes with failing tests.
6. If nothing is wrong, say so plainly rather than inventing issues.
