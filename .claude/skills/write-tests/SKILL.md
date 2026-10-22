---
name: write-tests
description: Writes pytest tests for existing code following this project's conventions. Use when the user asks for tests or for test coverage.
---

# Write Tests

1. Read the code under test before writing anything.
2. Look at an existing file in tests/ and copy its conventions.
3. Use pytest, never unittest. Use `tmp_path` for files.
4. Use `monkeypatch` to set `config` values, never assign them directly.
5. One behaviour per test; name it after the behaviour, not the function.
6. Cover the failure cases, not just the happy path.
7. Run `pytest -q` and fix anything that fails.
