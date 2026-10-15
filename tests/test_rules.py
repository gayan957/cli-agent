"""Path-scoped rules tests. No Ollama, no network."""

import pytest

from va_agent.core import config
from va_agent.harness import rules
from va_agent.harness.rules import BROKEN, matches, parse, patterns_of


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Empty project and user rule dirs, rules on, nothing injected."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(project))
    monkeypatch.setattr(config, "RULES_ENABLED", True)
    monkeypatch.setattr(config, "RULES", [])
    monkeypatch.setattr(config, "RULES_INJECTED", set())
    monkeypatch.setattr(config, "RULES_BROKEN", [])
    monkeypatch.setattr(rules, "USER_RULES_DIR", str(tmp_path / "home_rules"))
    rules.reset_rules()
    yield project
    rules.reset_rules()


def write_rule(base, name, text):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def project_rules(project):
    return project / ".claude" / "rules"


# ------------------------------------------------------------ glob matching

@pytest.mark.parametrize("path, pattern, expected", [
    ("src/api/orders.py", "src/api/**", True),
    ("src/api/orders.py", "src/db/**", False),
    ("notes/models.py", "**/*.py", True),
    ("models.py", "**/*.py", True),
    (".env", "**/.env*", True),
    ("config/.env.local", "**/.env*", True),
    ("tests/test_a.py", "tests/*", True),
    ("tests/sub/x.py", "tests/*", False),
    ("tests/sub/x.py", "tests/**", True),
    ("migrations/001.sql", "**/*.sql", True),
    ("notes/cli.py", "*.py", True),
    ("README.md", "**/*.py", False),
    ("src/a/b/c/deep.py", "src/**", True),
])
def test_glob_table(path, pattern, expected):
    assert matches(path, pattern) is expected


def test_leading_dot_slash_keeps_dotfiles():
    assert matches("./.env", ".env")
    assert matches("./notes/models.py", "notes/*.py")


def test_regex_characters_are_literal():
    assert matches("a(1).py", "a(1).py")
    assert not matches("aXpy", "a.py")


# ------------------------------------------------------------ frontmatter

EXPECTED = ["src/**", "lib/**"]


@pytest.mark.parametrize("frontmatter", [
    "paths: src/**, lib/**",
    'paths: "src/**, lib/**"',
    "paths: [src/**, lib/**]",
    'globs: ["src/**", "lib/**"]',
    "paths:\n  - src/**\n  - lib/**",
    "paths:\n  - 'src/**'\n  - \"lib/**\"",
])
def test_frontmatter_forms_agree(frontmatter):
    meta, body = parse(f"---\n{frontmatter}\n---\nBody text\n")
    assert patterns_of(meta) == EXPECTED
    assert body.strip() == "Body text"


@pytest.mark.parametrize("frontmatter, expected", [
    ("paths: src/api/**", ["src/api/**"]),
    ('paths: "src/api/**"', ["src/api/**"]),
    ('globs: ["**/*.py"]', ["**/*.py"]),
])
def test_single_pattern_forms(frontmatter, expected):
    meta, _ = parse(f"---\n{frontmatter}\n---\nx")
    assert patterns_of(meta) == expected


def test_paths_wins_over_globs():
    meta, _ = parse("---\npaths: a/**\nglobs: b/**\n---\nx")
    assert patterns_of(meta) == ["a/**"]


def test_no_frontmatter_is_global():
    meta, body = parse("# Style\nUse type hints.\n")
    assert meta == {} and patterns_of(meta) is None
    assert body.startswith("# Style")


def test_empty_patterns_are_broken_not_global():
    meta, _ = parse('---\npaths: ["", " "]\n---\nx')
    assert patterns_of(meta) is BROKEN


def test_empty_paths_string_is_broken():
    meta, _ = parse('---\npaths: ""\n---\nx')
    assert patterns_of(meta) is BROKEN


def test_list_frontmatter_is_broken():
    meta, _ = parse("---\n- src/**\n- lib/**\n---\nx")
    assert meta is None
    assert patterns_of(meta) is BROKEN


def test_unquoted_leading_star_is_broken_not_global():
    # "**/*.py" unquoted is a YAML alias error: must not become global.
    meta, _ = parse("---\npaths: **/*.py\n---\nx")
    assert patterns_of(meta) is BROKEN


def test_other_keys_without_patterns_are_global():
    meta, _ = parse("---\ndescription: style\n---\nx")
    assert patterns_of(meta) is None


def test_malformed_yaml_is_broken_not_global_and_does_not_raise():
    meta, body = parse("---\npaths: [unclosed\n---\nBody\n")
    assert meta is None
    assert patterns_of(meta) is BROKEN
    assert body.strip() == "Body"


# ------------------------------------------------------------ discovery

def test_discovery_finds_subdirectories(clean_state):
    base = project_rules(clean_state)
    write_rule(base, "top.md", "global")
    write_rule(base, "backend/api.md", "---\npaths: api/**\n---\napi")
    write_rule(base, "notes.txt", "not a rule")
    found = {rule.name: rule for rule in rules.discover()}
    assert set(found) == {"top.md", "backend/api.md"}
    assert found["backend/api.md"].patterns == ["api/**"]
    assert found["top.md"].source.endswith("top.md")


def test_missing_rules_dir_returns_empty():
    assert rules.discover() == []


def test_discover_populates_config_rules(clean_state):
    write_rule(project_rules(clean_state), "a.md", "x")
    returned = rules.discover()
    assert [rule.name for rule in config.RULES] == ["a.md"]
    assert returned == config.RULES


def test_broken_rule_excluded_and_recorded(clean_state):
    base = project_rules(clean_state)
    write_rule(base, "good.md", "fine")
    write_rule(base, "bad.md", "---\npaths: [unclosed\n---\n# Bad\n")
    write_rule(base, "empty.md", '---\npaths: ""\n---\n# Empty\n')
    rules.discover()

    assert [rule.name for rule in config.RULES] == ["good.md"]
    assert [name for name, _ in config.RULES_BROKEN] == ["bad.md", "empty.md"]
    assert "parse" in dict(config.RULES_BROKEN)["bad.md"]
    assert "bad.md" not in [rule.name for rule in rules.global_rules()]
    assert "# Bad" not in rules.system_text()


def test_project_rule_shadows_user_rule(clean_state, tmp_path):
    write_rule(tmp_path / "home_rules", "style.md", "user version")
    write_rule(tmp_path / "home_rules", "only_user.md", "user only")
    write_rule(project_rules(clean_state), "style.md", "project version")
    found = {rule.name: rule.body for rule in rules.discover()}
    assert found == {"style.md": "project version", "only_user.md": "user only"}


# ------------------------------------------------------------ loading

@pytest.fixture
def three_rules(clean_state):
    base = project_rules(clean_state)
    write_rule(base, "python-style.md", "Use type hints.")
    write_rule(base, "testing.md", "---\npaths: tests/**\n---\nUse pytest.")
    write_rule(base, "storage.md",
               "---\npaths: notes/storage.py\n---\nSave after mutating.")
    return rules.discover()


def names(found):
    return [rule.name for rule in found]


def test_global_rule_is_listed(three_rules):
    assert names(rules.global_rules()) == ["python-style.md"]
    assert "Use type hints." in rules.system_text()


def test_rules_for_matching_path(three_rules):
    assert names(rules.rules_for("tests/a.py")) == ["testing.md"]
    assert rules.rules_for("README.md") == []


def test_rules_for_absolute_path_inside_project(three_rules, clean_state):
    path = str(clean_state / "notes" / "storage.py")
    assert names(rules.rules_for(path)) == ["storage.md"]


def test_injected_rule_is_not_returned_again(three_rules):
    rule, = rules.rules_for("tests/a.py")
    rules.mark_injected(rule, "tests/a.py")
    assert rules.rules_for("tests/a.py") == []
    assert rules.rules_for("tests/other.py") == []
    assert rules.matched_path(rule) == "tests/a.py"


def test_reset_rules_makes_rules_available_again(three_rules):
    rule, = rules.rules_for("tests/a.py")
    rules.mark_injected(rule)
    rules.reset_rules()
    assert names(rules.rules_for("tests/a.py")) == ["testing.md"]


def test_rules_disabled_loads_nothing(three_rules, monkeypatch):
    monkeypatch.setattr(config, "RULES_ENABLED", False)
    assert rules.discover() == []
    assert config.RULES == []
    assert rules.global_rules() == []
    assert rules.rules_for("tests/a.py") == []
    assert rules.system_text() == ""


# ------------------------------------------------------------ agent

def test_agent_injects_rule_after_tool_result(three_rules, clean_state,
                                              monkeypatch):
    (clean_state / "tests").mkdir()
    (clean_state / "tests" / "test_a.py").write_text("def test(): pass\n")
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    monkeypatch.setattr(config, "READ_FILES", set())

    seen = []
    script = iter([
        {"content": "", "tool_calls": [
            {"name": "Read", "arguments": {"file_path": "tests/test_a.py"}}]},
        {"content": "", "tool_calls": [
            {"name": "Read", "arguments": {"file_path": "tests/test_a.py"}}]},
        {"content": "Done.", "tool_calls": []},
    ])

    from va_agent.core import llm

    def chat(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return next(script)

    monkeypatch.setattr(llm, "chat", chat)
    from va_agent.core.agent import run
    run("look at the tests", verbose=False)

    system = seen[0][0]["content"]
    assert "[project rule: python-style.md]" in system
    assert "Use pytest." not in system

    final = seen[-1]
    roles = [m["role"] for m in final]
    injected = [i for i, m in enumerate(final)
                if m["content"].startswith("[project rule: testing.md]")]
    assert len(injected) == 1                       # second Read: no repeat
    assert roles[injected[0]] == "user"
    assert roles[injected[0] - 1] == "tool"         # right after the result


def test_glob_and_grep_do_not_inject(three_rules, clean_state, monkeypatch):
    (clean_state / "tests").mkdir()
    (clean_state / "tests" / "test_a.py").write_text("x = 1\n")
    monkeypatch.setattr(config, "AUTO_APPROVE", True)

    seen = []
    script = iter([
        {"content": "", "tool_calls": [
            {"name": "Glob", "arguments": {"pattern": "*.py",
                                           "path": "tests"}}]},
        {"content": "", "tool_calls": [
            {"name": "Grep", "arguments": {"pattern": "x",
                                           "path": "tests/test_a.py"}}]},
        {"content": "Done.", "tool_calls": []},
    ])

    from va_agent.core import llm

    def chat(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return next(script)

    monkeypatch.setattr(llm, "chat", chat)
    from va_agent.core.agent import run
    run("search", verbose=False)

    assert not any(m["content"].startswith("[project rule: testing.md]")
                   for m in seen[-1])
    assert config.RULES_INJECTED == set()
